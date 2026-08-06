package migrations

import (
	"context"
	"errors"
	"reflect"
	"testing"
)

type fakeLocker struct {
	store *fakeLockedStore
	calls int
	err   error
}

func (l *fakeLocker) WithLock(ctx context.Context, fn func(LockedStore) error) error {
	l.calls++
	if l.err != nil {
		return l.err
	}
	return fn(l.store)
}

type fakeLockedStore struct {
	applied    []Applied
	applyCalls []Step
	downCalls  []Step
	applyErr   error
	downErr    error
}

func (s *fakeLockedStore) Applied(context.Context) ([]Applied, error) {
	return append([]Applied(nil), s.applied...), nil
}

func (s *fakeLockedStore) Apply(_ context.Context, step Step) error {
	if s.applyErr != nil {
		return s.applyErr
	}
	s.applyCalls = append(s.applyCalls, step)
	s.applied = append(s.applied, Applied{Version: step.Version, Name: step.Name, Checksum: step.Checksum})
	return nil
}

func (s *fakeLockedStore) Rollback(_ context.Context, step Step) error {
	if s.downErr != nil {
		return s.downErr
	}
	s.downCalls = append(s.downCalls, step)
	if len(s.applied) > 0 {
		s.applied = s.applied[:len(s.applied)-1]
	}
	return nil
}

func TestRunnerAppliesPendingMigrationUnderOneLock(t *testing.T) {
	store := &fakeLockedStore{}
	locker := &fakeLocker{store: store}
	runner, err := NewRunner(locker)
	if err != nil {
		t.Fatal(err)
	}

	if err := runner.Migrate(context.Background(), 1); err != nil {
		t.Fatal(err)
	}
	if locker.calls != 1 {
		t.Fatalf("lock calls = %d, want 1", locker.calls)
	}
	if len(store.applyCalls) != 1 || store.applyCalls[0].Direction != DirectionUp {
		t.Fatalf("unexpected apply calls: %#v", store.applyCalls)
	}
}

func TestRunnerIsIdempotentAtTarget(t *testing.T) {
	up, err := Plan(DirectionUp)
	if err != nil {
		t.Fatal(err)
	}
	store := &fakeLockedStore{applied: []Applied{{Version: up[0].Version, Name: up[0].Name, Checksum: up[0].Checksum}}}
	runner, err := NewRunner(&fakeLocker{store: store})
	if err != nil {
		t.Fatal(err)
	}

	if err := runner.Migrate(context.Background(), 1); err != nil {
		t.Fatal(err)
	}
	if len(store.applyCalls) != 0 || len(store.downCalls) != 0 {
		t.Fatalf("idempotent migration executed changes: up=%d down=%d", len(store.applyCalls), len(store.downCalls))
	}
}

func TestRunnerRejectsChecksumDriftBeforeExecution(t *testing.T) {
	store := &fakeLockedStore{applied: []Applied{{Version: 1, Name: "admin_sources_audit", Checksum: "tampered"}}}
	runner, err := NewRunner(&fakeLocker{store: store})
	if err != nil {
		t.Fatal(err)
	}

	err = runner.Migrate(context.Background(), 1)
	if !errors.Is(err, ErrChecksumDrift) {
		t.Fatalf("error = %v, want checksum drift", err)
	}
	if len(store.applyCalls) != 0 || len(store.downCalls) != 0 {
		t.Fatal("migration executed despite checksum drift")
	}
}

func TestRunnerRollsBackInReversePlanOrder(t *testing.T) {
	up, err := Plan(DirectionUp)
	if err != nil {
		t.Fatal(err)
	}
	store := &fakeLockedStore{applied: []Applied{{Version: up[0].Version, Name: up[0].Name, Checksum: up[0].Checksum}}}
	runner, err := NewRunner(&fakeLocker{store: store})
	if err != nil {
		t.Fatal(err)
	}

	if err := runner.Migrate(context.Background(), 0); err != nil {
		t.Fatal(err)
	}
	if len(store.downCalls) != 1 || store.downCalls[0].Direction != DirectionDown {
		t.Fatalf("unexpected rollback calls: %#v", store.downCalls)
	}
}

func TestRunnerRejectsInvalidAppliedSequence(t *testing.T) {
	store := &fakeLockedStore{applied: []Applied{{Version: 2, Name: "future", Checksum: "future"}}}
	runner, err := NewRunner(&fakeLocker{store: store})
	if err != nil {
		t.Fatal(err)
	}

	err = runner.Migrate(context.Background(), 0)
	if !errors.Is(err, ErrInvalidAppliedState) {
		t.Fatalf("error = %v, want invalid applied state", err)
	}
}

func TestRunnerRejectsTargetAboveEmbeddedPlan(t *testing.T) {
	locker := &fakeLocker{store: &fakeLockedStore{}}
	runner, err := NewRunner(locker)
	if err != nil {
		t.Fatal(err)
	}

	err = runner.Migrate(context.Background(), 2)
	if !errors.Is(err, ErrInvalidTarget) {
		t.Fatalf("error = %v, want invalid target", err)
	}
	if locker.calls != 0 {
		t.Fatal("lock acquired for invalid target")
	}
}

func TestValidateAppliedDoesNotMutateCallerSlice(t *testing.T) {
	up, err := Plan(DirectionUp)
	if err != nil {
		t.Fatal(err)
	}
	input := []Applied{{Version: 1, Name: up[0].Name, Checksum: up[0].Checksum}}
	before := append([]Applied(nil), input...)
	if _, err := validateApplied(input, up); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(input, before) {
		t.Fatalf("input mutated: got %#v want %#v", input, before)
	}
}
