package observability

import (
	"bytes"
	"context"
	"strings"
	"testing"
)

func TestLoggerRejectsSecretShapedAndArbitraryValues(t *testing.T) {
	var out bytes.Buffer
	logger := NewLogger(&out)

	unsafe := []Labels{
		{"url": "https://example.com/path?token=secret"},
		{"authorization": "Bearer-secret"},
		{"request_body": "hello world"},
		{"db": "postgres://user:pass@host/db"},
	}
	for _, labels := range unsafe {
		if err := logger.Event(context.Background(), "request.completed", labels); err == nil {
			t.Fatalf("expected unsafe labels to be rejected: %#v", labels)
		}
	}
	if out.Len() != 0 {
		t.Fatalf("rejected event must not be logged: %q", out.String())
	}
}

func TestLoggerEmitsOnlyValidatedStructuredFields(t *testing.T) {
	var out bytes.Buffer
	logger := NewLogger(&out)
	if err := logger.Event(context.Background(), "request.completed", Labels{"method": "GET", "status": "200"}); err != nil {
		t.Fatal(err)
	}
	logged := out.String()
	for _, want := range []string{`"event":"request.completed"`, `"method":"GET"`, `"status":"200"`} {
		if !strings.Contains(logged, want) {
			t.Fatalf("missing %s in %s", want, logged)
		}
	}
	for _, forbidden := range []string{"token", "cookie", "password", "postgres://"} {
		if strings.Contains(strings.ToLower(logged), forbidden) {
			t.Fatalf("log leaked forbidden data: %s", logged)
		}
	}
}

func TestCounterStoreIsBoundedByValidatedDimensions(t *testing.T) {
	store := NewCounterStore()
	if err := store.Add("http.requests", Labels{"route": "search", "status": "200"}, 2); err != nil {
		t.Fatal(err)
	}
	if err := store.Add("http.requests", Labels{"route": "search", "status": "200"}, 3); err != nil {
		t.Fatal(err)
	}
	values := store.Snapshot()
	if got := values["http.requests|route=search|status=200"]; got != 5 {
		t.Fatalf("counter = %d, want 5", got)
	}

	tooMany := Labels{}
	for i := 0; i < maxLabels+1; i++ {
		tooMany[string(rune('a'+i))] = "value"
	}
	if err := store.Add("http.requests", tooMany, 1); err != ErrTooManyLabels {
		t.Fatalf("expected ErrTooManyLabels, got %v", err)
	}
}

func TestMemoryTracerRecordsSafeOutcomeOnce(t *testing.T) {
	tracer := NewMemoryTracer()
	_, finish, err := tracer.Start(context.Background(), "search.resolve", Labels{"mode": "http"})
	if err != nil {
		t.Fatal(err)
	}
	finish("success")
	finish("failed")
	spans := tracer.Snapshot()
	if len(spans) != 1 || spans[0].Outcome != "success" || spans[0].FinishedAt.Before(spans[0].StartedAt) {
		t.Fatalf("unexpected spans: %#v", spans)
	}
}

func TestNamesAndLabelsRejectControlCharacters(t *testing.T) {
	if err := ValidateName("request\nsecret"); err != ErrInvalidName {
		t.Fatalf("expected invalid name, got %v", err)
	}
	if _, err := NormalizeLabels(Labels{"status": "200\nsecret"}); err != ErrInvalidLabel {
		t.Fatalf("expected invalid label, got %v", err)
	}
}
