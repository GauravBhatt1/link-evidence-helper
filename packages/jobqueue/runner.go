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

func (runner *Runner) Run(ctx context.Context) error {
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
			}
		}()
	}

	done := make(chan struct{})
	go func() {
		wait.Wait()
		close(done)
	}()

	select {
	case <-ctx.Done():
		<-done
		return nil
	case err := <-errorsChannel:
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
	defer func() {
		ackContext := context.WithoutCancel(ctx)
		_ = runner.store.Ack(ackContext, jobID)
	}()

	job, err := runner.store.Get(ctx, jobID)
	if errors.Is(err, ErrNotFound) {
		return nil
	}
	if err != nil {
		return err
	}
	if job.State.Terminal() {
		return nil
	}
	cancelled, err := runner.store.CancelRequested(ctx, jobID)
	if err != nil {
		return err
	}
	if cancelled {
		return nil
	}

	reporter := &storeReporter{store: runner.store, jobID: jobID}
	if err := runner.executor.Execute(ctx, job, reporter); err != nil {
		if errors.Is(err, ErrJobCancelled) || errors.Is(err, context.Canceled) {
			return nil
		}
		latest, getErr := runner.store.Get(context.WithoutCancel(ctx), jobID)
		if getErr == nil && !latest.State.Terminal() {
			_, _ = runner.store.Transition(
				context.WithoutCancel(ctx),
				jobID,
				StateFailed,
				"The development worker could not complete the job.",
				100,
				nil,
			)
		}
		return nil
	}
	return nil
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
