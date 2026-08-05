package sourceadmin

import (
	"errors"
	"sync"
	"testing"
	"time"
)

func TestRegistryLifecycle(t *testing.T) {
	registry := NewMemoryRegistry()
	now := time.Date(2026, 8, 5, 12, 0, 0, 0, time.FixedZone("offset", 3600))
	created, err := registry.Create(Draft{ID: "catalog-primary", DisplayName: "Catalog Primary", Kind: "http-json", Endpoint: "https://example.test/api", Enabled: true}, now)
	if err != nil { t.Fatal(err) }
	if created.Revision != 1 || created.CreatedAt.Location() != time.UTC { t.Fatalf("unexpected created source: %#v", created) }
	updated, err := registry.Update(created.ID, created.Revision, Draft{ID: created.ID, DisplayName: "Catalog Primary v2", Kind: "http-html", Endpoint: "https://example.test/search/", Enabled: true}, now.Add(time.Minute))
	if err != nil { t.Fatal(err) }
	disabled, err := registry.Disable(updated.ID, updated.Revision, now.Add(2*time.Minute))
	if err != nil { t.Fatal(err) }
	if disabled.Enabled || disabled.Revision != 3 || len(registry.List()) != 1 { t.Fatalf("unexpected lifecycle result: %#v", disabled) }
}

func TestRegistryRejectsSensitiveConfiguration(t *testing.T) {
	tests := []Draft{
		{ID: "bad id", DisplayName: "Name", Kind: "http-json", Endpoint: "https://example.test", Enabled: true},
		{ID: "source", DisplayName: " Name", Kind: "http-json", Endpoint: "https://example.test", Enabled: true},
		{ID: "source", DisplayName: "Name", Kind: "shell", Endpoint: "https://example.test", Enabled: true},
		{ID: "source", DisplayName: "Name", Kind: "http-json", Endpoint: "https://user:secret@example.test", Enabled: true},
		{ID: "source", DisplayName: "Name", Kind: "http-json", Endpoint: "https://example.test/?token=secret", Enabled: true},
		{ID: "source", DisplayName: "Name", Kind: "http-json", Endpoint: "file:///etc/passwd", Enabled: true},
	}
	for _, draft := range tests {
		if _, err := NewMemoryRegistry().Create(draft, time.Now()); !errors.Is(err, ErrInvalidSource) { t.Fatalf("Create(%#v) error = %v", draft, err) }
	}
}

func TestRegistryAllowsOnlyOneConcurrentRevisionWinner(t *testing.T) {
	registry := NewMemoryRegistry()
	now := time.Now()
	created, err := registry.Create(Draft{ID: "source", DisplayName: "Source", Kind: "http-json", Endpoint: "https://example.test", Enabled: true}, now)
	if err != nil { t.Fatal(err) }
	var wait sync.WaitGroup
	wait.Add(2)
	results := make(chan error, 2)
	for i := 0; i < 2; i++ { go func() { defer wait.Done(); _, err := registry.Disable(created.ID, created.Revision, now.Add(time.Second)); results <- err }() }
	wait.Wait(); close(results)
	successes, conflicts := 0, 0
	for result := range results { if result == nil { successes++ } else if errors.Is(result, ErrRevisionConflict) { conflicts++ } else { t.Fatal(result) } }
	if successes != 1 || conflicts != 1 { t.Fatalf("successes=%d conflicts=%d", successes, conflicts) }
}
