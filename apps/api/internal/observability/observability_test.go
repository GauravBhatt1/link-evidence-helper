package observability

import (
	"context"
	"math"
	"reflect"
	"testing"
	"time"
)

func TestNormalizeEvent(t *testing.T) {
	local := time.Date(2026, 8, 6, 13, 45, 0, 0, time.FixedZone("IST", 5*60*60+30*60))
	event, err := NormalizeEvent(Event{
		Timestamp: local,
		Level: LevelInfo,
		Component: "api",
		Operation: "search.submit",
		Outcome: OutcomeSuccess,
		RequestID: "req-123",
	})
	if err != nil {
		t.Fatalf("NormalizeEvent() error = %v", err)
	}
	if event.Timestamp.Location() != time.UTC {
		t.Fatalf("timestamp location = %v, want UTC", event.Timestamp.Location())
	}
}

func TestEventRejectsUnsafeVocabulary(t *testing.T) {
	base := Event{Timestamp: time.Now(), Level: LevelInfo, Component: "api", Operation: "search.submit", Outcome: OutcomeSuccess}
	tests := []Event{
		{Timestamp: base.Timestamp, Level: "debug", Component: base.Component, Operation: base.Operation, Outcome: base.Outcome},
		{Timestamp: base.Timestamp, Level: base.Level, Component: "API", Operation: base.Operation, Outcome: base.Outcome},
		{Timestamp: base.Timestamp, Level: base.Level, Component: base.Component, Operation: "GET /secret?token=x", Outcome: base.Outcome},
		{Timestamp: base.Timestamp, Level: base.Level, Component: base.Component, Operation: base.Operation, Outcome: "unknown"},
		{Timestamp: base.Timestamp, Level: base.Level, Component: base.Component, Operation: base.Operation, Outcome: base.Outcome, RequestID: "token='secret'"},
	}
	for _, event := range tests {
		if err := ValidateEvent(event); err == nil {
			t.Fatalf("ValidateEvent(%+v) succeeded, want error", event)
		}
	}
}

func TestObservationValidation(t *testing.T) {
	base := Observation{
		Timestamp: time.Now(), Name: MetricRequestDuration, Component: "api",
		Operation: "search.resolve", Outcome: OutcomeSuccess, Value: 0.25,
	}
	if err := ValidateObservation(base); err != nil {
		t.Fatalf("ValidateObservation() error = %v", err)
	}
	for _, value := range []float64{-1, math.NaN(), math.Inf(1)} {
		bad := base
		bad.Value = value
		if err := ValidateObservation(bad); err == nil {
			t.Fatalf("ValidateObservation(value=%v) succeeded, want error", value)
		}
	}
	ready := base
	ready.Name = MetricDependencyReady
	ready.Value = 2
	if err := ValidateObservation(ready); err == nil {
		t.Fatal("dependency readiness value 2 succeeded, want error")
	}
}

func TestTelemetryStructsExposeNoFreeFormOrSensitiveFields(t *testing.T) {
	for _, value := range []any{Event{}, Observation{}, SpanSpec{}} {
		typeOf := reflect.TypeOf(value)
		for i := 0; i < typeOf.NumField(); i++ {
			name := typeOf.Field(i).Name
			switch name {
			case "Message", "Metadata", "Labels", "Attributes", "URL", "Headers", "Body", "Cookie", "Token", "ConnectionString", "Error":
				t.Fatalf("%s exposes forbidden field %s", typeOf.Name(), name)
			}
		}
	}
}

func TestNopImplementationsStillValidate(t *testing.T) {
	ctx := context.Background()
	if err := (NopLogger{}).Record(ctx, Event{}); err == nil {
		t.Fatal("NopLogger accepted invalid event")
	}
	if err := (NopMetrics{}).Observe(ctx, Observation{}); err == nil {
		t.Fatal("NopMetrics accepted invalid observation")
	}
	if _, _, err := (NopTracer{}).Start(ctx, SpanSpec{}); err == nil {
		t.Fatal("NopTracer accepted invalid span")
	}

	_, span, err := (NopTracer{}).Start(ctx, SpanSpec{
		StartedAt: time.Now(), Component: "worker", Operation: "job.execute", RequestID: "job-1",
	})
	if err != nil {
		t.Fatalf("Start() error = %v", err)
	}
	if err := span.End(OutcomeSuccess); err != nil {
		t.Fatalf("End() error = %v", err)
	}
	if err := span.End("other"); err == nil {
		t.Fatal("End() accepted invalid outcome")
	}
}
