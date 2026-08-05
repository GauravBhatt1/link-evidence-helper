package audit

import (
	"testing"
	"time"
)

func TestMemoryRecorderStoresValidatedCopy(t *testing.T) {
	recorder := NewMemoryRecorder()
	event, err := NewEvent("evt-source-create-0001", "request-0001", "admin", "source.create", "source:example", "success", time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if err := recorder.Record(event); err != nil {
		t.Fatal(err)
	}
	events := recorder.List()
	if len(events) != 1 || events[0].Action != "source.create" {
		t.Fatalf("events = %#v", events)
	}
	events[0].Action = "tampered"
	if recorder.List()[0].Action != "source.create" {
		t.Fatal("List returned recorder-owned storage")
	}
}

func TestMemoryRecorderRejectsInvalidEvent(t *testing.T) {
	recorder := NewMemoryRecorder()
	if err := recorder.Record(Event{}); err == nil {
		t.Fatal("expected invalid event rejection")
	}
}
