package httpsearch

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

const (
	MaxQueryRunes              = 120
	MaxSources                 = 32
	DefaultMaxResponseBytes    = int64(2 << 20)
	DefaultMaxResultsPerSource = 200
)

var (
	ErrEmptyQuery             = errors.New("httpsearch: query is empty")
	ErrQueryTooLong           = errors.New("httpsearch: query is too long")
	ErrNoSources              = errors.New("httpsearch: no sources configured")
	ErrTooManySources         = errors.New("httpsearch: too many sources configured")
	ErrInvalidSource          = errors.New("httpsearch: invalid source configuration")
	ErrUnsafeEndpoint         = errors.New("httpsearch: endpoint is not network-safe")
	ErrResponseTooLarge       = errors.New("httpsearch: response exceeds the configured limit")
	ErrUnsupportedContentType = errors.New("httpsearch: response content type is unsupported")
	ErrTooManyResults         = errors.New("httpsearch: source returned too many results")
	ErrInvalidResult          = errors.New("httpsearch: source returned an invalid result")
)

// Source describes one explicitly approved HTTP search endpoint. BuildURL must
// derive a request URL from Endpoint and the normalized query. Parse receives a
// bounded in-memory response body and must not perform any network access.
type Source struct {
	ID                string
	Name              string
	Rank              int
	Endpoint          string
	BuildURL           func(endpoint, query string) (string, error)
	Parse              func(*http.Response) ([]Result, error)
	AllowedResultHosts map[string]struct{}
}

type Result struct {
	SourceID string `json:"sourceId"`
	Source   string `json:"source"`
	Title    string `json:"title"`
	URL      string `json:"url"`
	Rank     int    `json:"rank"`

	sourceOrder int
	resultOrder int
}

type SourceError struct {
	SourceID  string `json:"sourceId"`
	Source    string `json:"source"`
	Code      string `json:"code"`
	Message   string `json:"message"`
	Temporary bool   `json:"temporary"`
	Err       error  `json:"-"`
}

type Response struct {
	Results []Result      `json:"results"`
	Errors  []SourceError `json:"errors"`
}

type Resolver interface {
	LookupIPAddr(ctx context.Context, host string) ([]net.IPAddr, error)
}

type Engine struct {
	Transport           http.RoundTripper
	Resolver            Resolver
	AllowPrivate        bool
	AllowedHosts        map[string]struct{}
	SourceTimeout       time.Duration
	MaxResponseBytes    int64
	MaxResultsPerSource int
	Backoff             *Backoff
}

type Backoff struct {
	mu       sync.Mutex
	failures map[string]backoffFailure
	Base     time.Duration
	Maximum  time.Duration
	Now      func() time.Time
}

type backoffFailure struct {
	count int
	until time.Time
}

func NewBackoff(base, maximum time.Duration) *Backoff {
	if base <= 0 {
		base = 30 * time.Second
	}
	if maximum < base {
		maximum = 10 * time.Minute
	}
	return &Backoff{failures: map[string]backoffFailure{}, Base: base, Maximum: maximum, Now: time.Now}
}

func (backoff *Backoff) Ready(sourceID string) (bool, time.Duration) {
	if backoff == nil {
		return true, 0
	}
	backoff.mu.Lock()
	defer backoff.mu.Unlock()
	failure, found := backoff.failures[sourceID]
	if !found {
		return true, 0
	}
	now := backoff.now()
	if !now.Before(failure.until) {
		delete(backoff.failures, sourceID)
		return true, 0
	}
	return false, failure.until.Sub(now)
}

func (backoff *Backoff) Failure(sourceID string) {
	if backoff == nil {
		return
	}
	backoff.mu.Lock()
	defer backoff.mu.Unlock()
	failure := backoff.failures[sourceID]
	failure.count++
	delay := backoff.Base
	for index := 1; index < failure.count && delay < backoff.Maximum; index++ {
		delay *= 2
		if delay > backoff.Maximum {
			delay = backoff.Maximum
		}
	}
	failure.until = backoff.now().Add(delay)
	backoff.failures[sourceID] = failure
}

func (backoff *Backoff) Success(sourceID string) {
	if backoff == nil {
		return
	}
	backoff.mu.Lock()
	delete(backoff.failures, sourceID)
	backoff.mu.Unlock()
}

func (backoff *Backoff) now() time.Time {
	if backoff.Now != nil {
		return backoff.Now()
	}
	return time.Now()
}

func NormalizeQuery(query string) (string, error) {
	normalized := strings.Join(strings.Fields(query), " ")
	if normalized == "" {
		return "", ErrEmptyQuery
	}
	if utf8.RuneCountInString(normalized) > MaxQueryRunes {
		return "", ErrQueryTooLong
	}
	return normalized, nil
}

