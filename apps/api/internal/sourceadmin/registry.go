// Package sourceadmin provides a bounded, secret-safe administrative source registry.
package sourceadmin

import (
	"errors"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode"
)

var (
	ErrInvalidSource    = errors.New("invalid source")
	ErrSourceExists     = errors.New("source already exists")
	ErrSourceNotFound   = errors.New("source not found")
	ErrRevisionConflict = errors.New("source revision conflict")
	fieldPathPattern    = regexp.MustCompile(`^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$`)
)

// Source is deliberately credential-free. Headers, cookies, tokens, request
// bodies, scripts, and arbitrary metadata are not part of this contract.
type Source struct {
	ID                 string    `json:"id"`
	DisplayName        string    `json:"displayName"`
	Kind               string    `json:"kind"`
	Endpoint           string    `json:"endpoint"`
	QueryParameter     string    `json:"queryParameter"`
	ResultRoot         string    `json:"resultRoot,omitempty"`
	TitleField         string    `json:"titleField"`
	URLField           string    `json:"urlField"`
	AllowedResultHosts []string  `json:"allowedResultHosts,omitempty"`
	Enabled            bool      `json:"enabled"`
	Revision           uint64    `json:"revision"`
	CreatedAt          time.Time `json:"createdAt"`
	UpdatedAt          time.Time `json:"updatedAt"`
}

// Draft contains the only mutable configuration accepted by the registry.
type Draft struct {
	ID                 string
	DisplayName        string
	Kind               string
	Endpoint           string
	QueryParameter     string
	ResultRoot         string
	TitleField         string
	URLField           string
	AllowedResultHosts []string
	Enabled            bool
}

// Registry defines the administrative source-management boundary.
type Registry interface {
	List() []Source
	Create(Draft, time.Time) (Source, error)
	Update(id string, expectedRevision uint64, draft Draft, now time.Time) (Source, error)
	Disable(id string, expectedRevision uint64, now time.Time) (Source, error)
}

// MemoryRegistry is a concurrency-safe non-durable implementation used until
// the PostgreSQL repository is introduced. It is never selected implicitly.
type MemoryRegistry struct {
	mu      sync.RWMutex
	sources map[string]Source
}

func NewMemoryRegistry() *MemoryRegistry {
	return &MemoryRegistry{sources: make(map[string]Source)}
}

func (registry *MemoryRegistry) List() []Source {
	registry.mu.RLock()
	defer registry.mu.RUnlock()
	result := make([]Source, 0, len(registry.sources))
	for _, source := range registry.sources {
		result = append(result, source)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].ID < result[j].ID })
	return result
}

func (registry *MemoryRegistry) Create(draft Draft, now time.Time) (Source, error) {
	normalized, err := normalizeDraft(draft)
	if err != nil || now.IsZero() {
		return Source{}, ErrInvalidSource
	}
	registry.mu.Lock()
	defer registry.mu.Unlock()
	if _, exists := registry.sources[normalized.ID]; exists {
		return Source{}, ErrSourceExists
	}
	timestamp := now.UTC()
	source := Source{
		ID: normalized.ID, DisplayName: normalized.DisplayName, Kind: normalized.Kind,
		Endpoint: normalized.Endpoint, QueryParameter: normalized.QueryParameter,
		ResultRoot: normalized.ResultRoot, TitleField: normalized.TitleField,
		URLField: normalized.URLField, AllowedResultHosts: append([]string(nil), normalized.AllowedResultHosts...),
		Enabled: normalized.Enabled, Revision: 1, CreatedAt: timestamp, UpdatedAt: timestamp,
	}
	registry.sources[source.ID] = source
	return source, nil
}

func (registry *MemoryRegistry) Update(id string, expectedRevision uint64, draft Draft, now time.Time) (Source, error) {
	normalized, err := normalizeDraft(draft)
	if err != nil || normalized.ID != id || expectedRevision == 0 || now.IsZero() {
		return Source{}, ErrInvalidSource
	}
	registry.mu.Lock()
	defer registry.mu.Unlock()
	current, exists := registry.sources[id]
	if !exists {
		return Source{}, ErrSourceNotFound
	}
	if current.Revision != expectedRevision {
		return Source{}, ErrRevisionConflict
	}
	current.DisplayName = normalized.DisplayName
	current.Kind = normalized.Kind
	current.Endpoint = normalized.Endpoint
	current.QueryParameter = normalized.QueryParameter
	current.ResultRoot = normalized.ResultRoot
	current.TitleField = normalized.TitleField
	current.URLField = normalized.URLField
	current.AllowedResultHosts = append([]string(nil), normalized.AllowedResultHosts...)
	current.Enabled = normalized.Enabled
	current.Revision++
	current.UpdatedAt = now.UTC()
	registry.sources[id] = current
	return current, nil
}

