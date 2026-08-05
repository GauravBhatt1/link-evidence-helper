package resolution

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/worker/internal/linkverify"
	"github.com/GauravBhatt1/link-evidence-helper/packages/jobqueue"
)

type LinkVerifier interface {
	Verify(context.Context, linkverify.Candidate) (linkverify.DeliveryLink, error)
}

type Executor struct {
	Catalog  *Catalog
	Verifier LinkVerifier
}

type resolutionAttempt struct {
	SourceID      string  `json:"sourceId"`
	Status        string  `json:"status"`
	FailureReason *string `json:"failureReason"`
	DurationMS    int     `json:"durationMs"`
}

type resolutionResult struct {
	OK            bool                      `json:"ok"`
	Success       bool                      `json:"success"`
	Code          string                    `json:"code"`
	Status        string                    `json:"status"`
	ContentID     string                    `json:"contentId"`
	VariantID     string                    `json:"variantId"`
	DeliveryLinks []linkverify.DeliveryLink `json:"deliveryLinks"`
	Attempts      []resolutionAttempt       `json:"attempts"`
	Message       string                    `json:"message"`
}

func (executor Executor) Execute(ctx context.Context, job jobqueue.Job, reporter jobqueue.Reporter) error {
	if job.Kind != jobqueue.KindResolution {
		return errors.New("resolution executor received an unsupported job kind")
	}
	if executor.Catalog == nil || executor.Verifier == nil {
		return errors.New("resolution executor is not configured")
	}
	request, err := decodeRequest(job.Payload)
	if err != nil {
		return executor.finishInvalid(ctx, reporter, Request{}, "invalid_request", "The resolution request is invalid.")
	}
	if err := cancelled(ctx, reporter); err != nil {
		return err
	}
	if _, err := reporter.Transition(ctx, jobqueue.StateCheckingCache, "Checking for an existing coalesced resolution.", 5, nil); err != nil {
		return err
	}
	selection, err := executor.Catalog.Select(request)
	if err != nil {
		code := "selection_not_found"
		message := "The selected release is no longer available."
		if errors.Is(err, ErrQualityRequired) {
			code = "quality_required"
			message = "Select a quality before resolving links."
		}
		return executor.finishInvalid(ctx, reporter, request, code, message)
	}
	if err := cancelled(ctx, reporter); err != nil {
		return err
	}
	if _, err := reporter.Transition(ctx, jobqueue.StateCheckingPreferredSource, "Checking the preferred source.", 20, nil); err != nil {
		return err
	}

	attempts := make([]resolutionAttempt, 0, len(selection.Sources))
	allBlocked := true
	for index, source := range selection.Sources {
		if err := cancelled(ctx, reporter); err != nil {
			return err
		}
		if index == 1 {
			if _, err := reporter.Transition(ctx, jobqueue.StateCheckingBackupSource, "Checking backup sources.", 55, nil); err != nil {
				return err
			}
		}
		started := time.Now()
		link, verifyErr := executor.Verifier.Verify(ctx, linkverify.Candidate{
			SourceID:       source.SourceID,
			URL:            source.URL,
			Filename:       source.Filename,
			Size:           source.Size,
			Quality:        selection.Quality,
			AllowedOrigins: source.AllowedOrigins,
		})
		duration := boundedDurationMS(time.Since(started))
		if verifyErr == nil {
			attempts = append(attempts, resolutionAttempt{
				SourceID: source.SourceID,
				Status:   "verified",
				DurationMS: duration,
			})
			result := resolutionResult{
				OK:            true,
				Success:       true,
				Code:          "ok",
				Status:        "verified",
				ContentID:     selection.ContentID,
				VariantID:     selection.VariantID,
				DeliveryLinks: []linkverify.DeliveryLink{link},
				Attempts:      attempts,
				Message:       "Verified delivery links are ready.",
			}
			return transitionResult(ctx, reporter, jobqueue.StateVerified, "Verified delivery links are ready.", result)
		}

		failureMessage, blocked := safeFailure(verifyErr)
		allBlocked = allBlocked && blocked
		attempts = append(attempts, resolutionAttempt{
			SourceID:      source.SourceID,
			Status:        "failed",
			FailureReason: &failureMessage,
			DurationMS:    duration,
		})
	}

	state := jobqueue.StateFailed
	status := "failed"
	code := "no_verified_links"
	message := "No source returned a verified delivery link."
	if allBlocked {
		state = jobqueue.StateBlocked
		status = "blocked"
		code = "all_sources_blocked"
		message = "All configured sources were blocked by verification policy."
	}
	result := resolutionResult{
		OK:            false,
		Success:       false,
		Code:          code,
		Status:        status,
		ContentID:     selection.ContentID,
		VariantID:     selection.VariantID,
		DeliveryLinks: []linkverify.DeliveryLink{},
		Attempts:      attempts,
		Message:       message,
	}
	return transitionResult(ctx, reporter, state, message, result)
}

func (executor Executor) finishInvalid(ctx context.Context, reporter jobqueue.Reporter, request Request, code, message string) error {
	result := resolutionResult{
		OK:            false,
		Success:       false,
		Code:          code,
		Status:        "failed",
		ContentID:     request.ContentID,
		VariantID:     request.VariantID,
		DeliveryLinks: []linkverify.DeliveryLink{},
		Attempts:      []resolutionAttempt{},
		Message:       message,
	}
	return transitionResult(ctx, reporter, jobqueue.StateFailed, message, result)
}

func transitionResult(ctx context.Context, reporter jobqueue.Reporter, state jobqueue.State, message string, result resolutionResult) error {
	encoded, err := json.Marshal(result)
	if err != nil {
		return err
	}
	_, err = reporter.Transition(ctx, state, message, 100, encoded)
	return err
}

func decodeRequest(payload json.RawMessage) (Request, error) {
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.DisallowUnknownFields()
	var request Request
	if err := decoder.Decode(&request); err != nil {
		return Request{}, err
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return Request{}, errors.New("resolution payload must contain one JSON object")
	}
	if !validIdentifier(request.ContentID) || !validIdentifier(request.VariantID) {
		return Request{}, errors.New("resolution identifiers are invalid")
	}
	if request.Quality != nil && len(*request.Quality) > 80 {
		return Request{}, errors.New("resolution quality is invalid")
	}
	return request, nil
}

func cancelled(ctx context.Context, reporter jobqueue.Reporter) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	cancelled, err := reporter.Cancelled(ctx)
	if err != nil {
		return err
	}
	if cancelled {
		return jobqueue.ErrJobCancelled
	}
	return nil
}

func safeFailure(err error) (string, bool) {
	var failure *linkverify.Error
	if !errors.As(err, &failure) {
		return "Source verification failed.", false
	}
	switch failure.Code {
	case "unsafe_url", "unsafe_network", "unsafe_redirect", "redirect_limit", "invalid_candidate":
		return "Source was blocked by network safety policy.", true
	case "not_delivery", "invalid_filename":
		return "Source did not return a verified delivery file.", true
	case "timeout":
		return "Source verification timed out.", false
	case "http_status":
		return "Source returned an unavailable response.", false
	default:
		return "Source could not be verified.", failure.Blocked
	}
}

func boundedDurationMS(duration time.Duration) int {
	milliseconds := duration.Milliseconds()
	if milliseconds < 0 {
		return 0
	}
	const maximum = int64(^uint(0) >> 1)
	if milliseconds > maximum {
		return int(maximum)
	}
	return int(milliseconds)
}

var _ jobqueue.Executor = Executor{}
var _ = fmt.Sprintf
