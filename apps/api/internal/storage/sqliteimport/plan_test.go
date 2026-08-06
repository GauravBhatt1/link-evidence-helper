package sqliteimport

import (
	"errors"
	"testing"
	"time"
)

func TestBuildPlanIsDeterministicAndDryRunOnly(t *testing.T) {
	snapshot := Snapshot{
		Sources: []SourceRow{
			{ID: "src-b", DisplayName: "Beta", Domain: "beta.example", Endpoint: "https://beta.example", Enabled: true, Revision: 2},
			{ID: "src-a", DisplayName: "Alpha", Domain: "alpha.example", Endpoint: "https://alpha.example", Enabled: true, Revision: 1},
		},
		Audits: []AuditRow{
			{ID: "evt-1", Actor: "admin", Action: "source.create", Resource: "source", ResourceID: "src-a", Outcome: "success", OccurredAt: time.Date(2026, 8, 6, 0, 0, 0, 0, time.UTC)},
		},
	}

	plan, err := BuildPlan(snapshot)
	if err != nil {
		t.Fatalf("BuildPlan() error = %v", err)
	}
	if !plan.DryRun {
		t.Fatal("plan must remain dry-run until a separate executor is explicitly invoked")
	}
	if got, want := len(plan.Operations), 3; got != want {
		t.Fatalf("operations = %d, want %d", got, want)
	}
	if plan.Operations[0].Kind != "audit" || plan.Operations[1].ID != "src-a" || plan.Operations[2].ID != "src-b" {
		t.Fatalf("unexpected deterministic order: %#v", plan.Operations)
	}
	if plan.Rollback[0].Kind != "delete_source" || plan.Rollback[0].ID != "src-b" {
		t.Fatalf("unexpected rollback order: %#v", plan.Rollback)
	}
}

func TestBuildPlanRejectsCredentialShapedEndpoint(t *testing.T) {
	_, err := BuildPlan(Snapshot{Sources: []SourceRow{{
		ID: "src-1", DisplayName: "Unsafe", Domain: "example.test", Endpoint: "https://user:pass@example.test/path", Revision: 1,
	}}})
	if !errors.Is(err, ErrInvalidSnapshot) {
		t.Fatalf("error = %v, want ErrInvalidSnapshot", err)
	}
}

func TestBuildPlanRejectsDuplicateIDsWithinKind(t *testing.T) {
	row := SourceRow{ID: "src-1", DisplayName: "One", Domain: "one.test", Endpoint: "https://one.test", Revision: 1}
	_, err := BuildPlan(Snapshot{Sources: []SourceRow{row, row}})
	if !errors.Is(err, ErrDuplicateID) {
		t.Fatalf("error = %v, want ErrDuplicateID", err)
	}
}

func TestBuildPlanRejectsUnboundedSnapshot(t *testing.T) {
	sources := make([]SourceRow, maxRows+1)
	for i := range sources {
		sources[i] = SourceRow{ID: "unused"}
	}
	_, err := BuildPlan(Snapshot{Sources: sources})
	if !errors.Is(err, ErrTooManyRows) {
		t.Fatalf("error = %v, want ErrTooManyRows", err)
	}
}

func TestBuildPlanRejectsZeroAuditTimestamp(t *testing.T) {
	_, err := BuildPlan(Snapshot{Audits: []AuditRow{{
		ID: "evt-1", Actor: "admin", Action: "source.create", Resource: "source", ResourceID: "src-1", Outcome: "success",
	}}})
	if !errors.Is(err, ErrInvalidSnapshot) {
		t.Fatalf("error = %v, want ErrInvalidSnapshot", err)
	}
}
