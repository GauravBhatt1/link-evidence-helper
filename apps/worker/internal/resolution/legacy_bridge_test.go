package resolution

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/packages/jobqueue"
)

func TestLegacyResolverExecutesFindAndMapsLinks(t *testing.T) {
	verifiedAt := time.Date(2026, 8, 8, 10, 30, 0, 0, time.UTC)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/find" {
			t.Fatalf("path = %q", request.URL.Path)
		}
		if request.Header.Get("x-app-token") != "runtime-token" {
			t.Fatalf("token header = %q", request.Header.Get("x-app-token"))
		}
		var payload Request
		if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		if payload.ContentID != "content_3b750f8edc77152e" || payload.VariantID != "variant_051fab7b083f979a" || payload.Quality == nil || *payload.Quality != "1080p" {
			t.Fatalf("payload = %#v", payload)
		}
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{
			"ok": true,
			"links": [{
				"url": "https://downloads.example/file.mkv",
				"size": "2.4 GB",
				"quality": "1080p",
				"quality_label": "1080p WEB-DL",
				"source_name": "bollyflix.at",
				"variant": "Ikka 1080p"
			}],
			"debug": [{"secret": true}]
		}`))
	}))
	defer server.Close()
	resolver, err := NewLegacyResolver(LegacyResolverConfig{
		BaseURL:     server.URL,
		AccessToken: "runtime-token",
		Now:         func() time.Time { return verifiedAt },
	})
	if err != nil {
		t.Fatal(err)
	}
	reporter := &fakeReporter{}
	quality := "1080p"
	payload, _ := json.Marshal(Request{
		ContentID: "content_3b750f8edc77152e",
		VariantID: "variant_051fab7b083f979a",
		Quality:   &quality,
	})
	if err := resolver.Execute(context.Background(), jobqueue.Job{Kind: jobqueue.KindResolution, Payload: payload}, reporter); err != nil {
		t.Fatal(err)
	}
	if reporter.states[len(reporter.states)-1] != jobqueue.StateVerified {
		t.Fatalf("states = %#v", reporter.states)
	}
	var result resolutionResult
	if err := json.Unmarshal(reporter.result, &result); err != nil {
		t.Fatal(err)
	}
	if !result.OK || result.Code != "ok" || len(result.DeliveryLinks) != 1 || len(result.Attempts) != 1 {
		t.Fatalf("result = %#v", result)
	}
	link := result.DeliveryLinks[0]
	if link.URL != "https://downloads.example/file.mkv" || link.Filename != "Ikka 1080p" || link.SourceID != "bollyflix.at_1" || !link.VerifiedAt.Equal(verifiedAt) {
		t.Fatalf("link = %#v", link)
	}
}

func TestLegacyResolverRejectsNonLoopbackBaseURLByDefault(t *testing.T) {
	if _, err := NewLegacyResolver(LegacyResolverConfig{BaseURL: "https://example.com"}); !errors.Is(err, ErrLegacyResolverInvalid) {
		t.Fatalf("error = %v", err)
	}
	if _, err := NewLegacyResolver(LegacyResolverConfig{BaseURL: "https://example.com", AllowNonLoopback: true}); err != nil {
		t.Fatalf("explicit opt-in should allow non-loopback: %v", err)
	}
}

func TestLegacyResolverMapsUpstreamFailureSafely(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		writer.WriteHeader(http.StatusBadGateway)
		_, _ = writer.Write([]byte(`{"ok": false, "error": "Missing selected result"}`))
	}))
	defer server.Close()
	resolver, err := NewLegacyResolver(LegacyResolverConfig{BaseURL: server.URL})
	if err != nil {
		t.Fatal(err)
	}
	reporter := &fakeReporter{}
	if err := resolver.Execute(context.Background(), testJob(`{
		"contentId":"content_3b750f8edc77152e",
		"variantId":"variant_051fab7b083f979a",
		"quality":"1080p"
	}`), reporter); err != nil {
		t.Fatal(err)
	}
	var result resolutionResult
	if err := json.Unmarshal(reporter.result, &result); err != nil {
		t.Fatal(err)
	}
	if result.Code != "legacy_resolution_failed" || result.Message != "Missing selected result" || reporter.states[len(reporter.states)-1] != jobqueue.StateFailed {
		t.Fatalf("result=%#v states=%#v", result, reporter.states)
	}
}

func TestLegacyResolverDropsUnsafeLinks(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"ok": true, "links": [{"url": "https://downloads.example/file.mkv#secret"}, {"url": "not-a-url"}]}`))
	}))
	defer server.Close()
	resolver, err := NewLegacyResolver(LegacyResolverConfig{BaseURL: server.URL})
	if err != nil {
		t.Fatal(err)
	}
	reporter := &fakeReporter{}
	if err := resolver.Execute(context.Background(), testJob(`{
		"contentId":"content_3b750f8edc77152e",
		"variantId":"variant_051fab7b083f979a",
		"quality":"1080p"
	}`), reporter); err != nil {
		t.Fatal(err)
	}
	var result resolutionResult
	if err := json.Unmarshal(reporter.result, &result); err != nil {
		t.Fatal(err)
	}
	if result.Code != "no_verified_links" || len(result.DeliveryLinks) != 0 {
		t.Fatalf("result = %#v", result)
	}
}
