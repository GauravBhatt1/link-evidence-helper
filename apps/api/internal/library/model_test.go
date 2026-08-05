package library

import (
	"testing"
	"time"
)

func TestItemValidateAcceptsMovieWithoutJellyfinConfiguration(t *testing.T) {
	now := time.Date(2026, 8, 5, 12, 0, 0, 0, time.UTC)
	item := Item{
		ItemID:       "movie:550",
		Title:        "Example Movie",
		MediaType:    MediaMovie,
		LibraryState: StateMissing,
		Missing:      true,
		DateAdded:    now,
		UpdatedAt:    now,
		Jellyfin:     JellyfinRef{},
	}

	if err := item.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
}

func TestItemValidateRejectsEpisodeWithoutCoordinates(t *testing.T) {
	now := time.Now().UTC()
	item := Item{
		ItemID:       "episode:1",
		Title:        "Episode",
		MediaType:    MediaEpisode,
		LibraryState: StateAvailable,
		DateAdded:    now,
		UpdatedAt:    now,
	}

	if err := item.Validate(); err == nil {
		t.Fatal("Validate() expected error")
	}
}

func TestItemValidateRejectsUnconfiguredJellyfinData(t *testing.T) {
	now := time.Now().UTC()
	itemID := "jf-item"
	item := Item{
		ItemID:       "movie:1",
		Title:        "Movie",
		MediaType:    MediaMovie,
		LibraryState: StateAvailable,
		DateAdded:    now,
		UpdatedAt:    now,
		Jellyfin: JellyfinRef{
			Configured: false,
			Present:    true,
			ItemID:     &itemID,
		},
	}

	if err := item.Validate(); err == nil {
		t.Fatal("Validate() expected error")
	}
}

func TestItemValidateRejectsInconsistentMissingState(t *testing.T) {
	now := time.Now().UTC()
	item := Item{
		ItemID:       "movie:2",
		Title:        "Movie",
		MediaType:    MediaMovie,
		LibraryState: StateAvailable,
		Missing:      true,
		DateAdded:    now,
		UpdatedAt:    now,
	}

	if err := item.Validate(); err == nil {
		t.Fatal("Validate() expected error")
	}
}
