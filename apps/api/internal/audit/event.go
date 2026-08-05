// Package audit defines secret-safe administrative audit events.
package audit

import (
	"errors"
	"strings"
	"time"
	"unicode"
)

var ErrInvalidEvent = errors.New("invalid audit event")

// Event is deliberately small and excludes request bodies, headers, credentials,
// URLs, and arbitrary metadata so secrets cannot accidentally enter durable logs.
type Event struct {
	ID        string    `json:"id"`
	Occurred  time.Time `json:"occurredAt"`
	RequestID string    `json:"requestId"`
	Actor     string    `json:"actor"`
	Action    string    `json:"action"`
	Resource  string    `json:"resource"`
	Outcome   string    `json:"outcome"`
}

// NewEvent validates the allowlisted event vocabulary and returns a normalized event.
func NewEvent(id, requestID, actor, action, resource, outcome string, occurred time.Time) (Event, error) {
	if !safeIdentifier(id, 8, 128) || !safeIdentifier(requestID, 8, 128) {
		return Event{}, ErrInvalidEvent
	}
	if actor != "admin" {
		return Event{}, ErrInvalidEvent
	}
	if !allowed(action, "admin.session.verify", "source.create", "source.update", "source.disable", "diagnostic.run") {
		return Event{}, ErrInvalidEvent
	}
	if !safeResource(resource) {
		return Event{}, ErrInvalidEvent
	}
	if !allowed(outcome, "success", "denied", "failure") {
		return Event{}, ErrInvalidEvent
	}
	if occurred.IsZero() {
		return Event{}, ErrInvalidEvent
	}
	return Event{
		ID:        id,
		Occurred:  occurred.UTC(),
		RequestID: requestID,
		Actor:     actor,
		Action:    action,
		Resource:  resource,
		Outcome:   outcome,
	}, nil
}

func safeIdentifier(value string, minimum, maximum int) bool {
	if len(value) < minimum || len(value) > maximum || strings.TrimSpace(value) != value {
		return false
	}
	for _, character := range value {
		if !(unicode.IsLetter(character) || unicode.IsDigit(character) || strings.ContainsRune("-_:.", character)) {
			return false
		}
	}
	return true
}

func safeResource(value string) bool {
	if value == "system" {
		return true
	}
	if !strings.HasPrefix(value, "source:") {
		return false
	}
	return safeIdentifier(strings.TrimPrefix(value, "source:"), 1, 80)
}

func allowed(value string, options ...string) bool {
	for _, option := range options {
		if value == option {
			return true
		}
	}
	return false
}
