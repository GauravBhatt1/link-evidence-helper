package httpsearch

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

type staticResolver map[string][]net.IPAddr

func (resolver staticResolver) LookupIPAddr(_ context.Context, host string) ([]net.IPAddr, error) {
	addresses, found := resolver[host]
	if !found {
		return nil, errors.New("host not found")
	}
	return addresses, nil
}

func publicResolver(host string) staticResolver {
	return staticResolver{host: {{IP: net.ParseIP("203.0.113.10")}}}
}

func queryBuilder(endpoint, query string) (string, error) {
	parsed, err := url.Parse(endpoint)
	if err != nil {
		return "", err
	}
	values := parsed.Query()
	values.Set("q", query)
	parsed.RawQuery = values.Encode()
	return parsed.String(), nil
}

func parseJSON(response *http.Response) ([]Result, error) {
	var results []Result
	err := json.NewDecoder(response.Body).Decode(&results)
	return results, err
}

func TestNormalizeQueryUsesOnlyWhitespaceNormalization(t *testing.T) {
	query, err := NormalizeQuery("  Hockey   2025  ")
	if err != nil || query != "Hockey 2025" {
		t.Fatalf("NormalizeQuery = %q, %v", query, err)
	}
	if _, err := NormalizeQuery("   "); !errors.Is(err, ErrEmptyQuery) {
		t.Fatalf("empty query error = %v", err)
	}
	if _, err := NormalizeQuery(strings.Repeat("x", MaxQueryRunes+1)); !errors.Is(err, ErrQueryTooLong) {
		t.Fatalf("long query error = %v", err)
	}
}

func TestValidateURLRejectsUnsafeTargetsAndResolvedPrivateAddresses(t *testing.T) {
	engine := Engine{Resolver: staticResolver{
		"private.example": {{IP: net.ParseIP("10.0.0.8")}},
	}}
	for _, rawURL := range []string{
		"file:///etc/passwd",
		"http://localhost/test",
		"http://127.0.0.1/test",
		"http://10.0.0.1/test",
		"http://169.254.169.254/latest/meta-data",
		"http://[::1]/test",
		"https://user:pass@example.com/test",
		"https://private.example/test",
	} {
		if err := engine.ValidateURL(rawURL); !errors.Is(err, ErrUnsafeEndpoint) {
			t.Fatalf("ValidateURL(%q) error = %v, want ErrUnsafeEndpoint", rawURL, err)
		}
	}
}

func TestValidateURLUsesExplicitHostAllowlist(t *testing.T) {
	engine := Engine{
		AllowedHosts: map[string]struct{}{"approved.example": {}},
		Resolver:     publicResolver("approved.example"),
	}
	if err := engine.ValidateURL("https://approved.example/search?q=test"); err != nil {
		t.Fatalf("approved host rejected: %v", err)
	}
	if err := engine.ValidateURL("https://other.example/search?q=test"); !errors.Is(err, ErrUnsafeEndpoint) {
		t.Fatalf("unapproved host error = %v, want ErrUnsafeEndpoint", err)
	}
}

func TestSearchRanksByPriorityAndPreservesConfiguredSourceOrder(t *testing.T) {
	first := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("q") != "Hockey" {
			t.Fatalf("query = %q", r.URL.Query().Get("q"))
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode([]Result{
			{Title: "First result", URL: "/first#fragment"},
			{Title: "Second result", URL: "/second"},
		})
	}))
	defer first.Close()
	second := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode([]Result{{Title: "Third result", URL: "/third"}})
	}))
	defer second.Close()

	engine := Engine{AllowPrivate: true, SourceTimeout: time.Second}
	response, err := engine.Search(context.Background(), " Hockey ", []Source{
		{ID: "first", Name: "First", Rank: 10, Endpoint: first.URL, BuildURL: queryBuilder, Parse: parseJSON},
		{ID: "second", Name: "Second", Rank: 10, Endpoint: second.URL, BuildURL: queryBuilder, Parse: parseJSON},
	})
	if err != nil {
		t.Fatalf("Search error: %v", err)
	}
	if len(response.Results) != 3 {
		t.Fatalf("results = %#v", response.Results)
	}
	for index, expected := range []string{"First result", "Second result", "Third result"} {
		if response.Results[index].Title != expected {
			t.Fatalf("result order = %#v", response.Results)
		}
	}
	if strings.Contains(response.Results[0].URL, "#") || response.Results[0].SourceID != "first" {
		t.Fatalf("sanitized result = %#v", response.Results[0])
	}
}

func TestSearchIsolatesFailuresAndClassifiesTemporaryStatus(t *testing.T) {
	good := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode([]Result{{Title: "Hockey (2025)", URL: "/hockey"}})
	}))
	defer good.Close()
	failing := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "temporary failure", http.StatusBadGateway)
	}))
	defer failing.Close()

	engine := Engine{AllowPrivate: true, SourceTimeout: time.Second}
	response, err := engine.Search(context.Background(), "Hockey", []Source{
		{ID: "secondary", Name: "Secondary", Rank: 20, Endpoint: failing.URL, BuildURL: queryBuilder, Parse: parseJSON},
		{ID: "primary", Name: "Primary", Rank: 10, Endpoint: good.URL, BuildURL: queryBuilder, Parse: parseJSON},
	})
	if err != nil {
		t.Fatalf("Search error: %v", err)
	}
	if len(response.Results) != 1 || response.Results[0].SourceID != "primary" {
		t.Fatalf("results = %#v", response.Results)
	}
	if len(response.Errors) != 1 || response.Errors[0].SourceID != "secondary" || response.Errors[0].Code != "source_http_error" || !response.Errors[0].Temporary {
		t.Fatalf("errors = %#v", response.Errors)
	}
	serialized, err := json.Marshal(response.Errors[0])
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(serialized), "temporary failure") {
		t.Fatalf("wire error leaked upstream body: %s", serialized)
	}
}

func TestSearchHonorsPerSourceTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		time.Sleep(100 * time.Millisecond)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte("[]"))
	}))
	defer server.Close()

	engine := Engine{AllowPrivate: true, SourceTimeout: 10 * time.Millisecond}
	response, err := engine.Search(context.Background(), "query", []Source{{
		ID: "slow", Name: "Slow", Endpoint: server.URL, BuildURL: queryBuilder, Parse: parseJSON,
	}})
	if err != nil {
		t.Fatalf("Search error: %v", err)
	}
	if len(response.Errors) != 1 || response.Errors[0].Code != "source_timeout" || !response.Errors[0].Temporary {
		t.Fatalf("errors = %#v", response.Errors)
	}
}

func TestSearchRejectsCrossHostRequestAndResultURLs(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode([]Result{{Title: "Unsafe", URL: "https://other.example/result"}})
	}))
	defer server.Close()

	engine := Engine{AllowPrivate: true, SourceTimeout: time.Second}
	response, err := engine.Search(context.Background(), "query", []Source{{
		ID: "unsafe-result", Name: "Unsafe result", Endpoint: server.URL, BuildURL: queryBuilder, Parse: parseJSON,
	}})
	if err != nil {
		t.Fatal(err)
	}
	if len(response.Errors) != 1 || response.Errors[0].Code != "invalid_result" {
		t.Fatalf("cross-host result errors = %#v", response.Errors)
	}

	response, err = engine.Search(context.Background(), "query", []Source{{
		ID: "unsafe-request", Name: "Unsafe request", Endpoint: server.URL,
		BuildURL: func(_, _ string) (string, error) { return "https://other.example/search", nil },
		Parse:    parseJSON,
	}})
	if err != nil {
		t.Fatal(err)
	}
	if len(response.Errors) != 1 || response.Errors[0].Code != "unsafe_endpoint" {
		t.Fatalf("cross-host request errors = %#v", response.Errors)
	}
}

func TestSearchRejectsOversizedAndUnsupportedResponses(t *testing.T) {
	oversized := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		_, _ = w.Write([]byte(strings.Repeat("x", 64)))
	}))
	defer oversized.Close()
	binary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/octet-stream")
		_, _ = w.Write([]byte{0, 1, 2, 3})
	}))
	defer binary.Close()

	engine := Engine{AllowPrivate: true, SourceTimeout: time.Second, MaxResponseBytes: 16}
	response, err := engine.Search(context.Background(), "query", []Source{
		{ID: "large", Name: "Large", Endpoint: oversized.URL, BuildURL: queryBuilder, Parse: parseJSON},
		{ID: "binary", Name: "Binary", Endpoint: binary.URL, BuildURL: queryBuilder, Parse: parseJSON},
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(response.Errors) != 2 || response.Errors[0].Code != "response_too_large" || response.Errors[1].Code != "unsupported_content_type" {
		t.Fatalf("errors = %#v", response.Errors)
	}
}

func TestTemporaryFailuresActivateAndExpireBackoff(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requests.Add(1)
		http.Error(w, "retry later", http.StatusServiceUnavailable)
	}))
	defer server.Close()

	now := time.Date(2026, 8, 5, 0, 0, 0, 0, time.UTC)
	backoff := NewBackoff(time.Minute, 4*time.Minute)
	backoff.Now = func() time.Time { return now }
	engine := Engine{AllowPrivate: true, SourceTimeout: time.Second, Backoff: backoff}
	sources := []Source{{ID: "backoff", Name: "Backoff", Endpoint: server.URL, BuildURL: queryBuilder, Parse: parseJSON}}

	first, err := engine.Search(context.Background(), "query", sources)
	if err != nil || len(first.Errors) != 1 || first.Errors[0].Code != "source_http_error" {
		t.Fatalf("first = %#v err=%v", first, err)
	}
	second, err := engine.Search(context.Background(), "query", sources)
	if err != nil || len(second.Errors) != 1 || second.Errors[0].Code != "temporarily_backed_off" {
		t.Fatalf("second = %#v err=%v", second, err)
	}
	if requests.Load() != 1 {
		t.Fatalf("requests during backoff = %d", requests.Load())
	}
	now = now.Add(time.Minute)
	_, _ = engine.Search(context.Background(), "query", sources)
	if requests.Load() != 2 {
		t.Fatalf("requests after backoff = %d", requests.Load())
	}
}

func TestSearchRequiresBoundedSourceCount(t *testing.T) {
	if _, err := (Engine{}).Search(context.Background(), "query", nil); !errors.Is(err, ErrNoSources) {
		t.Fatalf("no-source error = %v", err)
	}
	sources := make([]Source, MaxSources+1)
	if _, err := (Engine{}).Search(context.Background(), "query", sources); !errors.Is(err, ErrTooManySources) {
		t.Fatalf("too-many-sources error = %v", err)
	}
}
