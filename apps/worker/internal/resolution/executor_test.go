package resolution

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/worker/internal/linkverify"
	"github.com/GauravBhatt1/link-evidence-helper/packages/jobqueue"
)

type fakeVerifier struct {
	links  map[string]linkverify.DeliveryLink
	errors map[string]error
	calls  []string
}

func (verifier *fakeVerifier) Verify(_ context.Context, candidate linkverify.Candidate) (linkverify.DeliveryLink, error) {
	verifier.calls = append(verifier.calls, candidate.SourceID)
	if err := verifier.errors[candidate.SourceID]; err != nil {
		return linkverify.DeliveryLink{}, err
	}
	return verifier.links[candidate.SourceID], nil
}

type fakeReporter struct {
	states    []jobqueue.State
	messages  []string
	result    json.RawMessage
	cancelled bool
}

func (reporter *fakeReporter) Transition(_ context.Context, state jobqueue.State, message string, _ int, result json.RawMessage) (jobqueue.Job, error) {
	reporter.states = append(reporter.states, state)
	reporter.messages = append(reporter.messages, message)
	if result != nil {
		reporter.result = append(json.RawMessage(nil), result...)
	}
	return jobqueue.Job{State: state, Result: result}, nil
}

func (reporter *fakeReporter) Cancelled(context.Context) (bool, error) {
	return reporter.cancelled, nil
}

func testCatalog(t *testing.T) *Catalog {
	t.Helper()
	catalog, err := CompileCatalog(CatalogFile{Version: CatalogVersion, Variants: []VariantCatalog{{
		ContentID: "content_3b750f8edc77152e",
		VariantID: "variant_051fab7b083f979a",
		Qualities: []string{"1080p"},
		Sources: []SourceCatalog{
			{SourceID: "preferred", Priority: 10, URL: "https://preferred.example/file.mkv"},
			{SourceID: "backup", Priority: 20, URL: "https://backup.example/file.mkv"},
		},
	}}})
	if err != nil {
		t.Fatal(err)
	}
	return catalog
}

func testJob(payload string) jobqueue.Job {
	return jobqueue.Job{Kind: jobqueue.KindResolution, Payload: json.RawMessage(payload)}
}

func TestExecutorUsesRankedFailoverAndReturnsVerifiedResult(t *testing.T) {
	verifiedAt := time.Date(2026, 8, 5, 4, 0, 0, 0, time.UTC)
	verifier := &fakeVerifier{
		links: map[string]linkverify.DeliveryLink{
			"backup": {
				URL: "https://backup.example/file.mkv", Filename: "file.mkv", Size: "1 GB",
				Quality: "1080p", SourceID: "backup", VerifiedAt: verifiedAt,
			},
		},
		errors: map[string]error{
			"preferred": &linkverify.Error{Code: "http_status", Temporary: true, Cause: linkverify.ErrUnavailable},
		},
	}
	reporter := &fakeReporter{}
	executor := Executor{Catalog: testCatalog(t), Verifier: verifier}
	if err := executor.Execute(context.Background(), testJob(`{
		"contentId":"content_3b750f8edc77152e",
		"variantId":"variant_051fab7b083f979a",
		"quality":"1080P"
	}`), reporter); err != nil {
		t.Fatal(err)
	}
	if len(verifier.calls) != 2 || verifier.calls[0] != "preferred" || verifier.calls[1] != "backup" {
		t.Fatalf("calls = %#v", verifier.calls)
	}
	expectedStates := []jobqueue.State{
		jobqueue.StateCheckingCache,
		jobqueue.StateCheckingPreferredSource,
		jobqueue.StateCheckingBackupSource,
		jobqueue.StateVerified,
	}
	if len(reporter.states) != len(expectedStates) {
		t.Fatalf("states = %#v", reporter.states)
	}
	for index := range expectedStates {
		if reporter.states[index] != expectedStates[index] {
			t.Fatalf("states = %#v", reporter.states)
		}
	}
	var result resolutionResult
	if err := json.Unmarshal(reporter.result, &result); err != nil {
		t.Fatal(err)
	}
	if !result.OK || !result.Success || result.Code != "ok" || result.Status != "verified" || len(result.DeliveryLinks) != 1 || result.DeliveryLinks[0].SourceID != "backup" || len(result.Attempts) != 2 {
		t.Fatalf("result = %#v", result)
	}
	if result.Attempts[0].FailureReason == nil || *result.Attempts[0].FailureReason != "Source returned an unavailable response." || result.Attempts[1].Status != "verified" {
		t.Fatalf("attempts = %#v", result.Attempts)
	}
}

