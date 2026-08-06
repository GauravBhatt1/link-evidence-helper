package observability

import (
	"context"
	"errors"
	"fmt"
	"time"
)

// EventName is a closed set of operational events. Values must never contain
// request URLs, headers, bodies, cookies, tokens, connection strings, or user data.
type EventName string

const (
	EventAPIStarted        EventName = "api_started"
	EventAPIStopped        EventName = "api_stopped"
	EventRequestCompleted  EventName = "request_completed"
	EventDependencyChecked EventName = "dependency_checked"
	EventJobTransition     EventName = "job_transition"
)

// RouteName is a bounded route identifier rather than the raw request path.
type RouteName string

const (
	RouteUnknown     RouteName = "unknown"
	RouteHealthLive  RouteName = "health_live"
	RouteHealthReady RouteName = "health_ready"
	RouteSearch      RouteName = "search"
	RouteLibrary     RouteName = "library"
	RouteAdmin       RouteName = "admin"
	RouteDiagnostics RouteName = "diagnostics"
)

// Outcome is intentionally low-cardinality.
type Outcome string

const (
	OutcomeSuccess Outcome = "success"
	OutcomeFailure Outcome = "failure"
	OutcomeDenied  Outcome = "denied"
)

// Severity is a closed logging level set.
type Severity string

const (
	SeverityInfo  Severity = "info"
	SeverityWarn  Severity = "warn"
	SeverityError Severity = "error"
)

// Event is the complete structured-log payload accepted by this boundary.
// Free-form strings are deliberately excluded.
type Event struct {
	Name     EventName
	Severity Severity
	Route    RouteName
	Outcome  Outcome
	Status   int
	Duration time.Duration
}

func (event Event) Validate() error {
	if !validEventName(event.Name) {
		return fmt.Errorf("unsupported event name %q", event.Name)
	}
	if !validSeverity(event.Severity) {
		return fmt.Errorf("unsupported severity %q", event.Severity)
	}
	if !validRoute(event.Route) {
		return fmt.Errorf("unsupported route %q", event.Route)
	}
	if !validOutcome(event.Outcome) {
		return fmt.Errorf("unsupported outcome %q", event.Outcome)
	}
	if event.Status < 0 || event.Status > 599 {
		return errors.New("status must be between 0 and 599")
	}
	if event.Duration < 0 || event.Duration > 24*time.Hour {
		return errors.New("duration must be between 0 and 24h")
	}
	return nil
}

// Logger consumes validated, bounded events.
type Logger interface {
	Log(context.Context, Event)
}

// MetricName is a closed metric set to prevent arbitrary names and labels.
type MetricName string

const (
	MetricRequestsTotal      MetricName = "requests_total"
	MetricRequestDuration    MetricName = "request_duration"
	MetricDependencyReady    MetricName = "dependency_ready"
	MetricJobsInFlight       MetricName = "jobs_in_flight"
)

// Metric is intentionally label-free. Dimensions are represented only by
// closed enums already validated above.
type Metric struct {
	Name    MetricName
	Route   RouteName
	Outcome Outcome
	Value   float64
}

func (metric Metric) Validate() error {
	if !validMetricName(metric.Name) {
		return fmt.Errorf("unsupported metric name %q", metric.Name)
	}
	if !validRoute(metric.Route) {
		return fmt.Errorf("unsupported route %q", metric.Route)
	}
	if !validOutcome(metric.Outcome) {
		return fmt.Errorf("unsupported outcome %q", metric.Outcome)
	}
	if metric.Value < 0 || metric.Value > 1_000_000_000 {
		return errors.New("metric value is outside the bounded range")
	}
	return nil
}

// Metrics consumes validated low-cardinality measurements.
type Metrics interface {
	Record(context.Context, Metric)
}

// SpanName is a closed tracing operation set.
type SpanName string

const (
	SpanHTTPRequest     SpanName = "http_request"
	SpanDependencyCheck SpanName = "dependency_check"
	SpanJobExecution    SpanName = "job_execution"
)

// Span contains no attributes and therefore cannot capture sensitive payloads.
type Span interface {
	End(Outcome)
}

// Tracer starts bounded spans without arbitrary attributes.
type Tracer interface {
	Start(context.Context, SpanName) (context.Context, Span)
}

// No-op implementations provide a safe disabled-by-default runtime.
type NopLogger struct{}
func (NopLogger) Log(context.Context, Event) {}

type NopMetrics struct{}
func (NopMetrics) Record(context.Context, Metric) {}

type NopTracer struct{}
func (NopTracer) Start(ctx context.Context, _ SpanName) (context.Context, Span) {
	return ctx, nopSpan{}
}
type nopSpan struct{}
func (nopSpan) End(Outcome) {}

func validEventName(value EventName) bool {
	switch value {
	case EventAPIStarted, EventAPIStopped, EventRequestCompleted, EventDependencyChecked, EventJobTransition:
		return true
	default:
		return false
	}
}

func validMetricName(value MetricName) bool {
	switch value {
	case MetricRequestsTotal, MetricRequestDuration, MetricDependencyReady, MetricJobsInFlight:
		return true
	default:
		return false
	}
}

func validSeverity(value Severity) bool {
	return value == SeverityInfo || value == SeverityWarn || value == SeverityError
}

func validRoute(value RouteName) bool {
	switch value {
	case RouteUnknown, RouteHealthLive, RouteHealthReady, RouteSearch, RouteLibrary, RouteAdmin, RouteDiagnostics:
		return true
	default:
		return false
	}
}

func validOutcome(value Outcome) bool {
	return value == OutcomeSuccess || value == OutcomeFailure || value == OutcomeDenied
}
