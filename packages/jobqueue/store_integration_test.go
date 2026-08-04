package jobqueue

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func testStore(t *testing.T, maxQueued int64) *Store {
	t.Helper()
	address := os.Getenv("LINK_EVIDENCE_TEST_REDIS_ADDR")
	if address == "" {
		t.Skip("LINK_EVIDENCE_TEST_REDIS_ADDR is not set")
	}
	config := DefaultConfig()
	config.Prefix = fmt.Sprintf("leh:test:%d:%s", time.Now().UnixNano(), strings.ReplaceAll(t.Name(), "/", "-"))
	config.JobTTL = 2 * time.Minute
	config.TerminalTTL = time.Minute
	config.BlockTime = 50 * time.Millisecond
	config.MaxQueued = maxQueued
	store, err := Open(address, "", 0, config)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = store.DeleteNamespace(ctx)
		_ = store.Close()
	})
	return store
}

func createRequest(label, idempotencyKey string) CreateRequest {
	payload := json.RawMessage(fmt.Sprintf(`{"contentId":%q,"variantId":"variant","quality":"1080p"}`, label))
	digest := sha256.Sum256(payload)
	return CreateRequest{
		Kind: KindResolution, Fingerprint: hex.EncodeToString(digest[:]),
		IdempotencyKey: idempotencyKey, Payload: payload,
	}
}

func TestCreateCoalesceIdempotencyAndCancellation(t *testing.T) {
	ctx := context.Background()
	store := testStore(t, 10)
	first, err := store.CreateOrJoin(ctx, createRequest("content", "request-0001"))
	if err != nil {
		t.Fatal(err)
	}
	if first.Outcome != OutcomeCreated || first.Job.SubscriberCount != 1 || first.Job.State != StateQueued {
		t.Fatalf("first create = %#v", first)
	}

	duplicate, err := store.CreateOrJoin(ctx, createRequest("content", "request-0001"))
	if err != nil {
		t.Fatal(err)
	}
	if duplicate.Outcome != OutcomeIdempotent || duplicate.Job.JobID != first.Job.JobID || duplicate.Job.SubscriberCount != 1 {
		t.Fatalf("duplicate create = %#v", duplicate)
	}

	joined, err := store.CreateOrJoin(ctx, createRequest("content", "request-0002"))
	if err != nil {
		t.Fatal(err)
	}
	if joined.Outcome != OutcomeJoined || joined.Job.JobID != first.Job.JobID || joined.Job.SubscriberCount != 2 {
		t.Fatalf("joined create = %#v", joined)
	}
	events, err := store.Events(ctx, first.Job.JobID)
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 2 || events[0].State != StateQueued || events[1].Message != "Joined an existing coalesced job." {
		t.Fatalf("events = %#v", events)
	}

	remaining, err := store.Unsubscribe(ctx, first.Job.JobID, "request-0001")
	if err != nil {
		t.Fatal(err)
	}
	if remaining.SubscriberCount != 1 || remaining.State != StateQueued {
		t.Fatalf("remaining job = %#v", remaining)
	}
	if _, err := store.Unsubscribe(ctx, first.Job.JobID, "request-0001"); !errors.Is(err, ErrSubscriptionNotFound) {
		t.Fatalf("duplicate unsubscribe error = %v", err)
	}

	cancelled, err := store.Unsubscribe(ctx, first.Job.JobID, "request-0002")
	if err != nil {
		t.Fatal(err)
	}
	if cancelled.SubscriberCount != 0 || cancelled.State != StateCancelled {
		t.Fatalf("cancelled job = %#v", cancelled)
	}
	requested, err := store.CancelRequested(ctx, first.Job.JobID)
	if err != nil || !requested {
		t.Fatalf("cancel flag = %v, %v", requested, err)
	}

	replacement, err := store.CreateOrJoin(ctx, createRequest("content", "request-0003"))
	if err != nil {
		t.Fatal(err)
	}
	if replacement.Outcome != OutcomeCreated || replacement.Job.JobID == first.Job.JobID {
		t.Fatalf("replacement = %#v", replacement)
	}
}

