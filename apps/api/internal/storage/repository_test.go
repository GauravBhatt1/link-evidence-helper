package storage

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/audit"
	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/sourceadmin"
)

type fakeRepository struct{}

func (fakeRepository) List(context.Context) ([]sourceadmin.Source, error) { return nil, nil }
func (fakeRepository) Create(context.Context, sourceadmin.Draft, time.Time) (sourceadmin.Source, error) {
	return sourceadmin.Source{}, nil
}
func (fakeRepository) Update(context.Context, string, uint64, sourceadmin.Draft, time.Time) (sourceadmin.Source, error) {
	return sourceadmin.Source{}, nil
}
func (fakeRepository) Disable(context.Context, string, uint64, time.Time) (sourceadmin.Source, error) {
	return sourceadmin.Source{}, nil
}
func (fakeRepository) Append(context.Context, audit.Event) error { return nil }

type fakeTransaction struct{ repository fakeRepository }

func (transaction fakeTransaction) Sources() SourceRepository { return transaction.repository }
func (transaction fakeTransaction) Audit() AuditRepository     { return transaction.repository }

type fakeTransactor struct{ transaction Transaction }

func (transactor fakeTransactor) WithinTransaction(_ context.Context, fn func(Transaction) error) error {
	return fn(transactor.transaction)
}

func TestPersistenceContractsComposeSourceAndAuditAtomically(t *testing.T) {
	var _ SourceRepository = fakeRepository{}
	var _ AuditRepository = fakeRepository{}
	var _ Transaction = fakeTransaction{}
	var _ Transactor = fakeTransactor{}

	expected := errors.New("rollback")
	transactor := fakeTransactor{transaction: fakeTransaction{}}
	if err := transactor.WithinTransaction(context.Background(), func(transaction Transaction) error {
		if transaction.Sources() == nil || transaction.Audit() == nil {
			t.Fatal("transaction did not expose both repositories")
		}
		return expected
	}); !errors.Is(err, expected) {
		t.Fatalf("transaction callback error was not preserved: %v", err)
	}
}
