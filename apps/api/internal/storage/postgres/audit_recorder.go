package postgres

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/audit"
)

const (
	appendAuditEventSQL = `INSERT INTO admin_audit_events
(id, occurred_at, request_id, actor, action, resource, outcome)
VALUES ($1, $2, $3, $4, $5, $6, $7)
RETURNING id`
	listAuditEventsSQL = `SELECT id, occurred_at, request_id, actor, action, resource, outcome
FROM admin_audit_events
ORDER BY occurred_at ASC, id ASC`
)

// AuditRecorder persists only the bounded, validated administrative audit contract.
type AuditRecorder struct {
	store   Store
	timeout time.Duration
}

func NewAuditRecorder(store Store, timeout time.Duration) (*AuditRecorder, error) {
	if store == nil || timeout <= 0 || timeout > 30*time.Second {
		return nil, errors.New("invalid PostgreSQL audit recorder configuration")
	}
	return &AuditRecorder{store: store, timeout: timeout}, nil
}

func (recorder *AuditRecorder) Record(event audit.Event) error {
	validated, err := audit.NewEvent(event.ID, event.RequestID, event.Actor, event.Action, event.Resource, event.Outcome, event.Occurred)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), recorder.timeout)
	defer cancel()
	tx, err := recorder.store.BeginTx(ctx, &sql.TxOptions{})
	if err != nil {
		return fmt.Errorf("begin PostgreSQL audit transaction: %w", err)
	}
	defer tx.Rollback()
	var insertedID string
	if err := tx.QueryRowContext(ctx, appendAuditEventSQL, validated.ID, validated.Occurred.UTC(), validated.RequestID, validated.Actor, validated.Action, validated.Resource, validated.Outcome).Scan(&insertedID); err != nil {
		return fmt.Errorf("insert PostgreSQL audit event: %w", err)
	}
	if insertedID != validated.ID {
		return errors.New("inserted PostgreSQL audit event identifier mismatch")
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit PostgreSQL audit transaction: %w", err)
	}
	return nil
}

// List returns validated events in deterministic chronological order.
func (recorder *AuditRecorder) List() []audit.Event {
	ctx, cancel := context.WithTimeout(context.Background(), recorder.timeout)
	defer cancel()
	rows, err := recorder.store.QueryContext(ctx, listAuditEventsSQL)
	if err != nil {
		return nil
	}
	defer rows.Close()
	result := make([]audit.Event, 0)
	for rows.Next() {
		var event audit.Event
		if err := rows.Scan(&event.ID, &event.Occurred, &event.RequestID, &event.Actor, &event.Action, &event.Resource, &event.Outcome); err != nil {
			return nil
		}
		validated, err := audit.NewEvent(event.ID, event.RequestID, event.Actor, event.Action, event.Resource, event.Outcome, event.Occurred)
		if err != nil {
			return nil
		}
		result = append(result, validated)
	}
	if rows.Err() != nil {
		return nil
	}
	return result
}

var _ audit.Recorder = (*AuditRecorder)(nil)
