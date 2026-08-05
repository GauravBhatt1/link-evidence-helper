package diagnostics

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func TestNewSnapshotNormalizesAndSorts(t *testing.T) {
	now := time.Date(2026, 8, 6, 1, 2, 3, 0, time.FixedZone("test", 5*60*60+30*60))
	snapshot, err := NewSnapshot(now, []Check{
		{Component: ComponentSearch, Status: StatusDisabled, Code: " fixture_mode "},
		{Component: ComponentAPI, Status: StatusOK, Code: "serving"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Version != Version || snapshot.Overall != StatusOK {
		t.Fatalf("unexpected snapshot: %#v", snapshot)
	}
	if snapshot.GeneratedAt.Location() != time.UTC {
		t.Fatalf("generated time must be UTC: %v", snapshot.GeneratedAt)
	}
	if snapshot.Checks[0].Component != ComponentAPI || snapshot.Checks[1].Code != "fixture_mode" {
		t.Fatalf("checks were not normalized and sorted: %#v", snapshot.Checks)
	}
}

func TestNewSnapshotDegradedOverall(t *testing.T) {
	snapshot, err := NewSnapshot(time.Now(), []Check{
		{Component: ComponentAPI, Status: StatusOK, Code: "serving"},
		{Component: ComponentRedis, Status: StatusDegraded, Code: "unreachable"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Overall != StatusDegraded {
		t.Fatalf("expected degraded overall status, got %q", snapshot.Overall)
	}
}

func TestNewSnapshotRejectsUnsafeOrAmbiguousValues(t *testing.T) {
	tests := []struct {
		name   string
		checks []Check
	}{
		{name: "empty", checks: nil},
		{name: "duplicate", checks: []Check{{Component: ComponentAPI, Status: StatusOK, Code: "serving"}, {Component: ComponentAPI, Status: StatusOK, Code: "ready"}}},
		{name: "unknown component", checks: []Check{{Component: "database_url", Status: StatusOK, Code: "serving"}}},
		{name: "unknown status", checks: []Check{{Component: ComponentAPI, Status: "failed", Code: "serving"}}},
		{name: "free text", checks: []Check{{Component: ComponentAPI, Status: StatusOK, Code: "token=secret"}}},
		{name: "url", checks: []Check{{Component: ComponentAPI, Status: StatusOK, Code: "https://example.test"}}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if _, err := NewSnapshot(time.Now(), tt.checks); err == nil {
				t.Fatal("expected validation error")
			}
		})
	}
	if _, err := NewSnapshot(time.Time{}, []Check{{Component: ComponentAPI, Status: StatusOK, Code: "serving"}}); err == nil {
		t.Fatal("expected zero time to fail")
	}
}

func TestSnapshotJSONCannotCarryArbitrarySecrets(t *testing.T) {
	snapshot, err := NewSnapshot(time.Now(), []Check{{Component: ComponentAPI, Status: StatusOK, Code: "serving"}})
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(snapshot)
	if err != nil {
		t.Fatal(err)
	}
	text := string(encoded)
	for _, forbidden := range []string{"token", "password", "cookie", "authorization", "http://", "https://"} {
		if strings.Contains(strings.ToLower(text), forbidden) {
			t.Fatalf("snapshot leaked forbidden material %q: %s", forbidden, text)
		}
	}
}
