package observability

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	maxEventName = 64
	maxLabelKey  = 32
	maxLabelVal  = 64
	maxLabels    = 8
)

var safeToken = regexp.MustCompile(`^[a-zA-Z0-9_.:-]+$`)

var (
	ErrInvalidName  = errors.New("observability name is invalid")
	ErrInvalidLabel = errors.New("observability label is invalid")
	ErrTooManyLabels = errors.New("too many observability labels")
)

type Labels map[string]string

func ValidateName(name string) error {
	if name == "" || len(name) > maxEventName || !safeToken.MatchString(name) {
		return ErrInvalidName
	}
	return nil
}

func NormalizeLabels(labels Labels) ([]slog.Attr, error) {
	if len(labels) > maxLabels {
		return nil, ErrTooManyLabels
	}
	keys := make([]string, 0, len(labels))
	for key := range labels {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	attrs := make([]slog.Attr, 0, len(keys))
	for _, key := range keys {
		value := labels[key]
		if key == "" || len(key) > maxLabelKey || len(value) > maxLabelVal || !safeToken.MatchString(key) || !safeToken.MatchString(value) {
			return nil, ErrInvalidLabel
		}
		attrs = append(attrs, slog.String(key, value))
	}
	return attrs, nil
}

type Logger struct{ inner *slog.Logger }

func NewLogger(w io.Writer) *Logger {
	return &Logger{inner: slog.New(slog.NewJSONHandler(w, &slog.HandlerOptions{Level: slog.LevelInfo}))}
}

func (l *Logger) Event(ctx context.Context, name string, labels Labels) error {
	if l == nil || l.inner == nil {
		return errors.New("logger is not configured")
	}
	if err := ValidateName(name); err != nil {
		return err
	}
	attrs, err := NormalizeLabels(labels)
	if err != nil {
		return err
	}
	args := make([]any, 0, len(attrs)+1)
	args = append(args, slog.String("event", name))
	for _, attr := range attrs {
		args = append(args, attr)
	}
	l.inner.Log(ctx, slog.LevelInfo, "event", args...)
	return nil
}

type CounterStore struct {
	mu sync.RWMutex
	values map[string]uint64
}

func NewCounterStore() *CounterStore { return &CounterStore{values: make(map[string]uint64)} }

func (s *CounterStore) Add(name string, labels Labels, delta uint64) error {
	if err := ValidateName(name); err != nil { return err }
	attrs, err := NormalizeLabels(labels)
	if err != nil { return err }
	var b strings.Builder
	b.WriteString(name)
	for _, attr := range attrs {
		b.WriteByte('|')
		b.WriteString(attr.Key)
		b.WriteByte('=')
		b.WriteString(attr.Value.String())
	}
	s.mu.Lock()
	s.values[b.String()] += delta
	s.mu.Unlock()
	return nil
}

func (s *CounterStore) Snapshot() map[string]uint64 {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make(map[string]uint64, len(s.values))
	for key, value := range s.values { out[key] = value }
	return out
}

type Span struct {
	Name string
	StartedAt time.Time
	FinishedAt time.Time
	Outcome string
}

type Tracer interface {
	Start(context.Context, string, Labels) (context.Context, func(string), error)
}

type MemoryTracer struct {
	mu sync.Mutex
	spans []Span
	clock func() time.Time
}

func NewMemoryTracer() *MemoryTracer { return &MemoryTracer{clock: time.Now} }

func (t *MemoryTracer) Start(ctx context.Context, name string, labels Labels) (context.Context, func(string), error) {
	if err := ValidateName(name); err != nil { return ctx, nil, err }
	if _, err := NormalizeLabels(labels); err != nil { return ctx, nil, err }
	started := t.clock().UTC()
	var once sync.Once
	finish := func(outcome string) {
		once.Do(func() {
			if ValidateName(outcome) != nil { outcome = "invalid" }
			t.mu.Lock()
			t.spans = append(t.spans, Span{Name: name, StartedAt: started, FinishedAt: t.clock().UTC(), Outcome: outcome})
			t.mu.Unlock()
		})
	}
	return ctx, finish, nil
}

func (t *MemoryTracer) Snapshot() []Span {
	t.mu.Lock()
	defer t.mu.Unlock()
	out := make([]Span, len(t.spans))
	copy(out, t.spans)
	return out
}
