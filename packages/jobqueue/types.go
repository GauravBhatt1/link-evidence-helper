package jobqueue

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"
)

type Kind string

const (
	KindSearch      Kind = "search"
	KindResolution  Kind = "resolution"
	KindLibraryScan Kind = "library-scan"
)

type State string

const (
	StateQueued                  State = "queued"
	StateCheckingCache           State = "checking-cache"
	StateSearching               State = "searching"
	StateCheckingPreferredSource State = "checking-preferred-source"
	StateCheckingBackupSource    State = "checking-backup-source"
	StateBrowserFallback         State = "browser-fallback"
	StateVerified                State = "verified"
	StatePartial                 State = "partial"
	StateBlocked                 State = "blocked"
	StateFailed                  State = "failed"
	StateCancelled               State = "cancelled"
)

var (
	ErrNotFound             = errors.New("job not found")
	ErrQueueFull            = errors.New("job queue is full")
	ErrNoJobAvailable       = errors.New("no job available")
	ErrSubscriptionNotFound = errors.New("job subscription not found")
	ErrJobCancelled         = errors.New("job is cancelled")
	ErrTerminalJob          = errors.New("job is already terminal")
	ErrInvalidInput         = errors.New("invalid jobqueue input")
)

var idempotencyPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$`)

type Job struct {
	JobID           string          `json:"jobId"`
	Kind            Kind            `json:"kind"`
	State           State           `json:"state"`
	SubscriberCount int             `json:"subscriberCount"`
	CreatedAt       time.Time       `json:"createdAt"`
	UpdatedAt       time.Time       `json:"updatedAt"`
	Result          json.RawMessage `json:"result"`
	Payload         json.RawMessage `json:"-"`
	Fingerprint     string          `json:"-"`
}

type Event struct {
	EventID    string    `json:"eventId"`
	JobID      string    `json:"jobId"`
	State      State     `json:"state"`
	Message    string    `json:"message"`
	OccurredAt time.Time `json:"occurredAt"`
	Progress   int       `json:"progress"`
}

type CreateRequest struct {
	Kind           Kind
	Fingerprint    string
	IdempotencyKey string
	Payload        json.RawMessage
}

type CreateOutcome string

const (
	OutcomeCreated    CreateOutcome = "created"
	OutcomeJoined     CreateOutcome = "joined"
	OutcomeIdempotent CreateOutcome = "idempotent"
)

type CreateResult struct {
	Job     Job
	Outcome CreateOutcome
}

func (state State) Terminal() bool {
	switch state {
	case StateVerified, StatePartial, StateBlocked, StateFailed, StateCancelled:
		return true
	default:
		return false
	}
}

func (state State) Valid() bool {
	switch state {
	case StateQueued, StateCheckingCache, StateSearching, StateCheckingPreferredSource,
		StateCheckingBackupSource, StateBrowserFallback, StateVerified, StatePartial,
		StateBlocked, StateFailed, StateCancelled:
		return true
	default:
		return false
	}
}

func (kind Kind) Valid() bool {
	switch kind {
	case KindSearch, KindResolution, KindLibraryScan:
		return true
	default:
		return false
	}
}

func ValidateIdempotencyKey(value string) error {
	if !idempotencyPattern.MatchString(value) {
		return fmt.Errorf("%w: idempotency key must be 8-128 safe ASCII characters", ErrInvalidInput)
	}
	return nil
}

func ValidateCreateRequest(request CreateRequest) error {
	if !request.Kind.Valid() {
		return fmt.Errorf("%w: unsupported job kind", ErrInvalidInput)
	}
	if len(request.Fingerprint) != 64 {
		return fmt.Errorf("%w: fingerprint must be a SHA-256 hex value", ErrInvalidInput)
	}
	if _, err := hex.DecodeString(request.Fingerprint); err != nil {
		return fmt.Errorf("%w: fingerprint is not hexadecimal", ErrInvalidInput)
	}
	if err := ValidateIdempotencyKey(request.IdempotencyKey); err != nil {
		return err
	}
	if len(request.Payload) == 0 || !json.Valid(request.Payload) {
		return fmt.Errorf("%w: payload must be valid JSON", ErrInvalidInput)
	}
	return nil
}

func NewJobID() (string, error) {
	buffer := make([]byte, 16)
	if _, err := rand.Read(buffer); err != nil {
		return "", err
	}
	return "job_" + hex.EncodeToString(buffer), nil
}

func NewEventID() (string, error) {
	buffer := make([]byte, 12)
	if _, err := rand.Read(buffer); err != nil {
		return "", err
	}
	return "evt_" + hex.EncodeToString(buffer), nil
}

func SafeMessage(value string) string {
	value = strings.Join(strings.Fields(value), " ")
	if len(value) > 240 {
		value = value[:240]
	}
	return value
}
