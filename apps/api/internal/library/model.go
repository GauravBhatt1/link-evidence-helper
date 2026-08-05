// Package library defines the durable boundary for Movies, TV, Missing,
// Recently Added, and future Jellyfin-backed synchronization.
package library

import (
	"errors"
	"strings"
	"time"
)

type MediaType string

const (
	MediaMovie   MediaType = "movie"
	MediaSeries  MediaType = "series"
	MediaSeason  MediaType = "season"
	MediaEpisode MediaType = "episode"
)

type State string

const (
	StateAvailable State = "available"
	StateMissing   State = "missing"
	StatePartial   State = "partial"
	StateUnknown   State = "unknown"
)

type JellyfinRef struct {
	Configured   bool       `json:"configured"`
	Present      bool       `json:"present"`
	ItemID       *string    `json:"itemId"`
	ServerID     *string    `json:"serverId"`
	LastSyncedAt *time.Time `json:"lastSyncedAt"`
}

type Item struct {
	ItemID       string      `json:"itemId"`
	ContentID    *string     `json:"contentId"`
	TMDBID       *string     `json:"tmdbId"`
	Title        string      `json:"title"`
	Year         *int        `json:"year"`
	MediaType    MediaType   `json:"mediaType"`
	Season       *int        `json:"season"`
	Episode      *int        `json:"episode"`
	Poster       *string     `json:"poster"`
	LibraryState State       `json:"libraryState"`
	Missing      bool        `json:"missing"`
	DateAdded    time.Time   `json:"dateAdded"`
	UpdatedAt    time.Time   `json:"updatedAt"`
	Jellyfin     JellyfinRef `json:"jellyfin"`
}

func (i Item) Validate() error {
	if strings.TrimSpace(i.ItemID) == "" {
		return errors.New("itemId is required")
	}
	if strings.TrimSpace(i.Title) == "" {
		return errors.New("title is required")
	}
	if !validMediaType(i.MediaType) {
		return errors.New("unsupported mediaType")
	}
	if !validState(i.LibraryState) {
		return errors.New("unsupported libraryState")
	}
	if i.DateAdded.IsZero() || i.UpdatedAt.IsZero() {
		return errors.New("dateAdded and updatedAt are required")
	}
	if i.UpdatedAt.Before(i.DateAdded) {
		return errors.New("updatedAt cannot be before dateAdded")
	}
	if i.Missing != (i.LibraryState == StateMissing) && i.LibraryState != StatePartial && i.LibraryState != StateUnknown {
		return errors.New("missing must agree with libraryState")
	}
	if i.MediaType == MediaEpisode && (i.Season == nil || i.Episode == nil) {
		return errors.New("episode items require season and episode")
	}
	if i.MediaType == MediaSeason && i.Season == nil {
		return errors.New("season items require season")
	}
	if !i.Jellyfin.Configured && (i.Jellyfin.Present || i.Jellyfin.ItemID != nil || i.Jellyfin.ServerID != nil || i.Jellyfin.LastSyncedAt != nil) {
		return errors.New("unconfigured Jellyfin reference must not contain server data")
	}
	if i.Jellyfin.Present && i.Jellyfin.ItemID == nil {
		return errors.New("present Jellyfin item requires itemId")
	}
	return nil
}

func validMediaType(value MediaType) bool {
	switch value {
	case MediaMovie, MediaSeries, MediaSeason, MediaEpisode:
		return true
	default:
		return false
	}
}

func validState(value State) bool {
	switch value {
	case StateAvailable, StateMissing, StatePartial, StateUnknown:
		return true
	default:
		return false
	}
}
