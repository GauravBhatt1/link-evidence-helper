package sourcemanagement

import (
	"errors"
	"sort"
	"strings"
	"sync"
	"time"
)

var (
	ErrInvalidSource = errors.New("invalid source")
	ErrSourceExists  = errors.New("source already exists")
	ErrSourceMissing = errors.New("source not found")
)

type Source struct {
	ID          string    `json:"id"`
	Name        string    `json:"name"`
	Enabled     bool      `json:"enabled"`
	Priority    int       `json:"priority"`
	ConfigRef   string    `json:"configRef"`
	CreatedAt   time.Time `json:"createdAt"`
	UpdatedAt   time.Time `json:"updatedAt"`
	Revision    uint64    `json:"revision"`
}

type CreateInput struct {
	ID        string
	Name      string
	Enabled   bool
	Priority  int
	ConfigRef string
}

type UpdateInput struct {
	Name      *string
	Enabled   *bool
	Priority  *int
	ConfigRef *string
}

type Registry struct {
	mu      sync.RWMutex
	now     func() time.Time
	sources map[string]Source
}

func NewRegistry(now func() time.Time) *Registry {
	if now == nil {
		now = time.Now
	}
	return &Registry{now: now, sources: make(map[string]Source)}
}

func (registry *Registry) Create(input CreateInput) (Source, error) {
	id := strings.TrimSpace(input.ID)
	name := strings.TrimSpace(input.Name)
	configRef := strings.TrimSpace(input.ConfigRef)
	if !validID(id) || name == "" || input.Priority < 0 || configRef == "" {
		return Source{}, ErrInvalidSource
	}

	registry.mu.Lock()
	defer registry.mu.Unlock()
	if _, exists := registry.sources[id]; exists {
		return Source{}, ErrSourceExists
	}
	now := registry.now().UTC()
	source := Source{ID: id, Name: name, Enabled: input.Enabled, Priority: input.Priority, ConfigRef: configRef, CreatedAt: now, UpdatedAt: now, Revision: 1}
	registry.sources[id] = source
	return source, nil
}

func (registry *Registry) Update(id string, expectedRevision uint64, input UpdateInput) (Source, error) {
	registry.mu.Lock()
	defer registry.mu.Unlock()
	source, exists := registry.sources[strings.TrimSpace(id)]
	if !exists {
		return Source{}, ErrSourceMissing
	}
	if expectedRevision == 0 || source.Revision != expectedRevision {
		return Source{}, ErrInvalidSource
	}
	if input.Name != nil {
		name := strings.TrimSpace(*input.Name)
		if name == "" {
			return Source{}, ErrInvalidSource
		}
		source.Name = name
	}
	if input.Enabled != nil {
		source.Enabled = *input.Enabled
	}
	if input.Priority != nil {
		if *input.Priority < 0 {
			return Source{}, ErrInvalidSource
		}
		source.Priority = *input.Priority
	}
	if input.ConfigRef != nil {
		configRef := strings.TrimSpace(*input.ConfigRef)
		if configRef == "" {
			return Source{}, ErrInvalidSource
		}
		source.ConfigRef = configRef
	}
	source.Revision++
	source.UpdatedAt = registry.now().UTC()
	registry.sources[source.ID] = source
	return source, nil
}

func (registry *Registry) Get(id string) (Source, bool) {
	registry.mu.RLock()
	defer registry.mu.RUnlock()
	source, exists := registry.sources[strings.TrimSpace(id)]
	return source, exists
}

func (registry *Registry) List() []Source {
	registry.mu.RLock()
	defer registry.mu.RUnlock()
	result := make([]Source, 0, len(registry.sources))
	for _, source := range registry.sources {
		result = append(result, source)
	}
	sort.Slice(result, func(left, right int) bool {
		if result[left].Priority == result[right].Priority {
			return result[left].ID < result[right].ID
		}
		return result[left].Priority < result[right].Priority
	})
	return result
}

func validID(value string) bool {
	if value == "" || len(value) > 64 {
		return false
	}
	for _, character := range value {
		if (character >= 'a' && character <= 'z') || (character >= '0' && character <= '9') || character == '-' {
			continue
		}
		return false
	}
	return true
}
