package sqliteimport

import (
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"
)

const maxRows = 10000

var (
	ErrInvalidSnapshot = errors.New("invalid sqlite import snapshot")
	ErrDuplicateID     = errors.New("duplicate import identifier")
	ErrTooManyRows     = errors.New("sqlite import snapshot exceeds row limit")
)

// Snapshot is a credential-free, already-extracted representation of legacy
// SQLite state. Reading SQLite files is intentionally kept outside this package.
type Snapshot struct {
	Sources []SourceRow
	Audits  []AuditRow
}

type SourceRow struct {
	ID          string
	DisplayName string
	Domain      string
	Endpoint    string
	Enabled     bool
	Revision    int64
}

type AuditRow struct {
	ID         string
	Actor      string
	Action     string
	Resource   string
	ResourceID string
	Outcome    string
	OccurredAt time.Time
}

type Operation struct {
	Kind string
	ID   string
}

type Plan struct {
	DryRun     bool
	Operations []Operation
	Rollback   []Operation
}

// BuildPlan validates and orders a dry-run-first import without opening either
// SQLite or PostgreSQL. Callers must explicitly opt into execution elsewhere.
func BuildPlan(snapshot Snapshot) (Plan, error) {
	if len(snapshot.Sources)+len(snapshot.Audits) > maxRows {
		return Plan{}, ErrTooManyRows
	}

	seen := make(map[string]struct{}, len(snapshot.Sources)+len(snapshot.Audits))
	ops := make([]Operation, 0, len(snapshot.Sources)+len(snapshot.Audits))

	for _, row := range snapshot.Sources {
		if err := validateSource(row); err != nil {
			return Plan{}, err
		}
		if err := reserveID(seen, "source", row.ID); err != nil {
			return Plan{}, err
		}
		ops = append(ops, Operation{Kind: "source", ID: row.ID})
	}

	for _, row := range snapshot.Audits {
		if err := validateAudit(row); err != nil {
			return Plan{}, err
		}
		if err := reserveID(seen, "audit", row.ID); err != nil {
			return Plan{}, err
		}
		ops = append(ops, Operation{Kind: "audit", ID: row.ID})
	}

	sort.Slice(ops, func(i, j int) bool {
		if ops[i].Kind == ops[j].Kind {
			return ops[i].ID < ops[j].ID
		}
		return ops[i].Kind < ops[j].Kind
	})

	rollback := make([]Operation, len(ops))
	for i := range ops {
		rollback[len(ops)-1-i] = Operation{Kind: "delete_" + ops[i].Kind, ID: ops[i].ID}
	}

	return Plan{DryRun: true, Operations: ops, Rollback: rollback}, nil
}

func reserveID(seen map[string]struct{}, kind, id string) error {
	key := kind + ":" + id
	if _, ok := seen[key]; ok {
		return fmt.Errorf("%w: %s", ErrDuplicateID, kind)
	}
	seen[key] = struct{}{}
	return nil
}

func validateSource(row SourceRow) error {
	if !bounded(row.ID, 1, 128) || !bounded(row.DisplayName, 1, 128) || !bounded(row.Domain, 1, 253) || !bounded(row.Endpoint, 1, 2048) || row.Revision < 1 {
		return ErrInvalidSnapshot
	}
	if strings.ContainsAny(row.Endpoint, "\r\n\t") || strings.Contains(row.Endpoint, "@") || strings.Contains(row.Endpoint, "?") || strings.Contains(row.Endpoint, "#") {
		return ErrInvalidSnapshot
	}
	return nil
}

func validateAudit(row AuditRow) error {
	if !bounded(row.ID, 1, 128) || !bounded(row.Actor, 1, 128) || !bounded(row.Action, 1, 64) || !bounded(row.Resource, 1, 64) || !bounded(row.ResourceID, 1, 128) || !bounded(row.Outcome, 1, 64) || row.OccurredAt.IsZero() {
		return ErrInvalidSnapshot
	}
	return nil
}

func bounded(value string, min, max int) bool {
	trimmed := strings.TrimSpace(value)
	return len(trimmed) >= min && len(trimmed) <= max && trimmed == value
}
