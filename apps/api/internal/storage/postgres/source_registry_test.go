package postgres

import (
	"context"
	"database/sql"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/sourceadmin"
)

func TestNewSourceRegistryRejectsUnsafeConfiguration(t *testing.T) {
	if _, err := NewSourceRegistry(nil, time.Second); err == nil {
		t.Fatal("expected nil store rejection")
	}
	store := &fakeStore{}
	for _, timeout := range []time.Duration{0, -time.Second, 31 * time.Second} {
		if _, err := NewSourceRegistry(store, timeout); err == nil {
			t.Fatalf("expected timeout %s rejection", timeout)
		}
	}
}

func TestCreateValidatesNormalizesAndUsesBoundedQuery(t *testing.T) {
	now := time.Date(2026, 8, 6, 1, 2, 3, 0, time.FixedZone("test", 3600))
	store := &fakeStore{}
	store.queryRow = func(_ context.Context, query string, args ...any) Row {
		if query != createSourceSQL {
			t.Fatalf("unexpected query: %s", query)
		}
		if got := args[3]; got != "https://example.invalid/" {
			t.Fatalf("endpoint was not normalized: %#v", got)
		}
		if got := args[4]; got != "q" {
			t.Fatalf("query parameter was not defaulted: %#v", got)
		}
		if got := args[8]; got != "[]" {
			t.Fatalf("allowed hosts were not JSON encoded: %#v", got)
		}
		if got := args[10].(time.Time); !got.Equal(now.UTC()) || got.Location() != time.UTC {
			t.Fatalf("timestamp was not normalized to UTC: %v", got)
		}
		return sourceRow(sourceadmin.Source{
			ID: "alpha", DisplayName: "Alpha", Kind: "http-json", Endpoint: "https://example.invalid/",
			Enabled: true, Revision: 1, CreatedAt: now.UTC(), UpdatedAt: now.UTC(),
		})
	}
	registry := mustRegistry(t, store)
	created, err := registry.Create(sourceadmin.Draft{
		ID: "alpha", DisplayName: "Alpha", Kind: "http-json", Endpoint: "https://example.invalid", Enabled: true,
	}, now)
	if err != nil {
		t.Fatal(err)
	}
	if created.ID != "alpha" || created.Revision != 1 {
		t.Fatalf("unexpected source: %#v", created)
	}
}

func TestCreateMapsUniqueViolationWithoutLeakingDatabaseDetails(t *testing.T) {
	store := &fakeStore{queryRow: func(context.Context, string, ...any) Row {
		return fakeRow{err: postgresError{state: "23505", message: "sensitive constraint detail"}}
	}}
	registry := mustRegistry(t, store)
	_, err := registry.Create(validDraft("alpha"), time.Now())
	if !errors.Is(err, sourceadmin.ErrSourceExists) {
		t.Fatalf("expected source exists, got %v", err)
	}
	if strings.Contains(err.Error(), "sensitive") {
		t.Fatalf("database detail leaked: %v", err)
	}
}

func TestUpdateCommitsExactlyOnce(t *testing.T) {
	now := time.Now().UTC()
	tx := &fakeTx{}
	tx.queryRow = func(_ context.Context, query string, args ...any) Row {
		if query != updateSourceSQL {
			t.Fatalf("unexpected query: %s", query)
		}
		if args[1] != uint64(2) {
			t.Fatalf("unexpected revision: %#v", args[1])
		}
		if args[5] != "q" || args[7] != "title" || args[8] != "url" || args[9] != "[]" {
			t.Fatalf("unexpected search mapping args: %#v", args)
		}
		return sourceRow(sourceadmin.Source{
			ID: "alpha", DisplayName: "Alpha 2", Kind: "http-html", Endpoint: "https://example.invalid/path",
			Enabled: true, Revision: 3, CreatedAt: now.Add(-time.Hour), UpdatedAt: now,
		})
	}
	store := &fakeStore{beginTx: func(context.Context, *sql.TxOptions) (Tx, error) { return tx, nil }}
	registry := mustRegistry(t, store)
	updated, err := registry.Update("alpha", 2, sourceadmin.Draft{
		ID: "alpha", DisplayName: "Alpha 2", Kind: "http-html", Endpoint: "https://example.invalid/path", Enabled: true,
	}, now)
	if err != nil {
		t.Fatal(err)
	}
	if updated.Revision != 3 || tx.commits != 1 {
		t.Fatalf("transaction was not committed exactly once: source=%#v commits=%d", updated, tx.commits)
	}
}

func TestUpdateDistinguishesRevisionConflictFromMissingSource(t *testing.T) {
	for _, test := range []struct {
		name   string
		exists bool
		want   error
	}{
		{name: "conflict", exists: true, want: sourceadmin.ErrRevisionConflict},
		{name: "missing", exists: false, want: sourceadmin.ErrSourceNotFound},
	} {
		t.Run(test.name, func(t *testing.T) {
			tx := &fakeTx{}
			tx.queryRow = func(_ context.Context, query string, _ ...any) Row {
				if query == updateSourceSQL {
					return fakeRow{err: sql.ErrNoRows}
				}
				if query == sourceExistsSQL {
					return fakeRow{values: []any{test.exists}}
				}
				t.Fatalf("unexpected query: %s", query)
				return nil
			}
			store := &fakeStore{beginTx: func(context.Context, *sql.TxOptions) (Tx, error) { return tx, nil }}
			registry := mustRegistry(t, store)
			_, err := registry.Update("alpha", 2, validDraft("alpha"), time.Now())
			if !errors.Is(err, test.want) {
				t.Fatalf("expected %v, got %v", test.want, err)
			}
			if tx.commits != 0 || tx.rollbacks != 1 {
				t.Fatalf("failed mutation transaction handling: commits=%d rollbacks=%d", tx.commits, tx.rollbacks)
			}
		})
	}
}

