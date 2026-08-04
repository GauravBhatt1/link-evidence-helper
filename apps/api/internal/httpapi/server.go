// Package httpapi exposes the versioned development-only Go API.
package httpapi

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"regexp"
	"strings"
	"sync/atomic"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/contracts"
	searchservice "github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/search"
	"github.com/GauravBhatt1/link-evidence-helper/packages/jobqueue"
)

const maxJSONBodyBytes = 16 << 10

var jobIDPattern = regexp.MustCompile(`^job_[a-f0-9]{32}$`)

type JobService interface {
	CreateResolution(ctx interface{ Done() <-chan struct{} }, request contracts.ResolutionRequest, idempotencyKey string) (contracts.Job, jobqueue.CreateOutcome, error)
}

// jobService uses context.Context in the concrete method set. The private
// adapter below keeps the public Handler signature compatible with Milestone 4.
type redisJobs interface {
	CreateResolution(ctxContext, contracts.ResolutionRequest, string) (contracts.Job, jobqueue.CreateOutcome, error)
	Get(ctxContext, string) (contracts.Job, error)
	Events(ctxContext, string) ([]contracts.JobEvent, error)
	Unsubscribe(ctxContext, string, string) (contracts.Job, error)
}

type ctxContext interface {
	Deadline() (deadlineTime interface{}, ok bool)
}

// Handler returns an isolated HTTP handler with job routes disabled. It remains
// available for tests and fixture-only development startup.
func Handler(searcher searchservice.Searcher) http.Handler {
	return HandlerWithJobs(searcher, nil)
}

// HandlerWithJobs enables Redis-backed job routes when jobs is non-nil. It does
// not enable CORS, contact production services, or proxy to the Python app.
func HandlerWithJobs(searcher searchservice.Searcher, jobs JobBackend) http.Handler {
	api := &apiHandler{searcher: searcher, jobs: jobs}
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", api.health)
	mux.HandleFunc("/api/v1/search", api.search)
	mux.HandleFunc("/api/v1/jobs/resolution", api.createResolutionJob)
	mux.HandleFunc("/api/v1/jobs/", api.jobResource)
	return api.securityHeaders(mux)
}

type JobBackend interface {
	CreateResolution(ctx context.Context, request contracts.ResolutionRequest, idempotencyKey string) (contracts.Job, jobqueue.CreateOutcome, error)
	Get(ctx context.Context, jobID string) (contracts.Job, error)
	Events(ctx context.Context, jobID string) ([]contracts.JobEvent, error)
	Unsubscribe(ctx context.Context, jobID, idempotencyKey string) (contracts.Job, error)
}

type apiHandler struct {
	searcher searchservice.Searcher
	jobs     JobBackend
	counter  atomic.Uint64
}

func (api *apiHandler) requestID() string {
	return fmt.Sprintf("req-%d", api.counter.Add(1))
}

func (api *apiHandler) securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Cache-Control", "no-store")
		writer.Header().Set("X-Content-Type-Options", "nosniff")
		writer.Header().Set("Referrer-Policy", "no-referrer")
		next.ServeHTTP(writer, request)
	})
}

func (api *apiHandler) health(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		methodNotAllowed(writer, http.MethodGet)
		return
	}
	jobsMode := "disabled"
	if api.jobs != nil {
		jobsMode = "redis-development"
	}
	writeJSON(writer, http.StatusOK, struct {
		OK      bool   `json:"ok"`
		Service string `json:"service"`
		Mode    string `json:"mode"`
		Jobs    string `json:"jobs"`
	}{OK: true, Service: "link-evidence-api", Mode: "development-fixture", Jobs: jobsMode})
}

