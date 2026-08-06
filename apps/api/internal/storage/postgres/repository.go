// Package postgres provides disabled-by-default PostgreSQL persistence boundaries.
package postgres

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"sort"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/audit"
	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/sourceadmin"
)

var ErrInvalidDatabase = errors.New("invalid postgres database")

// Beginner is the minimum database boundary required by the repositories.
type Beginner interface {
	BeginTx(context.Context, *sql.TxOptions) (*sql.Tx, error)
}

// Repositories groups durable administrative repositories sharing one database.
type Repositories struct {
	db Beginner
}

func New(db Beginner) (*Repositories, error) {
	if db == nil {
		return nil, ErrInvalidDatabase
	}
	return &Repositories{db: db}, nil
}

// ListSources returns sources in deterministic identifier order.
func (repositories *Repositories) ListSources(ctx context.Context) ([]sourceadmin.Source, error) {
	tx, err := repositories.db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return nil, fmt.Errorf("begin source list transaction: %w", err)
	}
	defer tx.Rollback()

	rows, err := tx.QueryContext(ctx, `
SELECT id, display_name, kind, endpoint, enabled, revision, created_at, updated_at
FROM admin_sources
ORDER BY id`)
	if err != nil {
		return nil, fmt.Errorf("query sources: %w", err)
	}
	defer rows.Close()

	result := make([]sourceadmin.Source, 0)
	for rows.Next() {
		var source sourceadmin.Source
		if err := rows.Scan(&source.ID, &source.DisplayName, &source.Kind, &source.Endpoint, &source.Enabled, &source.Revision, &source.CreatedAt, &source.UpdatedAt); err != nil {
			return nil, fmt.Errorf("scan source: %w", err)
		}
		source.CreatedAt = source.CreatedAt.UTC()
		source.UpdatedAt = source.UpdatedAt.UTC()
		result = append(result, source)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate sources: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("commit source list transaction: %w", err)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].ID < result[j].ID })
	return result, nil
}

