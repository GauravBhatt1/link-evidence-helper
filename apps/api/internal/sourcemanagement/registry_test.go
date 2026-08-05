package sourcemanagement

import (
	"errors"
	"testing"
	"time"
)

func TestRegistryCreateListAndUpdate(t *testing.T) {
	clock := time.Date(2026, time.August, 6, 0, 0, 0, 0, time.UTC)
	registry := NewRegistry(func() time.Time { return clock })

	second, err := registry.Create(CreateInput{ID: "second-source", Name: "Second", Enabled: true, Priority: 20, ConfigRef: "config/second.json"})
	if err != nil {
		t.Fatalf("create second source: %v", err)
	}
	if second.Revision != 1 || !second.Enabled {
		t.Fatalf("unexpected created source: %#v", second)
	}
	if _, err := registry.Create(CreateInput{ID: "first-source", Name: "First", Enabled: false, Priority: 10, ConfigRef: "config/first.json"}); err != nil {
		t.Fatalf("create first source: %v", err)
	}

	listed := registry.List()
	if len(listed) != 2 || listed[0].ID != "first-source" || listed[1].ID != "second-source" {
		t.Fatalf("unexpected list order: %#v", listed)
	}

	clock = clock.Add(time.Minute)
	enabled := false
	priority := 5
	updated, err := registry.Update("second-source", second.Revision, UpdateInput{Enabled: &enabled, Priority: &priority})
	if err != nil {
		t.Fatalf("update source: %v", err)
	}
	if updated.Enabled || updated.Priority != 5 || updated.Revision != 2 || !updated.UpdatedAt.Equal(clock) {
		t.Fatalf("unexpected updated source: %#v", updated)
	}
}

func TestRegistryRejectsUnsafeOrConflictingChanges(t *testing.T) {
	registry := NewRegistry(nil)
	invalid := []CreateInput{
		{ID: "UPPER", Name: "Name", ConfigRef: "config.json"},
		{ID: "valid", Name: "", ConfigRef: "config.json"},
		{ID: "valid", Name: "Name", Priority: -1, ConfigRef: "config.json"},
		{ID: "valid", Name: "Name", ConfigRef: ""},
	}
	for _, input := range invalid {
		if _, err := registry.Create(input); !errors.Is(err, ErrInvalidSource) {
			t.Fatalf("expected invalid source for %#v, got %v", input, err)
		}
	}

	created, err := registry.Create(CreateInput{ID: "safe-source", Name: "Safe", Priority: 1, ConfigRef: "config/safe.json"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := registry.Create(CreateInput{ID: "safe-source", Name: "Duplicate", Priority: 2, ConfigRef: "config/duplicate.json"}); !errors.Is(err, ErrSourceExists) {
		t.Fatalf("expected duplicate error, got %v", err)
	}
	if _, err := registry.Update(created.ID, created.Revision+1, UpdateInput{}); !errors.Is(err, ErrInvalidSource) {
		t.Fatalf("expected revision conflict, got %v", err)
	}
	if _, err := registry.Update("missing", 1, UpdateInput{}); !errors.Is(err, ErrSourceMissing) {
		t.Fatalf("expected missing error, got %v", err)
	}
}
