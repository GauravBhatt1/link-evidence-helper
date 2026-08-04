package jobs

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/contracts"
	"github.com/GauravBhatt1/link-evidence-helper/packages/jobqueue"
)

type Service struct {
	store *jobqueue.Store
}

func Open(addr, password string, db int, config jobqueue.Config) (*Service, error) {
	store, err := jobqueue.Open(addr, password, db, config)
	if err != nil {
		return nil, err
	}
	return &Service{store: store}, nil
}

func (service *Service) Close() error {
	return service.store.Close()
}

func (service *Service) Ping(ctx context.Context) error {
	return service.store.Ping(ctx)
}

func (service *Service) CreateResolution(ctx context.Context, request contracts.ResolutionRequest, idempotencyKey string) (contracts.Job, jobqueue.CreateOutcome, error) {
	normalized, payload, fingerprint, err := canonicalResolution(request)
	if err != nil {
		return contracts.Job{}, "", err
	}
	_ = normalized
	created, err := service.store.CreateOrJoin(ctx, jobqueue.CreateRequest{
		Kind:           jobqueue.KindResolution,
		Fingerprint:    fingerprint,
		IdempotencyKey: idempotencyKey,
		Payload:        payload,
	})
	if err != nil {
		return contracts.Job{}, "", err
	}
	return contractJob(created.Job), created.Outcome, nil
}

func canonicalResolution(request contracts.ResolutionRequest) (contracts.ResolutionRequest, json.RawMessage, string, error) {
	request.ContentID = strings.TrimSpace(request.ContentID)
	request.VariantID = strings.TrimSpace(request.VariantID)
	if request.ContentID == "" || request.VariantID == "" {
		return contracts.ResolutionRequest{}, nil, "", fmt.Errorf("%w: contentId and variantId are required", jobqueue.ErrInvalidInput)
	}
	if request.Quality != nil {
		quality := strings.TrimSpace(*request.Quality)
		if quality == "" {
			return contracts.ResolutionRequest{}, nil, "", fmt.Errorf("%w: quality cannot be blank", jobqueue.ErrInvalidInput)
		}
		request.Quality = &quality
	}
	payload, err := json.Marshal(request)
	if err != nil {
		return contracts.ResolutionRequest{}, nil, "", err
	}
	digest := sha256.Sum256(payload)
	return request, payload, hex.EncodeToString(digest[:]), nil
}

func (service *Service) Get(ctx context.Context, jobID string) (contracts.Job, error) {
	job, err := service.store.Get(ctx, jobID)
	if err != nil {
		return contracts.Job{}, err
	}
	return contractJob(job), nil
}

func (service *Service) Events(ctx context.Context, jobID string) ([]contracts.JobEvent, error) {
	events, err := service.store.Events(ctx, jobID)
	if err != nil {
		return nil, err
	}
	result := make([]contracts.JobEvent, 0, len(events))
	for _, event := range events {
		result = append(result, contracts.JobEvent{
			EventID: event.EventID, JobID: event.JobID, State: string(event.State),
			Message: event.Message, OccurredAt: event.OccurredAt, Progress: event.Progress,
		})
	}
	return result, nil
}

func (service *Service) Unsubscribe(ctx context.Context, jobID, idempotencyKey string) (contracts.Job, error) {
	job, err := service.store.Unsubscribe(ctx, jobID, idempotencyKey)
	if err != nil {
		return contracts.Job{}, err
	}
	return contractJob(job), nil
}

func contractJob(job jobqueue.Job) contracts.Job {
	return contracts.Job{
		JobID:           job.JobID,
		Kind:            string(job.Kind),
		State:           string(job.State),
		SubscriberCount: job.SubscriberCount,
		CreatedAt:       job.CreatedAt,
		UpdatedAt:       job.UpdatedAt,
		Result:          job.Result,
	}
}