func (api *apiHandler) search(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		methodNotAllowed(writer, http.MethodGet)
		return
	}
	requestID := api.requestID()
	writer.Header().Set("X-Request-ID", requestID)

	values := request.URL.Query()
	for key := range values {
		if key != "q" {
			writeError(writer, http.StatusBadRequest, "invalid_request", "Only the q search parameter is supported.", requestID)
			return
		}
	}
	queries, exists := values["q"]
	if !exists || len(queries) != 1 {
		writeError(writer, http.StatusBadRequest, "invalid_request", "Provide exactly one q search parameter.", requestID)
		return
	}

	response, err := api.searcher.Search(request.Context(), queries[0])
	if err != nil {
		switch {
		case errors.Is(err, searchservice.ErrEmptyQuery):
			writeError(writer, http.StatusBadRequest, "invalid_request", "Enter a title to search.", requestID)
		case errors.Is(err, searchservice.ErrQueryTooLong):
			writeError(writer, http.StatusBadRequest, "invalid_request", "Search queries are limited to 120 characters.", requestID)
		case errors.Is(err, searchservice.ErrDevelopmentFixture):
			writeError(writer, http.StatusServiceUnavailable, "development_fixture_error", "The development search could not be completed.", requestID)
		case errors.Is(err, request.Context().Err()):
			return
		default:
			writeError(writer, http.StatusInternalServerError, "internal_error", "The development search could not be completed.", requestID)
		}
		return
	}
	writeJSON(writer, http.StatusOK, response)
}

func (api *apiHandler) createResolutionJob(writer http.ResponseWriter, request *http.Request) {
	requestID := api.requestID()
	writer.Header().Set("X-Request-ID", requestID)
	if request.Method != http.MethodPost {
		methodNotAllowed(writer, http.MethodPost)
		return
	}
	if len(request.URL.Query()) != 0 {
		writeError(writer, http.StatusBadRequest, "invalid_request", "Job creation does not accept query parameters.", requestID)
		return
	}
	if api.jobs == nil {
		writeError(writer, http.StatusServiceUnavailable, "jobs_unavailable", "The development job service is not enabled.", requestID)
		return
	}
	idempotencyKey := request.Header.Get("Idempotency-Key")
	if err := jobqueue.ValidateIdempotencyKey(idempotencyKey); err != nil {
		writeError(writer, http.StatusBadRequest, "invalid_idempotency_key", "Provide a valid Idempotency-Key header.", requestID)
		return
	}
	if !isJSONContentType(request.Header.Get("Content-Type")) {
		writeError(writer, http.StatusUnsupportedMediaType, "unsupported_media_type", "Content-Type must be application/json.", requestID)
		return
	}

	request.Body = http.MaxBytesReader(writer, request.Body, maxJSONBodyBytes)
	decoder := json.NewDecoder(request.Body)
	decoder.DisallowUnknownFields()
	var resolutionRequest contracts.ResolutionRequest
	if err := decoder.Decode(&resolutionRequest); err != nil {
		writeError(writer, http.StatusBadRequest, "invalid_request", "The resolution request is invalid.", requestID)
		return
	}
	if err := ensureJSONEOF(decoder); err != nil {
		writeError(writer, http.StatusBadRequest, "invalid_request", "The request body must contain one JSON object.", requestID)
		return
	}

	job, outcome, err := api.jobs.CreateResolution(request.Context(), resolutionRequest, idempotencyKey)
	if err != nil {
		api.writeJobError(writer, requestID, err)
		return
	}
	writer.Header().Set("X-Job-Outcome", string(outcome))
	status := http.StatusOK
	if outcome == jobqueue.OutcomeCreated {
		status = http.StatusAccepted
	}
	writeJSON(writer, status, job)
}