func (engine Engine) Search(ctx context.Context, query string, sources []Source) (Response, error) {
	normalized, err := NormalizeQuery(query)
	if err != nil {
		return Response{}, err
	}
	if len(sources) == 0 {
		return Response{}, ErrNoSources
	}
	if len(sources) > MaxSources {
		return Response{}, ErrTooManySources
	}
	timeout := engine.SourceTimeout
	if timeout <= 0 {
		timeout = 8 * time.Second
	}

	type outcome struct {
		results []Result
		err     *SourceError
	}
	outcomes := make(chan outcome, len(sources))
	var wait sync.WaitGroup

	for sourceOrder, source := range sources {
		source := source
		sourceOrder := sourceOrder
		wait.Add(1)
		go func() {
			defer wait.Done()
			identity := sourceIdentity(source, sourceOrder)
			if ready, remaining := engine.Backoff.Ready(identity); !ready {
				outcomes <- outcome{err: &SourceError{
					SourceID:  identity,
					Source:    source.Name,
					Code:      "temporarily_backed_off",
					Message:   fmt.Sprintf("Source is temporarily paused for %s.", remaining.Round(time.Second)),
					Temporary: true,
				}}
				return
			}
			results, searchErr := engine.searchSource(ctx, timeout, normalized, source, sourceOrder)
			if searchErr != nil {
				sourceError := classifySourceError(identity, source.Name, searchErr)
				if sourceError.Temporary {
					engine.Backoff.Failure(identity)
				}
				outcomes <- outcome{err: &sourceError}
				return
			}
			engine.Backoff.Success(identity)
			outcomes <- outcome{results: results}
		}()
	}

	wait.Wait()
	close(outcomes)
	if err := ctx.Err(); err != nil {
		return Response{}, err
	}

	response := Response{Results: []Result{}, Errors: []SourceError{}}
	for item := range outcomes {
		if item.err != nil {
			response.Errors = append(response.Errors, *item.err)
			continue
		}
		response.Results = append(response.Results, item.results...)
	}

	sort.SliceStable(response.Results, func(i, j int) bool {
		if response.Results[i].Rank != response.Results[j].Rank {
			return response.Results[i].Rank < response.Results[j].Rank
		}
		if response.Results[i].sourceOrder != response.Results[j].sourceOrder {
			return response.Results[i].sourceOrder < response.Results[j].sourceOrder
		}
		return response.Results[i].resultOrder < response.Results[j].resultOrder
	})
	sort.SliceStable(response.Errors, func(i, j int) bool {
		return sourcePosition(sources, response.Errors[i].SourceID) < sourcePosition(sources, response.Errors[j].SourceID)
	})
	return response, nil
}

func sourceIdentity(source Source, order int) string {
	if value := strings.TrimSpace(source.ID); value != "" {
		return value
	}
	if value := strings.TrimSpace(source.Name); value != "" {
		return value
	}
	return fmt.Sprintf("source-%d", order+1)
}

func sourcePosition(sources []Source, sourceID string) int {
	for index, source := range sources {
		if sourceIdentity(source, index) == sourceID {
			return index
		}
	}
	return len(sources)
}

