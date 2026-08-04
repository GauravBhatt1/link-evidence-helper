package jobs

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/packages/jobqueue"
)

type recordedTransition struct {
	state    jobqueue.State
	message  string
	progress int
	result   json.RawMessage
}

type fakeReporter struct {
	transitions []recordedTransition
	cancelled   bool
}

func (reporter *fakeReporter) Transition(_ context.Context, state jobqueue.State, message string, progress int, result json.RawMessage) (jobqueue.Job, error) {
	reporter.transitions = append(reporter.transitions, recordedTransition{state: state, message: message, progress: progress, result: result})
	return jobqueue.Job{State: state, Result: result}, nil
}

func (reporter *fakeReporter) Cancelled(context.Context) (bool, error) {
	return reporter.cancelled, nil
}

func TestDevelopmentExecutorIsTransparentAndSourceFree(t *testing.T) {
	reporter := &fakeReporter{}
	executor := DevelopmentExecutor{}
	err := executor.Execute(context.Background(), jobqueue.Job{Kind: jobqueue.KindResolution}, reporter)
	if err != nil {
		t.Fatal(err)
	}
	if len(reporter.transitions) != 3 {
		t.Fatalf("transition count = %d", len(reporter.transitions))
	}
	expected := []jobqueue.State{
		jobqueue.StateCheckingCache,
		jobqueue.StateCheckingPreferredSource,
		jobqueue.StatePartial,
	}
	for index, transition := range reporter.transitions {
		if transition.state != expected[index] {
			t.Fatalf("transition %d state = %s", index, transition.state)
		}
	}
	last := reporter.transitions[len(reporter.transitions)-1]
	if last.progress != 100 || !json.Valid(last.result) {
		t.Fatalf("final transition = %#v", last)
	}
	var result map[string]any
	if err := json.Unmarshal(last.result, &result); err != nil {
		t.Fatal(err)
	}
	if result["mode"] != "development-job-foundation" {
		t.Fatalf("result = %#v", result)
	}
	for _, forbidden := range []string{"sourceId", "url", "deliveryLinks", "download"} {
		if _, found := result[forbidden]; found {
			t.Fatalf("development result must not contain %s", forbidden)
		}
	}
}

func TestDevelopmentExecutorHonorsCancellation(t *testing.T) {
	reporter := &fakeReporter{cancelled: true}
	err := (DevelopmentExecutor{StepDelay: time.Millisecond}).Execute(
		context.Background(),
		jobqueue.Job{Kind: jobqueue.KindResolution},
		reporter,
	)
	if !errors.Is(err, jobqueue.ErrJobCancelled) {
		t.Fatalf("cancel error = %v", err)
	}
	if len(reporter.transitions) != 1 || reporter.transitions[0].state != jobqueue.StateCheckingCache {
		t.Fatalf("transitions = %#v", reporter.transitions)
	}
}

func TestDevelopmentExecutorRejectsUnsupportedKind(t *testing.T) {
	err := (DevelopmentExecutor{}).Execute(
		context.Background(),
		jobqueue.Job{Kind: jobqueue.KindSearch},
		&fakeReporter{},
	)
	if err == nil {
		t.Fatal("unsupported job kind must fail")
	}
}
