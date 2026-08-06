package sqliteimport

import (
	"errors"
	"reflect"
	"testing"
	"time"
)

func TestNewPlanNormalizesAndOrdersSources(t *testing.T) {
	now := time.Date(2026, 8, 6, 4, 30, 0, 0, time.FixedZone("test", 19800))
	plan, err := NewPlan([]LegacySource{
		{ID: "zeta", DisplayName: "Zeta", Kind: "http-json", Endpoint: "https://example.com", Enabled: true},
		{ID: "alpha", DisplayName: "Alpha", Kind: "http-html", Endpoint: "http://example.org/search", Enabled: false},
	}, now)
	if err != nil {
		t.Fatalf("NewPlan() error = %v", err)
	}
	if !plan.CreatedAt.Equal(now.UTC()) {
		t.Fatalf("CreatedAt = %v, want %v", plan.CreatedAt, now.UTC())
	}
	gotIDs := []string{plan.Sources[0].Draft.ID, plan.Sources[1].Draft.ID}
	if want := []string{"alpha", "zeta"}; !reflect.DeepEqual(gotIDs, want) {
		t.Fatalf("source IDs = %v, want %v", gotIDs, want)
	}
	if got := plan.Sources[1].Draft.Endpoint; got != "https://example.com/" {
		t.Fatalf("normalized endpoint = %q", got)
	}
	gotRollback := []string{plan.Rollback[0].SourceID, plan.Rollback[1].SourceID}
	if want := []string{"zeta", "alpha"}; !reflect.DeepEqual(gotRollback, want) {
		t.Fatalf("rollback IDs = %v, want %v", gotRollback, want)
	}
}

func TestNewPlanRejectsUnsafeOrDuplicateRows(t *testing.T) {
	now := time.Now()
	tests := []struct {
		name string
		rows []LegacySource
		want error
	}{
		{name: "credentials in endpoint", rows: []LegacySource{{ID: "one", DisplayName: "One", Kind: "http-json", Endpoint: "https://user:pass@example.com/"}}, want: ErrInvalidInput},
		{name: "query parameters", rows: []LegacySource{{ID: "one", DisplayName: "One", Kind: "http-json", Endpoint: "https://example.com/?token=secret"}}, want: ErrInvalidInput},
		{name: "unsupported kind", rows: []LegacySource{{ID: "one", DisplayName: "One", Kind: "script", Endpoint: "https://example.com/"}}, want: ErrInvalidInput},
		{name: "duplicate", rows: []LegacySource{
			{ID: "one", DisplayName: "One", Kind: "http-json", Endpoint: "https://example.com/"},
			{ID: "one", DisplayName: "Other", Kind: "http-html", Endpoint: "https://example.org/"},
		}, want: ErrDuplicateSource},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := NewPlan(test.rows, now)
			if !errors.Is(err, test.want) {
				t.Fatalf("NewPlan() error = %v, want %v", err, test.want)
			}
		})
	}
}

func TestNewPlanRequiresTimestamp(t *testing.T) {
	if _, err := NewPlan(nil, time.Time{}); !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("NewPlan() error = %v, want %v", err, ErrInvalidInput)
	}
}

func TestCloneDefensivelyCopiesSlices(t *testing.T) {
	plan, err := NewPlan([]LegacySource{{ID: "one", DisplayName: "One", Kind: "http-json", Endpoint: "https://example.com/"}}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	clone := plan.Clone()
	clone.Sources[0].Draft.ID = "changed"
	clone.Rollback[0].SourceID = "changed"
	if plan.Sources[0].Draft.ID != "one" || plan.Rollback[0].SourceID != "one" {
		t.Fatal("Clone() shared mutable slices with original plan")
	}
}
