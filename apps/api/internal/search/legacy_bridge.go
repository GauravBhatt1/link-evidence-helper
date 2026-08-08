package search

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/contracts"
)

const maxLegacyBridgeBytes = 4 << 20

var (
	ErrLegacyBridgeUnavailable = errors.New("legacy bridge unavailable")
	ErrLegacyBridgeInvalid     = errors.New("legacy bridge returned invalid data")
)

type LegacyBridgeConfig struct {
	BaseURL          string
	AllowNonLoopback bool
	Timeout          time.Duration
	Client           *http.Client
}

type LegacyBridge struct {
	baseURL *url.URL
	client  *http.Client
}

type legacySearchResponse struct {
	OK              bool                   `json:"ok"`
	Contents        []legacyContent        `json:"contents"`
	AdapterFailures []legacyAdapterFailure `json:"adapterFailures"`
}

type legacyAdapterFailure struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	Reason string `json:"reason"`
	Error  string `json:"error"`
}

type legacyContent struct {
	ContentID       string          `json:"contentId"`
	TMDBID          any             `json:"tmdbId"`
	Title           string          `json:"title"`
	Year            string          `json:"year"`
	MediaType       string          `json:"mediaType"`
	Poster          string          `json:"poster"`
	Languages       []string        `json:"languages"`
	ReleaseVariants []legacyVariant `json:"releaseVariants"`
	TotalSources    int             `json:"totalSources"`
	JellyfinStatus  string          `json:"jellyfinStatus"`
}

type legacyVariant struct {
	VariantID          string         `json:"variantId"`
	Language           string         `json:"language"`
	AudioVariant       string         `json:"audioVariant"`
	Quality            string         `json:"quality"`
	AvailableQualities []string       `json:"availableQualities"`
	ReleaseType        string         `json:"releaseType"`
	PackType           string         `json:"packType"`
	Season             *int           `json:"season"`
	Episode            *int           `json:"episode"`
	ApproxSize         string         `json:"approxSize"`
	SourceCount        int            `json:"sourceCount"`
	Sources            []legacySource `json:"sources"`
}

type legacySource struct {
	SourceID          string `json:"sourceId"`
	DisplayName       string `json:"displayName"`
	SourceName        string `json:"sourceName"`
	AdapterName       string `json:"adapterName"`
	Priority          int    `json:"priority"`
	VerificationState string `json:"verificationState"`
}

func NewLegacyBridge(config LegacyBridgeConfig) (*LegacyBridge, error) {
	baseURL, err := url.Parse(strings.TrimSpace(config.BaseURL))
	if err != nil || baseURL == nil || baseURL.Scheme == "" || baseURL.Host == "" {
		return nil, fmt.Errorf("%w: base URL", ErrLegacyBridgeInvalid)
	}
	if baseURL.Scheme != "http" && baseURL.Scheme != "https" {
		return nil, fmt.Errorf("%w: base URL scheme", ErrLegacyBridgeInvalid)
	}
	if baseURL.User != nil || baseURL.RawQuery != "" || baseURL.Fragment != "" {
		return nil, fmt.Errorf("%w: base URL must be credential-free", ErrLegacyBridgeInvalid)
	}
	if !config.AllowNonLoopback && !isLoopbackHost(baseURL.Hostname()) {
		return nil, fmt.Errorf("%w: non-loopback base URL requires explicit opt-in", ErrLegacyBridgeInvalid)
	}
	timeout := config.Timeout
	if timeout <= 0 {
		timeout = 10 * time.Second
	}
	if timeout > 30*time.Second {
		return nil, fmt.Errorf("%w: timeout", ErrLegacyBridgeInvalid)
	}
	client := config.Client
	if client == nil {
		client = &http.Client{Timeout: timeout}
	}
	return &LegacyBridge{baseURL: baseURL, client: client}, nil
}

func (*LegacyBridge) Mode() string {
	return "legacy-bridge"
}

func (bridge *LegacyBridge) Search(ctx context.Context, query string) (contracts.SearchResponse, error) {
	normalized, err := NormalizeQuery(query)
	if err != nil {
		return contracts.SearchResponse{}, err
	}
	target := *bridge.baseURL
	target.Path = strings.TrimSuffix(bridge.baseURL.Path, "/") + "/api/search"
	target.RawQuery = ""
	values := target.Query()
	values.Set("q", normalized)
	target.RawQuery = values.Encode()

	request, err := http.NewRequestWithContext(ctx, http.MethodGet, target.String(), nil)
	if err != nil {
		return contracts.SearchResponse{}, ErrLegacyBridgeInvalid
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "link-evidence-helper-legacy-bridge/1.0")
	response, err := bridge.client.Do(request)
	if err != nil {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return contracts.SearchResponse{}, ctxErr
		}
		return contracts.SearchResponse{}, ErrLegacyBridgeUnavailable
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return contracts.SearchResponse{}, ErrLegacyBridgeUnavailable
	}
	if !strings.Contains(strings.ToLower(response.Header.Get("Content-Type")), "application/json") {
		return contracts.SearchResponse{}, ErrLegacyBridgeInvalid
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, maxLegacyBridgeBytes+1))
	if err != nil || len(body) > maxLegacyBridgeBytes {
		return contracts.SearchResponse{}, ErrLegacyBridgeUnavailable
	}
	var legacy legacySearchResponse
	decoder := json.NewDecoder(strings.NewReader(string(body)))
	if err := decoder.Decode(&legacy); err != nil {
		return contracts.SearchResponse{}, ErrLegacyBridgeInvalid
	}
	if !legacy.OK {
		return contracts.SearchResponse{}, ErrLegacyBridgeUnavailable
	}
	return contracts.SearchResponse{
		OK: true, Success: true, Code: "ok", Query: normalized,
		Contents:        bridgeContents(legacy.Contents),
		PartialFailures: bridgeFailures(legacy.AdapterFailures),
	}, nil
}

