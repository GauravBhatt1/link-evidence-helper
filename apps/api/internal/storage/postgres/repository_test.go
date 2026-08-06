package postgres

import (
	"context"
	"database/sql"
	"errors"
	"testing"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/audit"
	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/sourceadmin"
)

type countingBeginner struct {
	calls int
	err   error
}

func (database *countingBeginner) BeginTx(context.Context, *sql.TxOptions) (*sql.Tx, error) {
	database.calls++
	return nil, database.err
}

func TestNewRejectsNilDatabase(t *testing.T) {
	if _, err := New(nil); !errors.Is(err, ErrInvalidDatabase) {
		t.Fatalf("New(nil) error = %v, want %v", err, ErrInvalidDatabase)
	}
}

func TestSourceValidationHappensBeforeDatabaseAccess(t *testing.T) {
	database := &countingBeginner{}
	repositories, err := New(database)
	if err != nil {
		t.Fatal(err)
	}

	_, err = repositories.CreateSource(context.Background(), sourceadmin.Draft{
		ID:          "unsafe source",
		DisplayName: "Unsafe",
		Kind:        "http-json",
		Endpoint:    "https://example.invalid/",
		Enabled:     true,
	}, time.Now())
	if !errors.Is(err, sourceadmin.ErrInvalidSource) {
		t.Fatalf("CreateSource error = %v, want invalid source", err)
	}

	_, err = repositories.UpdateSource(context.Background(), "source-a", 0, sourceadmin.Draft{
		ID:          "source-a",
		DisplayName: "Source A",
		Kind:        "http-json",
		Endpoint:    "https://example.invalid/",
		Enabled:     true,
	}, time.Now())
	if !errors.Is(err, sourceadmin.ErrInvalidSource) {
		t.Fatalf("UpdateSource error = %v, want invalid source", err)
	}

	_, err = repositories.DisableSource(context.Background(), "", 1, time.Now())
	if !errors.Is(err, sourceadmin.ErrInvalidSource) {
		t.Fatalf("DisableSource error = %v, want invalid source", err)
	}

	if database.calls != 0 {
		t.Fatalf("database calls = %d, want 0", database.calls)
	}
}

func TestAuditValidationHappensBeforeDatabaseAccess(t *testing.T) {
	database := &countingBeginner{}
	repositories, err := New(database)
	if err != nil {
		t.Fatal(err)
	}

	err = repositories.AppendAuditEvent(context.Background(), audit.Event{
		ID:        "audit-1234",
		Occurred:  time.Now(),
		RequestID: "request-1234",
		Actor:     "admin",
		Action:    "source.create",
		Resource:  "https://secret.example/token",
		Outcome:   "success",
	})
	if !errors.Is(err, audit.ErrInvalidEvent) {
		t.Fatalf("AppendAuditEvent error = %v, want invalid event", err)
	}
	if database.calls != 0 {
		t.Fatalf("database calls = %d, want 0", database.calls)
	}
}

func TestBeginFailuresAreWrappedWithoutConnectionDetails(t *testing.T) {
	database := &countingBeginner{err: errors.New("connection unavailable")}
	repositories, err := New(database)
	if err != nil {
		t.Fatal(err)
	}

	_, err = repositories.CreateSource(context.Background(), sourceadmin.Draft{
		ID:          "source-a",
		DisplayName: "Source A",
		Kind:        "http-json",
		Endpoint:    "https://example.invalid/",
		Enabled:     true,
	}, time.Unix(1, 0))
	if err == nil {
		t.Fatal("CreateSource error = nil, want wrapped begin error")
	}
	if database.calls != 1 {
		t.Fatalf("database calls = %d, want 1", database.calls)
	}
}
