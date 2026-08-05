package httpapi

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/adminauth"
	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/sourceadmin"
)

func TestCompositeHandlerMountsSourceAdministrationExplicitly(t *testing.T) {
	const token = "this-is-a-long-development-admin-token"
	verifier, err := adminauth.NewVerifier(token)
	if err != nil {
		t.Fatal(err)
	}
	handler := HandlerWithServicesAndSources(nil, nil, nil, verifier, sourceadmin.NewMemoryRegistry())
	request := httptest.NewRequest(http.MethodGet, "/api/v1/admin/sources", nil)
	request.Header.Set("Authorization", "Bearer "+token)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d body=%s", response.Code, response.Body.String())
	}
	if response.Body.String() != "[]\n" {
		t.Fatalf("body = %q, want empty source list", response.Body.String())
	}
}

func TestCompositeHandlerLeavesSourceAdministrationFailClosedByDefault(t *testing.T) {
	handler := HandlerWithServices(nil, nil, nil, nil)
	request := httptest.NewRequest(http.MethodGet, "/api/v1/admin/sources", nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusServiceUnavailable)
	}
}
