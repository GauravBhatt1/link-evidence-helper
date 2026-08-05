package library

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/url"
	"path"
	"strings"
	"time"
)

const (
	defaultJellyfinPageSize         = 200
	defaultJellyfinMaxItems         = 5000
	defaultJellyfinMaxResponseBytes = 4 << 20
)

var (
	ErrJellyfinInvalidConfig   = errors.New("invalid Jellyfin configuration")
	ErrJellyfinUnsafeTarget    = errors.New("unsafe Jellyfin target")
	ErrJellyfinUnauthorized    = errors.New("Jellyfin authorization failed")
	ErrJellyfinUnavailable     = errors.New("Jellyfin is unavailable")
	ErrJellyfinInvalidResponse = errors.New("invalid Jellyfin response")
	ErrJellyfinTooManyItems    = errors.New("Jellyfin item limit exceeded")
)

type IPResolver interface {
	LookupIPAddr(ctx context.Context, host string) ([]net.IPAddr, error)
}

type JellyfinConfig struct {
	BaseURL          string
	APIKey           string
	LibraryIDs       []string
	AllowPrivate     bool
	Timeout          time.Duration
	PageSize         int
	MaxItems         int
	MaxResponseBytes int64
	Resolver         IPResolver
	Dialer           *net.Dialer
	Now              func() time.Time
}

func validateJellyfinConfig(config *JellyfinConfig) (*url.URL, error) {
	if config.Timeout == 0 {
		config.Timeout = 10 * time.Second
	}
	if config.Timeout < time.Second || config.Timeout > 60*time.Second {
		return nil, fmt.Errorf("%w: timeout", ErrJellyfinInvalidConfig)
	}
	if config.PageSize == 0 {
		config.PageSize = defaultJellyfinPageSize
	}
	if config.PageSize < 1 || config.PageSize > 500 {
		return nil, fmt.Errorf("%w: page size", ErrJellyfinInvalidConfig)
	}
	if config.MaxItems == 0 {
		config.MaxItems = defaultJellyfinMaxItems
	}
	if config.MaxItems < 1 || config.MaxItems > 5000 {
		return nil, fmt.Errorf("%w: max items", ErrJellyfinInvalidConfig)
	}
	if config.MaxResponseBytes == 0 {
		config.MaxResponseBytes = defaultJellyfinMaxResponseBytes
	}
	if config.MaxResponseBytes < 1024 || config.MaxResponseBytes > 16<<20 {
		return nil, fmt.Errorf("%w: response limit", ErrJellyfinInvalidConfig)
	}
	if config.Resolver == nil {
		config.Resolver = net.DefaultResolver
	}
	if config.Dialer == nil {
		config.Dialer = &net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}
	}
	if config.Now == nil {
		config.Now = func() time.Time { return time.Now().UTC() }
	}

	baseURL, err := url.Parse(strings.TrimSpace(config.BaseURL))
	if err != nil || baseURL == nil || baseURL.Host == "" {
		return nil, fmt.Errorf("%w: base URL", ErrJellyfinInvalidConfig)
	}
	if baseURL.Scheme != "http" && baseURL.Scheme != "https" {
		return nil, fmt.Errorf("%w: scheme", ErrJellyfinInvalidConfig)
	}
	if baseURL.User != nil || baseURL.RawQuery != "" || baseURL.Fragment != "" {
		return nil, fmt.Errorf("%w: URL components", ErrJellyfinInvalidConfig)
	}
	if strings.TrimSpace(config.APIKey) == "" || len(strings.TrimSpace(config.APIKey)) > 1024 {
		return nil, fmt.Errorf("%w: API key", ErrJellyfinInvalidConfig)
	}
	if strings.Contains(baseURL.Hostname(), "%") {
		return nil, fmt.Errorf("%w: scoped address", ErrJellyfinInvalidConfig)
	}
	baseURL.Path = strings.TrimSuffix(path.Clean("/"+strings.TrimPrefix(baseURL.EscapedPath(), "/")), "/")
	if baseURL.Path == "." || baseURL.Path == "/" {
		baseURL.Path = ""
	}
	baseURL.RawPath = ""

	seen := make(map[string]struct{}, len(config.LibraryIDs))
	libraryIDs := make([]string, 0, len(config.LibraryIDs))
	for _, raw := range config.LibraryIDs {
		value := strings.TrimSpace(raw)
		if value == "" {
			continue
		}
		if len(value) > 128 || strings.ContainsAny(value, "\r\n,&=?#/\\") {
			return nil, fmt.Errorf("%w: library ID", ErrJellyfinInvalidConfig)
		}
		if _, exists := seen[value]; exists {
			continue
		}
		seen[value] = struct{}{}
		libraryIDs = append(libraryIDs, value)
	}
	if len(libraryIDs) > 50 {
		return nil, fmt.Errorf("%w: library IDs", ErrJellyfinInvalidConfig)
	}
	config.LibraryIDs = libraryIDs
	return baseURL, nil
}
