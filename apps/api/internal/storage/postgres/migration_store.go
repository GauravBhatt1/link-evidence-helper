package postgres

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"link-evidence-helper/apps/api/internal/storage/migrations"
)

const migrationAdvisoryLockID int64 = 721946310784512903

var errMigrationStoreUnavailable = errors.New("migration store unavailable")

// MigrationDB supplies a dedicated PostgreSQL session. The session is required
// because advisory locks are connection-scoped.
type MigrationDB interface {
	MigrationConn(context.Context) (MigrationConn, error)
}

type MigrationConn interface {
	ExecContext(context.Context, string, ...any) (sql.Result, error)
	QueryContext(context.Context, string, ...any) (MigrationRows, error)
	QueryRowContext(context.Context, string, ...any) MigrationRow
	BeginTx(context.Context, *sql.TxOptions) (MigrationTx, error)
	Close() error
}

type MigrationTx interface {
	ExecContext(context.Context, string, ...any) (sql.Result, error)
	Commit() error
	Rollback() error
}

type MigrationRow interface {
	Scan(...any) error
}

type MigrationRows interface {
	Next() bool
	Scan(...any) error
	Err() error
	Close() error
}

// SQLMigrationDB adapts an existing *sql.DB. It never opens a driver or reads a
// connection string; runtime ownership remains with the caller.
type SQLMigrationDB struct {
	DB *sql.DB
}

func (database SQLMigrationDB) MigrationConn(ctx context.Context) (MigrationConn, error) {
	if database.DB == nil {
		return nil, errMigrationStoreUnavailable
	}
	connection, err := database.DB.Conn(ctx)
	if err != nil {
		return nil, err
	}
	return sqlMigrationConn{Conn: connection}, nil
}

type sqlMigrationConn struct{ Conn *sql.Conn }

func (connection sqlMigrationConn) ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error) {
	return connection.Conn.ExecContext(ctx, query, args...)
}
func (connection sqlMigrationConn) QueryContext(ctx context.Context, query string, args ...any) (MigrationRows, error) {
	return connection.Conn.QueryContext(ctx, query, args...)
}
func (connection sqlMigrationConn) QueryRowContext(ctx context.Context, query string, args ...any) MigrationRow {
	return connection.Conn.QueryRowContext(ctx, query, args...)
}
func (connection sqlMigrationConn) BeginTx(ctx context.Context, options *sql.TxOptions) (MigrationTx, error) {
	return connection.Conn.BeginTx(ctx, options)
}
func (connection sqlMigrationConn) Close() error { return connection.Conn.Close() }

// MigrationStore implements the migration runner's locking and durable history
// boundaries with a dedicated PostgreSQL session.
type MigrationStore struct {
	database MigrationDB
	timeout  time.Duration
}

func NewMigrationStore(database MigrationDB, timeout time.Duration) (*MigrationStore, error) {
	if database == nil {
		return nil, errMigrationStoreUnavailable
	}
	if timeout <= 0 || timeout > 30*time.Second {
		return nil, errors.New("migration timeout must be between 1ns and 30s")
	}
	return &MigrationStore{database: database, timeout: timeout}, nil
}

func (store *MigrationStore) WithMigrationLock(ctx context.Context, run func(migrations.LockedStore) error) error {
	if run == nil {
		return errors.New("migration callback is required")
	}
	bounded, cancel := context.WithTimeout(ctx, store.timeout)
	defer cancel()
	connection, err := store.database.MigrationConn(bounded)
	if err != nil {
		return fmt.Errorf("%w: acquire connection", errMigrationStoreUnavailable)
	}
	defer connection.Close()

	var locked bool
	if err := connection.QueryRowContext(bounded, `SELECT pg_try_advisory_lock($1)`, migrationAdvisoryLockID).Scan(&locked); err != nil {
		return fmt.Errorf("%w: acquire advisory lock", errMigrationStoreUnavailable)
	}
	if !locked {
		return migrations.ErrMigrationLocked
	}
	defer connection.ExecContext(context.WithoutCancel(ctx), `SELECT pg_advisory_unlock($1)`, migrationAdvisoryLockID) // best-effort session cleanup

	lockedStore := &postgresLockedMigrationStore{connection: connection}
	if err := lockedStore.ensureHistoryTable(bounded); err != nil {
		return err
	}
	return run(lockedStore)
}

type postgresLockedMigrationStore struct {
	connection MigrationConn
}

func (store *postgresLockedMigrationStore) ensureHistoryTable(ctx context.Context) error {
	_, err := store.connection.ExecContext(ctx, `
CREATE TABLE IF NOT EXISTS schema_migrations (
    version BIGINT PRIMARY KEY CHECK (version > 0),
    name TEXT NOT NULL CHECK (name ~ '^[a-z0-9_]+$'),
    checksum CHAR(64) NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)`)
	if err != nil {
		return fmt.Errorf("ensure migration history: %w", errMigrationStoreUnavailable)
	}
	return nil
}

func (store *postgresLockedMigrationStore) Applied(ctx context.Context) ([]migrations.Applied, error) {
	rows, err := store.connection.QueryContext(ctx, `SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version ASC`)
	if err != nil {
		return nil, errMigrationStoreUnavailable
	}
	defer rows.Close()
	var history []migrations.Applied
	for rows.Next() {
		var record migrations.Applied
		if err := rows.Scan(&record.Version, &record.Name, &record.Checksum, &record.AppliedAt); err != nil {
			return nil, errMigrationStoreUnavailable
		}
		record.AppliedAt = record.AppliedAt.UTC()
		history = append(history, record)
	}
	if err := rows.Err(); err != nil {
		return nil, errMigrationStoreUnavailable
	}
	return history, nil
}

func (store *postgresLockedMigrationStore) Apply(ctx context.Context, step migrations.Step) error {
	transaction, err := store.connection.BeginTx(ctx, &sql.TxOptions{Isolation: sql.LevelSerializable})
	if err != nil {
		return errMigrationStoreUnavailable
	}
	defer transaction.Rollback()
	if _, err := transaction.ExecContext(ctx, step.SQL); err != nil {
		return errMigrationStoreUnavailable
	}
	if _, err := transaction.ExecContext(ctx,
		`INSERT INTO schema_migrations (version, name, checksum) VALUES ($1, $2, $3)`,
		step.Version, step.Name, step.Checksum,
	); err != nil {
		return errMigrationStoreUnavailable
	}
	if err := transaction.Commit(); err != nil {
		return errMigrationStoreUnavailable
	}
	return nil
}

func (store *postgresLockedMigrationStore) Rollback(ctx context.Context, step migrations.Step) error {
	transaction, err := store.connection.BeginTx(ctx, &sql.TxOptions{Isolation: sql.LevelSerializable})
	if err != nil {
		return errMigrationStoreUnavailable
	}
	defer transaction.Rollback()
	if _, err := transaction.ExecContext(ctx, step.SQL); err != nil {
		return errMigrationStoreUnavailable
	}
	result, err := transaction.ExecContext(ctx, `DELETE FROM schema_migrations WHERE version = $1`, step.Version)
	if err != nil {
		return errMigrationStoreUnavailable
	}
	rows, err := result.RowsAffected()
	if err != nil || rows != 1 {
		return errMigrationStoreUnavailable
	}
	if err := transaction.Commit(); err != nil {
		return errMigrationStoreUnavailable
	}
	return nil
}
