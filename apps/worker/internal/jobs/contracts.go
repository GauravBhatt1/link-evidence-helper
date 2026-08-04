package jobs

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/packages/jobqueue"
)

type DevelopmentExecutor struct {
	StepDelay time.Duration
}

func (executor DevelopmentExecutor) Execute(ctx context.Context, job jobqueue.Job, reporter jobqueue.Reporter) error {
	if job.Kind != jobqueue.KindResolution {
		return errors.New("unsupported development job kind")
	}
	if _, err := reporter.Transition(ctx, jobqueue.StateCheckingCache, "Checking the development job cache.", 10, nil); err != nil {
		return err
	}
	if err := executor.waitOrCancel(ctx, reporter); err != nil {
		return err
	}
	if _, err := reporter.Transition(ctx, jobqueue.StateCheckingPreferredSource, "Verifying the development job pipeline without contacting sources.", 60, nil); err != nil {
		return err
	}
	if err := executor.waitOrCancel(ctx, reporter); err != nil {
		return err
	}
	result, err := json.Marshal(map[string]any{
		"mode":    "development-job-foundation",
		"message": "Job infrastructure completed; live resolution is not connected.",
	})
	if err != nil {
		return err
	}
	_, err = reporter.Transition(
		ctx,
		jobqueue.StatePartial,
		"Development job infrastructure completed; live resolution remains disconnected.",
		100,
		result,
	)
	return err
}

func (executor DevelopmentExecutor) waitOrCancel(ctx context.Context, reporter jobqueue.Reporter) error {
	cancelled, err := reporter.Cancelled(ctx)
	if err != nil {
		return err
	}
	if cancelled {
		return jobqueue.ErrJobCancelled
	}
	if executor.StepDelay <= 0 {
		return nil
	}
	timer := time.NewTimer(executor.StepDelay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
	}
	cancelled, err = reporter.Cancelled(ctx)
	if err != nil {
		return err
	}
	if cancelled {
		return jobqueue.ErrJobCancelled
	}
	return nil
}
