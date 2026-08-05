package httpapi

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/adminauth"
	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/sourceadmin"
)

func TestSourceAdminHandlerFailsClosed(t *testing.T) {
	handler := SourceAdminHandler(nil, sourceadmin.NewMemoryRegistry())
	request := httptest.NewRequest(http.MethodGet, "/api/v1/admin/sources", nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusServiceUnavailable)
	}
}

func TestSourceAdminHandlerRequiresBearerToken(t *testing.T) {
	verifier, err := adminauth.NewVerifier("this-is-a-long-development-admin-token")
	if err != nil { t.Fatal(err) }
	handler := SourceAdminHandler(verifier, sourceadmin.NewMemoryRegistry())
	request := httptest.NewRequest(http.MethodGet, "/api/v1/admin/sources", nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusUnauthorized)
	}
}

func TestSourceAdminCreateAndRevisionConflict(t *testing.T) {
	const token = "this-is-a-long-development-admin-token"
	verifier, err := adminauth.NewVerifier(token)
	if err != nil { t.Fatal(err) }
	handler := SourceAdminHandler(verifier, sourceadmin.NewMemoryRegistry())

	create := `{"id":"example","displayName":"Example","kind":"http-json","endpoint":"https://example.test/","enabled":true}`
	request := httptest.NewRequest(http.MethodPost, "/api/v1/admin/sources", strings.NewReader(create))
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusCreated {
		t.Fatalf("create status = %d body=%s", response.Code, response.Body.String())
	}
	if strings.Contains(response.Body.String(), token) {
		t.Fatal("response leaked administrator token")
	}

	update := `{"id":"example","displayName":"Example 2","kind":"http-json","endpoint":"https://example.test/","enabled":true,"expectedRevision":9}`
	request = httptest.NewRequest(http.MethodPut, "/api/v1/admin/sources/example", strings.NewReader(update))
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set("Content-Type", "application/json")
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusConflict {
		t.Fatalf("update status = %d body=%s", response.Code, response.Body.String())
	}
}

func TestSourceAdminRejectsQueryAndUnknownFields(t *testing.T) {
	const token = "this-is-a-long-development-admin-token"
	verifier, err := adminauth.NewVerifier(token)
	if err != nil { t.Fatal(err) }
	handler := SourceAdminHandler(verifier, sourceadmin.NewMemoryRegistry())

	request := httptest.NewRequest(http.MethodGet, "/api/v1/admin/sources?token=secret", nil)
	request.Header.Set("Authorization", "Bearer "+token)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusBadRequest { t.Fatalf("query status = %d", response.Code) }

	body := `{"id":"example","displayName":"Example","kind":"http-json","endpoint":"https://example.test/","enabled":true,"headers":{"Authorization":"secret"}}`
	request = httptest.NewRequest(http.MethodPost, "/api/v1/admin/sources", strings.NewReader(body))
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set("Content-Type", "application/json")
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusBadRequest { t.Fatalf("unknown field status = %d", response.Code) }
	if strings.Contains(response.Body.String(), "secret") { t.Fatal("error response leaked rejected secret") }
}
