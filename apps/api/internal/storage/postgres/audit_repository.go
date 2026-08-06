package postgres

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/audit"
)

const appendAuditEventSQL = `INSERT INTO admin_audit_events
(event_id, correlation_id, actor, action, resource, outcome, occurred_at)
VALUES ($1, $2, $3, $4, $5, $6, $7)
RETURNING event_id`

var ErrAuditEventExists = errors.New("audit event already exists")

// AuditRepository persists the bounded audit.Event contract. It never opens a
// database connection and is usable with either a pool or an existing
// transaction through the Queryer interface.
type AuditRepository struct {
	queryer Queryer
	timeout time.Duration
}

func NewAuditRepository(queryer Queryer, timeout time.Duration) (*AuditRepository, error) {
	if queryer == nil || timeout <= 0 || timeout > 30*time.Second {
		return nil, errors.New("invalid PostgreSQL audit repository configuration")
	}
	return &AuditRepository{queryer: queryer, timeout: timeout}, nil
}

func (repository *AuditRepository) Append(ctx context.Context, event audit.Event) error {
	if ctx == nil {
		return audit.ErrInvalidEvent
	}
	normalized, err := audit.NewEvent(
		event.ID,
		event.RequestID,
		event.Actor,
		event.Action,
		event.Resource,
		event.Outcome,
		event.Occurred,
	)
	if err != nil || !durableAuditVocabulary(normalized) {
		return audit.ErrInvalidEvent
	}

	bounded, cancel := context.WithTimeout(ctx, repository.timeout)
	defer cancel()
	var persistedID string
	if err := repository.queryer.QueryRowContext(
		bounded,
		appendAuditEventSQL,
		normalized.ID,
		normalized.RequestID,
		normalized.Actor,
		normalized.Action,
		normalized.Resource,
		normalized.Outcome,
		normalized.Occurred,
	).Scan(&persistedID); err != nil {
		if isUniqueViolation(err) {
			return ErrAuditEventExists
		}
		return fmt.Errorf("append audit event: %w", err)
	}
	if persistedID != normalized.ID {
		return errors.New("append audit event returned unexpected identifier")
	}
	return nil
}

func durableAuditVocabulary(event audit.Event) bool {
	switch event.Action {
	case "source.create", "source.update", "source.disable":
	default:
		return false
	}
	return event.Outcome == "success" || event.Outcome == "failure"
}
