package resolution

import (
	"bytes"
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

	"github.com/GauravBhatt1/link-evidence-helper/apps/worker/internal/linkverify"
	"github.com/GauravBhatt1/link-evidence-helper/packages/jobqueue"
)

const maxLegacyFindBytes = 4 << 20

var (
	ErrLegacyResolverInvalid     = errors.New("legacy resolver returned invalid data")
	ErrLegacyResolverUnavailable = errors.New("legacy resolver unavailable")
)

type LegacyResolverConfig struct {
	BaseURL          string
	AccessToken      string
	AllowNonLoopback bool
	Timeout          time.Duration
	Client           *http.Client
	Now              func() time.Time
}

type LegacyResolver struct {
	baseURL     *url.URL
	accessToken string
	client      *http.Client
	now         func() time.Time
}

type legacyFindResponse struct {
	OK      bool         `json:"ok"`
	Links   []legacyLink `json:"links"`
	Error   string       `json:"error"`
	Message string       `json:"message"`
}

type legacyLink struct {
	URL          string `json:"url"`
	Filename     string `json:"filename"`
	Size         string `json:"size"`
	Quality      string `json:"quality"`
	QualityLabel string `json:"quality_label"`
	Source       string `json:"source"`
	SourceName   string `json:"source_name"`
	Variant      string `json:"variant"`
}

func NewLegacyResolver(config LegacyResolverConfig) (*LegacyResolver, error) {
	baseURL, err := url.Parse(strings.TrimSpace(config.BaseURL))
	if err != nil || baseURL == nil || baseURL.Scheme == "" || baseURL.Host == "" {
		return nil, fmt.Errorf("%w: base URL", ErrLegacyResolverInvalid)
	}
	if baseURL.Scheme != "http" && baseURL.Scheme != "https" {
		return nil, fmt.Errorf("%w: base URL scheme", ErrLegacyResolverInvalid)
	}
	if baseURL.User != nil || baseURL.RawQuery != "" || baseURL.Fragment != "" {
		return nil, fmt.Errorf("%w: base URL must be credential-free", ErrLegacyResolverInvalid)
	}
	if !config.AllowNonLoopback && !legacyLoopbackHost(baseURL.Hostname()) {
		return nil, fmt.Errorf("%w: non-loopback base URL requires explicit opt-in", ErrLegacyResolverInvalid)
	}
	timeout := config.Timeout
	if timeout <= 0 {
		timeout = 30 * time.Second
	}
	if timeout > 120*time.Second {
		return nil, fmt.Errorf("%w: timeout", ErrLegacyResolverInvalid)
	}
	client := config.Client
	if client == nil {
		client = &http.Client{Timeout: timeout}
	}
	now := config.Now
	if now == nil {
		now = time.Now
	}
	return &LegacyResolver{
		baseURL:     baseURL,
		accessToken: strings.TrimSpace(config.AccessToken),
		client:      client,
		now:         now,
	}, nil
}

