package library

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"
)

type JellyfinClient struct {
	baseURL          *url.URL
	apiKey           string
	libraryIDs       []string
	allowPrivate     bool
	pageSize         int
	maxItems         int
	maxResponseBytes int64
	resolver         IPResolver
	dialer           *net.Dialer
	now              func() time.Time
	httpClient       *http.Client
}

type jellyfinItemsResponse struct {
	Items            []jellyfinItem `json:"Items"`
	TotalRecordCount int             `json:"TotalRecordCount"`
	StartIndex       int             `json:"StartIndex"`
}

type jellyfinItem struct {
	ID                string            `json:"Id"`
	ServerID          string            `json:"ServerId"`
	Name              string            `json:"Name"`
	Type              string            `json:"Type"`
	ProductionYear    *int              `json:"ProductionYear"`
	IndexNumber       *int              `json:"IndexNumber"`
	ParentIndexNumber *int              `json:"ParentIndexNumber"`
	DateCreated       string            `json:"DateCreated"`
	ProviderIDs       map[string]string `json:"ProviderIds"`
}

func NewJellyfinClient(config JellyfinConfig) (*JellyfinClient, error) {
	baseURL, err := validateJellyfinConfig(&config)
	if err != nil {
		return nil, err
	}
	client := &JellyfinClient{
		baseURL:          baseURL,
		apiKey:           strings.TrimSpace(config.APIKey),
		libraryIDs:       append([]string(nil), config.LibraryIDs...),
		allowPrivate:     config.AllowPrivate,
		pageSize:         config.PageSize,
		maxItems:         config.MaxItems,
		maxResponseBytes: config.MaxResponseBytes,
		resolver:         config.Resolver,
		dialer:           config.Dialer,
		now:              config.Now,
	}
	transport := &http.Transport{
		Proxy:                 nil,
		DialContext:           client.dialContext,
		ForceAttemptHTTP2:     true,
		MaxIdleConns:          4,
		MaxIdleConnsPerHost:   4,
		IdleConnTimeout:       30 * time.Second,
		TLSHandshakeTimeout:   5 * time.Second,
		ResponseHeaderTimeout: config.Timeout,
		ExpectContinueTimeout: time.Second,
		DisableCompression:    true,
	}
	client.httpClient = &http.Client{
		Transport: transport,
		Timeout:   config.Timeout,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return errors.New("Jellyfin redirects are disabled")
		},
	}
	return client, nil
}

func (client *JellyfinClient) Snapshot(ctx context.Context) ([]Item, JellyfinStatus, error) {
	if err := ctx.Err(); err != nil {
		return nil, JellyfinStatus{}, err
	}
	parents := client.libraryIDs
	if len(parents) == 0 {
		parents = []string{""}
	}

	syncedAt := client.now().UTC()
	itemsByID := make(map[string]Item)
	for _, parentID := range parents {
		startIndex := 0
		for {
			page, err := client.fetchItemsPage(ctx, parentID, startIndex)
			if err != nil {
				return nil, JellyfinStatus{}, err
			}
			for _, raw := range page.Items {
				item, supported, err := mapJellyfinItem(raw, syncedAt)
				if err != nil {
					return nil, JellyfinStatus{}, err
				}
				if !supported {
					continue
				}
				itemsByID[item.ItemID] = item
				if len(itemsByID) > client.maxItems {
					return nil, JellyfinStatus{}, ErrJellyfinTooManyItems
				}
			}

			count := len(page.Items)
			startIndex += count
			if count == 0 || count < client.pageSize || startIndex >= page.TotalRecordCount {
				break
			}
		}
	}

	items := make([]Item, 0, len(itemsByID))
	for _, item := range itemsByID {
		items = append(items, item)
	}
	sort.Slice(items, func(left, right int) bool {
		if !items[left].DateAdded.Equal(items[right].DateAdded) {
			return items[left].DateAdded.After(items[right].DateAdded)
		}
		return items[left].ItemID < items[right].ItemID
	})
	return items, JellyfinStatus{
		Configured:   true,
		Mode:         JellyfinConnected,
		LastSyncedAt: timePointer(syncedAt),
	}, nil
}

func (client *JellyfinClient) fetchItemsPage(ctx context.Context, parentID string, startIndex int) (jellyfinItemsResponse, error) {
	target := *client.baseURL
	target.Path = strings.TrimSuffix(client.baseURL.Path, "/") + "/Items"
	query := target.Query()
	query.Set("Recursive", "true")
	query.Set("IncludeItemTypes", "Movie,Series,Season,Episode")
	query.Set("Fields", "ProviderIds,DateCreated")
	query.Set("EnableImages", "false")
	query.Set("EnableUserData", "false")
	query.Set("EnableTotalRecordCount", "true")
	query.Set("SortBy", "DateCreated,SortName")
	query.Set("SortOrder", "Descending")
	query.Set("StartIndex", strconv.Itoa(startIndex))
	query.Set("Limit", strconv.Itoa(client.pageSize))
	if parentID != "" {
		query.Set("ParentId", parentID)
	}
	target.RawQuery = query.Encode()

	request, err := http.NewRequestWithContext(ctx, http.MethodGet, target.String(), nil)
	if err != nil {
		return jellyfinItemsResponse{}, ErrJellyfinInvalidConfig
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("X-Emby-Token", client.apiKey)
	request.Header.Set("User-Agent", "FREEMIUM-INDEX/1.0")

	response, err := client.httpClient.Do(request)
	if err != nil {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return jellyfinItemsResponse{}, ctxErr
		}
		if errors.Is(err, ErrJellyfinUnsafeTarget) {
			return jellyfinItemsResponse{}, ErrJellyfinUnsafeTarget
		}
		return jellyfinItemsResponse{}, ErrJellyfinUnavailable
	}
	defer response.Body.Close()

	switch response.StatusCode {
	case http.StatusOK:
	case http.StatusUnauthorized, http.StatusForbidden:
		return jellyfinItemsResponse{}, ErrJellyfinUnauthorized
	default:
		return jellyfinItemsResponse{}, ErrJellyfinUnavailable
	}
	mediaType, _, err := mime.ParseMediaType(response.Header.Get("Content-Type"))
	if err != nil || mediaType != "application/json" {
		return jellyfinItemsResponse{}, ErrJellyfinInvalidResponse
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, client.maxResponseBytes+1))
	if err != nil {
		return jellyfinItemsResponse{}, ErrJellyfinUnavailable
	}
	if int64(len(body)) > client.maxResponseBytes {
		return jellyfinItemsResponse{}, ErrJellyfinInvalidResponse
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	var payload jellyfinItemsResponse
	if err := decoder.Decode(&payload); err != nil {
		return jellyfinItemsResponse{}, ErrJellyfinInvalidResponse
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		return jellyfinItemsResponse{}, ErrJellyfinInvalidResponse
	}
	if payload.TotalRecordCount < 0 || payload.StartIndex != startIndex || len(payload.Items) > client.pageSize {
		return jellyfinItemsResponse{}, ErrJellyfinInvalidResponse
	}
	if payload.TotalRecordCount > client.maxItems {
		return jellyfinItemsResponse{}, ErrJellyfinTooManyItems
	}
	if payload.TotalRecordCount == 0 && len(payload.Items) > 0 {
		return jellyfinItemsResponse{}, ErrJellyfinInvalidResponse
	}
	if startIndex+len(payload.Items) > payload.TotalRecordCount {
		return jellyfinItemsResponse{}, ErrJellyfinInvalidResponse
	}
	return payload, nil
}
