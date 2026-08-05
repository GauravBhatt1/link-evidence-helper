package httpapi

import (
	"context"
	"errors"
	"net/http"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/adminauth"
	libraryservice "github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/library"
	searchservice "github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/search"
)

// HandlerWithJobsAndLibrary enables the deterministic library API alongside
// the existing search and job routes. Admin authentication remains disabled.
func HandlerWithJobsAndLibrary(searcher searchservice.Searcher, jobs JobBackend, library libraryservice.Repository) http.Handler {
	return HandlerWithServices(searcher, jobs, library, nil)
}

// HandlerWithServices composes all currently supported API boundaries. The
// admin route exists only as a session probe and is fail-closed when verifier
// is nil; future administrative mutations must be mounted behind the same
// verifier boundary.
func HandlerWithServices(searcher searchservice.Searcher, jobs JobBackend, library libraryservice.Repository, verifier *adminauth.Verifier) http.Handler {
	api := &apiHandler{searcher: searcher, jobs: jobs}
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", api.health)
	mux.HandleFunc("/api/v1/search", api.search)
	mux.HandleFunc("/api/v1/library", api.library(library))
	mux.HandleFunc("/api/v1/jobs/resolution", api.createResolutionJob)
	mux.HandleFunc("/api/v1/jobs/", api.jobResource)
	mux.HandleFunc("/api/v1/admin/session", api.adminSession(verifier))
	return api.securityHeaders(mux)
}

func (api *apiHandler) library(repository libraryservice.Repository) http.HandlerFunc {
	return func(writer http.ResponseWriter, request *http.Request) {
		requestID := api.requestID()
		writer.Header().Set("X-Request-ID", requestID)
		if request.Method != http.MethodGet {
			methodNotAllowed(writer, http.MethodGet)
			return
		}
		if repository == nil {
			writeError(writer, http.StatusServiceUnavailable, "library_unavailable", "The development library service is not enabled.", requestID)
			return
		}

		values := request.URL.Query()
		if len(values) != 1 {
			writeError(writer, http.StatusBadRequest, "invalid_request", "Provide exactly one view parameter.", requestID)
			return
		}
		views, exists := values["view"]
		if !exists || len(views) != 1 {
			writeError(writer, http.StatusBadRequest, "invalid_request", "Provide exactly one view parameter.", requestID)
			return
		}
		view, err := libraryservice.ParseView(views[0])
		if err != nil {
			writeError(writer, http.StatusBadRequest, "invalid_library_view", "Library view must be movies, tv, missing, or recent.", requestID)
			return
		}

		response, err := repository.List(request.Context(), view)
		if err != nil {
			switch {
			case errors.Is(err, context.Canceled), errors.Is(err, context.DeadlineExceeded):
				return
			case errors.Is(err, libraryservice.ErrInvalidView):
				writeError(writer, http.StatusBadRequest, "invalid_library_view", "Library view must be movies, tv, missing, or recent.", requestID)
			default:
				writeError(writer, http.StatusInternalServerError, "internal_error", "The development library could not be loaded.", requestID)
			}
			return
		}
		writeJSON(writer, http.StatusOK, response)
	}
}
