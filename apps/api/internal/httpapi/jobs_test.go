package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/contracts"
	"github.com/GauravBhatt1/link-evidence-helper/packages/jobqueue"
)

const testJobID = "job_0123456789abcdef0123456789abcdef"

type stubJobs struct {
	createJob        contracts.Job
	createOutcome    jobqueue.CreateOutcome
	createError      error
	getJob           contracts.Job
	getError         error
	events           []contracts.JobEvent
	eventsError      error
	unsubscribeJob   contracts.Job
	unsubscribeError error
	request          contracts.ResolutionRequest
	idempotencyKey   string
	jobID            string
}

func (stub *stubJobs) CreateResolution(_ context.Context, request contracts.ResolutionRequest, idempotencyKey string) (contracts.Job, jobqueue.CreateOutcome, error) {
	stub.request = request
	stub.idempotencyKey = idempotencyKey
	return stub.createJob, stub.createOutcome, stub.createError
}

func (stub *stubJobs) Get(_ context.Context, jobID string) (contracts.Job, error) {
	stub.jobID = jobID
	return stub.getJob, stub.getError
}

func (stub *stubJobs) Events(_ context.Context, jobID string) ([]contracts.JobEvent, error) {
	stub.jobID = jobID
	return stub.events, stub.eventsError
}

func (stub *stubJobs) Unsubscribe(_ context.Context, jobID, idempotencyKey string) (contracts.Job, error) {
	stub.jobID = jobID
	stub.idempotencyKey = idempotencyKey
	return stub.unsubscribeJob, stub.unsubscribeError
}

func canonicalJob() contracts.Job {
	now := time.Date(2026, 8, 5, 0, 0, 0, 0, time.UTC)
	return contracts.Job{
		JobID: testJobID, Kind: "resolution", State: "queued", SubscriberCount: 1,
		CreatedAt: now, UpdatedAt: now, Result: nil,
	}
}

func jobsHandler(stub *stubJobs) http.Handler {
	return HandlerWithJobs(&stubSearcher{}, stub)
}

func TestCreateResolutionJob(t *testing.T) {
	quality := "1080p"
	stub := &stubJobs{createJob: canonicalJob(), createOutcome: jobqueue.OutcomeCreated}
	request := httptest.NewRequest(
		http.MethodPost,
		"/api/v1/jobs/resolution",
		strings.NewReader(`{"contentId":"content","variantId":"variant","quality":"1080p"}`),
	)
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Idempotency-Key", "request-0001")
	recorder := httptest.NewRecorder()
	jobsHandler(stub).ServeHTTP(recorder, request)

	if recorder.Code != http.StatusAccepted {
		t.Fatalf("status = %d body=%s", recorder.Code, recorder.Body.String())
	}
	if recorder.Header().Get("X-Job-Outcome") != string(jobqueue.OutcomeCreated) {
		t.Fatalf("outcome = %q", recorder.Header().Get("X-Job-Outcome"))
	}
	if stub.idempotencyKey != "request-0001" || stub.request.ContentID != "content" || stub.request.VariantID != "variant" || stub.request.Quality == nil || *stub.request.Quality != quality {
		t.Fatalf("captured request = %#v key=%q", stub.request, stub.idempotencyKey)
	}
	var response contracts.Job
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if response.JobID != testJobID || response.Result != nil {
		t.Fatalf("response = %#v", response)
	}
}

func TestJoinedAndIdempotentJobsReturnOK(t *testing.T) {
	for _, outcome := range []jobqueue.CreateOutcome{jobqueue.OutcomeJoined, jobqueue.OutcomeIdempotent} {
		stub := &stubJobs{createJob: canonicalJob(), createOutcome: outcome}
		request := httptest.NewRequest(
			http.MethodPost,
			"/api/v1/jobs/resolution",
			strings.NewReader(`{"contentId":"content","variantId":"variant"}`),
		)
		request.Header.Set("Content-Type", "application/json; charset=utf-8")
		request.Header.Set("Idempotency-Key", "request-0002")
		recorder := httptest.NewRecorder()
		jobsHandler(stub).ServeHTTP(recorder, request)
		if recorder.Code != http.StatusOK || recorder.Header().Get("X-Job-Outcome") != string(outcome) {
			t.Fatalf("outcome %s status=%d header=%q", outcome, recorder.Code, recorder.Header().Get("X-Job-Outcome"))
		}
	}
}