func (api *apiHandler) jobResource(writer http.ResponseWriter, request *http.Request) {
	requestID := api.requestID()
	writer.Header().Set("X-Request-ID", requestID)
	if api.jobs == nil {
		writeError(writer, http.StatusServiceUnavailable, "jobs_unavailable", "The development job service is not enabled.", requestID)
		return
	}
	if len(request.URL.Query()) != 0 {
		writeError(writer, http.StatusBadRequest, "invalid_request", "Job routes do not accept query parameters.", requestID)
		return
	}

	remainder := strings.TrimPrefix(request.URL.Path, "/api/v1/jobs/")
	parts := strings.Split(remainder, "/")
	if len(parts) < 1 || len(parts) > 2 || !jobIDPattern.MatchString(parts[0]) {
		writeError(writer, http.StatusNotFound, "job_not_found", "Job not found.", requestID)
		return
	}
	jobID := parts[0]
	if len(parts) == 2 {
		if parts[1] != "events" {
			writeError(writer, http.StatusNotFound, "job_not_found", "Job not found.", requestID)
			return
		}
		if request.Method != http.MethodGet {
			methodNotAllowed(writer, http.MethodGet)
			return
		}
		events, err := api.jobs.Events(request.Context(), jobID)
		if err != nil {
			api.writeJobError(writer, requestID, err)
			return
		}
		writeJSON(writer, http.StatusOK, events)
		return
	}

	switch request.Method {
	case http.MethodGet:
		job, err := api.jobs.Get(request.Context(), jobID)
		if err != nil {
			api.writeJobError(writer, requestID, err)
			return
		}
		writeJSON(writer, http.StatusOK, job)
	case http.MethodDelete:
		idempotencyKey := request.Header.Get("Idempotency-Key")
		if err := jobqueue.ValidateIdempotencyKey(idempotencyKey); err != nil {
			writeError(writer, http.StatusBadRequest, "invalid_idempotency_key", "Provide the subscription Idempotency-Key header.", requestID)
			return
		}
		job, err := api.jobs.Unsubscribe(request.Context(), jobID, idempotencyKey)
		if err != nil {
			api.writeJobError(writer, requestID, err)
			return
		}
		writeJSON(writer, http.StatusOK, job)
	default:
		writer.Header().Set("Allow", "GET, DELETE")
		writeJSON(writer, http.StatusMethodNotAllowed, contracts.ErrorResponse{
			OK: false, Success: false, Code: "method_not_allowed", Error: "Method not allowed.", RequestID: &requestID,
		})
	}
}

func (api *apiHandler) writeJobError(writer http.ResponseWriter, requestID string, err error) {
	switch {
	case errors.Is(err, jobqueue.ErrInvalidInput):
		writeError(writer, http.StatusBadRequest, "invalid_request", "The job request is invalid.", requestID)
	case errors.Is(err, jobqueue.ErrQueueFull):
		writeError(writer, http.StatusTooManyRequests, "queue_full", "The development job queue is full.", requestID)
	case errors.Is(err, jobqueue.ErrNotFound):
		writeError(writer, http.StatusNotFound, "job_not_found", "Job not found.", requestID)
	case errors.Is(err, jobqueue.ErrSubscriptionNotFound):
		writeError(writer, http.StatusConflict, "subscription_not_found", "This job subscription is not active.", requestID)
	default:
		writeError(writer, http.StatusInternalServerError, "internal_error", "The development job request could not be completed.", requestID)
	}
}

func isJSONContentType(value string) bool {
	mediaType, _, err := mime.ParseMediaType(value)
	return err == nil && mediaType == "application/json"
}

func ensureJSONEOF(decoder *json.Decoder) error {
	var extra any
	err := decoder.Decode(&extra)
	if errors.Is(err, io.EOF) {
		return nil
	}
	if err == nil {
		return errors.New("extra JSON value")
	}
	return err
}

func methodNotAllowed(writer http.ResponseWriter, allowed string) {
	writer.Header().Set("Allow", allowed)
	writeJSON(writer, http.StatusMethodNotAllowed, contracts.ErrorResponse{
		OK:      false,
		Success: false,
		Code:    "method_not_allowed",
		Error:   "Method not allowed.",
	})
}

func writeError(writer http.ResponseWriter, status int, code, message, requestID string) {
	writeJSON(writer, status, contracts.ErrorResponse{
		OK:        false,
		Success:   false,
		Code:      code,
		Error:     message,
		RequestID: &requestID,
	})
}

func writeJSON(writer http.ResponseWriter, status int, value any) {
	writer.Header().Set("Content-Type", "application/json; charset=utf-8")
	writer.WriteHeader(status)
	encoder := json.NewEncoder(writer)
	encoder.SetEscapeHTML(true)
	_ = encoder.Encode(value)
}
