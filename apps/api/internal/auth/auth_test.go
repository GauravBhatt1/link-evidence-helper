package auth

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestVerifierRejectsEmptyConfiguration(t *testing.T) {
	if _, err := NewVerifier("  ", "admin"); err != ErrMissingCredential {
		t.Fatalf("expected ErrMissingCredential, got %v", err)
	}
}

func TestAuthenticateRequest(t *testing.T) {
	verifier, err := NewVerifier("correct-secret", "operator")
	if err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		name   string
		header string
		want   error
	}{
		{name: "missing", want: ErrMissingCredential},
		{name: "wrong scheme", header: "Basic abc", want: ErrMissingCredential},
		{name: "empty bearer", header: "Bearer ", want: ErrMissingCredential},
		{name: "wrong token", header: "Bearer wrong", want: ErrInvalidCredential},
		{name: "valid", header: "Bearer correct-secret"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodGet, "/admin", nil)
			request.Header.Set("Authorization", test.header)
			principal, got := verifier.AuthenticateRequest(request)
			if got != test.want {
				t.Fatalf("expected %v, got %v", test.want, got)
			}
			if test.want == nil && (principal.Subject != "operator" || principal.Role != RoleAdmin) {
				t.Fatalf("unexpected principal: %#v", principal)
			}
		})
	}
}

func TestRequireAdmin(t *testing.T) {
	verifier, err := NewVerifier("correct-secret", "operator")
	if err != nil {
		t.Fatal(err)
	}

	handler := verifier.RequireAdmin(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		principal, ok := PrincipalFromContext(request.Context())
		if !ok || principal.Subject != "operator" {
			t.Fatalf("principal missing from context: %#v", principal)
		}
		writer.WriteHeader(http.StatusNoContent)
	}))

	unauthorized := httptest.NewRecorder()
	handler.ServeHTTP(unauthorized, httptest.NewRequest(http.MethodGet, "/admin", nil))
	if unauthorized.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", unauthorized.Code)
	}
	if got := unauthorized.Header().Get("Cache-Control"); got != "no-store" {
		t.Fatalf("expected no-store, got %q", got)
	}

	authorizedRequest := httptest.NewRequest(http.MethodGet, "/admin", nil)
	authorizedRequest.Header.Set("Authorization", "Bearer correct-secret")
	authorized := httptest.NewRecorder()
	handler.ServeHTTP(authorized, authorizedRequest)
	if authorized.Code != http.StatusNoContent {
		t.Fatalf("expected 204, got %d", authorized.Code)
	}
}
