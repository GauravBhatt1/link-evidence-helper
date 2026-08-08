// Package postgres contains explicit PostgreSQL persistence adapters.
// Importing this package never opens a database or applies migrations.
package postgres

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/sourceadmin"
)

const (
	listSourcesSQL = `SELECT id, display_name, kind, endpoint, query_parameter, result_root, title_field, url_field, allowed_result_hosts::text, enabled, revision, created_at, updated_at
FROM admin_sources
ORDER BY id ASC`
	createSourceSQL = `INSERT INTO admin_sources
(id, display_name, kind, endpoint, query_parameter, result_root, title_field, url_field, allowed_result_hosts, enabled, revision, created_at, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, 1, $11, $11)
RETURNING id, display_name, kind, endpoint, query_parameter, result_root, title_field, url_field, allowed_result_hosts::text, enabled, revision, created_at, updated_at`
	updateSourceSQL = `UPDATE admin_sources
SET display_name = $3, kind = $4, endpoint = $5, query_parameter = $6,
    result_root = $7, title_field = $8, url_field = $9,
    allowed_result_hosts = $10::jsonb, enabled = $11,
    revision = revision + 1, updated_at = $12
WHERE id = $1 AND revision = $2
RETURNING id, display_name, kind, endpoint, query_parameter, result_root, title_field, url_field, allowed_result_hosts::text, enabled, revision, created_at, updated_at`
	disableSourceSQL = `UPDATE admin_sources
SET enabled = false, revision = revision + 1, updated_at = $3
WHERE id = $1 AND revision = $2
RETURNING id, display_name, kind, endpoint, query_parameter, result_root, title_field, url_field, allowed_result_hosts::text, enabled, revision, created_at, updated_at`
	sourceExistsSQL = `SELECT EXISTS (SELECT 1 FROM admin_sources WHERE id = $1)`
)

// Rows is the bounded subset of database/sql.Rows used by this adapter.
type Rows interface {
	Next() bool
	Scan(dest ...any) error
	Err() error
	Close() error
}

// Row is the bounded subset of database/sql.Row used by this adapter.
type Row interface {
	Scan(dest ...any) error
}

// Queryer is implemented by both a database pool and a transaction.
type Queryer interface {
	QueryContext(ctx context.Context, query string, args ...any) (Rows, error)
	QueryRowContext(ctx context.Context, query string, args ...any) Row
}

// Tx is the explicit transaction boundary required by mutating operations.
type Tx interface {
	Queryer
	Commit() error
	Rollback() error
}

// Store supplies reads and explicit transactions. It deliberately does not
// expose connection strings, driver configuration, or migration execution.
type Store interface {
	Queryer
	BeginTx(ctx context.Context, opts *sql.TxOptions) (Tx, error)
}

// SourceRegistry persists credential-free administrative sources.
type SourceRegistry struct {
	store   Store
	timeout time.Duration
}

// NewSourceRegistry constructs a disabled-by-default adapter around a supplied
// store. Runtime code must explicitly create and inject the store.
func NewSourceRegistry(store Store, timeout time.Duration) (*SourceRegistry, error) {
	if store == nil || timeout <= 0 || timeout > 30*time.Second {
		return nil, errors.New("invalid PostgreSQL source registry configuration")
	}
	return &SourceRegistry{store: store, timeout: timeout}, nil
}

func (registry *SourceRegistry) List() []sourceadmin.Source {
	ctx, cancel := context.WithTimeout(context.Background(), registry.timeout)
	defer cancel()
	rows, err := registry.store.QueryContext(ctx, listSourcesSQL)
	if err != nil {
		return nil
	}
	defer rows.Close()
	result := make([]sourceadmin.Source, 0)
	for rows.Next() {
		source, scanErr := scanSource(rows)
		if scanErr != nil {
			return nil
		}
		result = append(result, source)
	}
	if rows.Err() != nil {
		return nil
	}
	sort.Slice(result, func(i, j int) bool { return result[i].ID < result[j].ID })
	return result
}

func (registry *SourceRegistry) Create(draft sourceadmin.Draft, now time.Time) (sourceadmin.Source, error) {
	normalized, err := validateDraft(draft, now)
	if err != nil {
		return sourceadmin.Source{}, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), registry.timeout)
	defer cancel()
	row := registry.store.QueryRowContext(ctx, createSourceSQL,
		normalized.ID, normalized.DisplayName, normalized.Kind, normalized.Endpoint,
		normalized.QueryParameter, normalized.ResultRoot, normalized.TitleField,
		normalized.URLField, allowedHostsJSON(normalized.AllowedResultHosts),
		normalized.Enabled, normalized.CreatedAt,
	)
	created, err := scanSource(row)
	if isUniqueViolation(err) {
		return sourceadmin.Source{}, sourceadmin.ErrSourceExists
	}
	if err != nil {
		return sourceadmin.Source{}, fmt.Errorf("create source: %w", err)
	}
	return created, nil
}

