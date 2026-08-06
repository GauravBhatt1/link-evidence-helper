package runtimehealth

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestLivenessIsStableAndSecretSafe(t *testing.T) {
	h := New(Options{})
	req := httptest.NewRequest(http.MethodGet, "/health/live", nil)
	res := httptest.NewRecorder()

	h.Liveness(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", res.Code, http.StatusOK)
	}
	if got := res.Body.String(); got != "{\"status\":\"ok\"}\n" {
		t.Fatalf("body = %q", got)
	}
	assertSafeHeaders(t, res)
}

func TestReadinessReportsOnlyCheckNames(t *testing.T) {
	h := New(Options{Checks: map[string]Checker{
		"postgres": func(context.Context) error { return nil },
		"redis":    func(context.Context) error { return nil },
	}})
	res := httptest.NewRecorder()

	h.Readiness(res, httptest.NewRequest(http.MethodGet, "/health/ready", nil))

	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", res.Code, http.StatusOK)
	}
	if got := res.Body.String(); got != "{\"status\":\"ready\",\"checks\":[\"postgres\",\"redis\"]}\n" {
		t.Fatalf("body = %q", got)
	}
}

func TestReadinessDoesNotLeakBackendError(t *testing.T) {
	secret := "postgres://admin:super-secret@example.invalid/app"
	h := New(Options{Checks: map[string]Checker{
		"postgres": func(context.Context) error { return errors.New(secret) },
	}})
	res := httptest.NewRecorder()

	h.Readiness(res, httptest.NewRequest(http.MethodGet, "/health/ready", nil))

	if res.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want %d", res.Code, http.StatusServiceUnavailable)
	}
	if strings.Contains(res.Body.String(), secret) || strings.Contains(res.Body.String(), "postgres") {
		t.Fatalf("response leaked backend detail: %q", res.Body.String())
	}
	if got := res.Body.String(); got != "{\"status\":\"unavailable\"}\n" {
		t.Fatalf("body = %q", got)
	}
}

func TestReadinessAppliesBoundedTimeout(t *testing.T) {
	h := New(Options{
		Timeout: 10 * time.Millisecond,
		Checks: map[string]Checker{
			"blocked": func(ctx context.Context) error {
				<-ctx.Done()
				return ctx.Err()
			},
		},
	})
	res := httptest.NewRecorder()

	started := time.Now()
	h.Readiness(res, httptest.NewRequest(http.MethodGet, "/health/ready", nil))
	if elapsed := time.Since(started); elapsed > 250*time.Millisecond {
		t.Fatalf("readiness exceeded bounded timeout: %s", elapsed)
	}
	if res.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want %d", res.Code, http.StatusServiceUnavailable)
	}
}

func TestHealthEndpointsRejectQueriesAndMutatingMethods(t *testing.T) {
	h := New(Options{})

	for _, tc := range []struct {
		method string
		target string
		want   int
	}{
		{http.MethodGet, "/health/live?token=secret", http.StatusBadRequest},
		{http.MethodPost, "/health/live", http.StatusMethodNotAllowed},
	} {
		res := httptest.NewRecorder()
		h.Liveness(res, httptest.NewRequest(tc.method, tc.target, nil))
		if res.Code != tc.want {
			t.Fatalf("%s %s status = %d, want %d", tc.method, tc.target, res.Code, tc.want)
		}
		if strings.Contains(res.Body.String(), "secret") {
			t.Fatalf("response leaked query value: %q", res.Body.String())
		}
	}
}

func TestHeadRequestReturnsNoSensitiveDetail(t *testing.T) {
	h := New(Options{})
	res := httptest.NewRecorder()
	h.Liveness(res, httptest.NewRequest(http.MethodHead, "/health/live", nil))
	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", res.Code, http.StatusOK)
	}
}

func assertSafeHeaders(t *testing.T, res *httptest.ResponseRecorder) {
	t.Helper()
	if got := res.Header().Get("Cache-Control"); got != "no-store" {
		t.Fatalf("Cache-Control = %q", got)
	}
	if got := res.Header().Get("X-Content-Type-Options"); got != "nosniff" {
		t.Fatalf("X-Content-Type-Options = %q", got)
	}
}
