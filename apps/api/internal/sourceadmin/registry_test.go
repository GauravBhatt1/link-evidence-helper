package sourceadmin

import (
	"errors"
	"sync"
	"testing"
	"time"
)

func TestRegistryCreateUpdateDisableAndList(t *testing.T) {
	registry := NewMemoryRegistry()
	createdAt := time.Date(2026, 8, 5, 12, 0, 0, 0, time.FixedZone("offset", 3600))
	created, err := registry.Create(Draft{
		ID: "catalog-primary", DisplayName: "Catalog Primary", Kind: "http-json",
		Endpoint: "https://example.test/api", Enabled: true,
	}, createdAt)
	if err != nil {
		t.Fatalf("Create() error = %v", err)
	}
	if created.Revision != 1 || created.CreatedAt.Location() != time.UTC || created.Endpoint != "https://example.test/api" {
		t.Fatalf("unexpected created source: %#v", created)
	}
	if created.QueryParameter != "q" || created.TitleField != "title" || created.URLField != "url" || created.ResultRoot != "" || len(created.AllowedResultHosts) != 0 {
		t.Fatalf("unexpected default search mapping: %#v", created)
	}

	updated, err := registry.Update(created.ID, created.Revision, Draft{
		ID: created.ID, DisplayName: "Catalog Primary v2", Kind: "http-html",
		Endpoint: "https://example.test/search/", QueryParameter: "query",
		ResultRoot: "payload.items", TitleField: "metadata.title", URLField: "links.download",
		AllowedResultHosts: []string{"downloads.example.test", "cdn.example.test."}, Enabled: true,
	}, createdAt.Add(time.Minute))
	if err != nil {
		t.Fatalf("Update() error = %v", err)
	}
	if updated.Revision != 2 || updated.DisplayName != "Catalog Primary v2" {
		t.Fatalf("unexpected updated source: %#v", updated)
	}
	if updated.QueryParameter != "query" || updated.ResultRoot != "payload.items" || updated.TitleField != "metadata.title" || updated.URLField != "links.download" {
		t.Fatalf("unexpected updated mapping: %#v", updated)
	}
	if len(updated.AllowedResultHosts) != 2 || updated.AllowedResultHosts[0] != "cdn.example.test" || updated.AllowedResultHosts[1] != "downloads.example.test" {
		t.Fatalf("unexpected allowed hosts: %#v", updated.AllowedResultHosts)
	}

	disabled, err := registry.Disable(updated.ID, updated.Revision, createdAt.Add(2*time.Minute))
	if err != nil {
		t.Fatalf("Disable() error = %v", err)
	}
	if disabled.Enabled || disabled.Revision != 3 {
		t.Fatalf("unexpected disabled source: %#v", disabled)
	}

	listed := registry.List()
	if len(listed) != 1 || listed[0].ID != created.ID {
		t.Fatalf("List() = %#v", listed)
	}
}

func TestRegistryRejectsSensitiveOrUnboundedConfiguration(t *testing.T) {
	now := time.Now()
	tests := []Draft{
		{ID: "bad id", DisplayName: "Name", Kind: "http-json", Endpoint: "https://example.test", Enabled: true},
		{ID: "source", DisplayName: " Name", Kind: "http-json", Endpoint: "https://example.test", Enabled: true},
		{ID: "source", DisplayName: "Name", Kind: "shell", Endpoint: "https://example.test", Enabled: true},
		{ID: "source", DisplayName: "Name", Kind: "http-json", Endpoint: "https://user:secret@example.test", Enabled: true},
		{ID: "source", DisplayName: "Name", Kind: "http-json", Endpoint: "https://example.test/?token=secret", Enabled: true},
		{ID: "source", DisplayName: "Name", Kind: "http-json", Endpoint: "file:///etc/passwd", Enabled: true},
		{ID: "source", DisplayName: "Name", Kind: "http-json", Endpoint: "https://example.test", QueryParameter: "bad param", Enabled: true},
		{ID: "source", DisplayName: "Name", Kind: "http-json", Endpoint: "https://example.test", TitleField: "items[0].title", Enabled: true},
		{ID: "source", DisplayName: "Name", Kind: "http-json", Endpoint: "https://example.test", URLField: " links.url", Enabled: true},
		{ID: "source", DisplayName: "Name", Kind: "http-json", Endpoint: "https://example.test", AllowedResultHosts: []string{"127.0.0.1"}, Enabled: true},
		{ID: "source", DisplayName: "Name", Kind: "http-json", Endpoint: "https://example.test", AllowedResultHosts: []string{"cdn.example.test", "cdn.example.test."}, Enabled: true},
	}
	for _, draft := range tests {
		registry := NewMemoryRegistry()
		if _, err := registry.Create(draft, now); !errors.Is(err, ErrInvalidSource) {
			t.Fatalf("Create(%#v) error = %v, want ErrInvalidSource", draft, err)
		}
	}
}

func TestRegistryUsesOptimisticRevisionChecks(t *testing.T) {
	registry := NewMemoryRegistry()
	now := time.Now()
	created, err := registry.Create(Draft{ID: "source", DisplayName: "Source", Kind: "http-json", Endpoint: "https://example.test", Enabled: true}, now)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := registry.Disable(created.ID, created.Revision+1, now.Add(time.Second)); !errors.Is(err, ErrRevisionConflict) {
		t.Fatalf("Disable() error = %v, want ErrRevisionConflict", err)
	}
}

func TestRegistryAllowsOnlyOneConcurrentRevisionWinner(t *testing.T) {
	registry := NewMemoryRegistry()
	now := time.Now()
	created, err := registry.Create(Draft{ID: "source", DisplayName: "Source", Kind: "http-json", Endpoint: "https://example.test", Enabled: true}, now)
	if err != nil {
		t.Fatal(err)
	}

	var wait sync.WaitGroup
	wait.Add(2)
	results := make(chan error, 2)
	for index := 0; index < 2; index++ {
		go func() {
			defer wait.Done()
			_, updateErr := registry.Disable(created.ID, created.Revision, now.Add(time.Second))
			results <- updateErr
		}()
	}
	wait.Wait()
	close(results)

	successes := 0
	conflicts := 0
	for result := range results {
		switch {
		case result == nil:
			successes++
		case errors.Is(result, ErrRevisionConflict):
			conflicts++
		default:
			t.Fatalf("unexpected concurrent result: %v", result)
		}
	}
	if successes != 1 || conflicts != 1 {
		t.Fatalf("successes=%d conflicts=%d", successes, conflicts)
	}
}
