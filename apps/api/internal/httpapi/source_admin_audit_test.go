package httpapi

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/adminauth"
	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/audit"
	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/sourceadmin"
)

func TestSourceAdminRecordsBoundedMutationOutcomes(t *testing.T) {
	const token = "this-is-a-long-development-admin-token"
	verifier, err := adminauth.NewVerifier(token)
	if err != nil {
		t.Fatal(err)
	}
	recorder := audit.NewMemoryRecorder()
	handler := SourceAdminHandlerWithAudit(verifier, sourceadmin.NewMemoryRegistry(), recorder)

	body := `{"id":"example","displayName":"Example","kind":"http-json","endpoint":"https://example.test/","enabled":true}`
	request := httptest.NewRequest(http.MethodPost, "/api/v1/admin/sources", strings.NewReader(body))
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusCreated {
		t.Fatalf("status = %d body=%s", response.Code, response.Body.String())
	}

	events := recorder.List()
	if len(events) != 1 {
		t.Fatalf("event count = %d", len(events))
	}
	event := events[0]
	if event.Action != "source.create" || event.Resource != "source:example" || event.Outcome != "success" {
		t.Fatalf("event = %#v", event)
	}
	serialized := response.Body.String()
	if strings.Contains(serialized, token) {
		t.Fatal("response leaked administrator token")
	}
}

func TestSourceAdminRecordsFailedConflict(t *testing.T) {
	const token = "this-is-a-long-development-admin-token"
	verifier, err := adminauth.NewVerifier(token)
	if err != nil {
		t.Fatal(err)
	}
	registry := sourceadmin.NewMemoryRegistry()
	recorder := audit.NewMemoryRecorder()
	handler := SourceAdminHandlerWithAudit(verifier, registry, recorder)

	created, err := registry.Create(sourceadmin.Draft{ID: "example", DisplayName: "Example", Kind: "http-json", Endpoint: "https://example.test/", Enabled: true}, testNow())
	if err != nil {
		t.Fatal(err)
	}
	body := `{"id":"example","displayName":"Changed","kind":"http-json","endpoint":"https://example.test/","enabled":true,"expectedRevision":99}`
	request := httptest.NewRequest(http.MethodPut, "/api/v1/admin/sources/example", strings.NewReader(body))
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusConflict {
		t.Fatalf("status = %d body=%s created=%#v", response.Code, response.Body.String(), created)
	}
	events := recorder.List()
	if len(events) != 1 || events[0].Outcome != "failure" || events[0].Action != "source.update" {
		t.Fatalf("events = %#v", events)
	}
}
