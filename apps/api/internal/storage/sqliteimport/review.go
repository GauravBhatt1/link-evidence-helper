package sqliteimport

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"strings"
	"time"
)

var (
	ErrInvalidReview = errors.New("invalid SQLite import review")
	ErrPlanChanged   = errors.New("SQLite import plan changed after review")
)

const maxReviewerLength = 128

// Review seals a reviewed dry-run plan without containing credentials or
// database connection details. The hash covers the complete normalized plan,
// including source order and rollback order.
type Review struct {
	PlanHash   string
	Reviewer   string
	ReviewedAt time.Time
}

// NewReview creates an immutable approval record for a validated plan.
func NewReview(plan Plan, reviewer string, now time.Time) (Review, error) {
	reviewer = strings.TrimSpace(reviewer)
	if now.IsZero() || reviewer == "" || len(reviewer) > maxReviewerLength {
		return Review{}, ErrInvalidReview
	}

	hash, err := planHash(plan)
	if err != nil {
		return Review{}, err
	}

	return Review{
		PlanHash:   hash,
		Reviewer:   reviewer,
		ReviewedAt: now.UTC(),
	}, nil
}

// Verify confirms that the reviewed plan is byte-for-byte equivalent at the
// normalized contract level. Any source, ordering, timestamp, or rollback
// mutation invalidates the review.
func (review Review) Verify(plan Plan) error {
	if review.ReviewedAt.IsZero() || strings.TrimSpace(review.Reviewer) == "" || len(review.Reviewer) > maxReviewerLength {
		return ErrInvalidReview
	}
	if len(review.PlanHash) != sha256.Size*2 {
		return ErrInvalidReview
	}
	if _, err := hex.DecodeString(review.PlanHash); err != nil {
		return ErrInvalidReview
	}

	hash, err := planHash(plan)
	if err != nil {
		return err
	}
	if hash != review.PlanHash {
		return ErrPlanChanged
	}
	return nil
}

func planHash(plan Plan) (string, error) {
	if plan.CreatedAt.IsZero() {
		return "", ErrInvalidInput
	}

	h := sha256.New()
	writeField := func(value string) {
		// Length-prefix every field to avoid delimiter ambiguity.
		h.Write([]byte{byte(len(value) >> 24), byte(len(value) >> 16), byte(len(value) >> 8), byte(len(value))})
		h.Write([]byte(value))
	}

	writeField(plan.CreatedAt.UTC().Format(time.RFC3339Nano))
	writeField(string(rune(len(plan.Sources))))
	for _, step := range plan.Sources {
		writeField(step.Draft.ID)
		writeField(step.Draft.DisplayName)
		writeField(step.Draft.Kind)
		writeField(step.Draft.Endpoint)
		if step.Draft.Enabled {
			writeField("1")
		} else {
			writeField("0")
		}
	}
	writeField(string(rune(len(plan.Rollback))))
	for _, step := range plan.Rollback {
		writeField(step.SourceID)
	}

	return hex.EncodeToString(h.Sum(nil)), nil
}
