package jobqueue

import (
	"context"
	"testing"
	"time"
)

type interruptedExecutor struct {
	started chan struct{}
}

func (executor *interruptedExecutor) Execute(ctx context.Context, _ Job, _ Reporter) error {
	close(executor.started)
	<-ctx.Done()
	return ctx.Err()
}

func TestRunnerLeavesInterruptedJobForStartupRecovery(t *testing.T) {
	store := testStore(t, 10)
	created, err := store.CreateOrJoin(context.Background(), createRequest("interrupted", "request-4001"))
	if err != nil {
		t.Fatal(err)
	}

	executor := &interruptedExecutor{started: make(chan struct{})}
	runner, err := NewRunner(store, executor, 1)
	if err != nil {
		t.Fatal(err)
	}
	runContext, cancel := context.WithCancel(context.Background())
	runDone := make(chan error, 1)
	go func() { runDone <- runner.Run(runContext) }()

	select {
	case <-executor.started:
	case <-time.After(5 * time.Second):
		cancel()
		t.Fatal("worker did not start the claimed job")
	}
	cancel()
	select {
	case err := <-runDone:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("worker did not shut down")
	}

	recovered, err := store.Recover(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if recovered != 1 {
		t.Fatalf("recovered jobs = %d", recovered)
	}
	jobID, err := store.Claim(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if jobID != created.Job.JobID {
		t.Fatalf("recovered job = %q, want %q", jobID, created.Job.JobID)
	}
	if err := store.Ack(context.Background(), jobID); err != nil {
		t.Fatal(err)
	}
}