func TestListReturnsNilOnDatabaseFailureAndSortsDefensively(t *testing.T) {
	store := &fakeStore{query: func(context.Context, string, ...any) (Rows, error) {
		return &fakeRows{sources: []sourceadmin.Source{
			rowSource("zeta", 1), rowSource("alpha", 1),
		}}, nil
	}}
	registry := mustRegistry(t, store)
	listed := registry.List()
	if len(listed) != 2 || listed[0].ID != "alpha" || listed[1].ID != "zeta" {
		t.Fatalf("unexpected ordering: %#v", listed)
	}

	store.query = func(context.Context, string, ...any) (Rows, error) { return nil, errors.New("private database host") }
	if got := registry.List(); got != nil {
		t.Fatalf("expected nil on read failure, got %#v", got)
	}
}

func mustRegistry(t *testing.T, store Store) *SourceRegistry {
	t.Helper()
	registry, err := NewSourceRegistry(store, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	return registry
}

func validDraft(id string) sourceadmin.Draft {
	return sourceadmin.Draft{ID: id, DisplayName: "Alpha", Kind: "http-json", Endpoint: "https://example.invalid/", Enabled: true}
}

func rowSource(id string, revision uint64) sourceadmin.Source {
	now := time.Date(2026, 8, 6, 0, 0, 0, 0, time.UTC)
	return sourceadmin.Source{ID: id, DisplayName: id, Kind: "http-json", Endpoint: "https://example.invalid/", Enabled: true, Revision: revision, CreatedAt: now, UpdatedAt: now}
}

func sourceRow(source sourceadmin.Source) Row {
	if source.QueryParameter == "" {
		source.QueryParameter = "q"
	}
	if source.TitleField == "" {
		source.TitleField = "title"
	}
	if source.URLField == "" {
		source.URLField = "url"
	}
	return fakeRow{values: []any{
		source.ID, source.DisplayName, source.Kind, source.Endpoint,
		source.QueryParameter, source.ResultRoot, source.TitleField, source.URLField, allowedHostsJSON(source.AllowedResultHosts),
		source.Enabled, source.Revision, source.CreatedAt, source.UpdatedAt,
	}}
}

type fakeStore struct {
	query    func(context.Context, string, ...any) (Rows, error)
	queryRow func(context.Context, string, ...any) Row
	beginTx  func(context.Context, *sql.TxOptions) (Tx, error)
}

func (store *fakeStore) QueryContext(ctx context.Context, query string, args ...any) (Rows, error) {
	if store.query == nil {
		return nil, errors.New("unexpected query")
	}
	return store.query(ctx, query, args...)
}
func (store *fakeStore) QueryRowContext(ctx context.Context, query string, args ...any) Row {
	if store.queryRow == nil {
		return fakeRow{err: errors.New("unexpected query row")}
	}
	return store.queryRow(ctx, query, args...)
}
func (store *fakeStore) BeginTx(ctx context.Context, opts *sql.TxOptions) (Tx, error) {
	if store.beginTx == nil {
		return nil, errors.New("unexpected transaction")
	}
	return store.beginTx(ctx, opts)
}

type fakeTx struct {
	query     func(context.Context, string, ...any) (Rows, error)
	queryRow  func(context.Context, string, ...any) Row
	commits   int
	rollbacks int
}

func (tx *fakeTx) QueryContext(ctx context.Context, query string, args ...any) (Rows, error) {
	if tx.query == nil {
		return nil, errors.New("unexpected query")
	}
	return tx.query(ctx, query, args...)
}
func (tx *fakeTx) QueryRowContext(ctx context.Context, query string, args ...any) Row {
	if tx.queryRow == nil {
		return fakeRow{err: errors.New("unexpected query row")}
	}
	return tx.queryRow(ctx, query, args...)
}
func (tx *fakeTx) Commit() error   { tx.commits++; return nil }
func (tx *fakeTx) Rollback() error { tx.rollbacks++; return nil }

type fakeRow struct {
	values []any
	err    error
}

func (row fakeRow) Scan(dest ...any) error {
	if row.err != nil {
		return row.err
	}
	if len(dest) != len(row.values) {
		return errors.New("scan arity mismatch")
	}
	for index := range dest {
		target := reflect.ValueOf(dest[index])
		if target.Kind() != reflect.Pointer || target.IsNil() {
			return errors.New("invalid scan destination")
		}
		value := reflect.ValueOf(row.values[index])
		if !value.Type().AssignableTo(target.Elem().Type()) {
			return errors.New("scan type mismatch")
		}
		target.Elem().Set(value)
	}
	return nil
}

type fakeRows struct {
	sources []sourceadmin.Source
	index   int
	closed  bool
	err     error
}

func (rows *fakeRows) Next() bool { return rows.index < len(rows.sources) }
func (rows *fakeRows) Scan(dest ...any) error {
	if rows.index >= len(rows.sources) {
		return errors.New("scan past end")
	}
	err := sourceRow(rows.sources[rows.index]).Scan(dest...)
	rows.index++
	return err
}
func (rows *fakeRows) Err() error   { return rows.err }
func (rows *fakeRows) Close() error { rows.closed = true; return nil }

type postgresError struct {
	state   string
	message string
}

func (err postgresError) Error() string    { return err.message }
func (err postgresError) SQLState() string { return err.state }
