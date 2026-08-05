package library

import (
	"context"
	"errors"
	"sync"
	"time"
)

type JellyfinRepository struct {
	source   JellyfinSource
	cacheTTL time.Duration
	now      func() time.Time

	mu          sync.Mutex
	cachedItems []Item
	cachedState JellyfinStatus
	expiresAt   time.Time
}

func NewJellyfinRepository(source JellyfinSource, cacheTTL time.Duration) (*JellyfinRepository, error) {
	if source == nil || cacheTTL < 0 || cacheTTL > 10*time.Minute {
		return nil, ErrJellyfinInvalidConfig
	}
	return &JellyfinRepository{
		source:   source,
		cacheTTL: cacheTTL,
		now:      func() time.Time { return time.Now().UTC() },
	}, nil
}

func (repository *JellyfinRepository) List(ctx context.Context, view View) (Response, error) {
	if _, err := ParseView(string(view)); err != nil {
		return Response{}, err
	}
	items, status, err := repository.snapshot(ctx)
	if err != nil {
		return Response{}, err
	}
	filtered := make([]Item, 0, len(items))
	for _, item := range items {
		if includeInView(item, view) {
			filtered = append(filtered, item)
		}
	}
	sortItems(filtered, view)
	generatedAt := repository.now()
	if status.LastSyncedAt != nil {
		generatedAt = status.LastSyncedAt.UTC()
	}
	return Response{
		OK:          true,
		Success:     true,
		Code:        "ok",
		View:        view,
		GeneratedAt: generatedAt,
		Items:       cloneItems(filtered),
		Summary:     summarize(items),
		Jellyfin:    status,
	}, nil
}

func (repository *JellyfinRepository) snapshot(ctx context.Context) ([]Item, JellyfinStatus, error) {
	if err := ctx.Err(); err != nil {
		return nil, JellyfinStatus{}, err
	}
	repository.mu.Lock()
	defer repository.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return nil, JellyfinStatus{}, err
	}
	if len(repository.cachedItems) > 0 && repository.now().Before(repository.expiresAt) {
		return cloneItems(repository.cachedItems), repository.cachedState, nil
	}

	items, status, err := repository.source.Snapshot(ctx)
	if err != nil {
		return nil, JellyfinStatus{}, err
	}
	if !status.Configured || status.Mode != JellyfinConnected || status.LastSyncedAt == nil {
		return nil, JellyfinStatus{}, ErrJellyfinInvalidResponse
	}
	seen := make(map[string]struct{}, len(items))
	for _, item := range items {
		if err := item.Validate(); err != nil {
			return nil, JellyfinStatus{}, errors.Join(ErrJellyfinInvalidResponse, err)
		}
		if _, exists := seen[item.ItemID]; exists {
			return nil, JellyfinStatus{}, ErrJellyfinInvalidResponse
		}
		seen[item.ItemID] = struct{}{}
	}

	repository.cachedItems = cloneItems(items)
	repository.cachedState = status
	repository.expiresAt = repository.now().Add(repository.cacheTTL)
	return cloneItems(items), status, nil
}
