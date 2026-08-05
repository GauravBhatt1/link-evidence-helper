package library

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestJellyfinClientPaginatesAndMapsSafeLibraryItems(t *testing.T) {
	fixedNow := time.Date(2026, 8, 5, 15, 0, 0, 0, time.UTC)
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		requests.Add(1)
		if request.URL.Path != "/jellyfin/Items" {
			t.Errorf("path = %q", request.URL.Path)
		}
		if request.Header.Get("X-Emby-Token") != "test-secret" {
			t.Errorf("missing API token header")
		}
		if request.URL.Query().Get("api_key") != "" || strings.Contains(request.RawQuery, "test-secret") {
			t.Errorf("credential leaked in query: %q", request.RawQuery)
		}
		if request.URL.Query().Get("ParentId") != "library-a" {
			t.Errorf("ParentId = %q", request.URL.Query().Get("ParentId"))
		}
		if request.URL.Query().Get("IncludeItemTypes") != "Movie,Series,Season,Episode" {
			t.Errorf("IncludeItemTypes = %q", request.URL.Query().Get("IncludeItemTypes"))
		}
		writer.Header().Set("Content-Type", "application/json; charset=utf-8")
		start, _ := strconv.Atoi(request.URL.Query().Get("StartIndex"))
		items := []map[string]any{
			{
				"Id": "movie-1", "ServerId": "server-1", "Name": "Horizon Gate", "Type": "Movie",
				"ProductionYear": 2026, "DateCreated": "2026-08-05T09:30:00Z",
				"ProviderIds": map[string]string{"Tmdb": "100001"},
			},
			{
				"Id": "series-1", "ServerId": "server-1", "Name": "Signal House", "Type": "Series",
				"ProductionYear": 2025, "DateCreated": "2026-08-04T09:30:00Z",
				"ProviderIds": map[string]string{"Tmdb": "200100"},
			},
			{
				"Id": "episode-1", "ServerId": "server-1", "Name": "Signal House Episode 2", "Type": "Episode",
				"ProductionYear": 2025, "ParentIndexNumber": 1, "IndexNumber": 2,
				"DateCreated": "2026-08-03T09:30:00Z", "ProviderIds": map[string]string{"Tmdb": "200102"},
			},
		}
		end := start + 2
		if end > len(items) {
			end = len(items)
		}
		if start > len(items) {
			start = len(items)
		}
		_ = json.NewEncoder(writer).Encode(map[string]any{
			"Items": items[start:end], "TotalRecordCount": len(items), "StartIndex": start,
		})
	}))
	defer server.Close()

	client, err := NewJellyfinClient(JellyfinConfig{
		BaseURL:      server.URL + "/jellyfin",
		APIKey:       "test-secret",
		LibraryIDs:   []string{"library-a", "library-a"},
		AllowPrivate: true,
		PageSize:     2,
		MaxItems:     10,
		Timeout:      2 * time.Second,
		Now:          func() time.Time { return fixedNow },
	})
	if err != nil {
		t.Fatalf("NewJellyfinClient() error = %v", err)
	}
	items, status, err := client.Snapshot(context.Background())
	if err != nil {
		t.Fatalf("Snapshot() error = %v", err)
	}
	if requests.Load() != 2 {
		t.Fatalf("requests = %d, want 2", requests.Load())
	}
	if len(items) != 3 || items[0].Title != "Horizon Gate" || items[2].MediaType != MediaEpisode {
		t.Fatalf("items = %#v", items)
	}
	if items[0].TMDBID == nil || *items[0].TMDBID != "100001" || items[0].Poster != nil {
		t.Fatalf("movie mapping = %#v", items[0])
	}
	if items[2].Season == nil || *items[2].Season != 1 || items[2].Episode == nil || *items[2].Episode != 2 {
		t.Fatalf("episode mapping = %#v", items[2])
	}
	if !status.Configured || status.Mode != JellyfinConnected || status.LastSyncedAt == nil || !status.LastSyncedAt.Equal(fixedNow) {
		t.Fatalf("status = %#v", status)
	}
}

