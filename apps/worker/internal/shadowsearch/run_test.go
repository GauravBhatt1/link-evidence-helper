package shadowsearch

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/worker/internal/httpsearch"
	"github.com/GauravBhatt1/link-evidence-helper/apps/worker/internal/sourceconfig"
)

func TestRunProducesUnifiedContentAndSafePartialFailure(t *testing.T) {
	good := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("q") != "Example Film" {
			t.Fatalf("query = %q", r.URL.Query().Get("q"))
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`[
			{"title":"Example Film 2024 Hindi Dual Audio 1080p WEB-DL 1.5GB","url":"/release"}
		]`))
	}))
	defer good.Close()
	failing := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "upstream secret text", http.StatusBadGateway)
	}))
	defer failing.Close()

	config := sourceconfig.Config{Version: 1, Sources: []sourceconfig.SourceConfig{
		{ID: "primary", Name: "Primary", Enabled: true, Rank: 10, Endpoint: good.URL, QueryParameter: "q", Format: "json", TitleField: "title", URLField: "url"},
		{ID: "backup", Name: "Backup", Enabled: true, Rank: 20, Endpoint: failing.URL, QueryParameter: "q", Format: "json", TitleField: "title", URLField: "url"},
	}}
	sources, err := sourceconfig.Compile(config)
	if err != nil {
		t.Fatal(err)
	}
	output, err := Run(context.Background(), httpsearch.Engine{
		AllowPrivate:  true,
		SourceTimeout: time.Second,
	}, "  Example   Film ", sources)
	if err != nil {
		t.Fatal(err)
	}
	if output.Mode != "development-shadow-http-search" || output.Query != "Example Film" {
		t.Fatalf("output = %#v", output)
	}
	if len(output.Contents) != 1 || output.Contents[0].TotalSources != 1 || len(output.Contents[0].ReleaseVariants) != 1 {
		t.Fatalf("contents = %#v", output.Contents)
	}
	variant := output.Contents[0].ReleaseVariants[0]
	if variant.Language != "Hindi" || variant.AudioVariant != "Dual Audio" || variant.Quality != "1080P" || len(variant.Sources) != 1 || variant.Sources[0].AdapterName != "primary" {
		t.Fatalf("variant = %#v", variant)
	}
	if len(output.PartialFailures) != 1 || output.PartialFailures[0].SourceID != "backup" || output.PartialFailures[0].Message == "upstream secret text" {
		t.Fatalf("failures = %#v", output.PartialFailures)
	}
}

func TestRunReturnsCanonicalEmptyCollections(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`[]`))
	}))
	defer server.Close()
	sources, err := sourceconfig.Compile(sourceconfig.Config{Version: 1, Sources: []sourceconfig.SourceConfig{{
		ID: "source", Name: "Source", Enabled: true, Endpoint: server.URL,
		QueryParameter: "q", Format: "json", TitleField: "title", URLField: "url",
	}}})
	if err != nil {
		t.Fatal(err)
	}
	output, err := Run(context.Background(), httpsearch.Engine{AllowPrivate: true}, "Unknown", sources)
	if err != nil {
		t.Fatal(err)
	}
	if output.Contents == nil || output.PartialFailures == nil || len(output.Contents) != 0 || len(output.PartialFailures) != 0 {
		t.Fatalf("output = %#v", output)
	}
}