func bridgeContents(contents []legacyContent) []contracts.Content {
	if contents == nil {
		return []contracts.Content{}
	}
	result := make([]contracts.Content, 0, len(contents))
	for _, content := range contents {
		var tmdbID *string
		if value := stringify(content.TMDBID); value != "" {
			tmdbID = &value
		}
		jellyfinStatus := strings.TrimSpace(content.JellyfinStatus)
		if jellyfinStatus == "" {
			jellyfinStatus = "unknown"
		}
		result = append(result, contracts.Content{
			ContentID:       strings.TrimSpace(content.ContentID),
			TMDBID:          tmdbID,
			Title:           strings.TrimSpace(content.Title),
			Year:            strings.TrimSpace(content.Year),
			MediaType:       strings.TrimSpace(content.MediaType),
			Poster:          strings.TrimSpace(content.Poster),
			Languages:       nonNilStrings(content.Languages),
			ReleaseVariants: bridgeVariants(content.ReleaseVariants),
			TotalSources:    content.TotalSources,
			JellyfinStatus:  jellyfinStatus,
		})
	}
	return result
}

func bridgeVariants(variants []legacyVariant) []contracts.ReleaseVariant {
	if variants == nil {
		return []contracts.ReleaseVariant{}
	}
	result := make([]contracts.ReleaseVariant, 0, len(variants))
	for _, variant := range variants {
		sources := bridgeSources(variant.Sources)
		sourceCount := variant.SourceCount
		if sourceCount == 0 {
			sourceCount = len(sources)
		}
		result = append(result, contracts.ReleaseVariant{
			VariantID:          strings.TrimSpace(variant.VariantID),
			Language:           defaultString(variant.Language, "Unknown"),
			AudioVariant:       defaultString(variant.AudioVariant, "Unknown"),
			Quality:            defaultString(variant.Quality, "Unknown"),
			AvailableQualities: nonNilStrings(variant.AvailableQualities),
			ReleaseType:        defaultString(variant.ReleaseType, "Unknown"),
			PackType:           defaultString(variant.PackType, "single"),
			Season:             variant.Season,
			Episode:            variant.Episode,
			ApproxSize:         strings.TrimSpace(variant.ApproxSize),
			SourceCount:        sourceCount,
			Sources:            sources,
		})
	}
	return result
}

func bridgeSources(sources []legacySource) []contracts.SourceCandidate {
	if sources == nil {
		return []contracts.SourceCandidate{}
	}
	result := make([]contracts.SourceCandidate, 0, len(sources))
	for _, source := range sources {
		displayName := firstNonEmpty(source.DisplayName, source.SourceName, source.AdapterName, source.SourceID)
		state := strings.TrimSpace(source.VerificationState)
		if state == "" {
			state = "unverified"
		}
		result = append(result, contracts.SourceCandidate{
			SourceID:          strings.TrimSpace(source.SourceID),
			DisplayName:       displayName,
			Priority:          source.Priority,
			VerificationState: state,
		})
	}
	return result
}

func bridgeFailures(failures []legacyAdapterFailure) []contracts.PartialFailure {
	if failures == nil {
		return []contracts.PartialFailure{}
	}
	result := make([]contracts.PartialFailure, 0, len(failures))
	for _, failure := range failures {
		sourceID := firstNonEmpty(failure.ID, failure.Name, "legacy-source")
		reason := firstNonEmpty(failure.Reason, failure.Error, "source failed")
		result = append(result, contracts.PartialFailure{SourceID: sourceID, Reason: reason})
	}
	return result
}

func stringify(value any) string {
	switch typed := value.(type) {
	case string:
		return strings.TrimSpace(typed)
	case float64:
		if typed == 0 {
			return ""
		}
		return fmt.Sprintf("%.0f", typed)
	default:
		return ""
	}
}

func nonNilStrings(values []string) []string {
	if values == nil {
		return []string{}
	}
	result := make([]string, 0, len(values))
	for _, value := range values {
		if trimmed := strings.TrimSpace(value); trimmed != "" {
			result = append(result, trimmed)
		}
	}
	return result
}

func defaultString(value, fallback string) string {
	if trimmed := strings.TrimSpace(value); trimmed != "" {
		return trimmed
	}
	return fallback
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if trimmed := strings.TrimSpace(value); trimmed != "" {
			return trimmed
		}
	}
	return ""
}

func isLoopbackHost(host string) bool {
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(strings.Trim(host, "[]"))
	return ip != nil && ip.IsLoopback()
}
