package migrations

import (
	"context"
	"errors"
	"fmt"
	"sort"
)

var (
	ErrChecksumDrift      = errors.New("migration checksum drift")
	ErrInvalidAppliedState = errors.New("invalid applied migration state")
	ErrInvalidTarget       = errors.New("invalid migration target")
)

// Applied records the durable identity of one successfully applied migration.
type Applied struct {
	Version  uint64
	Name     string
	Checksum string
}

// LockedStore is the transaction-scoped migration storage boundary.
// Implementations must keep the runner lock and transaction ownership active
// for the entire callback passed to Locker.WithLock.
type LockedStore interface {
	Applied(context.Context) ([]Applied, error)
	Apply(context.Context, Step) error
	Rollback(context.Context, Step) error
}

// Locker serializes migration execution. Implementations are responsible for
// acquiring a database-scoped lock and releasing it after the callback returns.
type Locker interface {
	WithLock(context.Context, func(LockedStore) error) error
}

// Runner validates durable migration history before applying or rolling back
// deterministic embedded plans. It never opens a database connection itself.
type Runner struct {
	locker Locker
}

func NewRunner(locker Locker) (*Runner, error) {
	if locker == nil {
		return nil, errors.New("migration locker is required")
	}
	return &Runner{locker: locker}, nil
}

// Migrate moves the schema to targetVersion. Target zero rolls back all known
// migrations. A target above the latest embedded version is rejected.
func (r *Runner) Migrate(ctx context.Context, targetVersion uint64) error {
	if ctx == nil {
		return errors.New("migration context is required")
	}

	up, err := Plan(DirectionUp)
	if err != nil {
		return err
	}
	if targetVersion > up[len(up)-1].Version {
		return fmt.Errorf("%w: %d", ErrInvalidTarget, targetVersion)
	}
	down, err := Plan(DirectionDown)
	if err != nil {
		return err
	}

	return r.locker.WithLock(ctx, func(store LockedStore) error {
		if store == nil {
			return errors.New("locked migration store is required")
		}
		applied, err := store.Applied(ctx)
		if err != nil {
			return fmt.Errorf("read applied migrations: %w", err)
		}
		current, err := validateApplied(applied, up)
		if err != nil {
			return err
		}

		if current < targetVersion {
			for _, step := range up {
				if step.Version <= current || step.Version > targetVersion {
					continue
				}
				if err := store.Apply(ctx, step); err != nil {
					return fmt.Errorf("apply migration %04d: %w", step.Version, err)
				}
			}
			return nil
		}

		for _, step := range down {
			if step.Version > current || step.Version <= targetVersion {
				continue
			}
			if err := store.Rollback(ctx, step); err != nil {
				return fmt.Errorf("rollback migration %04d: %w", step.Version, err)
			}
		}
		return nil
	})
}

func validateApplied(applied []Applied, plan []Step) (uint64, error) {
	if len(applied) == 0 {
		return 0, nil
	}
	copyApplied := append([]Applied(nil), applied...)
	sort.Slice(copyApplied, func(i, j int) bool { return copyApplied[i].Version < copyApplied[j].Version })
	if len(copyApplied) > len(plan) {
		return 0, fmt.Errorf("%w: applied version exceeds embedded plan", ErrInvalidAppliedState)
	}
	for index, record := range copyApplied {
		expectedVersion := uint64(index + 1)
		if record.Version != expectedVersion {
			return 0, fmt.Errorf("%w: expected version %04d, found %04d", ErrInvalidAppliedState, expectedVersion, record.Version)
		}
		expected := plan[index]
		if record.Name != expected.Name || record.Checksum != expected.Checksum {
			return 0, fmt.Errorf("%w: version %04d", ErrChecksumDrift, record.Version)
		}
	}
	return copyApplied[len(copyApplied)-1].Version, nil
}
