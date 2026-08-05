package audit

import (
	"errors"
	"fmt"
	"sync"
	"testing"
	"time"
)

func TestMemoryRecorderRejectsInvalidCapacity(t *testing.T) {
	for _, capacity := range []int{0, -1, 10001} {
		if _, err := NewMemoryRecorder(capacity); !errors.Is(err, ErrRecorderFull) {
			t.Fatalf("capacity %d error = %v, want ErrRecorderFull", capacity, err)
		}
	}
}

func TestMemoryRecorderStoresValidatedCopies(t *testing.T) {
	recorder, err := NewMemoryRecorder(2)
	if err != nil {
		t.Fatal(err)
	}
	event, err := NewEvent("event-0001", "request-0001", "admin", "source.create", "source:alpha", "success", time.Unix(1, 0))
	if err != nil {
		t.Fatal(err)
	}
	if err := recorder.Record(event); err != nil {
		t.Fatal(err)
	}
	listed := recorder.List()
	if len(listed) != 1 || listed[0] != event {
		t.Fatalf("events = %#v, want %#v", listed, []Event{event})
	}
	listed[0].Action = "source.disable"
	if got := recorder.List()[0].Action; got != "source.create" {
		t.Fatalf("stored event mutated through List: %q", got)
	}
}

func TestMemoryRecorderRejectsInvalidEventAndCapacityOverflow(t *testing.T) {
	recorder, err := NewMemoryRecorder(1)
	if err != nil {
		t.Fatal(err)
	}
	if err := recorder.Record(Event{}); !errors.Is(err, ErrInvalidEvent) {
		t.Fatalf("invalid event error = %v", err)
	}
	event, err := NewEvent("event-0002", "request-0002", "admin", "source.update", "source:beta", "failure", time.Unix(2, 0))
	if err != nil {
		t.Fatal(err)
	}
	if err := recorder.Record(event); err != nil {
		t.Fatal(err)
	}
	if err := recorder.Record(event); !errors.Is(err, ErrRecorderFull) {
		t.Fatalf("overflow error = %v, want ErrRecorderFull", err)
	}
}

func TestMemoryRecorderConcurrentBoundedWrites(t *testing.T) {
	const capacity = 64
	recorder, err := NewMemoryRecorder(capacity)
	if err != nil {
		t.Fatal(err)
	}
	var wait sync.WaitGroup
	for index := 0; index < capacity*2; index++ {
		wait.Add(1)
		go func(index int) {
			defer wait.Done()
			event, eventErr := NewEvent(
				fmt.Sprintf("event-%04d", index),
				fmt.Sprintf("request-%04d", index),
				"admin", "source.disable", "source:bounded", "success", time.Unix(int64(index+1), 0),
			)
			if eventErr != nil {
				t.Errorf("event %d: %v", index, eventErr)
				return
			}
			if recordErr := recorder.Record(event); recordErr != nil && !errors.Is(recordErr, ErrRecorderFull) {
				t.Errorf("record %d: %v", index, recordErr)
			}
		}(index)
	}
	wait.Wait()
	if got := len(recorder.List()); got != capacity {
		t.Fatalf("stored %d events, want %d", got, capacity)
	}
}