func (engine Engine) searchSource(parent context.Context, timeout time.Duration, query string, source Source, sourceOrder int) ([]Result, error) {
	identity := sourceIdentity(source, sourceOrder)
	if strings.TrimSpace(source.Name) == "" || strings.TrimSpace(source.Endpoint) == "" || source.BuildURL == nil || source.Parse == nil {
		return nil, ErrInvalidSource
	}

	endpoint, _, err := engine.validateURL(parent, source.Endpoint, "")
	if err != nil {
		return nil, err
	}
	rawURL, err := source.BuildURL(source.Endpoint, query)
	if err != nil {
		return nil, fmt.Errorf("build request URL: %w", err)
	}
	target, approvedIPs, err := engine.validateURL(parent, rawURL, canonicalHost(endpoint.Hostname()))
	if err != nil {
		return nil, err
	}

	ctx, cancel := context.WithTimeout(parent, timeout)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, target.String(), nil)
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}
	request.Header.Set("Accept", "text/html,application/xhtml+xml,application/json;q=0.9")
	request.Header.Set("User-Agent", "link-evidence-helper/next (development shadow search)")

	client := engine.clientFor(target, approvedIPs, timeout)
	response, err := client.Do(request)
	if err != nil {
		return nil, fmt.Errorf("request source: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode > 299 {
		return nil, httpStatusError{status: response.StatusCode}
	}

	body, err := readBoundedBody(response.Body, engine.maxResponseBytes())
	if err != nil {
		return nil, err
	}
	if !supportedContentType(response.Header.Get("Content-Type"), body) {
		return nil, ErrUnsupportedContentType
	}
	response.Body = io.NopCloser(bytes.NewReader(body))
	response.ContentLength = int64(len(body))

	results, err := source.Parse(response)
	if err != nil {
		return nil, fmt.Errorf("parse response: %w", err)
	}
	if len(results) > engine.maxResultsPerSource() {
		return nil, ErrTooManyResults
	}

	seen := make(map[string]struct{}, len(results))
	validated := make([]Result, 0, len(results))
	for resultOrder, result := range results {
		title := strings.Join(strings.Fields(result.Title), " ")
		if title == "" || utf8.RuneCountInString(title) > 300 {
			return nil, ErrInvalidResult
		}
		resultURL, err := resolveResultURL(target, result.URL, source.AllowedResultHosts)
		if err != nil {
			return nil, err
		}
		key := identity + "\x00" + resultURL
		if _, duplicate := seen[key]; duplicate {
			continue
		}
		seen[key] = struct{}{}
		result.SourceID = identity
		result.Source = source.Name
		result.Title = title
		result.URL = resultURL
		result.Rank = source.Rank
		result.sourceOrder = sourceOrder
		result.resultOrder = resultOrder
		validated = append(validated, result)
	}
	return validated, nil
}

func (engine Engine) ValidateURL(rawURL string) error {
	_, _, err := engine.validateURL(context.Background(), rawURL, "")
	return err
}

func (engine Engine) validateURL(ctx context.Context, rawURL, expectedHost string) (*url.URL, []net.IP, error) {
	parsed, err := url.Parse(rawURL)
	if err != nil || parsed.Scheme == "" || parsed.Hostname() == "" {
		return nil, nil, ErrUnsafeEndpoint
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return nil, nil, ErrUnsafeEndpoint
	}
	if parsed.User != nil {
		return nil, nil, ErrUnsafeEndpoint
	}
	host := canonicalHost(parsed.Hostname())
	if host == "" || (expectedHost != "" && host != expectedHost) {
		return nil, nil, ErrUnsafeEndpoint
	}
	if len(engine.AllowedHosts) > 0 {
		if _, approved := engine.AllowedHosts[host]; !approved {
			return nil, nil, ErrUnsafeEndpoint
		}
	}
	if host == "localhost" || strings.HasSuffix(host, ".localhost") {
		if !engine.AllowPrivate {
			return nil, nil, ErrUnsafeEndpoint
		}
	}
	if literal := net.ParseIP(host); literal != nil {
		if !engine.AllowPrivate && unsafeIP(literal) {
			return nil, nil, ErrUnsafeEndpoint
		}
		return parsed, []net.IP{literal}, nil
	}

	resolver := engine.Resolver
	if resolver == nil {
		resolver = net.DefaultResolver
	}
	addresses, err := resolver.LookupIPAddr(ctx, host)
	if err != nil || len(addresses) == 0 {
		return nil, nil, ErrUnsafeEndpoint
	}
	approved := make([]net.IP, 0, len(addresses))
	for _, address := range addresses {
		if address.IP == nil || (!engine.AllowPrivate && unsafeIP(address.IP)) {
			return nil, nil, ErrUnsafeEndpoint
		}
		approved = append(approved, address.IP)
	}
	return parsed, approved, nil
}

func (engine Engine) clientFor(target *url.URL, approvedIPs []net.IP, timeout time.Duration) *http.Client {
	transport := engine.Transport
	if transport == nil {
		base := http.DefaultTransport.(*http.Transport).Clone()
		base.Proxy = nil
		if len(approvedIPs) > 0 {
			host := canonicalHost(target.Hostname())
			base.DialContext = pinnedDialer(host, approvedIPs)
		}
		transport = base
	}
	return &http.Client{
		Transport: transport,
		Timeout:   timeout + time.Second,
		CheckRedirect: func(request *http.Request, via []*http.Request) error {
			if len(via) >= 3 {
				return ErrUnsafeEndpoint
			}
			if canonicalHost(request.URL.Hostname()) != canonicalHost(target.Hostname()) || request.URL.Scheme != target.Scheme || request.URL.User != nil {
				return ErrUnsafeEndpoint
			}
			return nil
		},
	}
}

