package runtimehealth

import (
	"context"
	"encoding/json"
	"net/http"
	"sort"
	"time"
)

const defaultCheckTimeout = 2 * time.Second

type Checker func(context.Context) error

type Options struct {
	Checks  map[string]Checker
	Timeout time.Duration
}

type Handler struct {
	checks  map[string]Checker
	timeout time.Duration
}

type response struct {
	Status string   `json:"status"`
	Checks []string `json:"checks,omitempty"`
}

func New(opts Options) *Handler {
	timeout := opts.Timeout
	if timeout <= 0 {
		timeout = defaultCheckTimeout
	}

	checks := make(map[string]Checker, len(opts.Checks))
	for name, check := range opts.Checks {
		if name == "" || check == nil {
			continue
		}
		checks[name] = check
	}

	return &Handler{checks: checks, timeout: timeout}
}

func (h *Handler) Liveness(w http.ResponseWriter, r *http.Request) {
	if !validRequest(w, r) {
		return
	}
	writeJSON(w, http.StatusOK, response{Status: "ok"})
}

func (h *Handler) Readiness(w http.ResponseWriter, r *http.Request) {
	if !validRequest(w, r) {
		return
	}

	names := make([]string, 0, len(h.checks))
	for name := range h.checks {
		names = append(names, name)
	}
	sort.Strings(names)

	ctx, cancel := context.WithTimeout(r.Context(), h.timeout)
	defer cancel()

	for _, name := range names {
		if err := h.checks[name](ctx); err != nil {
			writeJSON(w, http.StatusServiceUnavailable, response{Status: "unavailable"})
			return
		}
	}

	writeJSON(w, http.StatusOK, response{Status: "ready", Checks: names})
}

func validRequest(w http.ResponseWriter, r *http.Request) bool {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD")
		writeJSON(w, http.StatusMethodNotAllowed, response{Status: "method_not_allowed"})
		return false
	}
	if r.URL.RawQuery != "" {
		writeJSON(w, http.StatusBadRequest, response{Status: "invalid_request"})
		return false
	}
	return true
}

func writeJSON(w http.ResponseWriter, status int, payload response) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}