func (resolver *LegacyResolver) Execute(ctx context.Context, job jobqueue.Job, reporter jobqueue.Reporter) error {
	if job.Kind != jobqueue.KindResolution {
		return errors.New("legacy resolver received an unsupported job kind")
	}
	request, err := decodeRequest(job.Payload)
	if err != nil {
		return finishLegacyInvalid(ctx, reporter, Request{}, "invalid_request", "The resolution request is invalid.")
	}
	if err := cancelled(ctx, reporter); err != nil {
		return err
	}
	if _, err := reporter.Transition(ctx, jobqueue.StateCheckingCache, "Preparing the legacy resolver selection.", 10, nil); err != nil {
		return err
	}
	if err := cancelled(ctx, reporter); err != nil {
		return err
	}
	if _, err := reporter.Transition(ctx, jobqueue.StateCheckingPreferredSource, "Resolving links through the legacy engine.", 45, nil); err != nil {
		return err
	}

	started := time.Now()
	find, err := resolver.find(ctx, request)
	duration := boundedDurationMS(time.Since(started))
	if err != nil {
		reason := safeLegacyReason(find, err)
		result := resolutionResult{
			OK:            false,
			Success:       false,
			Code:          "legacy_resolution_failed",
			Status:        "failed",
			ContentID:     request.ContentID,
			VariantID:     request.VariantID,
			DeliveryLinks: []linkverify.DeliveryLink{},
			Attempts: []resolutionAttempt{{
				SourceID:      "legacy-python",
				Status:        "failed",
				FailureReason: &reason,
				DurationMS:    duration,
			}},
			Message: reason,
		}
		return transitionResult(ctx, reporter, jobqueue.StateFailed, reason, result)
	}
	links := resolver.deliveryLinks(find.Links, request)
	if len(links) == 0 {
		message := "The legacy engine did not return delivery links."
		result := resolutionResult{
			OK:            false,
			Success:       false,
			Code:          "no_verified_links",
			Status:        "failed",
			ContentID:     request.ContentID,
			VariantID:     request.VariantID,
			DeliveryLinks: []linkverify.DeliveryLink{},
			Attempts: []resolutionAttempt{{
				SourceID:      "legacy-python",
				Status:        "failed",
				FailureReason: &message,
				DurationMS:    duration,
			}},
			Message: message,
		}
		return transitionResult(ctx, reporter, jobqueue.StateFailed, message, result)
	}
	result := resolutionResult{
		OK:            true,
		Success:       true,
		Code:          "ok",
		Status:        "verified",
		ContentID:     request.ContentID,
		VariantID:     request.VariantID,
		DeliveryLinks: links,
		Attempts: []resolutionAttempt{{
			SourceID:   "legacy-python",
			Status:     "verified",
			DurationMS: duration,
		}},
		Message: "Verified delivery links are ready.",
	}
	return transitionResult(ctx, reporter, jobqueue.StateVerified, "Verified delivery links are ready.", result)
}

func (resolver *LegacyResolver) find(ctx context.Context, request Request) (legacyFindResponse, error) {
	target := *resolver.baseURL
	target.Path = strings.TrimSuffix(resolver.baseURL.Path, "/") + "/api/find"
	target.RawQuery = ""
	payload, err := json.Marshal(request)
	if err != nil {
		return legacyFindResponse{}, ErrLegacyResolverInvalid
	}
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodPost, target.String(), bytes.NewReader(payload))
	if err != nil {
		return legacyFindResponse{}, ErrLegacyResolverInvalid
	}
	httpRequest.Header.Set("Accept", "application/json")
	httpRequest.Header.Set("Content-Type", "application/json")
	httpRequest.Header.Set("User-Agent", "link-evidence-helper-legacy-resolver/1.0")
	if resolver.accessToken != "" {
		httpRequest.Header.Set("x-app-token", resolver.accessToken)
	}
	response, err := resolver.client.Do(httpRequest)
	if err != nil {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return legacyFindResponse{}, ctxErr
		}
		return legacyFindResponse{}, ErrLegacyResolverUnavailable
	}
	defer response.Body.Close()
	if !strings.Contains(strings.ToLower(response.Header.Get("Content-Type")), "application/json") {
		return legacyFindResponse{}, ErrLegacyResolverInvalid
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, maxLegacyFindBytes+1))
	if err != nil || len(body) > maxLegacyFindBytes {
		return legacyFindResponse{}, ErrLegacyResolverUnavailable
	}
	var find legacyFindResponse
	if err := json.Unmarshal(body, &find); err != nil {
		return legacyFindResponse{}, ErrLegacyResolverInvalid
	}
	if response.StatusCode != http.StatusOK || !find.OK {
		return find, ErrLegacyResolverUnavailable
	}
	return find, nil
}

