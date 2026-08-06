package observability

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestLoggerEmitsOnlyValidatedStructuredFields(t *testing.T) {
	var buf bytes.Buffer
	logger, err := NewLogger(&buf)
	if err != nil {
		t.Fatal(err)
	}
	if err := logger.Event(context.Background(), "search.completed", Labels{"outcome": "success", "worker": "http"}); err != nil {
		t.Fatal(err)
	}
	var record map[string]any
	if err := json.Unmarshal(buf.Bytes(), &record); err != nil {
		t.Fatal(err)
	}
	if record["event"] != "search.completed" || record["outcome"] != "success" || record["worker"] != "http" {
		t.Fatalf("unexpected record: %#v", record)
	}
}

func TestValidationRejectsUnboundedOrSensitiveShapedValues(t *testing.T) {
	for _, name := range []string{"", "has space", strings.Repeat("a", maxEventName+1), "https://example.test"} {
		if !errors.Is(ValidateName(name), ErrInvalidName) {
			t.Fatalf("expected invalid name: %q", name)
		}
	}
	for _, labels := range []Labels{
		{"url": "https://example.test"},
		{"token": "abc/def"},
		{"empty": ""},
		{strings.Repeat("k", maxLabelKey+1): "value"},
	} {
		if _, err := NormalizeLabels(labels); !errors.Is(err, ErrInvalidLabel) {
			t.Fatalf("expected invalid labels %#v, got %v", labels, err)
		}
	}
}

func TestCounterStoreIsDeterministicDefensiveAndConcurrent(t *testing.T) {
	store := NewCounterStore()
	labels := Labels{"outcome": "success", "worker": "http"}
	const workers = 32
	var wg sync.WaitGroup
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := store.Add("search.completed", labels, 1); err != nil {
				t.Errorf("add: %v", err)
			}
		}()
	}
	wg.Wait()
	key := "search.completed|outcome=success|worker=http"
	snapshot := store.Snapshot()
	if snapshot[key] != workers {
		t.Fatalf("counter = %d, want %d", snapshot[key], workers)
	}
	snapshot[key] = 0
	if store.Snapshot()[key] != workers {
		t.Fatal("snapshot mutation changed store")
	}
}

func TestMemoryTracerBoundsHistoryAndFinishesOnce(t *testing.T) {
	tracer := NewMemoryTracer()
	current := time.Date(2026, 8, 6, 7, 0, 0, 0, time.FixedZone("test", 3600))
	tracer.clock = func() time.Time {
		current = current.Add(time.Millisecond)
		return current
	}
	for i := 0; i < maxSpans+1; i++ {
		_, finish, err := tracer.Start(context.Background(), "job.run", Labels{"worker": "http"})
		if err != nil {
			t.Fatal(err)
		}
		finish("success")
		finish("failure")
	}
	spans := tracer.Snapshot()
	if len(spans) != maxSpans {
		t.Fatalf("span count = %d, want %d", len(spans), maxSpans)
	}
	for _, span := range spans {
		if span.Outcome != "success" {
			t.Fatalf("finish was not once-only: %#v", span)
		}
		if span.StartedAt.Location() != time.UTC || span.FinishedAt.Location() != time.UTC {
			t.Fatalf("timestamps are not UTC: %#v", span)
		}
	}
}

func TestNilComponentsFailClosed(t *testing.T) {
	if _, err := NewLogger(nil); !errors.Is(err, ErrNotConfigured) {
		t.Fatal("nil writer did not fail closed")
	}
	var logger *Logger
	if !errors.Is(logger.Event(context.Background(), "event", nil), ErrNotConfigured) {
		t.Fatal("nil logger did not fail closed")
	}
	var store *CounterStore
	if !errors.Is(store.Add("metric", nil, 1), ErrNotConfigured) {
		t.Fatal("nil counter store did not fail closed")
	}
	var tracer *MemoryTracer
	if _, _, err := tracer.Start(context.Background(), "span", nil); !errors.Is(err, ErrNotConfigured) {
		t.Fatal("nil tracer did not fail closed")
	}
}