func TestCreateResolutionRejectsUnsafeInputs(t *testing.T) {
	tests := []struct {
		name        string
		target      string
		contentType string
		key         string
		body        string
		status      int
	}{
		{"query", "/api/v1/jobs/resolution?source=hidden", "application/json", "request-0001", `{}`, http.StatusBadRequest},
		{"missing-key", "/api/v1/jobs/resolution", "application/json", "", `{}`, http.StatusBadRequest},
		{"content-type", "/api/v1/jobs/resolution", "text/plain", "request-0001", `{}`, http.StatusUnsupportedMediaType},
		{"unknown-field", "/api/v1/jobs/resolution", "application/json", "request-0001", `{"contentId":"c","variantId":"v","sourceId":"secret"}`, http.StatusBadRequest},
		{"extra-json", "/api/v1/jobs/resolution", "application/json", "request-0001", `{"contentId":"c","variantId":"v"} {}`, http.StatusBadRequest},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodPost, test.target, strings.NewReader(test.body))
			request.Header.Set("Content-Type", test.contentType)
			if test.key != "" {
				request.Header.Set("Idempotency-Key", test.key)
			}
			recorder := httptest.NewRecorder()
			jobsHandler(&stubJobs{}).ServeHTTP(recorder, request)
			if recorder.Code != test.status {
				t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
			}
			if strings.Contains(strings.ToLower(recorder.Body.String()), "secret") || strings.Contains(strings.ToLower(recorder.Body.String()), "hidden") {
				t.Fatal("safe errors must not echo internal values")
			}
		})
	}
}

func TestJobStatusEventsAndUnsubscribe(t *testing.T) {
	eventTime := time.Date(2026, 8, 5, 0, 0, 0, 0, time.UTC)
	stub := &stubJobs{
		getJob: canonicalJob(),
		events: []contracts.JobEvent{{
			EventID: "evt_0123456789abcdef01234567", JobID: testJobID, State: "queued",
			Message: "Job queued.", OccurredAt: eventTime, Progress: 0,
		}},
		unsubscribeJob: func() contracts.Job {
			job := canonicalJob()
			job.State = "cancelled"
			job.SubscriberCount = 0
			return job
		}(),
	}

	for _, target := range []string{
		"/api/v1/jobs/" + testJobID,
		"/api/v1/jobs/" + testJobID + "/events",
	} {
		recorder := httptest.NewRecorder()
		jobsHandler(stub).ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, target, nil))
		if recorder.Code != http.StatusOK {
			t.Fatalf("target %s status=%d body=%s", target, recorder.Code, recorder.Body.String())
		}
	}

	request := httptest.NewRequest(http.MethodDelete, "/api/v1/jobs/"+testJobID, nil)
	request.Header.Set("Idempotency-Key", "request-0001")
	recorder := httptest.NewRecorder()
	jobsHandler(stub).ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK || stub.idempotencyKey != "request-0001" || stub.jobID != testJobID {
		t.Fatalf("unsubscribe status=%d key=%q job=%q", recorder.Code, stub.idempotencyKey, stub.jobID)
	}
}

func TestJobErrorsAreMappedSafely(t *testing.T) {
	tests := []struct {
		err    error
		status int
		code   string
	}{
		{jobqueue.ErrQueueFull, http.StatusTooManyRequests, "queue_full"},
		{jobqueue.ErrNotFound, http.StatusNotFound, "job_not_found"},
		{jobqueue.ErrSubscriptionNotFound, http.StatusConflict, "subscription_not_found"},
		{errors.New("redis password secret-value"), http.StatusInternalServerError, "internal_error"},
	}
	for _, test := range tests {
		stub := &stubJobs{getError: test.err}
		recorder := httptest.NewRecorder()
		jobsHandler(stub).ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/api/v1/jobs/"+testJobID, nil))
		if recorder.Code != test.status {
			t.Fatalf("error %v status=%d", test.err, recorder.Code)
		}
		var response contracts.ErrorResponse
		if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
			t.Fatal(err)
		}
		if response.Code != test.code || strings.Contains(recorder.Body.String(), "secret-value") {
			t.Fatalf("response = %#v body=%s", response, recorder.Body.String())
		}
	}
}
