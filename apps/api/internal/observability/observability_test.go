package observability

import (
	"context"
	"testing"
	"time"
)

func TestEventValidationAcceptsBoundedEvent(t *testing.T) {
	t.Parallel()
	event := Event{
		Name: EventRequestCompleted, Severity: SeverityInfo, Route: RouteSearch,
		Outcome: OutcomeSuccess, Status: 200, Duration: 250 * time.Millisecond,
	}
	if err := event.Validate(); err != nil {
		t.Fatalf("Validate() error = %v", err)
	}
}

func TestEventValidationRejectsArbitraryValues(t *testing.T) {
	t.Parallel()
	tests := []Event{
		{Name: EventName("https://example.invalid/private?q=token"), Severity: SeverityInfo, Route: RouteSearch, Outcome: OutcomeSuccess},
		{Name: EventRequestCompleted, Severity: Severity("debug"), Route: RouteSearch, Outcome: OutcomeSuccess},
		{Name: EventRequestCompleted, Severity: SeverityInfo, Route: RouteName("/users/123"), Outcome: OutcomeSuccess},
		{Name: EventRequestCompleted, Severity: SeverityInfo, Route: RouteSearch, Outcome: Outcome("bearer secret")},
		{Name: EventRequestCompleted, Severity: SeverityInfo, Route: RouteSearch, Outcome: OutcomeSuccess, Status: 600},
		{Name: EventRequestCompleted, Severity: SeverityInfo, Route: RouteSearch, Outcome: OutcomeSuccess, Duration: 25 * time.Hour},
	}
	for _, event := range tests {
		if err := event.Validate(); err == nil {
			t.Fatalf("Validate() accepted unsafe event: %#v", event)
		}
	}
}

func TestMetricValidationRejectsArbitraryNamesAndLabels(t *testing.T) {
	t.Parallel()
	unsafe := []Metric{
		{Name: MetricName("request_https://example.invalid"), Route: RouteSearch, Outcome: OutcomeSuccess, Value: 1},
		{Name: MetricRequestsTotal, Route: RouteName("/search?q=secret"), Outcome: OutcomeSuccess, Value: 1},
		{Name: MetricRequestsTotal, Route: RouteSearch, Outcome: Outcome("user@example.com"), Value: 1},
		{Name: MetricRequestsTotal, Route: RouteSearch, Outcome: OutcomeSuccess, Value: -1},
	}
	for _, metric := range unsafe {
		if err := metric.Validate(); err == nil {
			t.Fatalf("Validate() accepted unsafe metric: %#v", metric)
		}
	}
}

func TestNoopImplementationsAreSafe(t *testing.T) {
	t.Parallel()
	ctx := context.Background()
	NopLogger{}.Log(ctx, Event{})
	NopMetrics{}.Record(ctx, Metric{})
	returned, span := NopTracer{}.Start(ctx, SpanHTTPRequest)
	if returned != ctx {
		t.Fatal("NopTracer changed context")
	}
	span.End(OutcomeSuccess)
}
