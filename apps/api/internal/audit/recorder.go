package audit

import (
	"errors"
	"sync"
)

var ErrRecorderFull = errors.New("audit recorder capacity reached")

// Recorder is the minimal secret-safe audit sink boundary. Implementations must
// persist only validated Event values and must not enrich them with request
// headers, bodies, URLs, credentials, cookies, tokens, or arbitrary metadata.
type Recorder interface {
	Record(Event) error
	List() []Event
}

// MemoryRecorder is a bounded, concurrency-safe, non-durable implementation.
// It is intended for development and tests until the PostgreSQL audit repository
// is introduced. Capacity is fixed at construction time so audit growth cannot
// become unbounded accidentally.
type MemoryRecorder struct {
	mu       sync.RWMutex
	capacity int
	events   []Event
}

func NewMemoryRecorder(capacity int) (*MemoryRecorder, error) {
	if capacity < 1 || capacity > 10000 {
		return nil, ErrRecorderFull
	}
	return &MemoryRecorder{capacity: capacity, events: make([]Event, 0, capacity)}, nil
}

func (recorder *MemoryRecorder) Record(event Event) error {
	if recorder == nil {
		return ErrInvalidEvent
	}
	if _, err := NewEvent(event.ID, event.RequestID, event.Actor, event.Action, event.Resource, event.Outcome, event.Occurred); err != nil {
		return err
	}
	recorder.mu.Lock()
	defer recorder.mu.Unlock()
	if len(recorder.events) >= recorder.capacity {
		return ErrRecorderFull
	}
	recorder.events = append(recorder.events, event)
	return nil
}

func (recorder *MemoryRecorder) List() []Event {
	if recorder == nil {
		return nil
	}
	recorder.mu.RLock()
	defer recorder.mu.RUnlock()
	result := make([]Event, len(recorder.events))
	copy(result, recorder.events)
	return result
}
