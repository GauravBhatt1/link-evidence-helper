package httpapi

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/adminauth"
	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/diagnostics"
)

type diagnosticsProviderStub struct {
	snapshot diagnostics.Snapshot
	err      error
}

func (provider diagnosticsProviderStub) Snapshot(context.Context) (diagnostics.Snapshot, error) {
	return provider.snapshot, provider.err
}

func TestDiagnosticsRouteFailsClosed(t *testing.T) {
	handler := HandlerWithServicesSourcesAndDiagnostics(nil, nil, nil, nil, nil, nil)
	request := httptest.NewRequest(http.MethodGet, "/api/v1/admin/diagnostics", nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusServiceUnavailable)
	}
}

func TestDiagnosticsRouteRequiresAuthorizationAndRejectsQuery(t *testing.T) {
	verifier, err := adminauth.NewVerifier("0123456789abcdef0123456789abcdef")
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := diagnostics.NewSnapshot(time.Unix(1, 0), []diagnostics.Check{{Component: diagnostics.ComponentAPI, Status: diagnostics.StatusOK, Code: "api_ready"}})
	if err != nil {
		t.Fatal(err)
	}
	handler := HandlerWithServicesSourcesAndDiagnostics(nil, nil, nil, verifier, nil, diagnosticsProviderStub{snapshot: snapshot})

	unauthorized := httptest.NewRecorder()
	handler.ServeHTTP(unauthorized, httptest.NewRequest(http.MethodGet, "/api/v1/admin/diagnostics", nil))
	if unauthorized.Code != http.StatusUnauthorized || unauthorized.Header().Get("WWW-Authenticate") == "" {
		t.Fatalf("unauthorized response = %d, auth header %q", unauthorized.Code, unauthorized.Header().Get("WWW-Authenticate"))
	}

	query := httptest.NewRequest(http.MethodGet, "/api/v1/admin/diagnostics?token=secret-value", nil)
	query.Header.Set("Authorization", "Bearer 0123456789abcdef0123456789abcdef")
	queryResponse := httptest.NewRecorder()
	handler.ServeHTTP(queryResponse, query)
	if queryResponse.Code != http.StatusBadRequest {
		t.Fatalf("query status = %d, want %d", queryResponse.Code, http.StatusBadRequest)
	}
	if strings.Contains(queryResponse.Body.String(), "secret-value") {
		t.Fatal("response leaked query value")
	}
}

func TestDiagnosticsRouteReturnsBoundedSnapshot(t *testing.T) {
	verifier, err := adminauth.NewVerifier("0123456789abcdef0123456789abcdef")
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := diagnostics.NewSnapshot(time.Unix(1, 0), []diagnostics.Check{
		{Component: diagnostics.ComponentAPI, Status: diagnostics.StatusOK, Code: "api_ready"},
		{Component: diagnostics.ComponentPostgres, Status: diagnostics.StatusDisabled, Code: "not_configured"},
	})
	if err != nil {
		t.Fatal(err)
	}
	handler := HandlerWithServicesSourcesAndDiagnostics(nil, nil, nil, verifier, nil, diagnosticsProviderStub{snapshot: snapshot})
	request := httptest.NewRequest(http.MethodGet, "/api/v1/admin/diagnostics", nil)
	request.Header.Set("Authorization", "Bearer 0123456789abcdef0123456789abcdef")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", response.Code, response.Body.String())
	}
	body := response.Body.String()
	for _, forbidden := range []string{"Authorization", "Bearer", "password", "cookie", "http://", "https://"} {
		if strings.Contains(strings.ToLower(body), strings.ToLower(forbidden)) {
			t.Fatalf("response contains forbidden value %q: %s", forbidden, body)
		}
	}
}

func TestDiagnosticsRouteMapsProviderFailure(t *testing.T) {
	verifier, err := adminauth.NewVerifier("0123456789abcdef0123456789abcdef")
	if err != nil {
		t.Fatal(err)
	}
	handler := HandlerWithServicesSourcesAndDiagnostics(nil, nil, nil, verifier, nil, diagnosticsProviderStub{err: errors.New("sensitive backend detail")})
	request := httptest.NewRequest(http.MethodGet, "/api/v1/admin/diagnostics", nil)
	request.Header.Set("Authorization", "Bearer 0123456789abcdef0123456789abcdef")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusServiceUnavailable)
	}
	if strings.Contains(response.Body.String(), "sensitive backend detail") {
		t.Fatal("response leaked provider error")
	}
}
