package library

import (
	"context"
	"sync/atomic"
	"testing"
	"time"
)

type countingJellyfinSource struct {
	calls  atomic.Int32
	items  []Item
	status JellyfinStatus
	err    error
}

func (source *countingJellyfinSource) Snapshot(context.Context) ([]Item, JellyfinStatus, error) {
	source.calls.Add(1)
	return cloneItems(source.items), source.status, source.err
}

func TestJellyfinRepositoryFiltersAndCachesSnapshot(t *testing.T) {
	now := time.Date(2026, 8, 5, 15, 0, 0, 0, time.UTC)
	movieID := "movie-1"
	seriesID := "series-1"
	source := &countingJellyfinSource{
		items: []Item{
			{
				ItemID: "jellyfin:movie-1", ContentID: &movieID, Title: "Movie", MediaType: MediaMovie,
				LibraryState: StateAvailable, DateAdded: now.Add(-time.Hour), UpdatedAt: now,
				Jellyfin: JellyfinRef{Configured: true, Present: true, ItemID: &movieID, LastSyncedAt: &now},
			},
			{
				ItemID: "jellyfin:series-1", ContentID: &seriesID, Title: "Series", MediaType: MediaSeries,
				LibraryState: StateAvailable, DateAdded: now.Add(-2 * time.Hour), UpdatedAt: now,
				Jellyfin: JellyfinRef{Configured: true, Present: true, ItemID: &seriesID, LastSyncedAt: &now},
			},
		},
		status: JellyfinStatus{Configured: true, Mode: JellyfinConnected, LastSyncedAt: &now},
	}
	repository, err := NewJellyfinRepository(source, time.Minute)
	if err != nil {
		t.Fatalf("NewJellyfinRepository() error = %v", err)
	}
	repository.now = func() time.Time { return now }

	movies, err := repository.List(context.Background(), ViewMovies)
	if err != nil {
		t.Fatalf("List(movies) error = %v", err)
	}
	tv, err := repository.List(context.Background(), ViewTV)
	if err != nil {
		t.Fatalf("List(tv) error = %v", err)
	}
	missing, err := repository.List(context.Background(), ViewMissing)
	if err != nil {
		t.Fatalf("List(missing) error = %v", err)
	}
	if source.calls.Load() != 1 {
		t.Fatalf("Snapshot calls = %d, want 1", source.calls.Load())
	}
	if len(movies.Items) != 1 || movies.Items[0].MediaType != MediaMovie {
		t.Fatalf("movies = %#v", movies.Items)
	}
	if len(tv.Items) != 1 || tv.Items[0].MediaType != MediaSeries {
		t.Fatalf("tv = %#v", tv.Items)
	}
	if len(missing.Items) != 0 {
		t.Fatalf("missing = %#v", missing.Items)
	}
	if movies.Summary != (Summary{Total: 2, Movies: 1, TV: 1, Missing: 0}) {
		t.Fatalf("summary = %#v", movies.Summary)
	}
	if !movies.Jellyfin.Configured || movies.Jellyfin.Mode != JellyfinConnected {
		t.Fatalf("Jellyfin status = %#v", movies.Jellyfin)
	}
}

func TestJellyfinRepositoryCachesEmptySnapshot(t *testing.T) {
	now := time.Date(2026, 8, 5, 15, 0, 0, 0, time.UTC)
	source := &countingJellyfinSource{
		status: JellyfinStatus{Configured: true, Mode: JellyfinConnected, LastSyncedAt: &now},
	}
	repository, err := NewJellyfinRepository(source, time.Minute)
	if err != nil {
		t.Fatalf("NewJellyfinRepository() error = %v", err)
	}
	repository.now = func() time.Time { return now }

	for _, view := range []View{ViewMovies, ViewTV, ViewRecent} {
		response, err := repository.List(context.Background(), view)
		if err != nil {
			t.Fatalf("List(%s) error = %v", view, err)
		}
		if len(response.Items) != 0 || response.Summary.Total != 0 {
			t.Fatalf("List(%s) response = %#v", view, response)
		}
	}
	if source.calls.Load() != 1 {
		t.Fatalf("Snapshot calls = %d, want 1", source.calls.Load())
	}
}

func TestJellyfinRepositoryRejectsInvalidSnapshots(t *testing.T) {
	now := time.Date(2026, 8, 5, 15, 0, 0, 0, time.UTC)
	tests := []struct {
		name   string
		items  []Item
		status JellyfinStatus
	}{
		{name: "unconfigured status", status: JellyfinStatus{Mode: JellyfinDisabled}},
		{
			name: "duplicate items",
			items: []Item{
				{ItemID: "same", Title: "One", MediaType: MediaMovie, LibraryState: StateAvailable, DateAdded: now, UpdatedAt: now, Jellyfin: JellyfinRef{Configured: true}},
				{ItemID: "same", Title: "Two", MediaType: MediaMovie, LibraryState: StateAvailable, DateAdded: now, UpdatedAt: now, Jellyfin: JellyfinRef{Configured: true}},
			},
			status: JellyfinStatus{Configured: true, Mode: JellyfinConnected, LastSyncedAt: &now},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			repository, err := NewJellyfinRepository(&countingJellyfinSource{items: test.items, status: test.status}, 0)
			if err != nil {
				t.Fatalf("NewJellyfinRepository() error = %v", err)
			}
			if _, err := repository.List(context.Background(), ViewRecent); err == nil {
				t.Fatal("List() unexpectedly succeeded")
			}
		})
	}
}
