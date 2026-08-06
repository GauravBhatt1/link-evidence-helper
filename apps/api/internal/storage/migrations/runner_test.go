package migrations

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"
)

type fakeMigrationBackend struct {
	mu      sync.Mutex
	locked  bool
	history []Applied
	applied []Step
	rolled  []Step
	failOn  string
}

func (backend *fakeMigrationBackend) WithMigrationLock(_ context.Context, fn func(LockedStore) error) error {
	backend.mu.Lock()
	defer backend.mu.Unlock()
	if backend.locked {
		return ErrMigrationLocked
	}
	backend.locked = true
	defer func() { backend.locked = false }()
	return fn(backend)
}

func (backend *fakeMigrationBackend) Applied(context.Context) ([]Applied, error) {
	if backend.failOn == "history" {
		return nil, errors.New("history unavailable")
	}
	return append([]Applied(nil), backend.history...), nil
}

func (backend *fakeMigrationBackend) Apply(_ context.Context, step Step) error {
	if backend.failOn == "apply" {
		return errors.New("apply failed")
	}
	backend.applied = append(backend.applied, step)
	backend.history = append(backend.history, Applied{
		Version: step.Version, Name: step.Name, Checksum: step.Checksum, AppliedAt: time.Now().UTC(),
	})
	return nil
}

func (backend *fakeMigrationBackend) Rollback(_ context.Context, step Step) error {
	if backend.failOn == "rollback" {
		return errors.New("rollback failed")
	}
	backend.rolled = append(backend.rolled, step)
	backend.history = backend.history[:len(backend.history)-1]
	return nil
}

func TestRunnerAppliesMissingMigrationsAndIsIdempotent(t *testing.T) {
	backend := &fakeMigrationBackend{}
	runner, err := NewRunner(backend)
	if err != nil {
		t.Fatalf("NewRunner() error = %v", err)
	}
	if err := runner.Up(context.Background()); err != nil {
		t.Fatalf("Up() error = %v", err)
	}
	if len(backend.applied) != 1 || backend.applied[0].Version != 1 {
		t.Fatalf("applied = %#v", backend.applied)
	}
	if err := runner.Up(context.Background()); err != nil {
		t.Fatalf("second Up() error = %v", err)
	}
	if len(backend.applied) != 1 {
		t.Fatalf("second Up() reapplied migrations: %#v", backend.applied)
	}
}

func TestRunnerRejectsChecksumDriftBeforeMutation(t *testing.T) {
	steps, err := Plan(DirectionUp)
	if err != nil {
		t.Fatalf("Plan() error = %v", err)
	}
	backend := &fakeMigrationBackend{history: []Applied{{
		Version: 1, Name: steps[0].Name, Checksum: "tampered", AppliedAt: time.Now().UTC(),
	}}}
	runner, _ := NewRunner(backend)
	err = runner.Up(context.Background())
	if !errors.Is(err, ErrChecksumDrift) {
		t.Fatalf("Up() error = %v, want checksum drift", err)
	}
	if len(backend.applied) != 0 {
		t.Fatalf("mutated after drift: %#v", backend.applied)
	}
}

func TestRunnerRejectsNonContiguousHistory(t *testing.T) {
	steps, err := Plan(DirectionUp)
	if err != nil {
		t.Fatalf("Plan() error = %v", err)
	}
	backend := &fakeMigrationBackend{history: []Applied{{
		Version: 2, Name: steps[0].Name, Checksum: steps[0].Checksum, AppliedAt: time.Now().UTC(),
	}}}
	runner, _ := NewRunner(backend)
	err = runner.Up(context.Background())
	if !errors.Is(err, ErrInvalidHistory) {
		t.Fatalf("Up() error = %v, want invalid history", err)
	}
}

func TestRunnerRollsBackExactlyLatestMigration(t *testing.T) {
	steps, err := Plan(DirectionUp)
	if err != nil {
		t.Fatalf("Plan() error = %v", err)
	}
	backend := &fakeMigrationBackend{history: []Applied{{
		Version: steps[0].Version, Name: steps[0].Name, Checksum: steps[0].Checksum, AppliedAt: time.Now().UTC(),
	}}}
	runner, _ := NewRunner(backend)
	if err := runner.Down(context.Background()); err != nil {
		t.Fatalf("Down() error = %v", err)
	}
	if len(backend.rolled) != 1 || backend.rolled[0].Direction != DirectionDown {
		t.Fatalf("rolled = %#v", backend.rolled)
	}
	if len(backend.history) != 0 {
		t.Fatalf("history after rollback = %#v", backend.history)
	}
}

func TestRunnerReleasesLockAfterFailure(t *testing.T) {
	backend := &fakeMigrationBackend{failOn: "apply"}
	runner, _ := NewRunner(backend)
	if err := runner.Up(context.Background()); err == nil {
		t.Fatal("Up() error = nil, want failure")
	}
	backend.failOn = ""
	if err := runner.Up(context.Background()); err != nil {
		t.Fatalf("Up() after failure error = %v", err)
	}
}

func TestNewRunnerRejectsNilBackend(t *testing.T) {
	if _, err := NewRunner(nil); err == nil {
		t.Fatal("NewRunner(nil) error = nil")
	}
}
