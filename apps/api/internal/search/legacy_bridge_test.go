package search

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestLegacyBridgeMapsSearchResponseToContract(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/search" {
			t.Fatalf("path = %q", request.URL.Path)
		}
		if request.URL.Query().Get("q") != "Ikka" {
			t.Fatalf("query = %q", request.URL.RawQuery)
		}
		if request.Header.Get("x-app-token") != "runtime-token" {
			t.Fatalf("token header = %q", request.Header.Get("x-app-token"))
		}
		writer.Header().Set("Content-Type", "application/json; charset=utf-8")
		_, _ = writer.Write([]byte(`{
			"ok": true,
			"contents": [{
				"contentId": "content_ikka",
				"tmdbId": "",
				"title": "Ikka",
				"year": "2025",
				"mediaType": "movie",
				"poster": "/index/api/tmdb-image?path=/poster.jpg",
				"languages": null,
				"totalSources": 1,
				"releaseVariants": [{
					"variantId": "variant_ikka_1080p",
					"quality": "1080p",
					"sources": [{
						"sourceId": "source_hdmovie2r_ltd",
						"adapterName": "HDMovie2",
						"workflowMetadata": {
							"internal": true,
							"candidate": {"library_status": "available"}
						}
					}]
				}]
			}],
			"adapterFailures": [{"name": "mkvcinemas", "error": "timeout"}],
			"debugSecret": "must-not-leak"
		}`))
	}))
	defer server.Close()

	bridge, err := NewLegacyBridge(LegacyBridgeConfig{BaseURL: server.URL, AccessToken: "runtime-token", Timeout: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	response, err := bridge.Search(context.Background(), "  Ikka  ")
	if err != nil {
		t.Fatal(err)
	}
	if !response.OK || !response.Success || response.Code != "ok" || response.Query != "Ikka" {
		t.Fatalf("response status = %#v", response)
	}
	if len(response.Contents) != 1 {
		t.Fatalf("contents = %d", len(response.Contents))
	}
	content := response.Contents[0]
	if content.TMDBID != nil {
		t.Fatalf("empty tmdbId should map to nil: %#v", *content.TMDBID)
	}
	if content.JellyfinStatus != "available" {
		t.Fatalf("jellyfinStatus = %q", content.JellyfinStatus)
	}
	if content.Languages == nil {
		t.Fatal("languages must be non-nil")
	}
	variant := content.ReleaseVariants[0]
	if variant.SourceCount != 1 || variant.Language != "Unknown" || variant.ReleaseType != "Unknown" || variant.PackType != "single" {
		t.Fatalf("variant defaults = %#v", variant)
	}
	if got := variant.Sources[0].DisplayName; got != "HDMovie2" {
		t.Fatalf("displayName = %q", got)
	}
	if got := variant.Sources[0].VerificationState; got != "unverified" {
		t.Fatalf("verificationState = %q", got)
	}
	if len(response.PartialFailures) != 1 || response.PartialFailures[0].SourceID != "mkvcinemas" || response.PartialFailures[0].Reason != "timeout" {
		t.Fatalf("partial failures = %#v", response.PartialFailures)
	}
	body, err := json.Marshal(response)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(body), "debugSecret") || strings.Contains(string(body), "workflowMetadata") {
		t.Fatalf("legacy internals leaked: %s", body)
	}
}

func TestLegacyBridgeRejectsNonLoopbackBaseURLByDefault(t *testing.T) {
	if _, err := NewLegacyBridge(LegacyBridgeConfig{BaseURL: "https://example.com"}); !errors.Is(err, ErrLegacyBridgeInvalid) {
		t.Fatalf("error = %v", err)
	}
	if _, err := NewLegacyBridge(LegacyBridgeConfig{BaseURL: "https://example.com", AllowNonLoopback: true}); err != nil {
		t.Fatalf("explicit opt-in should allow non-loopback: %v", err)
	}
}

func TestLegacyBridgeMapsUnsafeUpstreamFailures(t *testing.T) {
	tests := []struct {
		name        string
		status      int
		contentType string
		body        string
		want        error
	}{
		{name: "non-200", status: http.StatusBadGateway, contentType: "application/json", body: `{"ok": false}`, want: ErrLegacyBridgeUnavailable},
		{name: "non-json", status: http.StatusOK, contentType: "text/html", body: `<html></html>`, want: ErrLegacyBridgeInvalid},
		{name: "invalid-json", status: http.StatusOK, contentType: "application/json", body: `{`, want: ErrLegacyBridgeInvalid},
		{name: "not-ok", status: http.StatusOK, contentType: "application/json", body: `{"ok": false, "error": "secret"}`, want: ErrLegacyBridgeUnavailable},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
				writer.Header().Set("Content-Type", test.contentType)
				writer.WriteHeader(test.status)
				_, _ = writer.Write([]byte(test.body))
			}))
			defer server.Close()
			bridge, err := NewLegacyBridge(LegacyBridgeConfig{BaseURL: server.URL})
			if err != nil {
				t.Fatal(err)
			}
			_, err = bridge.Search(context.Background(), "Ikka")
			if !errors.Is(err, test.want) {
				t.Fatalf("error = %v, want %v", err, test.want)
			}
		})
	}
}