func TestTransitionsEventsQueueCapacityAndRecovery(t *testing.T) {
	ctx := context.Background()
	store := testStore(t, 2)
	created, err := store.CreateOrJoin(ctx, createRequest("transition", "request-1001"))
	if err != nil {
		t.Fatal(err)
	}
	claimed, err := store.Claim(ctx)
	if err != nil || claimed != created.Job.JobID {
		t.Fatalf("claim = %q, %v", claimed, err)
	}
	recovered, err := store.Recover(ctx)
	if err != nil || recovered != 1 {
		t.Fatalf("recover = %d, %v", recovered, err)
	}
	claimed, err = store.Claim(ctx)
	if err != nil || claimed != created.Job.JobID {
		t.Fatalf("recovered claim = %q, %v", claimed, err)
	}

	if _, err := store.Transition(ctx, claimed, StateCheckingCache, "Checking cache.", 10, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := store.Transition(ctx, claimed, StateCheckingPreferredSource, "Checking pipeline.", 60, nil); err != nil {
		t.Fatal(err)
	}
	result := json.RawMessage(`{"mode":"development-job-foundation"}`)
	completed, err := store.Transition(ctx, claimed, StatePartial, "Development pipeline complete.", 100, result)
	if err != nil {
		t.Fatal(err)
	}
	if completed.State != StatePartial || string(completed.Result) != string(result) {
		t.Fatalf("completed job = %#v", completed)
	}
	if err := store.Ack(ctx, claimed); err != nil {
		t.Fatal(err)
	}
	events, err := store.Events(ctx, claimed)
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 4 || events[len(events)-1].Progress != 100 {
		t.Fatalf("events = %#v", events)
	}

	newJob, err := store.CreateOrJoin(ctx, createRequest("transition", "request-1002"))
	if err != nil {
		t.Fatal(err)
	}
	if newJob.Outcome != OutcomeCreated || newJob.Job.JobID == claimed {
		t.Fatalf("post-terminal job = %#v", newJob)
	}
}

func TestQueueCapacity(t *testing.T) {
	ctx := context.Background()
	store := testStore(t, 1)
	if _, err := store.CreateOrJoin(ctx, createRequest("one", "request-2001")); err != nil {
		t.Fatal(err)
	}
	if _, err := store.CreateOrJoin(ctx, createRequest("two", "request-2002")); !errors.Is(err, ErrQueueFull) {
		t.Fatalf("queue full error = %v", err)
	}
}

type concurrencyExecutor struct {
	active atomic.Int32
	max    atomic.Int32
	done   chan struct{}
}

func (executor *concurrencyExecutor) Execute(ctx context.Context, _ Job, reporter Reporter) error {
	active := executor.active.Add(1)
	for {
		maximum := executor.max.Load()
		if active <= maximum || executor.max.CompareAndSwap(maximum, active) {
			break
		}
	}
	defer executor.active.Add(-1)
	if _, err := reporter.Transition(ctx, StateCheckingCache, "Checking bounded worker.", 10, nil); err != nil {
		return err
	}
	timer := time.NewTimer(75 * time.Millisecond)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
	}
	if _, err := reporter.Transition(ctx, StatePartial, "Bounded worker complete.", 100, json.RawMessage(`{"mode":"test"}`)); err != nil {
		return err
	}
	executor.done <- struct{}{}
	return nil
}

func TestRunnerEnforcesBoundedConcurrency(t *testing.T) {
	ctx := context.Background()
	store := testStore(t, 10)
	jobIDs := make([]string, 0, 4)
	for index := 0; index < 4; index++ {
		created, err := store.CreateOrJoin(ctx, createRequest(fmt.Sprintf("runner-%d", index), fmt.Sprintf("request-3%03d", index)))
		if err != nil {
			t.Fatal(err)
		}
		jobIDs = append(jobIDs, created.Job.JobID)
	}
	executor := &concurrencyExecutor{done: make(chan struct{}, len(jobIDs))}
	runner, err := NewRunner(store, executor, 2)
	if err != nil {
		t.Fatal(err)
	}
	runContext, cancel := context.WithCancel(context.Background())
	runDone := make(chan error, 1)
	go func() { runDone <- runner.Run(runContext) }()
	for range jobIDs {
		select {
		case <-executor.done:
		case <-time.After(5 * time.Second):
			t.Fatal("runner timed out")
		}
	}
	cancel()
	if err := <-runDone; err != nil {
		t.Fatal(err)
	}
	if maximum := executor.max.Load(); maximum < 1 || maximum > 2 {
		t.Fatalf("maximum concurrency = %d", maximum)
	}
	for _, jobID := range jobIDs {
		job, err := store.Get(ctx, jobID)
		if err != nil || job.State != StatePartial {
			t.Fatalf("job %s = %#v, %v", jobID, job, err)
		}
	}
	if _, err := NewRunner(store, executor, MaxWorkerConcurrency+1); !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("unbounded concurrency error = %v", err)
	}
}
