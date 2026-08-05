package library

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
)

func fixtureRepository(t *testing.T) *FixtureRepository {
	t.Helper()
	repository, err := NewFixtureRepository(filepath.Join("..", "..", "..", "..", "packages", "testing", "fixtures"))
	if err != nil {
		t.Fatalf("NewFixtureRepository() error = %v", err)
	}
	return repository
}

func TestFixtureRepositoryFiltersAndSortsViews(t *testing.T) {
	repository := fixtureRepository(t)
	tests := []struct {
		view      View
		wantCount int
		firstItem string
	}{
		{view: ViewMovies, wantCount: 3, firstItem: "Archive Zero"},
		{view: ViewTV, wantCount: 3, firstItem: "Signal House"},
		{view: ViewMissing, wantCount: 3, firstItem: "Paper City"},
		{view: ViewRecent, wantCount: 6, firstItem: "Horizon Gate"},
	}
	for _, test := range tests {
		t.Run(string(test.view), func(t *testing.T) {
			response, err := repository.List(context.Background(), test.view)
			if err != nil {
				t.Fatalf("List() error = %v", err)
			}
			if response.View != test.view || len(response.Items) != test.wantCount {
				t.Fatalf("List() view/count = %q/%d, want %q/%d", response.View, len(response.Items), test.view, test.wantCount)
			}
			if response.Items[0].Title != test.firstItem {
				t.Fatalf("List() first title = %q, want %q", response.Items[0].Title, test.firstItem)
			}
			if response.Summary != (Summary{Total: 6, Movies: 3, TV: 3, Missing: 3}) {
				t.Fatalf("List() summary = %#v", response.Summary)
			}
			if response.Jellyfin.Configured || response.Jellyfin.Mode != JellyfinDisabled {
				t.Fatalf("List() Jellyfin status = %#v", response.Jellyfin)
			}
		})
	}
}

func TestFixtureRepositoryRejectsInvalidViewAndCancellation(t *testing.T) {
	repository := fixtureRepository(t)
	if _, err := repository.List(context.Background(), View("all")); !errors.Is(err, ErrInvalidView) {
		t.Fatalf("List() error = %v, want ErrInvalidView", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := repository.List(ctx, ViewMovies); !errors.Is(err, context.Canceled) {
		t.Fatalf("List() cancellation error = %v", err)
	}
}

func TestDisabledJellyfinSourceDoesNotExposeData(t *testing.T) {
	items, status, err := (DisabledJellyfinSource{}).Snapshot(context.Background())
	if !errors.Is(err, ErrJellyfinNotConfigured) {
		t.Fatalf("Snapshot() error = %v", err)
	}
	if len(items) != 0 || status.Configured || status.Mode != JellyfinDisabled || status.LastSyncedAt != nil {
		t.Fatalf("Snapshot() returned data: items=%v status=%#v", items, status)
	}
}
