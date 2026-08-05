// Package linkverify verifies a configured delivery candidate without
// downloading the file body. It performs a one-byte ranged GET through a
// DNS-pinned, proxy-free client and returns only validated delivery metadata.
package linkverify

import (
	"context"
	"errors"
	"fmt"
	"io"
	"mime"
	"net"
	"net/http"
	"net/url"
	"path"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

const (
	DefaultTimeout      = 12 * time.Second
	DefaultMaxRedirects = 4
)

var (
	ErrInvalidCandidate = errors.New("linkverify: invalid candidate")
	ErrUnsafeURL        = errors.New("linkverify: unsafe URL")
	ErrUnavailable      = errors.New("linkverify: delivery URL unavailable")
	ErrNotDelivery      = errors.New("linkverify: response is not a delivery file")
)

type Resolver interface {
	LookupIPAddr(context.Context, string) ([]net.IPAddr, error)
}

type Candidate struct {
	SourceID       string
	URL            string
	Filename       string
	Size           string
	Quality        string
	AllowedOrigins []string
}

type DeliveryLink struct {
	URL        string    `json:"url"`
	Filename   string    `json:"filename"`
	Size       string    `json:"size"`
	Quality    string    `json:"quality"`
	SourceID   string    `json:"sourceId"`
	VerifiedAt time.Time `json:"verifiedAt"`
}

type Error struct {
	Code      string
	Blocked   bool
	Temporary bool
	Cause     error
}

func (failure *Error) Error() string {
	return failure.Code
}

func (failure *Error) Unwrap() error {
	return failure.Cause
}

type Verifier struct {
	Resolver     Resolver
	AllowPrivate bool
	Timeout      time.Duration
	MaxRedirects int
	Now          func() time.Time
}

func (verifier Verifier) Verify(ctx context.Context, candidate Candidate) (DeliveryLink, error) {
	if err := validateCandidate(candidate); err != nil {
		return DeliveryLink{}, &Error{Code: "invalid_candidate", Blocked: true, Cause: err}
	}
	initial, err := parseHTTPURL(candidate.URL)
	if err != nil {
		return DeliveryLink{}, &Error{Code: "unsafe_url", Blocked: true, Cause: err}
	}
	allowedOrigins, err := compileOrigins(initial, candidate.AllowedOrigins)
	if err != nil {
		return DeliveryLink{}, &Error{Code: "unsafe_url", Blocked: true, Cause: err}
	}

	timeout := verifier.Timeout
	if timeout <= 0 {
		timeout = DefaultTimeout
	}
	maxRedirects := verifier.MaxRedirects
	if maxRedirects <= 0 {
		maxRedirects = DefaultMaxRedirects
	}
	requestContext, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	request, err := http.NewRequestWithContext(requestContext, http.MethodGet, initial.String(), nil)
	if err != nil {
		return DeliveryLink{}, &Error{Code: "invalid_candidate", Blocked: true, Cause: err}
	}
	request.Header.Set("Accept", "application/octet-stream,video/*,audio/*,application/zip,application/x-7z-compressed,application/x-rar-compressed")
	request.Header.Set("Accept-Encoding", "identity")
	request.Header.Set("Range", "bytes=0-0")
	request.Header.Set("User-Agent", "link-evidence-helper-verifier/next")

	policy := &networkPolicy{
		resolver:      verifier.Resolver,
		allowPrivate: verifier.AllowPrivate,
		origins:      allowedOrigins,
	}
	client := &http.Client{
		Transport: policy,
		Timeout:   timeout + time.Second,
		CheckRedirect: func(next *http.Request, previous []*http.Request) error {
			if len(previous) >= maxRedirects {
				return &Error{Code: "redirect_limit", Blocked: true, Cause: ErrUnsafeURL}
			}
			parsed, parseErr := parseHTTPURL(next.URL.String())
			if parseErr != nil || !policy.originAllowed(parsed) {
				return &Error{Code: "unsafe_redirect", Blocked: true, Cause: ErrUnsafeURL}
			}
			if previous[len(previous)-1].URL.Scheme == "https" && parsed.Scheme != "https" {
				return &Error{Code: "unsafe_redirect", Blocked: true, Cause: ErrUnsafeURL}
			}
			next.Header.Set("Accept-Encoding", "identity")
			next.Header.Set("Range", "bytes=0-0")
			return nil
		},
	}

	response, err := client.Do(request)
	if err != nil {
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) || errors.Is(requestContext.Err(), context.DeadlineExceeded) {
			return DeliveryLink{}, &Error{Code: "timeout", Temporary: true, Cause: err}
		}
		var failure *Error
		if errors.As(err, &failure) {
			return DeliveryLink{}, failure
		}
		var networkError net.Error
		return DeliveryLink{}, &Error{Code: "network_error", Temporary: errors.As(err, &networkError), Cause: err}
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK && response.StatusCode != http.StatusPartialContent {
		return DeliveryLink{}, &Error{
			Code:      "http_status",
			Temporary: response.StatusCode == http.StatusTooManyRequests || response.StatusCode >= 500,
			Cause:     fmt.Errorf("%w: status %d", ErrUnavailable, response.StatusCode),
		}
	}
	if !deliveryResponse(response) {
		return DeliveryLink{}, &Error{Code: "not_delivery", Blocked: true, Cause: ErrNotDelivery}
	}
	oneByte := make([]byte, 1)
	if _, readErr := response.Body.Read(oneByte); readErr != nil && !errors.Is(readErr, io.EOF) {
		return DeliveryLink{}, &Error{Code: "read_failed", Temporary: true, Cause: readErr}
	}

	finalURL := *response.Request.URL
	finalURL.Fragment = ""
	filename := verifiedFilename(response, candidate.Filename)
	if filename == "" {
		return DeliveryLink{}, &Error{Code: "invalid_filename", Blocked: true, Cause: ErrNotDelivery}
	}
	quality := strings.TrimSpace(candidate.Quality)
	if quality == "" {
		quality = "Unknown"
	}
	return DeliveryLink{
		URL:        finalURL.String(),
		Filename:   filename,
		Size:       verifiedSize(response, candidate.Size),
		Quality:    quality,
		SourceID:   candidate.SourceID,
		VerifiedAt: verifier.now().UTC(),
	}, nil
}

func validateCandidate(candidate Candidate) error {
	if strings.TrimSpace(candidate.SourceID) == "" || len(candidate.SourceID) > 128 {
		return ErrInvalidCandidate
	}
	if strings.ContainsAny(candidate.SourceID, "\r\n\x00") {
		return ErrInvalidCandidate
	}
	if _, err := parseHTTPURL(candidate.URL); err != nil {
		return err
	}
	if utf8.RuneCountInString(candidate.Filename) > 300 || utf8.RuneCountInString(candidate.Quality) > 80 || utf8.RuneCountInString(candidate.Size) > 80 {
		return ErrInvalidCandidate
	}
	return nil
}

func compileOrigins(initial *url.URL, configured []string) (map[string]struct{}, error) {
	if len(configured) > 32 {
		return nil, ErrUnsafeURL
	}
	origins := map[string]struct{}{initialOrigin(initial): {}}
	for _, raw := range configured {
		parsed, err := parseHTTPURL(raw)
		if err != nil || parsed.Path != "" && parsed.Path != "/" || parsed.RawQuery != "" || parsed.Fragment != "" {
			return nil, ErrUnsafeURL
		}
		origins[initialOrigin(parsed)] = struct{}{}
	}
	return origins, nil
}

func parseHTTPURL(raw string) (*url.URL, error) {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || parsed.Hostname() == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil {
		return nil, ErrUnsafeURL
	}
	return parsed, nil
}

func initialOrigin(parsed *url.URL) string {
	return strings.ToLower(parsed.Scheme + "://" + parsed.Host)
}

type networkPolicy struct {
	resolver      Resolver
	allowPrivate bool
	origins      map[string]struct{}
}

func (policy *networkPolicy) RoundTrip(request *http.Request) (*http.Response, error) {
	parsed, err := parseHTTPURL(request.URL.String())
	if err != nil || !policy.originAllowed(parsed) {
		return nil, &Error{Code: "unsafe_url", Blocked: true, Cause: ErrUnsafeURL}
	}
	addresses, err := policy.resolve(request.Context(), parsed.Hostname())
	if err != nil {
		return nil, err
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DisableCompression = true
	transport.ForceAttemptHTTP2 = true
	transport.DialContext = pinnedDialer(canonicalHost(parsed.Hostname()), addresses)
	return transport.RoundTrip(request)
}

func (policy *networkPolicy) originAllowed(parsed *url.URL) bool {
	_, allowed := policy.origins[initialOrigin(parsed)]
	return allowed
}

func (policy *networkPolicy) resolve(ctx context.Context, hostname string) ([]net.IP, error) {
	host := canonicalHost(hostname)
	if literal := net.ParseIP(host); literal != nil {
		if !policy.allowPrivate && unsafeIP(literal) {
			return nil, &Error{Code: "unsafe_network", Blocked: true, Cause: ErrUnsafeURL}
		}
		return []net.IP{literal}, nil
	}
	resolver := policy.resolver
	if resolver == nil {
		resolver = net.DefaultResolver
	}
	rows, err := resolver.LookupIPAddr(ctx, host)
	if err != nil || len(rows) == 0 {
		return nil, &Error{Code: "dns_failed", Temporary: true, Cause: err}
	}
	addresses := make([]net.IP, 0, len(rows))
	for _, row := range rows {
		if row.IP == nil || (!policy.allowPrivate && unsafeIP(row.IP)) {
			return nil, &Error{Code: "unsafe_network", Blocked: true, Cause: ErrUnsafeURL}
		}
		addresses = append(addresses, row.IP)
	}
	return addresses, nil
}

func pinnedDialer(expectedHost string, addresses []net.IP) func(context.Context, string, string) (net.Conn, error) {
	dialer := &net.Dialer{Timeout: 8 * time.Second, KeepAlive: 30 * time.Second}
	return func(ctx context.Context, network, address string) (net.Conn, error) {
		host, port, err := net.SplitHostPort(address)
		if err != nil || canonicalHost(host) != expectedHost {
			return nil, &Error{Code: "unsafe_network", Blocked: true, Cause: ErrUnsafeURL}
		}
		var last error
		for _, ip := range addresses {
			connection, dialErr := dialer.DialContext(ctx, network, net.JoinHostPort(ip.String(), port))
			if dialErr == nil {
				return connection, nil
			}
			last = dialErr
		}
		return nil, &Error{Code: "network_error", Temporary: true, Cause: last}
	}
}

func deliveryResponse(response *http.Response) bool {
	disposition := response.Header.Get("Content-Disposition")
	if disposition != "" {
		_, parameters, err := mime.ParseMediaType(disposition)
		if err == nil && strings.TrimSpace(parameters["filename"]) != "" {
			return true
		}
	}
	mediaType, _, _ := mime.ParseMediaType(response.Header.Get("Content-Type"))
	mediaType = strings.ToLower(mediaType)
	if strings.HasPrefix(mediaType, "video/") || strings.HasPrefix(mediaType, "audio/") {
		return true
	}
	switch mediaType {
	case "application/octet-stream", "application/zip", "application/x-7z-compressed", "application/x-rar-compressed", "application/vnd.rar":
		return true
	case "text/html", "application/xhtml+xml", "application/json", "text/plain":
		return false
	}
	extension := strings.ToLower(path.Ext(response.Request.URL.Path))
	switch extension {
	case ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".ts", ".zip", ".7z", ".rar":
		return true
	default:
		return false
	}
}

func verifiedFilename(response *http.Response, fallback string) string {
	filename := ""
	if disposition := response.Header.Get("Content-Disposition"); disposition != "" {
		_, parameters, err := mime.ParseMediaType(disposition)
		if err == nil {
			filename = parameters["filename"]
		}
	}
	if strings.TrimSpace(filename) == "" {
		filename = strings.TrimSpace(fallback)
	}
	if filename == "" {
		filename = path.Base(response.Request.URL.Path)
	}
	filename = strings.TrimSpace(strings.ReplaceAll(strings.ReplaceAll(filename, "\r", ""), "\n", ""))
	filename = path.Base(filename)
	if filename == "." || filename == "/" || filename == "" || utf8.RuneCountInString(filename) > 300 {
		return ""
	}
	return filename
}

func verifiedSize(response *http.Response, fallback string) string {
	if contentRange := response.Header.Get("Content-Range"); contentRange != "" {
		if slash := strings.LastIndex(contentRange, "/"); slash >= 0 {
			if total, err := strconv.ParseInt(contentRange[slash+1:], 10, 64); err == nil && total >= 0 {
				return formatBytes(total)
			}
		}
	}
	if response.StatusCode == http.StatusOK && response.ContentLength >= 0 {
		return formatBytes(response.ContentLength)
	}
	return strings.TrimSpace(fallback)
}

func formatBytes(value int64) string {
	const unit = int64(1024)
	if value < unit {
		return fmt.Sprintf("%d B", value)
	}
	units := []string{"KB", "MB", "GB", "TB"}
	size := float64(value)
	index := -1
	for size >= float64(unit) && index < len(units)-1 {
		size /= float64(unit)
		index++
	}
	return strings.TrimRight(strings.TrimRight(fmt.Sprintf("%.1f", size), "0"), ".") + " " + units[index]
}

func (verifier Verifier) now() time.Time {
	if verifier.Now != nil {
		return verifier.Now()
	}
	return time.Now()
}

func canonicalHost(value string) string {
	return strings.ToLower(strings.TrimSuffix(strings.Trim(strings.TrimSpace(value), "[]"), "."))
}

var blockedNetworks = mustNetworks(
	"0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
	"172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.168.0.0/16", "198.18.0.0/15",
	"198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
	"::/128", "::1/128", "fc00::/7", "fe80::/10", "ff00::/8", "2001:db8::/32",
)

func mustNetworks(values ...string) []*net.IPNet {
	networks := make([]*net.IPNet, 0, len(values))
	for _, value := range values {
		_, network, err := net.ParseCIDR(value)
		if err != nil {
			panic(err)
		}
		networks = append(networks, network)
	}
	return networks
}

func unsafeIP(ip net.IP) bool {
	if ip == nil || ip.IsUnspecified() || ip.IsLoopback() || ip.IsPrivate() || ip.IsMulticast() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() {
		return true
	}
	for _, network := range blockedNetworks {
		if network.Contains(ip) {
			return true
		}
	}
	return false
}