func TestJellyfinClientRejectsUnsafeTargetsAndRedirects(t *testing.T) {
	privateServer := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	defer privateServer.Close()
	client, err := NewJellyfinClient(JellyfinConfig{BaseURL: privateServer.URL, APIKey: "secret"})
	if err != nil {
		t.Fatalf("NewJellyfinClient() error = %v", err)
	}
	if _, _, err := client.Snapshot(context.Background()); !errors.Is(err, ErrJellyfinUnsafeTarget) {
		t.Fatalf("Snapshot() error = %v, want unsafe target", err)
	}

	var redirectedHits atomic.Int32
	redirectTarget := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		redirectedHits.Add(1)
	}))
	defer redirectTarget.Close()
	redirectSource := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		http.Redirect(writer, request, redirectTarget.URL+"/Items", http.StatusTemporaryRedirect)
	}))
	defer redirectSource.Close()
	redirectClient, err := NewJellyfinClient(JellyfinConfig{
		BaseURL: redirectSource.URL, APIKey: "secret", AllowPrivate: true,
	})
	if err != nil {
		t.Fatalf("NewJellyfinClient() error = %v", err)
	}
	if _, _, err := redirectClient.Snapshot(context.Background()); !errors.Is(err, ErrJellyfinUnavailable) {
		t.Fatalf("redirect Snapshot() error = %v", err)
	}
	if redirectedHits.Load() != 0 {
		t.Fatalf("redirect target received %d requests", redirectedHits.Load())
	}
}

func TestJellyfinClientUsesSafeErrorsAndBoundsResponses(t *testing.T) {
	tests := []struct {
		name      string
		handler   http.HandlerFunc
		wantError error
		limit     int64
	}{
		{
			name: "unauthorized",
			handler: func(writer http.ResponseWriter, _ *http.Request) { writer.WriteHeader(http.StatusUnauthorized) },
			wantError: ErrJellyfinUnauthorized,
		},
		{
			name: "non-json",
			handler: func(writer http.ResponseWriter, _ *http.Request) {
				writer.Header().Set("Content-Type", "text/html")
				_, _ = writer.Write([]byte("not json"))
			},
			wantError: ErrJellyfinInvalidResponse,
		},
		{
			name: "oversized",
			handler: func(writer http.ResponseWriter, _ *http.Request) {
				writer.Header().Set("Content-Type", "application/json")
				_, _ = writer.Write([]byte(`{"Items":[]}` + strings.Repeat(" ", 2048)))
			},
			wantError: ErrJellyfinInvalidResponse,
			limit: 1024,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := httptest.NewServer(test.handler)
			defer server.Close()
			client, err := NewJellyfinClient(JellyfinConfig{
				BaseURL: server.URL, APIKey: "secret", AllowPrivate: true, MaxResponseBytes: test.limit,
			})
			if err != nil {
				t.Fatalf("NewJellyfinClient() error = %v", err)
			}
			if _, _, err := client.Snapshot(context.Background()); !errors.Is(err, test.wantError) {
				t.Fatalf("Snapshot() error = %v, want %v", err, test.wantError)
			}
		})
	}
}

func TestJellyfinClientConfigurationAndCancellation(t *testing.T) {
	for _, rawURL := range []string{"", "ftp://example.com", "https://user:pass@example.com", "https://example.com?token=x"} {
		if _, err := NewJellyfinClient(JellyfinConfig{BaseURL: rawURL, APIKey: "secret"}); !errors.Is(err, ErrJellyfinInvalidConfig) {
			t.Fatalf("BaseURL %q error = %v", rawURL, err)
		}
	}
	if _, err := NewJellyfinClient(JellyfinConfig{BaseURL: "https://example.com", APIKey: ""}); !errors.Is(err, ErrJellyfinInvalidConfig) {
		t.Fatalf("missing API key error = %v", err)
	}

	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		<-request.Context().Done()
		writer.WriteHeader(http.StatusGatewayTimeout)
	}))
	defer server.Close()
	client, err := NewJellyfinClient(JellyfinConfig{BaseURL: server.URL, APIKey: "secret", AllowPrivate: true})
	if err != nil {
		t.Fatalf("NewJellyfinClient() error = %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, _, err := client.Snapshot(ctx); !errors.Is(err, context.Canceled) {
		t.Fatalf("Snapshot() cancellation error = %v", err)
	}
}

func TestJellyfinConfigKeepsTokenOutOfURL(t *testing.T) {
	client, err := NewJellyfinClient(JellyfinConfig{BaseURL: "https://media.example/jellyfin", APIKey: "secret"})
	if err != nil {
		t.Fatalf("NewJellyfinClient() error = %v", err)
	}
	parsed, _ := url.Parse(client.baseURL.String())
	if parsed.Query().Get("api_key") != "" || strings.Contains(client.baseURL.String(), "secret") {
		t.Fatalf("token leaked into base URL")
	}
}
