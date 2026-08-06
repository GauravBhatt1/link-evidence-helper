// Package sqliteimport defines a dry-run-first boundary for importing legacy
// SQLite source records. It never opens a database or performs writes.
package sqliteimport

import (
	"errors"
	"sort"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/sourceadmin"
)

var (
	ErrInvalidInput    = errors.New("invalid SQLite import input")
	ErrDuplicateSource = errors.New("duplicate SQLite source")
)

// LegacySource is the credential-free subset accepted from an explicit SQLite
// reader. Headers, cookies, tokens, request bodies, scripts, and arbitrary
// metadata are intentionally unsupported.
type LegacySource struct {
	ID          string
	DisplayName string
	Kind        string
	Endpoint    string
	Enabled     bool
}

// SourceStep is one validated source creation proposed by a dry run.
type SourceStep struct {
	Draft sourceadmin.Draft
}

// RollbackStep identifies a source created by the corresponding import plan.
// Consumers must verify ownership before applying a rollback.
type RollbackStep struct {
	SourceID string
}

// Plan is immutable by convention. NewPlan returns defensive copies in a
// deterministic order so operators can review exactly what would change.
type Plan struct {
	CreatedAt time.Time
	Sources   []SourceStep
	Rollback  []RollbackStep
}

// NewPlan validates and normalizes legacy rows without opening SQLite or
// PostgreSQL and without mutating any repository.
func NewPlan(rows []LegacySource, now time.Time) (Plan, error) {
	if now.IsZero() {
		return Plan{}, ErrInvalidInput
	}

	ordered := append([]LegacySource(nil), rows...)
	sort.Slice(ordered, func(i, j int) bool { return ordered[i].ID < ordered[j].ID })

	validator := sourceadmin.NewMemoryRegistry()
	steps := make([]SourceStep, 0, len(ordered))
	seen := make(map[string]struct{}, len(ordered))
	for _, row := range ordered {
		if _, exists := seen[row.ID]; exists {
			return Plan{}, ErrDuplicateSource
		}
		seen[row.ID] = struct{}{}

		draft := sourceadmin.Draft{
			ID:          row.ID,
			DisplayName: row.DisplayName,
			Kind:        row.Kind,
			Endpoint:    row.Endpoint,
			Enabled:     row.Enabled,
		}
		validated, err := validator.Create(draft, now)
		if err != nil {
			return Plan{}, ErrInvalidInput
		}
		steps = append(steps, SourceStep{Draft: sourceadmin.Draft{
			ID:          validated.ID,
			DisplayName: validated.DisplayName,
			Kind:        validated.Kind,
			Endpoint:    validated.Endpoint,
			Enabled:     validated.Enabled,
		}})
	}

	rollback := make([]RollbackStep, len(steps))
	for index := range steps {
		rollback[len(steps)-1-index] = RollbackStep{SourceID: steps[index].Draft.ID}
	}

	return Plan{
		CreatedAt: now.UTC(),
		Sources:   append([]SourceStep(nil), steps...),
		Rollback:  append([]RollbackStep(nil), rollback...),
	}, nil
}

// Clone returns a defensive copy suitable for handoff to a separate operator
// command or reviewed execution layer.
func (plan Plan) Clone() Plan {
	return Plan{
		CreatedAt: plan.CreatedAt,
		Sources:   append([]SourceStep(nil), plan.Sources...),
		Rollback:  append([]RollbackStep(nil), plan.Rollback...),
	}
}
