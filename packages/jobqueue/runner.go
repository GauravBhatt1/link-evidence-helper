package jobqueue

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
)

const MaxWorkerConcurrency = 8

type Reporter interface {
	Transition(ctx context.Context, state State, message string, progress int, result json.RawMessage) (Job, error)
	Cancelled(ctx context.Context) (bool, error)
}

type Executor interface {
	Execute(ctx context.Context, job Job, reporter Reporter) error
}

type Runner struct {
	store       *Store
	executor    Executor
	concurrency int
}

func NewRunner(store *Store, executor Executor, concurrency int) (*Runner, error) {
	if store == nil || executor == nil {
		return nil, fmt.Errorf("%w: store and executor are required", ErrInvalidInput)
	}
	if concurrency < 1 || concurrency > MaxWorkerConcurrency {
		return nil, fmt.Errorf("%w: concurrency must be between 1 and %d", ErrInvalidInput, MaxWorkerConcurrency)
	}
	return &Runner{store: store, executor: executor, concurrency: concurrency}, nil
}

func (runner *Runner) Run(parent context.Context) error {
	ctx, cancel := context.WithCancel(parent)
	defer cancel()
	if _, err := runner.store.Recover(ctx); err != nil {
		return err
	}

	var wait sync.WaitGroup
	errorsChannel := make(chan error, runner.concurrency)
	for index := 0; index < runner.concurrency; index++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			if err := runner.worker(ctx); err != nil && !errors.Is(err, context.Canceled) {
				select {
				case errorsChannel <- err:
				default:
				}
				cancel()
			}
		}()
	}

	done := make(chan struct{})
	go func() {
		wait.Wait()
		close(done)
	}()

	select {
	case <-parent.Done():
		cancel()
		<-done
		return nil
	case err := <-errorsChannel:
		cancel()
		<-done
		return err
	case <-done:
		return nil
	}
}

func (runner *Runner) worker(ctx context.Context) error {
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		jobID, err := runner.store.Claim(ctx)
		if errors.Is(err, ErrNoJobAvailable) {
			continue
		}
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			return err
		}
		if err := runner.process(ctx, jobID); err != nil {
			return err
		}
	}
}

func (runner *Runner) process(ctx context.Context, jobID string) error {
	job, err := runner.store.Get(ctx, jobID)
	if errors.Is(err, ErrNotFound) {
		return runner.ack(ctx, jobID)
	}
	if err != nil {
		return err
	}
	if job.State.Terminal() {
		return runner.ack(ctx, jobID)
	}
	cancelled, err := runner.store.CancelRequested(ctx, jobID)
	if err != nil {
		return err
	}
	if cancelled {
		return runner.ack(ctx, jobID)
	}

	reporter := &storeReporter{store: runner.store, jobID: jobID}
	executeErr := runner.executor.Execute(ctx, job, reporter)
	if executeErr != nil {
		if errors.Is(executeErr, context.Canceled) || errors.Is(ctx.Err(), context.Canceled) {
			// Keep the job in the processing list. The next worker startup calls
			// Recover and moves it back to the queue.
			return context.Canceled
		}
		if errors.Is(executeErr, ErrJobCancelled) {
			return runner.ack(ctx, jobID)
		}
		return runner.failAndAck(ctx, jobID)
	}

	latest, err := runner.store.Get(context.WithoutCancel(ctx), jobID)
	if err != nil {
		return err
	}
	if !latest.State.Terminal() {
		if _, err := runner.store.Transition(
			context.WithoutCancel(ctx),
			jobID,
			StateFailed,
			"The development worker ended without a terminal job state.",
			100,
			nil,
		); err != nil {
			return err
		}
	}
	return runner.ack(ctx, jobID)
}

func (runner *Runner) failAndAck(ctx context.Context, jobID string) error {
	background := context.WithoutCancel(ctx)
	latest, err := runner.store.Get(background, jobID)
	if err != nil {
		return err
	}
	if !latest.State.Terminal() {
		if _, err := runner.store.Transition(
			background,
			jobID,
			StateFailed,
			"The development worker could not complete the job.",
			100,
			nil,
		); err != nil {
			return err
		}
	}
	return runner.ack(ctx, jobID)
}

func (runner *Runner) ack(ctx context.Context, jobID string) error {
	return runner.store.Ack(context.WithoutCancel(ctx), jobID)
}

type storeReporter struct {
	store *Store
	jobID string
}

func (reporter *storeReporter) Transition(ctx context.Context, state State, message string, progress int, result json.RawMessage) (Job, error) {
	return reporter.store.Transition(ctx, reporter.jobID, state, message, progress, result)
}

func (reporter *storeReporter) Cancelled(ctx context.Context) (bool, error) {
	return reporter.store.CancelRequested(ctx, reporter.jobID)
}
