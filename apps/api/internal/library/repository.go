package library

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const maxFixtureBytes = 2 << 20

var (
	ErrInvalidView           = errors.New("invalid library view")
	ErrJellyfinNotConfigured = errors.New("jellyfin is not configured")
	ErrInvalidLibraryFixture = errors.New("invalid library fixture")
)

type View string

const (
	ViewMovies  View = "movies"
	ViewTV      View = "tv"
	ViewMissing View = "missing"
	ViewRecent  View = "recent"
)

type Summary struct {
	Total   int `json:"total"`
	Movies  int `json:"movies"`
	TV      int `json:"tv"`
	Missing int `json:"missing"`
}

type JellyfinMode string

const (
	JellyfinDisabled  JellyfinMode = "disabled"
	JellyfinFixture   JellyfinMode = "fixture"
	JellyfinConnected JellyfinMode = "connected"
)

type JellyfinStatus struct {
	Configured   bool         `json:"configured"`
	Mode         JellyfinMode `json:"mode"`
	LastSyncedAt *time.Time   `json:"lastSyncedAt"`
}

type Response struct {
	OK          bool           `json:"ok"`
	Success     bool           `json:"success"`
	Code        string         `json:"code"`
	View        View           `json:"view"`
	GeneratedAt time.Time      `json:"generatedAt"`
	Items       []Item         `json:"items"`
	Summary     Summary        `json:"summary"`
	Jellyfin    JellyfinStatus `json:"jellyfin"`
}

type Repository interface {
	List(ctx context.Context, view View) (Response, error)
}

// JellyfinSource is the credential-free boundary used by the library service.
// Implementations receive credentials through runtime configuration, never
// through public contracts, fixtures, or Git history.
type JellyfinSource interface {
	Snapshot(ctx context.Context) ([]Item, JellyfinStatus, error)
}

type DisabledJellyfinSource struct{}

func (DisabledJellyfinSource) Snapshot(context.Context) ([]Item, JellyfinStatus, error) {
	return nil, JellyfinStatus{Configured: false, Mode: JellyfinDisabled}, ErrJellyfinNotConfigured
}

type FixtureRepository struct {
	generatedAt time.Time
	items       []Item
	summary     Summary
	jellyfin    JellyfinStatus
}

func ParseView(value string) (View, error) {
	switch View(strings.TrimSpace(value)) {
	case ViewMovies:
		return ViewMovies, nil
	case ViewTV:
		return ViewTV, nil
	case ViewMissing:
		return ViewMissing, nil
	case ViewRecent:
		return ViewRecent, nil
	default:
		return "", ErrInvalidView
	}
}

func NewFixtureRepository(fixtureDir string) (*FixtureRepository, error) {
	path := filepath.Join(fixtureDir, "library-response.json")
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read library fixture: %w", err)
	}
	if len(data) == 0 || len(data) > maxFixtureBytes {
		return nil, fmt.Errorf("%w: fixture size", ErrInvalidLibraryFixture)
	}

	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	var fixture Response
	if err := decoder.Decode(&fixture); err != nil {
		return nil, fmt.Errorf("%w: %v", ErrInvalidLibraryFixture, err)
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		if err == nil {
			return nil, fmt.Errorf("%w: multiple JSON values", ErrInvalidLibraryFixture)
		}
		return nil, fmt.Errorf("%w: trailing JSON data: %v", ErrInvalidLibraryFixture, err)
	}
	if err := validateFixture(fixture); err != nil {
		return nil, err
	}

	return &FixtureRepository{
		generatedAt: fixture.GeneratedAt,
		items:       cloneItems(fixture.Items),
		summary:     fixture.Summary,
		jellyfin:    fixture.Jellyfin,
	}, nil
}

func (repository *FixtureRepository) List(ctx context.Context, view View) (Response, error) {
	if _, err := ParseView(string(view)); err != nil {
		return Response{}, err
	}
	if err := ctx.Err(); err != nil {
		return Response{}, err
	}

	items := make([]Item, 0, len(repository.items))
	for _, item := range repository.items {
		if includeInView(item, view) {
			items = append(items, item)
		}
	}
	sortItems(items, view)

	return Response{
		OK:          true,
		Success:     true,
		Code:        "ok",
		View:        view,
		GeneratedAt: repository.generatedAt,
		Items:       cloneItems(items),
		Summary:     repository.summary,
		Jellyfin:    repository.jellyfin,
	}, nil
}

func validateFixture(fixture Response) error {
	if !fixture.OK || !fixture.Success || fixture.Code != "ok" {
		return fmt.Errorf("%w: invalid success envelope", ErrInvalidLibraryFixture)
	}
	if _, err := ParseView(string(fixture.View)); err != nil {
		return fmt.Errorf("%w: %v", ErrInvalidLibraryFixture, err)
	}
	if fixture.GeneratedAt.IsZero() || len(fixture.Items) > 5000 {
		return fmt.Errorf("%w: invalid generatedAt or item count", ErrInvalidLibraryFixture)
	}
	if err := validateJellyfinStatus(fixture.Jellyfin); err != nil {
		return fmt.Errorf("%w: %v", ErrInvalidLibraryFixture, err)
	}
	seen := make(map[string]struct{}, len(fixture.Items))
	for _, item := range fixture.Items {
		if err := item.Validate(); err != nil {
			return fmt.Errorf("%w: item %q: %v", ErrInvalidLibraryFixture, item.ItemID, err)
		}
		if _, exists := seen[item.ItemID]; exists {
			return fmt.Errorf("%w: duplicate itemId %q", ErrInvalidLibraryFixture, item.ItemID)
		}
		seen[item.ItemID] = struct{}{}
	}
	if expected := summarize(fixture.Items); expected != fixture.Summary {
		return fmt.Errorf("%w: summary mismatch", ErrInvalidLibraryFixture)
	}
	return nil
}

func validateJellyfinStatus(status JellyfinStatus) error {
	if !status.Configured {
		if status.Mode != JellyfinDisabled || status.LastSyncedAt != nil {
			return errors.New("disabled Jellyfin status contains connected data")
		}
		return nil
	}
	if status.Mode != JellyfinFixture && status.Mode != JellyfinConnected {
		return errors.New("configured Jellyfin status has invalid mode")
	}
	return nil
}

func summarize(items []Item) Summary {
	summary := Summary{Total: len(items)}
	for _, item := range items {
		if item.MediaType == MediaMovie {
			summary.Movies++
		} else {
			summary.TV++
		}
		if item.Missing || item.LibraryState == StatePartial {
			summary.Missing++
		}
	}
	return summary
}

func includeInView(item Item, view View) bool {
	switch view {
	case ViewMovies:
		return item.MediaType == MediaMovie
	case ViewTV:
		return item.MediaType != MediaMovie
	case ViewMissing:
		return item.Missing || item.LibraryState == StatePartial
	case ViewRecent:
		return true
	default:
		return false
	}
}

func sortItems(items []Item, view View) {
	sort.SliceStable(items, func(left, right int) bool {
		if view == ViewRecent && !items[left].DateAdded.Equal(items[right].DateAdded) {
			return items[left].DateAdded.After(items[right].DateAdded)
		}
		leftTitle := strings.ToLower(strings.TrimSpace(items[left].Title))
		rightTitle := strings.ToLower(strings.TrimSpace(items[right].Title))
		if leftTitle != rightTitle {
			return leftTitle < rightTitle
		}
		return items[left].ItemID < items[right].ItemID
	})
}

func cloneItems(items []Item) []Item {
	return append([]Item(nil), items...)
}