func pinnedDialer(expectedHost string, approvedIPs []net.IP) func(context.Context, string, string) (net.Conn, error) {
	dialer := &net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second}
	return func(ctx context.Context, network, address string) (net.Conn, error) {
		host, port, err := net.SplitHostPort(address)
		if err != nil || canonicalHost(host) != expectedHost {
			return nil, ErrUnsafeEndpoint
		}
		var lastErr error
		for _, ip := range approvedIPs {
			connection, dialErr := dialer.DialContext(ctx, network, net.JoinHostPort(ip.String(), port))
			if dialErr == nil {
				return connection, nil
			}
			lastErr = dialErr
		}
		if lastErr == nil {
			lastErr = ErrUnsafeEndpoint
		}
		return nil, lastErr
	}
}

func resolveResultURL(requestURL *url.URL, rawResult string, allowedHosts map[string]struct{}) (string, error) {
	parsed, err := url.Parse(strings.TrimSpace(rawResult))
	if err != nil || strings.TrimSpace(rawResult) == "" {
		return "", ErrInvalidResult
	}
	resolved := requestURL.ResolveReference(parsed)
	if resolved.Scheme != "http" && resolved.Scheme != "https" {
		return "", ErrInvalidResult
	}
	if resolved.User != nil || resolved.Hostname() == "" {
		return "", ErrInvalidResult
	}
	host := canonicalHost(resolved.Hostname())
	requestHost := canonicalHost(requestURL.Hostname())
	if host != requestHost {
		if _, approved := allowedHosts[host]; !approved {
			return "", ErrInvalidResult
		}
	}
	resolved.Fragment = ""
	return resolved.String(), nil
}

func readBoundedBody(reader io.Reader, maximum int64) ([]byte, error) {
	body, err := io.ReadAll(io.LimitReader(reader, maximum+1))
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}
	if int64(len(body)) > maximum {
		return nil, ErrResponseTooLarge
	}
	return body, nil
}

func supportedContentType(header string, body []byte) bool {
	mediaType := strings.ToLower(strings.TrimSpace(strings.Split(header, ";")[0]))
	if mediaType == "" {
		mediaType = strings.ToLower(strings.TrimSpace(strings.Split(http.DetectContentType(body), ";")[0]))
	}
	switch mediaType {
	case "application/json", "text/html", "application/xhtml+xml", "text/plain":
		return true
	default:
		return false
	}
}

func (engine Engine) maxResponseBytes() int64 {
	if engine.MaxResponseBytes > 0 {
		return engine.MaxResponseBytes
	}
	return DefaultMaxResponseBytes
}

func (engine Engine) maxResultsPerSource() int {
	if engine.MaxResultsPerSource > 0 {
		return engine.MaxResultsPerSource
	}
	return DefaultMaxResultsPerSource
}

func canonicalHost(value string) string {
	return strings.ToLower(strings.TrimSuffix(strings.TrimSpace(value), "."))
}

func unsafeIP(ip net.IP) bool {
	return ip.IsLoopback() || ip.IsPrivate() || ip.IsUnspecified() || ip.IsMulticast() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast()
}

type httpStatusError struct {
	status int
}

func (failure httpStatusError) Error() string {
	return fmt.Sprintf("unexpected HTTP status %d", failure.status)
}

func classifySourceError(sourceID, sourceName string, err error) SourceError {
	failure := SourceError{SourceID: sourceID, Source: sourceName, Code: "source_failed", Message: "Source search failed.", Err: err}
	var statusFailure httpStatusError
	switch {
	case errors.Is(err, context.DeadlineExceeded):
		failure.Code, failure.Message, failure.Temporary = "source_timeout", "Source search timed out.", true
	case errors.As(err, &statusFailure):
		failure.Code = "source_http_error"
		failure.Message = "Source returned an unsuccessful HTTP response."
		failure.Temporary = statusFailure.status == http.StatusTooManyRequests || statusFailure.status >= 500
	case errors.Is(err, ErrUnsafeEndpoint):
		failure.Code, failure.Message = "unsafe_endpoint", "Source endpoint was rejected by network safety policy."
	case errors.Is(err, ErrResponseTooLarge):
		failure.Code, failure.Message = "response_too_large", "Source response exceeded the safe size limit."
	case errors.Is(err, ErrUnsupportedContentType):
		failure.Code, failure.Message = "unsupported_content_type", "Source returned an unsupported response type."
	case errors.Is(err, ErrTooManyResults):
		failure.Code, failure.Message = "too_many_results", "Source returned more results than allowed."
	case errors.Is(err, ErrInvalidResult):
		failure.Code, failure.Message = "invalid_result", "Source returned an invalid result."
	case errors.Is(err, ErrInvalidSource):
		failure.Code, failure.Message = "invalid_source", "Source configuration is invalid."
	default:
		var networkError net.Error
		if errors.As(err, &networkError) {
			failure.Temporary = true
		}
	}
	return failure
}
