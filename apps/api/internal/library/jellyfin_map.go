package library

import (
	"strings"
	"time"
)

func mapJellyfinItem(raw jellyfinItem, syncedAt time.Time) (Item, bool, error) {
	mediaType, supported := mapJellyfinMediaType(raw.Type)
	if !supported {
		return Item{}, false, nil
	}
	id := strings.TrimSpace(raw.ID)
	title := strings.TrimSpace(raw.Name)
	if id == "" || title == "" || len(id) > 128 || len(title) > 300 {
		return Item{}, false, ErrJellyfinInvalidResponse
	}

	var season, episode *int
	switch mediaType {
	case MediaSeason:
		season = boundedNumber(raw.IndexNumber)
		if season == nil {
			return Item{}, false, ErrJellyfinInvalidResponse
		}
	case MediaEpisode:
		season = boundedNumber(raw.ParentIndexNumber)
		episode = boundedNumber(raw.IndexNumber)
		if season == nil || episode == nil {
			return Item{}, false, ErrJellyfinInvalidResponse
		}
	}

	dateAdded := parseJellyfinTime(raw.DateCreated, syncedAt)
	updatedAt := syncedAt
	if updatedAt.Before(dateAdded) {
		updatedAt = dateAdded
	}
	tmdbID := providerID(raw.ProviderIDs, "tmdb")
	contentID := "jellyfin:" + id
	if tmdbID != nil {
		contentID = "tmdb:" + string(mediaType) + ":" + *tmdbID
	}
	item := Item{
		ItemID:       "jellyfin:" + id,
		ContentID:    stringPointer(contentID),
		TMDBID:       tmdbID,
		Title:        title,
		Year:         boundedYear(raw.ProductionYear),
		MediaType:    mediaType,
		Season:       season,
		Episode:      episode,
		Poster:       nil,
		LibraryState: StateAvailable,
		Missing:      false,
		DateAdded:    dateAdded,
		UpdatedAt:    updatedAt,
		Jellyfin: JellyfinRef{
			Configured:   true,
			Present:      true,
			ItemID:       stringPointer(id),
			ServerID:     optionalString(raw.ServerID, 128),
			LastSyncedAt: timePointer(syncedAt),
		},
	}
	if err := item.Validate(); err != nil {
		return Item{}, false, ErrJellyfinInvalidResponse
	}
	return item, true, nil
}

func mapJellyfinMediaType(value string) (MediaType, bool) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "movie":
		return MediaMovie, true
	case "series":
		return MediaSeries, true
	case "season":
		return MediaSeason, true
	case "episode":
		return MediaEpisode, true
	default:
		return "", false
	}
}

func boundedNumber(value *int) *int {
	if value == nil || *value < 0 || *value > 10000 {
		return nil
	}
	copy := *value
	return &copy
}

func boundedYear(value *int) *int {
	if value == nil || *value < 1874 || *value > 2200 {
		return nil
	}
	copy := *value
	return &copy
}

func parseJellyfinTime(value string, fallback time.Time) time.Time {
	parsed, err := time.Parse(time.RFC3339Nano, strings.TrimSpace(value))
	if err != nil || parsed.IsZero() {
		return fallback
	}
	return parsed.UTC()
}

func providerID(values map[string]string, wanted string) *string {
	for key, raw := range values {
		if !strings.EqualFold(strings.TrimSpace(key), wanted) {
			continue
		}
		value := strings.TrimSpace(raw)
		if value == "" || len(value) > 128 {
			return nil
		}
		for _, character := range value {
			if character < '0' || character > '9' {
				return nil
			}
		}
		return stringPointer(value)
	}
	return nil
}

func optionalString(value string, maximum int) *string {
	trimmed := strings.TrimSpace(value)
	if trimmed == "" || len(trimmed) > maximum {
		return nil
	}
	return stringPointer(trimmed)
}

func stringPointer(value string) *string {
	copy := value
	return &copy
}

func timePointer(value time.Time) *time.Time {
	copy := value
	return &copy
}
