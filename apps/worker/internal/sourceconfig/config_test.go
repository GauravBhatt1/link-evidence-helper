package sourceconfig

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/worker/internal/httpsearch"
)

func TestLoadRejectsUnknownFieldsAndTrailingDocuments(t *testing.T) {
	for _, raw := range []string{
		`{"version":1,"sources":[],"cookies":"secret"}`,
		`{"version":1,"sources":[]} {}`,
	} {
		if _, err := Load(strings.NewReader(raw)); !errors.Is(err, ErrInvalidConfig) {
			t.Fatalf("Load(%s) error = %v", raw, err)
		}
	}
}

func TestValidateRejectsUnsafeOrAmbiguousConfiguration(t *testing.T) {
	valid := SourceConfig{
		ID: "source-a", Name: "Source A", Enabled: true, Rank: 10,
		Endpoint: "https://source.example/search", QueryParameter: "q", Format: "json",
		TitleField: "title", URLField: "url",
	}
	tests := []Config{
		{Version: 2, Sources: []SourceConfig{valid}},
		{Version: 1, Sources: []SourceConfig{}},
		{Version: 1, Sources: []SourceConfig{valid, valid}},
		{Version: 1, Sources: []SourceConfig{func() SourceConfig { value := valid; value.Enabled = false; return value }()}},
		{Version: 1, Sources: []SourceConfig{func() SourceConfig { value := valid; value.Endpoint = "https://user:pass@source.example/search"; return value }()}},
		{Version: 1, Sources: []SourceConfig{func() SourceConfig { value := valid; value.Endpoint = "https://source.example/search?fixed=1"; return value }()}},
		{Version: 1, Sources: []SourceConfig{func() SourceConfig { value := valid; value.Format = "html"; return value }()}},
		{Version: 1, Sources: []SourceConfig{func() SourceConfig { value := valid; value.TitleField = "items[0].title"; return value }()}},
	}
	for index, config := range tests {
		if err := Validate(config); !errors.Is(err, ErrInvalidConfig) {
			t.Fatalf("case %d error = %v", index, err)
		}
	}
}

func TestCompileSortsEnabledSourcesAndParsesNestedJSON(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("query") != "Example Film" {
			t.Fatalf("query = %q", r.URL.Query().Get("query"))
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"payload":{"items":[{"metadata":{"name":"Example Film 2024 Hindi 1080p"},"links":{"page":"/item"}}]}}`))
	}))
	defer server.Close()

	config := Config{Version: 1, Sources: []SourceConfig{
		{ID: "disabled", Name: "Disabled", Enabled: false, Rank: 1, Endpoint: server.URL, QueryParameter: "q", Format: "json", TitleField: "title", URLField: "url"},
		{ID: "later", Name: "Later", Enabled: true, Rank: 20, Endpoint: server.URL, QueryParameter: "q", Format: "json", ResultRoot: "payload.items", TitleField: "metadata.name", URLField: "links.page"},
		{ID: "first", Name: "First", Enabled: true, Rank: 10, Endpoint: server.URL, QueryParameter: "query", Format: "json", ResultRoot: "payload.items", TitleField: "metadata.name", URLField: "links.page"},
	}}
	sources, err := Compile(config)
	if err != nil {
		t.Fatal(err)
	}
	if len(sources) != 2 || sources[0].ID != "first" || sources[1].ID != "later" {
		t.Fatalf("sources = %#v", sources)
	}

	engine := httpsearch.Engine{AllowPrivate: true, SourceTimeout: time.Second}
	response, err := engine.Search(context.Background(), "Example Film", sources[:1])
	if err != nil {
		t.Fatal(err)
	}
	if len(response.Results) != 1 {
		t.Fatalf("response = %#v", response)
	}
	result := response.Results[0]
	if result.SourceID != "first" || result.Title != "Example Film 2024 Hindi 1080p" || !strings.HasSuffix(result.URL, "/item") {
		t.Fatalf("result = %#v", result)
	}
}

func TestCompileRejectsMissingConfiguredJSONFields(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`[{"name":"missing URL"}]`))
	}))
	defer server.Close()
	config := Config{Version: 1, Sources: []SourceConfig{{
		ID: "source", Name: "Source", Enabled: true, Endpoint: server.URL,
		QueryParameter: "q", Format: "json", TitleField: "name", URLField: "url",
	}}}
	sources, err := Compile(config)
	if err != nil {
		t.Fatal(err)
	}
	response, err := (httpsearch.Engine{AllowPrivate: true}).Search(context.Background(), "query", sources)
	if err != nil {
		t.Fatal(err)
	}
	if len(response.Errors) != 1 || response.Errors[0].Code != "source_failed" {
		t.Fatalf("response = %#v", response)
	}
}
