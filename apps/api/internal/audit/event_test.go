package audit

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func TestNewEventAcceptsAllowlistedSecretSafeValues(t *testing.T) {
	event, err := NewEvent(
		"evt-12345678",
		"req-12345678",
		"admin",
		"source.update",
		"source:catalog-primary",
		"success",
		time.Date(2026, 8, 5, 12, 0, 0, 0, time.FixedZone("offset", 3600)),
	)
	if err != nil {
		t.Fatalf("NewEvent() error = %v", err)
	}
	if event.Occurred.Location() != time.UTC {
		t.Fatalf("event time location = %v", event.Occurred.Location())
	}
	encoded, err := json.Marshal(event)
	if err != nil {
		t.Fatalf("json.Marshal() error = %v", err)
	}
	text := string(encoded)
	for _, forbidden := range []string{"authorization", "cookie", "token", "password", "url", "header", "body", "metadata"} {
		if strings.Contains(strings.ToLower(text), forbidden) {
			t.Fatalf("serialized event unexpectedly contains %q: %s", forbidden, text)
		}
	}
}

func TestNewEventRejectsUnboundedOrSensitiveInputs(t *testing.T) {
	validTime := time.Now()
	tests := []struct {
		name      string
		id        string
		requestID string
		actor     string
		action    string
		resource  string
		outcome   string
		occurred  time.Time
	}{
		{"short id", "x", "req-12345678", "admin", "source.update", "system", "success", validTime},
		{"unsafe request", "evt-12345678", "Bearer secret", "admin", "source.update", "system", "success", validTime},
		{"unknown actor", "evt-12345678", "req-12345678", "root", "source.update", "system", "success", validTime},
		{"unknown action", "evt-12345678", "req-12345678", "admin", "secret.export", "system", "success", validTime},
		{"url resource", "evt-12345678", "req-12345678", "admin", "source.update", "https://example.test/?token=secret", "success", validTime},
		{"unknown outcome", "evt-12345678", "req-12345678", "admin", "source.update", "system", "maybe", validTime},
		{"zero time", "evt-12345678", "req-12345678", "admin", "source.update", "system", "success", time.Time{}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := NewEvent(test.id, test.requestID, test.actor, test.action, test.resource, test.outcome, test.occurred); err == nil {
				t.Fatal("NewEvent() unexpectedly succeeded")
			}
		})
	}
}
