package library

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const maxLegacyLibraryBytes = 8 << 20

type LegacyRepositoryConfig struct {
	BaseURL          string
	AccessToken      string
	AllowNonLoopback bool
	Timeout          time.Duration
}

type LegacyRepository struct {
	baseURL    *url.URL
	token      string
	httpClient *http.Client
}

type legacyLibraryResponse struct {
	Items []legacyLibraryItem `json:"items"`
}

type legacyLibraryStats struct {
	Movies struct {
		Count int `json:"count"`
	} `json:"movies"`
	TV struct {
		Count int `json:"count"`
	} `json:"tv"`
	ConfigurationErrors []any  `json:"configurationErrors"`
	LastScan            string `json:"lastScan"`
}

type legacyLibraryItem struct {
	ID            string          `json:"id"`
	Type          string          `json:"type"`
	Title         string          `json:"title"`
	Year          *int            `json:"year"`
	TMDBID        any             `json:"tmdb_id"`
	PosterPath    string          `json:"poster_path"`
	PosterURL     string          `json:"posterUrl"`
	Available     bool            `json:"available"`
	NeedsMatch    bool            `json:"needsMatch"`
	DateAdded     string          `json:"dateAdded"`
	LastScannedAt string          `json:"lastScannedAt"`
	TotalFiles    int             `json:"total_files"`
	Metadata      json.RawMessage `json:"metadata"`
}

func NewLegacyRepository(config LegacyRepositoryConfig) (*LegacyRepository, error) {
	baseURL, err := validateLegacyLibraryBaseURL(config.BaseURL, config.AllowNonLoopback)
	if err != nil {
		return nil, err
	}
	timeout := config.Timeout
	if timeout <= 0 {
		timeout = 10 * time.Second
	}
	if timeout > 30*time.Second {
		return nil, errors.New("legacy library timeout is too large")
	}
	return &LegacyRepository{
		baseURL: baseURL,
		token:   strings.TrimSpace(config.AccessToken),
		httpClient: &http.Client{
			Timeout: timeout,
			CheckRedirect: func(*http.Request, []*http.Request) error {
				return errors.New("legacy library redirects are disabled")
			},
		},
	}, nil
}

func (repository *LegacyRepository) List(ctx context.Context, view View) (Response, error) {
	if _, err := ParseView(string(view)); err != nil {
		return Response{}, err
	}
	stats, err := repository.fetchStats(ctx)
	if err != nil {
		return Response{}, err
	}
	items, err := repository.fetchItems(ctx, view)
	if err != nil {
		return Response{}, err
	}
	generatedAt := time.Now().UTC()
	if parsed, ok := parseLegacyTime(stats.LastScan); ok {
		generatedAt = parsed
	}
	return Response{
		OK:          true,
		Success:     true,
		Code:        "ok",
		View:        view,
		GeneratedAt: generatedAt,
		Items:       items,
		Summary: Summary{
			Total:   stats.Movies.Count + stats.TV.Count,
			Movies:  stats.Movies.Count,
			TV:      stats.TV.Count,
			Missing: countMissing(items),
		},
		Jellyfin: JellyfinStatus{
			Configured:   true,
			Mode:         JellyfinConnected,
			LastSyncedAt: timePointer(generatedAt),
		},
	}, nil
}

func (repository *LegacyRepository) fetchStats(ctx context.Context) (legacyLibraryStats, error) {
	var stats legacyLibraryStats
	if err := repository.getJSON(ctx, "/api/library/stats", nil, &stats); err != nil {
		return legacyLibraryStats{}, err
	}
	return stats, nil
}

func (repository *LegacyRepository) fetchItems(ctx context.Context, view View) ([]Item, error) {
	query := url.Values{}
	query.Set("limit", "5000")
	var endpoint string
	switch view {
	case ViewMovies:
		endpoint = "/api/library/movies"
	case ViewTV:
		endpoint = "/api/library/tv"
	case ViewRecent:
		endpoint = "/api/library/recent"
		query.Set("limit", "200")
	case ViewMissing:
		endpoint = "/api/library/missing"
		query.Del("limit")
	default:
		return nil, ErrInvalidView
	}
	var payload legacyLibraryResponse
	if err := repository.getJSON(ctx, endpoint, query, &payload); err != nil {
		return nil, err
	}
	items := make([]Item, 0, len(payload.Items))
	for _, raw := range payload.Items {
		item, err := mapLegacyLibraryItem(raw)
		if err != nil {
			continue
		}
		if view != ViewMissing || item.Missing || item.LibraryState == StatePartial {
			items = append(items, item)
		}
	}
	sortItems(items, view)
	return items, nil
}

