// Package httpapi exposes the versioned development-only Go API.
package httpapi

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sync/atomic"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/contracts"
	searchservice "github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/search"
)

// Handler returns an isolated HTTP handler. It does not enable CORS, contact
// production services, or proxy to the Python application.
func Handler(searcher searchservice.Searcher) http.Handler {
	api := &apiHandler{searcher: searcher}
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", api.health)
	mux.HandleFunc("/api/v1/search", api.search)
	return api.securityHeaders(mux)
}

type apiHandler struct {
	searcher searchservice.Searcher
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
	writeJSON(writer, http.StatusOK, struct {
		OK      bool   `json:"ok"`
		Service string `json:"service"`
		Mode    string `json:"mode"`
	}{OK: true, Service: "link-evidence-api", Mode: "development-fixture"})
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
