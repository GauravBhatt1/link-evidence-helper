// Package contracts contains public wire representations validated against
// packages/contracts/schema. The JSON Schemas remain the canonical source.
package contracts

import (
	"encoding/json"
	"time"
)

type SourceCandidate struct {
	SourceID          string `json:"sourceId"`
	DisplayName       string `json:"displayName"`
	Priority          int    `json:"priority"`
	VerificationState string `json:"verificationState"`
}

type ReleaseVariant struct {
	VariantID          string            `json:"variantId"`
	Language           string            `json:"language"`
	AudioVariant       string            `json:"audioVariant"`
	Quality            string            `json:"quality"`
	AvailableQualities []string          `json:"availableQualities"`
	ReleaseType        string            `json:"releaseType"`
	PackType           string            `json:"packType"`
	Season             *int              `json:"season"`
	Episode            *int              `json:"episode"`
	ApproxSize         string            `json:"approxSize"`
	SourceCount        int               `json:"sourceCount"`
	Sources            []SourceCandidate `json:"sources"`
}

type Content struct {
	ContentID       string           `json:"contentId"`
	TMDBID          *string          `json:"tmdbId"`
	Title           string           `json:"title"`
	Year            string           `json:"year"`
	MediaType       string           `json:"mediaType"`
	Poster          string           `json:"poster"`
	Languages       []string         `json:"languages"`
	ReleaseVariants []ReleaseVariant `json:"releaseVariants"`
	TotalSources    int              `json:"totalSources"`
	JellyfinStatus  string           `json:"jellyfinStatus"`
}

type PartialFailure struct {
	SourceID string `json:"sourceId"`
	Reason   string `json:"reason"`
}

type SearchResponse struct {
	OK              bool             `json:"ok"`
	Success         bool             `json:"success"`
	Code            string           `json:"code"`
	Query           string           `json:"query"`
	Contents        []Content        `json:"contents"`
	PartialFailures []PartialFailure `json:"partialFailures"`
}

type LibraryItemJellyfin struct {
	Configured   bool       `json:"configured"`
	Present      bool       `json:"present"`
	ItemID       *string    `json:"itemId"`
	ServerID     *string    `json:"serverId"`
	LastSyncedAt *time.Time `json:"lastSyncedAt"`
}

type LibraryItem struct {
	ItemID       string               `json:"itemId"`
	ContentID    *string              `json:"contentId"`
	TMDBID       *string              `json:"tmdbId"`
	Title        string               `json:"title"`
	Year         *int                 `json:"year"`
	MediaType    string               `json:"mediaType"`
	Season       *int                 `json:"season"`
	Episode      *int                 `json:"episode"`
	Poster       *string              `json:"poster"`
	LibraryState string               `json:"libraryState"`
	Missing      bool                 `json:"missing"`
	DateAdded    time.Time            `json:"dateAdded"`
	UpdatedAt    time.Time            `json:"updatedAt"`
	Jellyfin     LibraryItemJellyfin `json:"jellyfin"`
}

type LibrarySummary struct {
	Total   int `json:"total"`
	Movies  int `json:"movies"`
	TV      int `json:"tv"`
	Missing int `json:"missing"`
}

type LibraryJellyfinStatus struct {
	Configured   bool       `json:"configured"`
	Mode         string     `json:"mode"`
	LastSyncedAt *time.Time `json:"lastSyncedAt"`
}

type LibraryResponse struct {
	OK          bool                   `json:"ok"`
	Success     bool                   `json:"success"`
	Code        string                 `json:"code"`
	View        string                 `json:"view"`
	GeneratedAt time.Time              `json:"generatedAt"`
	Items       []LibraryItem          `json:"items"`
	Summary     LibrarySummary         `json:"summary"`
	Jellyfin    LibraryJellyfinStatus `json:"jellyfin"`
}

type ResolutionRequest struct {
	ContentID string  `json:"contentId"`
	VariantID string  `json:"variantId"`
	Quality   *string `json:"quality,omitempty"`
}

type DeliveryLink struct {
	URL        string    `json:"url"`
	Filename   string    `json:"filename"`
	Size       string    `json:"size"`
	Quality    string    `json:"quality"`
	SourceID   string    `json:"sourceId"`
	VerifiedAt time.Time `json:"verifiedAt"`
}

type ResolutionAttempt struct {
	SourceID      string  `json:"sourceId"`
	Status        string  `json:"status"`
	FailureReason *string `json:"failureReason"`
	DurationMS    int     `json:"durationMs"`
}

type ResolutionResult struct {
	OK            bool                `json:"ok"`
	Success       bool                `json:"success"`
	Code          string              `json:"code"`
	Status        string              `json:"status"`
	ContentID     string              `json:"contentId"`
	VariantID     string              `json:"variantId"`
	DeliveryLinks []DeliveryLink      `json:"deliveryLinks"`
	Attempts      []ResolutionAttempt `json:"attempts"`
	Message       string              `json:"message"`
}

type Job struct {
	JobID           string          `json:"jobId"`
	Kind            string          `json:"kind"`
	State           string          `json:"state"`
	SubscriberCount int             `json:"subscriberCount"`
	CreatedAt       time.Time       `json:"createdAt"`
	UpdatedAt       time.Time       `json:"updatedAt"`
	Result          json.RawMessage `json:"result"`
}

type JobEvent struct {
	EventID    string    `json:"eventId"`
	JobID      string    `json:"jobId"`
	State      string    `json:"state"`
	Message    string    `json:"message"`
	OccurredAt time.Time `json:"occurredAt"`
	Progress   int       `json:"progress"`
}

type ErrorResponse struct {
	OK        bool    `json:"ok"`
	Success   bool    `json:"success"`
	Code      string  `json:"code"`
	Error     string  `json:"error"`
	RequestID *string `json:"requestId"`
}
