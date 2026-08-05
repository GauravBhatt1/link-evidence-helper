package httpapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/adminauth"
	searchservice "github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/search"
)

func TestAdminSessionFailsClosedWhenUnconfigured(t *testing.T) {
	handler := HandlerWithServices(searchservice.Searcher(nil), nil, nil, nil)
	request := httptest.NewRequest(http.MethodGet, "/api/v1/admin/session", nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d", response.Code)
	}
	assertAdminErrorCode(t, response, "admin_auth_unavailable")
}

func TestAdminSessionRequiresExactBearerCredential(t *testing.T) {
	token := strings.Repeat("AdminToken_", 4)
	verifier, err := adminauth.NewVerifier(token)
	if err != nil {
		t.Fatalf("NewVerifier() error = %v", err)
	}
	handler := HandlerWithServices(searchservice.Searcher(nil), nil, nil, verifier)

	for _, header := range []string{"", "Bearer wrong", "Basic " + token} {
		request := httptest.NewRequest(http.MethodGet, "/api/v1/admin/session", nil)
		request.Header.Set("Authorization", header)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != http.StatusUnauthorized {
			t.Fatalf("header %q status = %d", header, response.Code)
		}
		if response.Header().Get("WWW-Authenticate") == "" {
			t.Fatal("missing WWW-Authenticate header")
		}
		assertAdminErrorCode(t, response, "unauthorized")
	}

	request := httptest.NewRequest(http.MethodGet, "/api/v1/admin/session", nil)
	request.Header.Set("Authorization", "Bearer "+token)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d body=%s", response.Code, response.Body.String())
	}
	var payload struct {
		OK      bool   `json:"ok"`
		Success bool   `json:"success"`
		Role    string `json:"role"`
	}
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if !payload.OK || !payload.Success || payload.Role != "admin" {
		t.Fatalf("payload = %#v", payload)
	}
}

func TestAdminSessionRejectsQueriesAndWrongMethods(t *testing.T) {
	verifier, _ := adminauth.NewVerifier(strings.Repeat("x", 40))
	handler := HandlerWithServices(searchservice.Searcher(nil), nil, nil, verifier)

	request := httptest.NewRequest(http.MethodGet, "/api/v1/admin/session?debug=true", nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusBadRequest {
		t.Fatalf("query status = %d", response.Code)
	}

	request = httptest.NewRequest(http.MethodPost, "/api/v1/admin/session", nil)
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusMethodNotAllowed || response.Header().Get("Allow") != http.MethodGet {
		t.Fatalf("method status=%d allow=%q", response.Code, response.Header().Get("Allow"))
	}
}

func assertAdminErrorCode(t *testing.T, response *httptest.ResponseRecorder, expected string) {
	t.Helper()
	var payload struct {
		Code string `json:"code"`
	}
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		t.Fatalf("decode error response: %v", err)
	}
	if payload.Code != expected {
		t.Fatalf("code = %q, want %q", payload.Code, expected)
	}
}
