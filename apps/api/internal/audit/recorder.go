package audit

import (
	"errors"
	"sync"
)

var ErrRecorderUnavailable = errors.New("audit recorder unavailable")

// Recorder accepts only validated, secret-safe audit events.
type Recorder interface {
	Record(Event) error
}

// MemoryRecorder is a concurrency-safe, non-durable development recorder.
// It stores only the bounded Event contract and is never enabled implicitly.
type MemoryRecorder struct {
	mu     sync.RWMutex
	events []Event
}

func NewMemoryRecorder() *MemoryRecorder {
	return &MemoryRecorder{}
}

func (recorder *MemoryRecorder) Record(event Event) error {
	if recorder == nil {
		return ErrRecorderUnavailable
	}
	validated, err := NewEvent(event.ID, event.RequestID, event.Actor, event.Action, event.Resource, event.Outcome, event.Occurred)
	if err != nil {
		return err
	}
	recorder.mu.Lock()
	defer recorder.mu.Unlock()
	recorder.events = append(recorder.events, validated)
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
