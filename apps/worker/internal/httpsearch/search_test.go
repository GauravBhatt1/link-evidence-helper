package httpsearch

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"
)

func TestValidateURLRejectsUnsafeTargets(t *testing.T) {
	engine := Engine{}
	for _, rawURL := range []string{
		"file:///etc/passwd",
		"http://localhost/test",
		"http://127.0.0.1/test",
		"http://10.0.0.1/test",
		"http://169.254.169.254/latest/meta-data",
		"http://[::1]/test",
		"https://user:pass@example.com/test",
	} {
		if err := engine.ValidateURL(rawURL); !errors.Is(err, ErrUnsafeEndpoint) {
			t.Fatalf("ValidateURL(%q) error = %v, want ErrUnsafeEndpoint", rawURL, err)
		}
	}
}

func TestValidateURLUsesExplicitHostAllowlist(t *testing.T) {
	engine := Engine{AllowedHosts: map[string]struct{}{"approved.example": {}}}
	if err := engine.ValidateURL("https://approved.example/search?q=test"); err != nil {
		t.Fatalf("approved host rejected: %v", err)
	}
	if err := engine.ValidateURL("https://other.example/search?q=test"); !errors.Is(err, ErrUnsafeEndpoint) {
		t.Fatalf("unapproved host error = %v, want ErrUnsafeEndpoint", err)
	}
}

func TestSearchRanksResultsAndIsolatesSourceFailures(t *testing.T) {
	good := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("q") != "Hockey" {
			t.Fatalf("query = %q", r.URL.Query().Get("q"))
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode([]Result{{Title: "Hockey (2025)", URL: "https://delivery.invalid/hockey"}})
	}))
	defer good.Close()

	failing := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "temporary failure", http.StatusBadGateway)
	}))
	defer failing.Close()

	buildURL := func(endpoint, query string) (string, error) {
		u, err := url.Parse(endpoint)
		if err != nil {
			return "", err
		}
		values := u.Query()
		values.Set("q", query)
		u.RawQuery = values.Encode()
		return u.String(), nil
	}
	parseJSON := func(resp *http.Response) ([]Result, error) {
		var results []Result
		err := json.NewDecoder(resp.Body).Decode(&results)
		return results, err
	}

	engine := Engine{AllowPrivate: true, SourceTimeout: time.Second}
	response, err := engine.Search(context.Background(), " Hockey ", []Source{
		{Name: "secondary", Rank: 20, Endpoint: failing.URL, BuildURL: buildURL, Parse: parseJSON},
		{Name: "primary", Rank: 10, Endpoint: good.URL, BuildURL: buildURL, Parse: parseJSON},
	})
	if err != nil {
		t.Fatalf("Search error: %v", err)
	}
	if len(response.Results) != 1 {
		t.Fatalf("results = %#v, want one", response.Results)
	}
	if got := response.Results[0]; got.Source != "primary" || got.Rank != 10 || got.Title != "Hockey (2025)" {
		t.Fatalf("result = %#v", got)
	}
	if len(response.Errors) != 1 || response.Errors[0].Source != "secondary" {
		t.Fatalf("errors = %#v", response.Errors)
	}
}

func TestSearchHonorsPerSourceTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(100 * time.Millisecond)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	engine := Engine{AllowPrivate: true, SourceTimeout: 10 * time.Millisecond}
	response, err := engine.Search(context.Background(), "query", []Source{{
		Name:     "slow",
		Endpoint: server.URL,
		BuildURL: func(endpoint, query string) (string, error) { return endpoint, nil },
		Parse:    func(resp *http.Response) ([]Result, error) { return nil, nil },
	}})
	if err != nil {
		t.Fatalf("Search error: %v", err)
	}
	if len(response.Errors) != 1 {
		t.Fatalf("errors = %#v, want timeout error", response.Errors)
	}
}

func TestSearchRequiresSources(t *testing.T) {
	_, err := (Engine{}).Search(context.Background(), "query", nil)
	if !errors.Is(err, ErrNoSources) {
		t.Fatalf("error = %v, want ErrNoSources", err)
	}
}