// CreateSource inserts one validated source in a transaction.
func (repositories *Repositories) CreateSource(ctx context.Context, draft sourceadmin.Draft, now time.Time) (sourceadmin.Source, error) {
	validated, err := validateDraft(draft, now)
	if err != nil {
		return sourceadmin.Source{}, err
	}
	tx, err := repositories.db.BeginTx(ctx, nil)
	if err != nil {
		return sourceadmin.Source{}, fmt.Errorf("begin source create transaction: %w", err)
	}
	defer tx.Rollback()

	var source sourceadmin.Source
	err = tx.QueryRowContext(ctx, `
INSERT INTO admin_sources (id, display_name, kind, endpoint, enabled, revision, created_at, updated_at)
VALUES ($1, $2, $3, $4, $5, 1, $6, $6)
RETURNING id, display_name, kind, endpoint, enabled, revision, created_at, updated_at`,
		validated.ID, validated.DisplayName, validated.Kind, validated.Endpoint, validated.Enabled, now.UTC(),
	).Scan(&source.ID, &source.DisplayName, &source.Kind, &source.Endpoint, &source.Enabled, &source.Revision, &source.CreatedAt, &source.UpdatedAt)
	if err != nil {
		return sourceadmin.Source{}, fmt.Errorf("insert source: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return sourceadmin.Source{}, fmt.Errorf("commit source create transaction: %w", err)
	}
	source.CreatedAt = source.CreatedAt.UTC()
	source.UpdatedAt = source.UpdatedAt.UTC()
	return source, nil
}

// UpdateSource applies optimistic revision protection in the database predicate.
func (repositories *Repositories) UpdateSource(ctx context.Context, id string, expectedRevision uint64, draft sourceadmin.Draft, now time.Time) (sourceadmin.Source, error) {
	validated, err := validateDraft(draft, now)
	if err != nil || validated.ID != id || expectedRevision == 0 {
		return sourceadmin.Source{}, sourceadmin.ErrInvalidSource
	}
	tx, err := repositories.db.BeginTx(ctx, nil)
	if err != nil {
		return sourceadmin.Source{}, fmt.Errorf("begin source update transaction: %w", err)
	}
	defer tx.Rollback()

	var source sourceadmin.Source
	err = tx.QueryRowContext(ctx, `
UPDATE admin_sources
SET display_name = $2, kind = $3, endpoint = $4, enabled = $5,
    revision = revision + 1, updated_at = $6
WHERE id = $1 AND revision = $7
RETURNING id, display_name, kind, endpoint, enabled, revision, created_at, updated_at`,
		id, validated.DisplayName, validated.Kind, validated.Endpoint, validated.Enabled, now.UTC(), expectedRevision,
	).Scan(&source.ID, &source.DisplayName, &source.Kind, &source.Endpoint, &source.Enabled, &source.Revision, &source.CreatedAt, &source.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return sourceadmin.Source{}, repositories.classifyMissingSource(ctx, tx, id)
	}
	if err != nil {
		return sourceadmin.Source{}, fmt.Errorf("update source: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return sourceadmin.Source{}, fmt.Errorf("commit source update transaction: %w", err)
	}
	source.CreatedAt = source.CreatedAt.UTC()
	source.UpdatedAt = source.UpdatedAt.UTC()
	return source, nil
}

// DisableSource disables one source with optimistic revision protection.
func (repositories *Repositories) DisableSource(ctx context.Context, id string, expectedRevision uint64, now time.Time) (sourceadmin.Source, error) {
	if id == "" || expectedRevision == 0 || now.IsZero() {
		return sourceadmin.Source{}, sourceadmin.ErrInvalidSource
	}
	tx, err := repositories.db.BeginTx(ctx, nil)
	if err != nil {
		return sourceadmin.Source{}, fmt.Errorf("begin source disable transaction: %w", err)
	}
	defer tx.Rollback()

	var source sourceadmin.Source
	err = tx.QueryRowContext(ctx, `
UPDATE admin_sources
SET enabled = FALSE, revision = revision + 1, updated_at = $3
WHERE id = $1 AND revision = $2
RETURNING id, display_name, kind, endpoint, enabled, revision, created_at, updated_at`,
		id, expectedRevision, now.UTC(),
	).Scan(&source.ID, &source.DisplayName, &source.Kind, &source.Endpoint, &source.Enabled, &source.Revision, &source.CreatedAt, &source.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return sourceadmin.Source{}, repositories.classifyMissingSource(ctx, tx, id)
	}
	if err != nil {
		return sourceadmin.Source{}, fmt.Errorf("disable source: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return sourceadmin.Source{}, fmt.Errorf("commit source disable transaction: %w", err)
	}
	source.CreatedAt = source.CreatedAt.UTC()
	source.UpdatedAt = source.UpdatedAt.UTC()
	return source, nil
}

func (repositories *Repositories) classifyMissingSource(ctx context.Context, tx *sql.Tx, id string) error {
	var exists bool
	if err := tx.QueryRowContext(ctx, `SELECT EXISTS (SELECT 1 FROM admin_sources WHERE id = $1)`, id).Scan(&exists); err != nil {
		return fmt.Errorf("classify source conflict: %w", err)
	}
	if exists {
		return sourceadmin.ErrRevisionConflict
	}
	return sourceadmin.ErrSourceNotFound
}

// AppendAuditEvent writes only the bounded audit contract in its own transaction.
func (repositories *Repositories) AppendAuditEvent(ctx context.Context, event audit.Event) error {
	validated, err := audit.NewEvent(event.ID, event.RequestID, event.Actor, event.Action, event.Resource, event.Outcome, event.Occurred)
	if err != nil {
		return err
	}
	tx, err := repositories.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin audit transaction: %w", err)
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, `
INSERT INTO admin_audit_events (id, occurred_at, request_id, actor, action, resource, outcome)
VALUES ($1, $2, $3, $4, $5, $6, $7)`, validated.ID, validated.Occurred, validated.RequestID, validated.Actor, validated.Action, validated.Resource, validated.Outcome); err != nil {
		return fmt.Errorf("insert audit event: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit audit transaction: %w", err)
	}
	return nil
}

func validateDraft(draft sourceadmin.Draft, now time.Time) (sourceadmin.Draft, error) {
	if now.IsZero() {
		return sourceadmin.Draft{}, sourceadmin.ErrInvalidSource
	}
	registry := sourceadmin.NewMemoryRegistry()
	source, err := registry.Create(draft, now)
	if err != nil {
		return sourceadmin.Draft{}, err
	}
	return sourceadmin.Draft{ID: source.ID, DisplayName: source.DisplayName, Kind: source.Kind, Endpoint: source.Endpoint, Enabled: source.Enabled}, nil
}
