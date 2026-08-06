package postgres

import (
	"context"
	"database/sql"
	"errors"
)

// SQLStore adapts database/sql to the narrow persistence interfaces used by
// this package. Construction is explicit and does not open a connection.
type SQLStore struct {
	db *sql.DB
}

func NewSQLStore(db *sql.DB) (*SQLStore, error) {
	if db == nil {
		return nil, errors.New("nil PostgreSQL database")
	}
	return &SQLStore{db: db}, nil
}

func (store *SQLStore) QueryContext(ctx context.Context, query string, args ...any) (Rows, error) {
	return store.db.QueryContext(ctx, query, args...)
}

func (store *SQLStore) QueryRowContext(ctx context.Context, query string, args ...any) Row {
	return store.db.QueryRowContext(ctx, query, args...)
}

func (store *SQLStore) BeginTx(ctx context.Context, opts *sql.TxOptions) (Tx, error) {
	tx, err := store.db.BeginTx(ctx, opts)
	if err != nil {
		return nil, err
	}
	return sqlTx{tx: tx}, nil
}

type sqlTx struct {
	tx *sql.Tx
}

func (transaction sqlTx) QueryContext(ctx context.Context, query string, args ...any) (Rows, error) {
	return transaction.tx.QueryContext(ctx, query, args...)
}

func (transaction sqlTx) QueryRowContext(ctx context.Context, query string, args ...any) Row {
	return transaction.tx.QueryRowContext(ctx, query, args...)
}

func (transaction sqlTx) Commit() error   { return transaction.tx.Commit() }
func (transaction sqlTx) Rollback() error { return transaction.tx.Rollback() }

var _ Store = (*SQLStore)(nil)