func TestExecutorStopsAfterPreferredSourceSuccess(t *testing.T) {
	verifier := &fakeVerifier{links: map[string]linkverify.DeliveryLink{
		"preferred": {URL: "https://preferred.example/file.mkv", Filename: "file.mkv", SourceID: "preferred"},
	}, errors: map[string]error{}}
	reporter := &fakeReporter{}
	quality := "1080p"
	payload, _ := json.Marshal(Request{
		ContentID: "content_3b750f8edc77152e",
		VariantID: "variant_051fab7b083f979a",
		Quality:   &quality,
	})
	if err := (Executor{Catalog: testCatalog(t), Verifier: verifier}).Execute(
		context.Background(),
		jobqueue.Job{Kind: jobqueue.KindResolution, Payload: payload},
		reporter,
	); err != nil {
		t.Fatal(err)
	}
	if len(verifier.calls) != 1 || verifier.calls[0] != "preferred" || reporter.states[len(reporter.states)-1] != jobqueue.StateVerified {
		t.Fatalf("calls=%#v states=%#v", verifier.calls, reporter.states)
	}
}

func TestExecutorReturnsBlockedWhenAllSourcesViolatePolicy(t *testing.T) {
	blocked := &linkverify.Error{Code: "unsafe_network", Blocked: true, Cause: linkverify.ErrUnsafeURL}
	verifier := &fakeVerifier{links: map[string]linkverify.DeliveryLink{}, errors: map[string]error{
		"preferred": blocked,
		"backup":    blocked,
	}}
	reporter := &fakeReporter{}
	if err := (Executor{Catalog: testCatalog(t), Verifier: verifier}).Execute(context.Background(), testJob(`{
		"contentId":"content_3b750f8edc77152e",
		"variantId":"variant_051fab7b083f979a",
		"quality":"1080p"
	}`), reporter); err != nil {
		t.Fatal(err)
	}
	if reporter.states[len(reporter.states)-1] != jobqueue.StateBlocked {
		t.Fatalf("states = %#v", reporter.states)
	}
	var result resolutionResult
	if err := json.Unmarshal(reporter.result, &result); err != nil {
		t.Fatal(err)
	}
	if result.Code != "all_sources_blocked" || result.Status != "blocked" || len(result.DeliveryLinks) != 0 || len(result.Attempts) != 2 {
		t.Fatalf("result = %#v", result)
	}
	for _, attempt := range result.Attempts {
		if attempt.FailureReason == nil || *attempt.FailureReason != "Source was blocked by network safety policy." {
			t.Fatalf("attempt = %#v", attempt)
		}
	}
}

func TestExecutorRejectsUnknownPayloadFieldsAndMissingSelection(t *testing.T) {
	verifier := &fakeVerifier{links: map[string]linkverify.DeliveryLink{}, errors: map[string]error{}}
	for _, test := range []struct {
		payload string
		code    string
	}{
		{`{"contentId":"content_3b750f8edc77152e","variantId":"variant_051fab7b083f979a","sourceId":"secret"}`, "invalid_request"},
		{`{"contentId":"missing_content","variantId":"missing_variant","quality":"1080p"}`, "selection_not_found"},
	} {
		reporter := &fakeReporter{}
		if err := (Executor{Catalog: testCatalog(t), Verifier: verifier}).Execute(context.Background(), testJob(test.payload), reporter); err != nil {
			t.Fatal(err)
		}
		var result resolutionResult
		if err := json.Unmarshal(reporter.result, &result); err != nil {
			t.Fatal(err)
		}
		if result.Code != test.code || reporter.states[len(reporter.states)-1] != jobqueue.StateFailed {
			t.Fatalf("payload=%s result=%#v states=%#v", test.payload, result, reporter.states)
		}
	}
	if len(verifier.calls) != 0 {
		t.Fatalf("unexpected verifier calls = %#v", verifier.calls)
	}
}

func TestExecutorHonorsCancellationBeforeNetworkAccess(t *testing.T) {
	verifier := &fakeVerifier{links: map[string]linkverify.DeliveryLink{}, errors: map[string]error{}}
	reporter := &fakeReporter{cancelled: true}
	err := (Executor{Catalog: testCatalog(t), Verifier: verifier}).Execute(context.Background(), testJob(`{
		"contentId":"content_3b750f8edc77152e",
		"variantId":"variant_051fab7b083f979a",
		"quality":"1080p"
	}`), reporter)
	if !errors.Is(err, jobqueue.ErrJobCancelled) || len(verifier.calls) != 0 || len(reporter.states) != 0 {
		t.Fatalf("err=%v calls=%#v states=%#v", err, verifier.calls, reporter.states)
	}
}