func (registry *SourceRegistry) Update(id string, expectedRevision uint64, draft sourceadmin.Draft, now time.Time) (sourceadmin.Source, error) {
	normalized, err := validateDraft(draft, now)
	if err != nil || normalized.ID != id || expectedRevision == 0 {
		return sourceadmin.Source{}, sourceadmin.ErrInvalidSource
	}
	return registry.mutate(id, func(ctx context.Context, tx Tx) (sourceadmin.Source, error) {
		return scanSource(tx.QueryRowContext(ctx, updateSourceSQL,
			id, expectedRevision, normalized.DisplayName, normalized.Kind,
			normalized.Endpoint, normalized.QueryParameter, normalized.ResultRoot,
			normalized.TitleField, normalized.URLField, allowedHostsJSON(normalized.AllowedResultHosts),
			normalized.Enabled, normalized.UpdatedAt,
		))
	})
}

func (registry *SourceRegistry) Disable(id string, expectedRevision uint64, now time.Time) (sourceadmin.Source, error) {
	if expectedRevision == 0 || now.IsZero() {
		return sourceadmin.Source{}, sourceadmin.ErrInvalidSource
	}
	// Reuse the domain validator to reject unsafe identifiers without exporting
	// implementation-only normalization helpers.
	if _, err := validateDraft(sourceadmin.Draft{ID: id, DisplayName: "validation", Kind: "http-json", Endpoint: "https://example.invalid/", Enabled: false}, now); err != nil {
		return sourceadmin.Source{}, sourceadmin.ErrInvalidSource
	}
	return registry.mutate(id, func(ctx context.Context, tx Tx) (sourceadmin.Source, error) {
		return scanSource(tx.QueryRowContext(ctx, disableSourceSQL, id, expectedRevision, now.UTC()))
	})
}

func (registry *SourceRegistry) mutate(id string, operation func(context.Context, Tx) (sourceadmin.Source, error)) (sourceadmin.Source, error) {
	ctx, cancel := context.WithTimeout(context.Background(), registry.timeout)
	defer cancel()
	tx, err := registry.store.BeginTx(ctx, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return sourceadmin.Source{}, fmt.Errorf("begin source transaction: %w", err)
	}
	defer tx.Rollback()
	updated, err := operation(ctx, tx)
	if errors.Is(err, sql.ErrNoRows) {
		exists, existsErr := sourceExists(ctx, tx, id)
		if existsErr != nil {
			return sourceadmin.Source{}, fmt.Errorf("resolve source conflict: %w", existsErr)
		}
		if exists {
			return sourceadmin.Source{}, sourceadmin.ErrRevisionConflict
		}
		return sourceadmin.Source{}, sourceadmin.ErrSourceNotFound
	}
	if err != nil {
		return sourceadmin.Source{}, fmt.Errorf("mutate source: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return sourceadmin.Source{}, fmt.Errorf("commit source transaction: %w", err)
	}
	return updated, nil
}

func sourceExists(ctx context.Context, queryer Queryer, id string) (bool, error) {
	var exists bool
	if err := queryer.QueryRowContext(ctx, sourceExistsSQL, id).Scan(&exists); err != nil {
		return false, err
	}
	return exists, nil
}

func validateDraft(draft sourceadmin.Draft, now time.Time) (sourceadmin.Source, error) {
	validator := sourceadmin.NewMemoryRegistry()
	normalized, err := validator.Create(draft, now)
	if err != nil {
		return sourceadmin.Source{}, sourceadmin.ErrInvalidSource
	}
	return normalized, nil
}

type scanner interface {
	Scan(dest ...any) error
}

func scanSource(row scanner) (sourceadmin.Source, error) {
	var source sourceadmin.Source
	var allowedHosts string
	if err := row.Scan(
		&source.ID, &source.DisplayName, &source.Kind, &source.Endpoint,
		&source.QueryParameter, &source.ResultRoot, &source.TitleField, &source.URLField,
		&allowedHosts, &source.Enabled, &source.Revision, &source.CreatedAt, &source.UpdatedAt,
	); err != nil {
		return sourceadmin.Source{}, err
	}
	if allowedHosts != "" {
		if err := json.Unmarshal([]byte(allowedHosts), &source.AllowedResultHosts); err != nil {
			return sourceadmin.Source{}, errors.New("invalid source row")
		}
	}
	if source.Revision == 0 || source.CreatedAt.IsZero() || source.UpdatedAt.IsZero() {
		return sourceadmin.Source{}, errors.New("invalid source row")
	}
	source.CreatedAt = source.CreatedAt.UTC()
	source.UpdatedAt = source.UpdatedAt.UTC()
	return source, nil
}

func allowedHostsJSON(hosts []string) string {
	if hosts == nil {
		hosts = []string{}
	}
	data, err := json.Marshal(hosts)
	if err != nil {
		return "[]"
	}
	return string(data)
}

type sqlStateError interface {
	SQLState() string
}

func isUniqueViolation(err error) bool {
	var state sqlStateError
	return errors.As(err, &state) && state.SQLState() == "23505"
}

var _ sourceadmin.Registry = (*SourceRegistry)(nil)
