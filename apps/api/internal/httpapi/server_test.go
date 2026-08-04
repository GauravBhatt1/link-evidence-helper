package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/contracts"
	searchservice "github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/search"
)

type stubSearcher struct {
	response contracts.SearchResponse
	err      error
	query    string
}

func (stub *stubSearcher) Search(_ context.Context, query string) (contracts.SearchResponse, error) {
	stub.query = query
	return stub.response, stub.err
}

func TestHealthAndMethodBoundaries(t *testing.T) {
	handler := Handler(&stubSearcher{})
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if recorder.Code != http.StatusOK {
		t.Fatalf("health status = %d", recorder.Code)
	}
	if recorder.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("health response must disable caching")
	}

	recorder = httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodPost, "/api/v1/search?q=Example+Film", nil))
	if recorder.Code != http.StatusMethodNotAllowed {
		t.Fatalf("method status = %d", recorder.Code)
	}
	if recorder.Header().Get("Allow") != http.MethodGet {
		t.Fatalf("allow header = %q", recorder.Header().Get("Allow"))
	}
}

func TestSearchReturnsCanonicalJSONAndRequestID(t *testing.T) {
	stub := &stubSearcher{response: contracts.SearchResponse{
		OK:              true,
		Success:         true,
		Code:            "ok",
		Query:           "Example Film",
		Contents:        []contracts.Content{},
		PartialFailures: []contracts.PartialFailure{},
	}}
	recorder := httptest.NewRecorder()
	Handler(stub).ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/api/v1/search?q=Example+Film", nil))
	if recorder.Code != http.StatusOK {
		t.Fatalf("search status = %d body=%s", recorder.Code, recorder.Body.String())
	}
	if stub.query != "Example Film" {
		t.Fatalf("search query = %q", stub.query)
	}
	if !strings.HasPrefix(recorder.Header().Get("X-Request-ID"), "req-") {
		t.Fatalf("request ID = %q", recorder.Header().Get("X-Request-ID"))
	}
	var response contracts.SearchResponse
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if response.Contents == nil || response.PartialFailures == nil {
		t.Fatal("canonical arrays must be non-nil")
	}
}

func TestSearchRejectsUnexpectedOrRepeatedParameters(t *testing.T) {
	for _, target := range []string{
		"/api/v1/search",
		"/api/v1/search?q=one&q=two",
		"/api/v1/search?q=one&source=hidden",
	} {
		recorder := httptest.NewRecorder()
		Handler(&stubSearcher{}).ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, target, nil))
		if recorder.Code != http.StatusBadRequest {
			t.Fatalf("target %q status = %d", target, recorder.Code)
		}
		if strings.Contains(strings.ToLower(recorder.Body.String()), "hidden") {
			t.Fatal("error response must not echo internal parameter values")
		}
	}
}

func TestSearchMapsErrorsToSafeResponses(t *testing.T) {
	tests := []struct {
		err    error
		status int
		code   string
	}{
		{searchservice.ErrEmptyQuery, http.StatusBadRequest, "invalid_request"},
		{searchservice.ErrQueryTooLong, http.StatusBadRequest, "invalid_request"},
		{searchservice.ErrDevelopmentFixture, http.StatusServiceUnavailable, "development_fixture_error"},
		{errors.New("database password secret-value"), http.StatusInternalServerError, "internal_error"},
	}
	for _, test := range tests {
		recorder := httptest.NewRecorder()
		Handler(&stubSearcher{err: test.err}).ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/api/v1/search?q=Example", nil))
		if recorder.Code != test.status {
			t.Fatalf("error %v status = %d", test.err, recorder.Code)
		}
		var response contracts.ErrorResponse
		if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
			t.Fatal(err)
		}
		if response.Code != test.code || response.OK || response.Success {
			t.Fatalf("error response = %#v", response)
		}
		if strings.Contains(recorder.Body.String(), "secret-value") {
			t.Fatal("internal errors must not be exposed")
		}
	}
}
