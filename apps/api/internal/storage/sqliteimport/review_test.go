package sqliteimport

import (
	"errors"
	"testing"
	"time"
)

func TestReviewVerifiesUnchangedPlan(t *testing.T) {
	createdAt := time.Date(2026, 8, 6, 4, 0, 0, 0, time.FixedZone("source", 19800))
	plan, err := NewPlan([]LegacySource{{
		ID:          "source-a",
		DisplayName: "Source A",
		Kind:        "http",
		Endpoint:    "https://example.com/path",
		Enabled:     true,
	}}, createdAt)
	if err != nil {
		t.Fatalf("NewPlan() error = %v", err)
	}

	reviewedAt := createdAt.Add(time.Hour)
	review, err := NewReview(plan, " migration-operator ", reviewedAt)
	if err != nil {
		t.Fatalf("NewReview() error = %v", err)
	}
	if review.Reviewer != "migration-operator" {
		t.Fatalf("Reviewer = %q", review.Reviewer)
	}
	if review.ReviewedAt.Location() != time.UTC {
		t.Fatalf("ReviewedAt location = %v", review.ReviewedAt.Location())
	}
	if err := review.Verify(plan.Clone()); err != nil {
		t.Fatalf("Verify() error = %v", err)
	}
}

func TestReviewRejectsMutatedPlan(t *testing.T) {
	now := time.Date(2026, 8, 6, 4, 0, 0, 0, time.UTC)
	plan, err := NewPlan([]LegacySource{{
		ID:          "source-a",
		DisplayName: "Source A",
		Kind:        "http",
		Endpoint:    "https://example.com/path",
		Enabled:     true,
	}}, now)
	if err != nil {
		t.Fatalf("NewPlan() error = %v", err)
	}
	review, err := NewReview(plan, "operator", now.Add(time.Minute))
	if err != nil {
		t.Fatalf("NewReview() error = %v", err)
	}

	mutated := plan.Clone()
	mutated.Sources[0].Draft.Endpoint = "https://example.org/path"
	if err := review.Verify(mutated); !errors.Is(err, ErrPlanChanged) {
		t.Fatalf("Verify() error = %v, want ErrPlanChanged", err)
	}

	mutated = plan.Clone()
	mutated.Rollback = nil
	if err := review.Verify(mutated); !errors.Is(err, ErrPlanChanged) {
		t.Fatalf("Verify() rollback error = %v, want ErrPlanChanged", err)
	}
}

func TestReviewRejectsInvalidMetadata(t *testing.T) {
	now := time.Date(2026, 8, 6, 4, 0, 0, 0, time.UTC)
	plan, err := NewPlan(nil, now)
	if err != nil {
		t.Fatalf("NewPlan() error = %v", err)
	}

	for _, test := range []struct {
		name     string
		reviewer string
		time     time.Time
	}{
		{name: "blank reviewer", reviewer: "   ", time: now},
		{name: "zero time", reviewer: "operator"},
		{name: "oversized reviewer", reviewer: string(make([]byte, maxReviewerLength+1)), time: now},
	} {
		t.Run(test.name, func(t *testing.T) {
			if _, err := NewReview(plan, test.reviewer, test.time); !errors.Is(err, ErrInvalidReview) {
				t.Fatalf("NewReview() error = %v, want ErrInvalidReview", err)
			}
		})
	}
}

func TestReviewHashIsDeterministicAcrossEquivalentTimeZones(t *testing.T) {
	instant := time.Date(2026, 8, 6, 4, 0, 0, 123, time.UTC)
	planUTC, err := NewPlan(nil, instant)
	if err != nil {
		t.Fatalf("NewPlan() UTC error = %v", err)
	}
	planOffset := planUTC.Clone()
	planOffset.CreatedAt = instant.In(time.FixedZone("offset", 19800))

	review, err := NewReview(planUTC, "operator", instant.Add(time.Minute))
	if err != nil {
		t.Fatalf("NewReview() error = %v", err)
	}
	if err := review.Verify(planOffset); err != nil {
		t.Fatalf("Verify() equivalent instant error = %v", err)
	}
}