func (repository *LegacyRepository) getJSON(ctx context.Context, path string, query url.Values, target any) error {
	requestURL := *repository.baseURL
	requestURL.Path = strings.TrimSuffix(repository.baseURL.Path, "/") + path
	requestURL.RawQuery = query.Encode()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, requestURL.String(), nil)
	if err != nil {
		return err
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("User-Agent", "FREEMIUM-INDEX/1.0")
	if repository.token != "" {
		request.Header.Set("X-Access-Token", repository.token)
	}
	response, err := repository.httpClient.Do(request)
	if err != nil {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return ctxErr
		}
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("legacy library returned HTTP %d", response.StatusCode)
	}
	mediaType, _, err := mime.ParseMediaType(response.Header.Get("Content-Type"))
	if err != nil || mediaType != "application/json" {
		return errors.New("legacy library returned a non-JSON response")
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, maxLegacyLibraryBytes+1))
	if err != nil {
		return err
	}
	if len(body) > maxLegacyLibraryBytes {
		return errors.New("legacy library response is too large")
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	if err := decoder.Decode(target); err != nil {
		return err
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		return errors.New("legacy library returned multiple JSON values")
	}
	return nil
}

func validateLegacyLibraryBaseURL(value string, allowNonLoopback bool) (*url.URL, error) {
	parsed, err := url.Parse(strings.TrimSpace(value))
	if err != nil || parsed.Scheme != "http" || parsed.Host == "" {
		return nil, errors.New("legacy library base URL must be an HTTP URL")
	}
	if parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, errors.New("legacy library base URL must not contain query or fragment")
	}
	if !allowNonLoopback && !isLoopbackHost(parsed.Hostname()) {
		return nil, errors.New("legacy library base URL must be loopback unless explicitly allowed")
	}
	return parsed, nil
}

func isLoopbackHost(host string) bool {
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func mapLegacyLibraryItem(raw legacyLibraryItem) (Item, error) {
	added, ok := parseLegacyTime(raw.DateAdded)
	if !ok {
		added = time.Now().UTC()
	}
	updated, ok := parseLegacyTime(raw.LastScannedAt)
	if !ok || updated.Before(added) {
		updated = added
	}
	mediaType := MediaSeries
	if strings.EqualFold(raw.Type, "movie") {
		mediaType = MediaMovie
	}
	state := StateAvailable
	missing := false
	if !raw.Available {
		state = StateMissing
		missing = true
	} else if raw.NeedsMatch {
		state = StateUnknown
	}
	tmdbID := legacyTMDBID(raw.TMDBID)
	contentID := "library_" + raw.ID
	poster := legacyPoster(raw)
	item := Item{
		ItemID:       raw.ID,
		ContentID:    &contentID,
		TMDBID:       tmdbID,
		Title:        strings.TrimSpace(raw.Title),
		Year:         raw.Year,
		MediaType:    mediaType,
		Poster:       poster,
		LibraryState: state,
		Missing:      missing,
		DateAdded:    added,
		UpdatedAt:    updated,
		Jellyfin: JellyfinRef{
			Configured:   true,
			Present:      raw.Available,
			LastSyncedAt: timePointer(updated),
		},
	}
	if raw.ID != "" && raw.Available {
		item.Jellyfin.ItemID = &raw.ID
	}
	if err := item.Validate(); err != nil {
		return Item{}, err
	}
	return item, nil
}

func legacyTMDBID(value any) *string {
	switch typed := value.(type) {
	case float64:
		if typed > 0 {
			result := strconv.FormatInt(int64(typed), 10)
			return &result
		}
	case string:
		if trimmed := strings.TrimSpace(typed); trimmed != "" {
			return &trimmed
		}
	}
	return nil
}

func legacyPoster(raw legacyLibraryItem) *string {
	if strings.TrimSpace(raw.PosterURL) != "" {
		value := raw.PosterURL
		return &value
	}
	if strings.TrimSpace(raw.PosterPath) != "" {
		value := "/api/tmdb-image?path=w342" + raw.PosterPath
		return &value
	}
	return nil
}

func parseLegacyTime(value string) (time.Time, bool) {
	if strings.TrimSpace(value) == "" {
		return time.Time{}, false
	}
	parsed, err := time.Parse(time.RFC3339Nano, value)
	if err != nil {
		return time.Time{}, false
	}
	return parsed.UTC(), true
}

func countMissing(items []Item) int {
	count := 0
	for _, item := range items {
		if item.Missing || item.LibraryState == StatePartial {
			count++
		}
	}
	return count
}
