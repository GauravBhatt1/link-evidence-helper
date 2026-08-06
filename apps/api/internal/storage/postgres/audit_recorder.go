package postgres

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/audit"
)

const appendAuditEventSQL = `INSERT INTO admin_audit_events
(id, occurred_at, request_id, actor, action, resource, outcome)
VALUES ($1, $2, $3, $4, $5, $6, $7)
RETURNING id`

// AuditRecorder persists only the bounded, validated administrative audit contract.
// It never accepts request bodies, headers, credentials, URLs, or arbitrary metadata.
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
	if err := tx.QueryRowContext(ctx, appendAuditEventSQL,
		validated.ID,
		validated.Occurred.UTC(),
		validated.RequestID,
		validated.Actor,
		validated.Action,
		validated.Resource,
		validated.Outcome,
	).Scan(&insertedID); err != nil {
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

var _ audit.Recorder = (*AuditRecorder)(nil)
