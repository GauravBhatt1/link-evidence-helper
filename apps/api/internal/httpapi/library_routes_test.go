package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/contracts"
	libraryservice "github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/library"
)

type libraryTestSearcher struct{}

func (libraryTestSearcher) Search(context.Context, string) (contracts.SearchResponse, error) {
	return contracts.SearchResponse{}, errors.New("unused")
}

type libraryTestRepository struct {
	view     libraryservice.View
	response libraryservice.Response
	err      error
}

func (repository *libraryTestRepository) List(_ context.Context, view libraryservice.View) (libraryservice.Response, error) {
	repository.view = view
	return repository.response, repository.err
}

func TestLibraryRouteReturnsCanonicalView(t *testing.T) {
	generatedAt := time.Date(2026, 8, 5, 12, 0, 0, 0, time.UTC)
	repository := &libraryTestRepository{response: libraryservice.Response{
		OK: true, Success: true, Code: "ok", View: libraryservice.ViewMovies,
		GeneratedAt: generatedAt, Items: []libraryservice.Item{},
		Summary: libraryservice.Summary{},
		Jellyfin: libraryservice.JellyfinStatus{Configured: false, Mode: libraryservice.JellyfinDisabled},
	}}
	handler := HandlerWithJobsAndLibrary(libraryTestSearcher{}, nil, repository)
	request := httptest.NewRequest(http.MethodGet, "/api/v1/library?view=movies", nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d body=%s", response.Code, response.Body.String())
	}
	if repository.view != libraryservice.ViewMovies {
		t.Fatalf("repository view = %q", repository.view)
	}
	if response.Header().Get("Cache-Control") != "no-store" || response.Header().Get("X-Request-ID") == "" {
		t.Fatalf("missing safe response headers: %#v", response.Header())
	}
	var body libraryservice.Response
	if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode body: %v", err)
	}
	if body.View != libraryservice.ViewMovies || !body.GeneratedAt.Equal(generatedAt) {
		t.Fatalf("body = %#v", body)
	}
}

func TestLibraryRouteRejectsInvalidInputAndUnavailableRepository(t *testing.T) {
	tests := []struct {
		name       string
		method     string
		target     string
		repository libraryservice.Repository
		wantStatus int
		wantCode   string
	}{
		{name: "method", method: http.MethodPost, target: "/api/v1/library?view=movies", repository: &libraryTestRepository{}, wantStatus: http.StatusMethodNotAllowed, wantCode: "method_not_allowed"},
		{name: "missing view", method: http.MethodGet, target: "/api/v1/library", repository: &libraryTestRepository{}, wantStatus: http.StatusBadRequest, wantCode: "invalid_request"},
		{name: "extra query", method: http.MethodGet, target: "/api/v1/library?view=movies&extra=1", repository: &libraryTestRepository{}, wantStatus: http.StatusBadRequest, wantCode: "invalid_request"},
		{name: "invalid view", method: http.MethodGet, target: "/api/v1/library?view=all", repository: &libraryTestRepository{}, wantStatus: http.StatusBadRequest, wantCode: "invalid_library_view"},
		{name: "unavailable", method: http.MethodGet, target: "/api/v1/library?view=movies", repository: nil, wantStatus: http.StatusServiceUnavailable, wantCode: "library_unavailable"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			handler := HandlerWithJobsAndLibrary(libraryTestSearcher{}, nil, test.repository)
			request := httptest.NewRequest(test.method, test.target, nil)
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if response.Code != test.wantStatus {
				t.Fatalf("status = %d body=%s", response.Code, response.Body.String())
			}
			var body contracts.ErrorResponse
			if err := json.Unmarshal(response.Body.Bytes(), &body); err != nil {
				t.Fatalf("decode error body: %v", err)
			}
			if body.Code != test.wantCode {
				t.Fatalf("code = %q want %q", body.Code, test.wantCode)
			}
		})
	}
}
