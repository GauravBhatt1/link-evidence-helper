package httpsearch

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"sync"
	"time"
)

var (
	ErrNoSources      = errors.New("httpsearch: no sources configured")
	ErrUnsafeEndpoint = errors.New("httpsearch: endpoint is not network-safe")
)

// Source describes one explicitly approved HTTP search endpoint. BuildURL must
// return a URL derived from the source's configured endpoint and the query.
type Source struct {
	Name     string
	Rank     int
	Endpoint string
	BuildURL func(endpoint, query string) (string, error)
	Parse    func(*http.Response) ([]Result, error)
}

type Result struct {
	Source string
	Title  string
	URL    string
	Rank   int
}

type SourceError struct {
	Source string
	Err    error
}

type Response struct {
	Results []Result
	Errors  []SourceError
}

type Engine struct {
	Client        *http.Client
	AllowPrivate  bool
	AllowedHosts  map[string]struct{}
	SourceTimeout time.Duration
}

func (e Engine) Search(ctx context.Context, query string, sources []Source) (Response, error) {
	query = strings.TrimSpace(query)
	if len(sources) == 0 {
		return Response{}, ErrNoSources
	}
	client := e.Client
	if client == nil {
		client = &http.Client{Timeout: 12 * time.Second}
	}
	timeout := e.SourceTimeout
	if timeout <= 0 {
		timeout = 8 * time.Second
	}

	type outcome struct {
		results []Result
		err     SourceError
	}
	outcomes := make(chan outcome, len(sources))
	var wg sync.WaitGroup

	for _, source := range sources {
		source := source
		wg.Add(1)
		go func() {
			defer wg.Done()
			results, err := e.searchSource(ctx, client, timeout, query, source)
			if err != nil {
				outcomes <- outcome{err: SourceError{Source: source.Name, Err: err}}
				return
			}
			outcomes <- outcome{results: results}
		}()
	}
	wg.Wait()
	close(outcomes)

	response := Response{}
	for outcome := range outcomes {
		if outcome.err.Err != nil {
			response.Errors = append(response.Errors, outcome.err)
			continue
		}
		response.Results = append(response.Results, outcome.results...)
	}

	sort.SliceStable(response.Results, func(i, j int) bool {
		if response.Results[i].Rank != response.Results[j].Rank {
			return response.Results[i].Rank < response.Results[j].Rank
		}
		if response.Results[i].Source != response.Results[j].Source {
			return response.Results[i].Source < response.Results[j].Source
		}
		return response.Results[i].Title < response.Results[j].Title
	})
	sort.SliceStable(response.Errors, func(i, j int) bool { return response.Errors[i].Source < response.Errors[j].Source })
	return response, nil
}

func (e Engine) searchSource(parent context.Context, client *http.Client, timeout time.Duration, query string, source Source) ([]Result, error) {
	if strings.TrimSpace(source.Name) == "" || source.BuildURL == nil || source.Parse == nil {
		return nil, errors.New("invalid source configuration")
	}
	rawURL, err := source.BuildURL(source.Endpoint, query)
	if err != nil {
		return nil, fmt.Errorf("build URL: %w", err)
	}
	if err := e.ValidateURL(rawURL); err != nil {
		return nil, err
	}

	ctx, cancel := context.WithTimeout(parent, timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Accept", "text/html,application/json;q=0.9")
	req.Header.Set("User-Agent", "link-evidence-helper/next (development shadow search)")

	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("request: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return nil, fmt.Errorf("unexpected HTTP status %d", resp.StatusCode)
	}
	results, err := source.Parse(resp)
	if err != nil {
		return nil, fmt.Errorf("parse response: %w", err)
	}
	for i := range results {
		results[i].Source = source.Name
		results[i].Rank = source.Rank
	}
	return results, nil
}

func (e Engine) ValidateURL(rawURL string) error {
	u, err := url.Parse(rawURL)
	if err != nil || u.Scheme == "" || u.Hostname() == "" {
		return ErrUnsafeEndpoint
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return ErrUnsafeEndpoint
	}
	if u.User != nil {
		return ErrUnsafeEndpoint
	}
	host := strings.ToLower(strings.TrimSuffix(u.Hostname(), "."))
	if len(e.AllowedHosts) > 0 {
		if _, ok := e.AllowedHosts[host]; !ok {
			return ErrUnsafeEndpoint
		}
	}
	if e.AllowPrivate {
		return nil
	}
	if host == "localhost" || strings.HasSuffix(host, ".localhost") {
		return ErrUnsafeEndpoint
	}
	if ip := net.ParseIP(host); ip != nil && isPrivateIP(ip) {
		return ErrUnsafeEndpoint
	}
	return nil
}

func isPrivateIP(ip net.IP) bool {
	return ip.IsLoopback() || ip.IsPrivate() || ip.IsUnspecified() || ip.IsMulticast() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast()
}
