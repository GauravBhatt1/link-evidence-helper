// Package storage defines durable persistence boundaries without selecting or opening a database.
package storage

import (
	"context"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/audit"
	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/sourceadmin"
)

// SourceRepository is the context-aware durable counterpart of sourceadmin.Registry.
// Implementations must preserve optimistic revision conflicts and deterministic ordering.
type SourceRepository interface {
	List(ctx context.Context) ([]sourceadmin.Source, error)
	Create(ctx context.Context, draft sourceadmin.Draft, now time.Time) (sourceadmin.Source, error)
	Update(ctx context.Context, id string, expectedRevision uint64, draft sourceadmin.Draft, now time.Time) (sourceadmin.Source, error)
	Disable(ctx context.Context, id string, expectedRevision uint64, now time.Time) (sourceadmin.Source, error)
}

// AuditRepository persists only the bounded, validated audit.Event contract.
type AuditRepository interface {
	Append(ctx context.Context, event audit.Event) error
}

// Transaction groups source mutation and audit persistence atomically.
// Callers must return an error to roll back the transaction.
type Transaction interface {
	Sources() SourceRepository
	Audit() AuditRepository
}

// Transactor owns transaction lifecycle. It must commit only when fn returns nil,
// and roll back on any error or panic.
type Transactor interface {
	WithinTransaction(ctx context.Context, fn func(Transaction) error) error
}