func (registry *MemoryRegistry) Disable(id string, expectedRevision uint64, now time.Time) (Source, error) {
	if !safeIdentifier(id, 1, 80) || expectedRevision == 0 || now.IsZero() {
		return Source{}, ErrInvalidSource
	}
	registry.mu.Lock()
	defer registry.mu.Unlock()
	current, exists := registry.sources[id]
	if !exists {
		return Source{}, ErrSourceNotFound
	}
	if current.Revision != expectedRevision {
		return Source{}, ErrRevisionConflict
	}
	current.Enabled = false
	current.Revision++
	current.UpdatedAt = now.UTC()
	registry.sources[id] = current
	return current, nil
}

func normalizeDraft(draft Draft) (Draft, error) {
	if !safeIdentifier(draft.ID, 1, 80) {
		return Draft{}, ErrInvalidSource
	}
	name := strings.TrimSpace(draft.DisplayName)
	if name != draft.DisplayName || len(name) < 1 || len(name) > 120 || containsControl(name) {
		return Draft{}, ErrInvalidSource
	}
	if draft.Kind != "http-json" && draft.Kind != "http-html" && draft.Kind != "browser-html" {
		return Draft{}, ErrInvalidSource
	}
	parsed, err := url.Parse(draft.Endpoint)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return Draft{}, ErrInvalidSource
	}
	if parsed.Scheme != "https" && parsed.Scheme != "http" {
		return Draft{}, ErrInvalidSource
	}
	if parsed.Path == "" {
		parsed.Path = "/"
	}
	queryParameter := strings.TrimSpace(draft.QueryParameter)
	titleField := strings.TrimSpace(draft.TitleField)
	urlField := strings.TrimSpace(draft.URLField)
	resultRoot := strings.TrimSpace(draft.ResultRoot)
	if queryParameter != draft.QueryParameter || titleField != draft.TitleField || urlField != draft.URLField || resultRoot != draft.ResultRoot {
		return Draft{}, ErrInvalidSource
	}
	if queryParameter == "" {
		queryParameter = "q"
	}
	if titleField == "" {
		titleField = "title"
	}
	if urlField == "" {
		urlField = "url"
	}
	if !safeIdentifier(queryParameter, 1, 64) || !fieldPathPattern.MatchString(titleField) || !fieldPathPattern.MatchString(urlField) {
		return Draft{}, ErrInvalidSource
	}
	if resultRoot != "" && !fieldPathPattern.MatchString(resultRoot) {
		return Draft{}, ErrInvalidSource
	}
	allowedHosts, err := normalizeAllowedHosts(draft.AllowedResultHosts)
	if err != nil {
		return Draft{}, err
	}
	return Draft{
		ID: draft.ID, DisplayName: name, Kind: draft.Kind, Endpoint: parsed.String(),
		QueryParameter: queryParameter, ResultRoot: resultRoot, TitleField: titleField,
		URLField: urlField, AllowedResultHosts: allowedHosts, Enabled: draft.Enabled,
	}, nil
}

func normalizeAllowedHosts(values []string) ([]string, error) {
	seen := map[string]struct{}{}
	result := make([]string, 0, len(values))
	for _, value := range values {
		host := strings.ToLower(strings.TrimSuffix(strings.TrimSpace(value), "."))
		if host == "" || strings.ContainsAny(host, "/:@?#") || netIPLiteral(host) {
			return nil, ErrInvalidSource
		}
		if _, exists := seen[host]; exists {
			return nil, ErrInvalidSource
		}
		seen[host] = struct{}{}
		result = append(result, host)
	}
	sort.Strings(result)
	return result, nil
}

func safeIdentifier(value string, minimum, maximum int) bool {
	if len(value) < minimum || len(value) > maximum || strings.TrimSpace(value) != value {
		return false
	}
	for _, character := range value {
		if !(unicode.IsLetter(character) || unicode.IsDigit(character) || character == '-' || character == '_') {
			return false
		}
	}
	return true
}

func containsControl(value string) bool {
	for _, character := range value {
		if unicode.IsControl(character) {
			return true
		}
	}
	return false
}

func netIPLiteral(value string) bool {
	if !strings.Contains(value, ".") && !strings.Contains(value, ":") {
		return false
	}
	for _, character := range value {
		if (character < '0' || character > '9') && character != '.' && character != ':' &&
			(character < 'a' || character > 'f') && (character < 'A' || character > 'F') {
			return false
		}
	}
	return true
}
