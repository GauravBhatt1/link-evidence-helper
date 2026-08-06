package postgres

import (
	"context"
	"database/sql"
	"errors"
	"testing"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/audit"
)

type auditRow struct {
	id  string
	err error
}

func (row auditRow) Scan(dest ...any) error {
	if row.err != nil {
		return row.err
	}
	*(dest[0].(*string)) = row.id
	return nil
}

type auditQueryer struct {
	row   Row
	query string
	args  []any
}

func (queryer *auditQueryer) QueryContext(context.Context, string, ...any) (Rows, error) {
	return nil, errors.New("unexpected query")
}

func (queryer *auditQueryer) QueryRowContext(_ context.Context, query string, args ...any) Row {
	queryer.query = query
	queryer.args = args
	return queryer.row
}

func TestAuditRepositoryAppend(t *testing.T) {
	occurred := time.Date(2026, 8, 6, 1, 2, 3, 0, time.FixedZone("test", 3600))
	event, err := audit.NewEvent(
		"event-12345678",
		"request-12345678",
		"admin",
		"source.create",
		"source:alpha",
		"success",
		occurred,
	)
	if err != nil {
		t.Fatal(err)
	}
	queryer := &auditQueryer{row: auditRow{id: event.ID}}
	repository, err := NewAuditRepository(queryer, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if err := repository.Append(context.Background(), event); err != nil {
		t.Fatal(err)
	}
	if queryer.query != appendAuditEventSQL || len(queryer.args) != 7 {
		t.Fatalf("unexpected insert: %q %#v", queryer.query, queryer.args)
	}
	if got := queryer.args[6].(time.Time); !got.Equal(occurred.UTC()) || got.Location() != time.UTC {
		t.Fatalf("occurred_at was not normalized to UTC: %v", got)
	}
}

func TestAuditRepositoryRejectsNonDurableVocabulary(t *testing.T) {
	event, err := audit.NewEvent(
		"event-12345678",
		"request-12345678",
		"admin",
		"diagnostic.run",
		"system",
		"denied",
		time.Now(),
	)
	if err != nil {
		t.Fatal(err)
	}
	queryer := &auditQueryer{row: auditRow{id: event.ID}}
	repository, _ := NewAuditRepository(queryer, time.Second)
	if err := repository.Append(context.Background(), event); !errors.Is(err, audit.ErrInvalidEvent) {
		t.Fatalf("expected invalid event, got %v", err)
	}
	if queryer.query != "" {
		t.Fatal("invalid event reached database")
	}
}

type duplicateAuditError struct{}

func (duplicateAuditError) Error() string    { return "duplicate" }
func (duplicateAuditError) SQLState() string { return "23505" }

func TestAuditRepositoryMapsDuplicateEvent(t *testing.T) {
	event, _ := audit.NewEvent(
		"event-12345678",
		"request-12345678",
		"admin",
		"source.disable",
		"source:alpha",
		"failure",
		time.Now(),
	)
	queryer := &auditQueryer{row: auditRow{err: duplicateAuditError{}}}
	repository, _ := NewAuditRepository(queryer, time.Second)
	if err := repository.Append(context.Background(), event); !errors.Is(err, ErrAuditEventExists) {
		t.Fatalf("expected duplicate mapping, got %v", err)
	}
}

func TestAuditRepositoryRejectsBadConfiguration(t *testing.T) {
	if _, err := NewAuditRepository(nil, time.Second); err == nil {
		t.Fatal("expected nil queryer rejection")
	}
	queryer := &auditQueryer{row: auditRow{err: sql.ErrNoRows}}
	if _, err := NewAuditRepository(queryer, 31*time.Second); err == nil {
		t.Fatal("expected excessive timeout rejection")
	}
}