func (resolver *LegacyResolver) deliveryLinks(legacyLinks []legacyLink, request Request) []linkverify.DeliveryLink {
	if legacyLinks == nil {
		return []linkverify.DeliveryLink{}
	}
	links := make([]linkverify.DeliveryLink, 0, len(legacyLinks))
	for index, item := range legacyLinks {
		rawURL := strings.TrimSpace(item.URL)
		if !safeLegacyDeliveryURL(rawURL) {
			continue
		}
		quality := firstResolutionValue(item.QualityLabel, item.Quality)
		if quality == "" && request.Quality != nil {
			quality = strings.TrimSpace(*request.Quality)
		}
		if quality == "" {
			quality = "Unknown"
		}
		sourceID := firstResolutionValue(item.SourceName, item.Source)
		if sourceID == "" {
			sourceID = "legacy-python"
		}
		links = append(links, linkverify.DeliveryLink{
			URL:        rawURL,
			Filename:   safeLegacyFilename(item.Filename, item.Variant, rawURL),
			Size:       firstResolutionValue(item.Size, "unknown"),
			Quality:    quality,
			SourceID:   safeLegacySourceID(sourceID, index),
			VerifiedAt: resolver.now().UTC(),
		})
	}
	return links
}

func finishLegacyInvalid(ctx context.Context, reporter jobqueue.Reporter, request Request, code, message string) error {
	result := resolutionResult{
		OK:            false,
		Success:       false,
		Code:          code,
		Status:        "failed",
		ContentID:     request.ContentID,
		VariantID:     request.VariantID,
		DeliveryLinks: []linkverify.DeliveryLink{},
		Attempts:      []resolutionAttempt{},
		Message:       message,
	}
	return transitionResult(ctx, reporter, jobqueue.StateFailed, message, result)
}

func safeLegacyReason(find legacyFindResponse, err error) string {
	if message := firstResolutionValue(find.Error, find.Message); message != "" {
		return message
	}
	if errors.Is(err, ErrLegacyResolverInvalid) {
		return "The legacy resolver returned an invalid response."
	}
	return "The legacy resolver could not complete the request."
}

func safeLegacyDeliveryURL(raw string) bool {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	return err == nil && parsed.Hostname() != "" && (parsed.Scheme == "http" || parsed.Scheme == "https") && parsed.User == nil && parsed.Fragment == ""
}

func safeLegacyFilename(filename, variant, rawURL string) string {
	for _, candidate := range []string{filename, variant} {
		cleaned := strings.TrimSpace(strings.ReplaceAll(strings.ReplaceAll(candidate, "\r", ""), "\n", ""))
		if cleaned != "" && len(cleaned) <= 300 {
			return cleaned
		}
	}
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return "delivery-link"
	}
	if path := strings.Trim(strings.TrimSpace(parsed.Path), "/"); path != "" && len(path) <= 300 {
		parts := strings.Split(path, "/")
		return parts[len(parts)-1]
	}
	return "delivery-link"
}

func safeLegacySourceID(value string, index int) string {
	cleaned := strings.ToLower(strings.TrimSpace(value))
	cleaned = strings.Map(func(r rune) rune {
		if r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || r == '_' || r == '-' || r == '.' {
			return r
		}
		return '_'
	}, cleaned)
	cleaned = strings.Trim(cleaned, "._-")
	if cleaned == "" {
		cleaned = "legacy-python"
	}
	if len(cleaned) > 96 {
		cleaned = cleaned[:96]
	}
	return fmt.Sprintf("%s_%d", cleaned, index+1)
}

func firstResolutionValue(values ...string) string {
	for _, value := range values {
		if trimmed := strings.TrimSpace(value); trimmed != "" {
			return trimmed
		}
	}
	return ""
}

func legacyLoopbackHost(host string) bool {
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(strings.Trim(host, "[]"))
	return ip != nil && ip.IsLoopback()
}

var _ jobqueue.Executor = (*LegacyResolver)(nil)
