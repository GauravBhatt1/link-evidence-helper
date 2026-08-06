package migrations

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"time"
)

var (
	ErrChecksumDrift   = errors.New("migration checksum drift")
	ErrMigrationLocked = errors.New("migration lock unavailable")
	ErrInvalidHistory  = errors.New("invalid migration history")
)

// Applied records the immutable identity of a migration already committed.
type Applied struct {
	Version   uint64
	Name      string
	Checksum  string
	AppliedAt time.Time
}

// LockedStore is available only while the backend's migration lock is held.
// Apply and Rollback must each be atomic at the database boundary.
type LockedStore interface {
	Applied(context.Context) ([]Applied, error)
	Apply(context.Context, Step) error
	Rollback(context.Context, Step) error
}

// Locker guarantees that at most one runner can inspect and mutate migration
// history at a time. Implementations must release the lock on every exit path.
type Locker interface {
	WithMigrationLock(context.Context, func(LockedStore) error) error
}

// Runner executes deterministic plans against an explicitly supplied locked
// backend. It never opens a database, reads credentials, or runs implicitly.
type Runner struct {
	backend Locker
}

func NewRunner(backend Locker) (*Runner, error) {
	if backend == nil {
		return nil, errors.New("migration backend is required")
	}
	return &Runner{backend: backend}, nil
}

// Up applies every missing migration in ascending order after validating all
// recorded checksums. Existing history is never rewritten.
func (runner *Runner) Up(ctx context.Context) error {
	steps, err := Plan(DirectionUp)
	if err != nil {
		return err
	}
	return runner.backend.WithMigrationLock(ctx, func(store LockedStore) error {
		history, err := store.Applied(ctx)
		if err != nil {
			return fmt.Errorf("read migration history: %w", err)
		}
		applied, err := validateHistory(history, steps)
		if err != nil {
			return err
		}
		for _, step := range steps {
			if applied[step.Version] {
				continue
			}
			if err := store.Apply(ctx, step); err != nil {
				return fmt.Errorf("apply migration %04d: %w", step.Version, err)
			}
		}
		return nil
	})
}

// Down rolls back exactly one latest migration. The current checksum must match
// the reviewed up migration before its paired down migration can execute.
func (runner *Runner) Down(ctx context.Context) error {
	upSteps, err := Plan(DirectionUp)
	if err != nil {
		return err
	}
	downSteps, err := Plan(DirectionDown)
	if err != nil {
		return err
	}
	return runner.backend.WithMigrationLock(ctx, func(store LockedStore) error {
		history, err := store.Applied(ctx)
		if err != nil {
			return fmt.Errorf("read migration history: %w", err)
		}
		applied, err := validateHistory(history, upSteps)
		if err != nil {
			return err
		}
		if len(applied) == 0 {
			return nil
		}
		latest := uint64(0)
		for version := range applied {
			if version > latest {
				latest = version
			}
		}
		for _, step := range downSteps {
			if step.Version == latest {
				if err := store.Rollback(ctx, step); err != nil {
					return fmt.Errorf("rollback migration %04d: %w", step.Version, err)
				}
				return nil
			}
		}
		return fmt.Errorf("%w: missing rollback for version %04d", ErrInvalidHistory, latest)
	})
}

func validateHistory(history []Applied, plan []Step) (map[uint64]bool, error) {
	known := make(map[uint64]Step, len(plan))
	for _, step := range plan {
		known[step.Version] = step
	}
	sorted := append([]Applied(nil), history...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].Version < sorted[j].Version })
	applied := make(map[uint64]bool, len(sorted))
	for index, record := range sorted {
		if record.Version == 0 || record.Version != uint64(index+1) {
			return nil, fmt.Errorf("%w: non-contiguous version %04d", ErrInvalidHistory, record.Version)
		}
		step, ok := known[record.Version]
		if !ok || step.Name != record.Name {
			return nil, fmt.Errorf("%w: unknown migration %04d", ErrInvalidHistory, record.Version)
		}
		if step.Checksum != record.Checksum {
			return nil, fmt.Errorf("%w: version %04d", ErrChecksumDrift, record.Version)
		}
		if record.AppliedAt.IsZero() {
			return nil, fmt.Errorf("%w: missing applied timestamp for version %04d", ErrInvalidHistory, record.Version)
		}
		if applied[record.Version] {
			return nil, fmt.Errorf("%w: duplicate version %04d", ErrInvalidHistory, record.Version)
		}
		applied[record.Version] = true
	}
	return applied, nil
}
