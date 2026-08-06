// Package observability defines fixed-vocabulary telemetry boundaries that cannot
// carry URLs, headers, bodies, credentials, connection strings, or arbitrary labels.
package observability

import (
	"context"
	"errors"
	"math"
	"regexp"
	"strings"
	"time"
)

const (
	maxIdentifierLength = 64
	maxRequestIDLength  = 128
)

var identifierPattern = regexp.MustCompile(`^[a-z][a-z0-9_.-]*$`)

type Level string

const (
	LevelInfo  Level = "info"
	LevelWarn  Level = "warn"
	LevelError Level = "error"
)

type Outcome string

const (
	OutcomeSuccess  Outcome = "success"
	OutcomeRejected Outcome = "rejected"
	OutcomeFailed   Outcome = "failed"
	OutcomeTimeout  Outcome = "timeout"
)

type MetricName string

const (
	MetricRequestCount    MetricName = "request_count"
	MetricRequestDuration MetricName = "request_duration_seconds"
	MetricJobCount        MetricName = "job_count"
	MetricJobDuration     MetricName = "job_duration_seconds"
	MetricDependencyReady MetricName = "dependency_ready"
)

// Event deliberately has no message, metadata, error text, URL, or payload field.
type Event struct {
	Timestamp time.Time
	Level     Level
	Component string
	Operation string
	Outcome   Outcome
	RequestID string
}

// Observation deliberately exposes only bounded fixed dimensions.
type Observation struct {
	Timestamp time.Time
	Name      MetricName
	Component string
	Operation string
	Outcome   Outcome
	Value     float64
}

// SpanSpec deliberately has no free-form attributes.
type SpanSpec struct {
	StartedAt time.Time
	Component string
	Operation string
	RequestID string
}

type Logger interface {
	Record(context.Context, Event) error
}

type Metrics interface {
	Observe(context.Context, Observation) error
}

type Tracer interface {
	Start(context.Context, SpanSpec) (context.Context, Span, error)
}

type Span interface {
	End(Outcome) error
}

func ValidateEvent(event Event) error {
	if event.Timestamp.IsZero() {
		return errors.New("observability event timestamp is required")
	}
	if !validLevel(event.Level) {
		return errors.New("observability event level is invalid")
	}
	if err := validateIdentifier("component", event.Component); err != nil {
		return err
	}
	if err := validateIdentifier("operation", event.Operation); err != nil {
		return err
	}
	if !validOutcome(event.Outcome) {
		return errors.New("observability event outcome is invalid")
	}
	return validateRequestID(event.RequestID)
}

func ValidateObservation(observation Observation) error {
	if observation.Timestamp.IsZero() {
		return errors.New("metric timestamp is required")
	}
	if !validMetricName(observation.Name) {
		return errors.New("metric name is invalid")
	}
	if err := validateIdentifier("component", observation.Component); err != nil {
		return err
	}
	if err := validateIdentifier("operation", observation.Operation); err != nil {
		return err
	}
	if !validOutcome(observation.Outcome) {
		return errors.New("metric outcome is invalid")
	}
	if math.IsNaN(observation.Value) || math.IsInf(observation.Value, 0) || observation.Value < 0 {
		return errors.New("metric value must be finite and non-negative")
	}
	if observation.Name == MetricDependencyReady && observation.Value != 0 && observation.Value != 1 {
		return errors.New("dependency readiness metric must be zero or one")
	}
	return nil
}

func ValidateSpanSpec(spec SpanSpec) error {
	if spec.StartedAt.IsZero() {
		return errors.New("span start timestamp is required")
	}
	if err := validateIdentifier("component", spec.Component); err != nil {
		return err
	}
	if err := validateIdentifier("operation", spec.Operation); err != nil {
		return err
	}
	return validateRequestID(spec.RequestID)
}

func NormalizeEvent(event Event) (Event, error) {
	if err := ValidateEvent(event); err != nil {
		return Event{}, err
	}
	event.Timestamp = event.Timestamp.UTC()
	return event, nil
}

func NormalizeObservation(observation Observation) (Observation, error) {
	if err := ValidateObservation(observation); err != nil {
		return Observation{}, err
	}
	observation.Timestamp = observation.Timestamp.UTC()
	return observation, nil
}

func NormalizeSpanSpec(spec SpanSpec) (SpanSpec, error) {
	if err := ValidateSpanSpec(spec); err != nil {
		return SpanSpec{}, err
	}
	spec.StartedAt = spec.StartedAt.UTC()
	return spec, nil
}

type NopLogger struct{}

func (NopLogger) Record(_ context.Context, event Event) error {
	_, err := NormalizeEvent(event)
	return err
}

type NopMetrics struct{}

func (NopMetrics) Observe(_ context.Context, observation Observation) error {
	_, err := NormalizeObservation(observation)
	return err
}

type NopTracer struct{}

func (NopTracer) Start(ctx context.Context, spec SpanSpec) (context.Context, Span, error) {
	if _, err := NormalizeSpanSpec(spec); err != nil {
		return ctx, nil, err
	}
	return ctx, nopSpan{}, nil
}

type nopSpan struct{}

func (nopSpan) End(outcome Outcome) error {
	if !validOutcome(outcome) {
		return errors.New("span outcome is invalid")
	}
	return nil
}

func validateIdentifier(name, value string) error {
	if value == "" || len(value) > maxIdentifierLength || !identifierPattern.MatchString(value) {
		return errors.New(name + " must use bounded lowercase identifier syntax")
	}
	return nil
}

func validateRequestID(value string) error {
	if len(value) > maxRequestIDLength {
		return errors.New("request identifier is too long")
	}
	for _, r := range value {
		if r < 0x21 || r > 0x7e || strings.ContainsRune(`\"'=`, r) {
			return errors.New("request identifier contains unsafe characters")
		}
	}
	return nil
}

func validLevel(level Level) bool {
	return level == LevelInfo || level == LevelWarn || level == LevelError
}

func validOutcome(outcome Outcome) bool {
	return outcome == OutcomeSuccess || outcome == OutcomeRejected || outcome == OutcomeFailed || outcome == OutcomeTimeout
}

func validMetricName(name MetricName) bool {
	switch name {
	case MetricRequestCount, MetricRequestDuration, MetricJobCount, MetricJobDuration, MetricDependencyReady:
		return true
	default:
		return false
	}
}
