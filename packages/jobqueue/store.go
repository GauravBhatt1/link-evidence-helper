package jobqueue

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
)

type Config struct {
	Prefix      string
	JobTTL      time.Duration
	TerminalTTL time.Duration
	MaxQueued   int64
	BlockTime   time.Duration
}

func DefaultConfig() Config {
	return Config{
		Prefix:      "leh:development:jobs:v1",
		JobTTL:      30 * time.Minute,
		TerminalTTL: 10 * time.Minute,
		MaxQueued:   256,
		BlockTime:   time.Second,
	}
}

type Store struct {
	client *redis.Client
	config Config
}

func Open(addr, password string, db int, config Config) (*Store, error) {
	config = normalizeConfig(config)
	client := redis.NewClient(&redis.Options{
		Addr:         addr,
		Password:     password,
		DB:           db,
		DialTimeout:  3 * time.Second,
		ReadTimeout:  5 * time.Second,
		WriteTimeout: 5 * time.Second,
		PoolSize:     16,
		MinIdleConns: 1,
	})
	store := &Store{client: client, config: config}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := store.Ping(ctx); err != nil {
		_ = client.Close()
		return nil, fmt.Errorf("connect to Redis job store: %w", err)
	}
	return store, nil
}

func normalizeConfig(config Config) Config {
	defaults := DefaultConfig()
	if config.Prefix == "" {
		config.Prefix = defaults.Prefix
	}
	if config.JobTTL <= 0 {
		config.JobTTL = defaults.JobTTL
	}
	if config.TerminalTTL <= 0 || config.TerminalTTL > config.JobTTL {
		config.TerminalTTL = defaults.TerminalTTL
	}
	if config.MaxQueued <= 0 {
		config.MaxQueued = defaults.MaxQueued
	}
	if config.BlockTime <= 0 {
		config.BlockTime = defaults.BlockTime
	}
	return config
}

func (store *Store) Ping(ctx context.Context) error {
	return store.client.Ping(ctx).Err()
}

func (store *Store) Close() error {
	return store.client.Close()
}

func (store *Store) queueKey() string      { return store.config.Prefix + ":queue" }
func (store *Store) processingKey() string { return store.config.Prefix + ":processing" }
func (store *Store) jobKey(jobID string) string {
	return store.config.Prefix + ":job:" + jobID
}
func (store *Store) eventsKey(jobID string) string {
	return store.config.Prefix + ":events:" + jobID
}
func (store *Store) subscribersKey(jobID string) string {
	return store.config.Prefix + ":subscribers:" + jobID
}
func (store *Store) cancelKey(jobID string) string {
	return store.config.Prefix + ":cancel:" + jobID
}
func (store *Store) coalesceKey(fingerprint string) string {
	return store.config.Prefix + ":coalesce:" + fingerprint
}
func (store *Store) idempotencyKey(fingerprint, idempotencyKey string) string {
	digest := sha256.Sum256([]byte(idempotencyKey))
	return store.config.Prefix + ":idempotency:" + fingerprint + ":" + hex.EncodeToString(digest[:])
}
func subscriberID(idempotencyKey string) string {
	digest := sha256.Sum256([]byte(idempotencyKey))
	return hex.EncodeToString(digest[:])
}

var createOrJoinScript = redis.NewScript(`
local function terminal(state)
  return state == 'verified' or state == 'partial' or state == 'blocked' or state == 'failed' or state == 'cancelled'
end

local idempotent = redis.call('GET', KEYS[2])
if idempotent then
  return {idempotent, 'idempotent'}
end

local prefix = ARGV[11]
local existing = redis.call('GET', KEYS[1])
if existing then
  local jobKey = prefix .. ':job:' .. existing
  local state = redis.call('HGET', jobKey, 'state')
  if state and not terminal(state) then
    local subscribersKey = prefix .. ':subscribers:' .. existing
    local eventsKey = prefix .. ':events:' .. existing
    local added = redis.call('SADD', subscribersKey, ARGV[6])
    local count = redis.call('SCARD', subscribersKey)
    redis.call('HSET', jobKey, 'subscriberCount', count, 'updatedAt', ARGV[1])
    redis.call('SET', KEYS[2], existing, 'EX', ARGV[7])
    redis.call('EXPIRE', jobKey, ARGV[7])
    redis.call('EXPIRE', subscribersKey, ARGV[7])
    redis.call('EXPIRE', eventsKey, ARGV[7])
    redis.call('EXPIRE', KEYS[1], ARGV[7])
    if added == 1 then
      redis.call('RPUSH', eventsKey, cjson.encode({
        eventId = ARGV[10],
        jobId = existing,
        state = state,
        message = 'Joined an existing coalesced job.',
        occurredAt = ARGV[1],
        progress = tonumber(redis.call('HGET', jobKey, 'progress') or '0')
      }))
      return {existing, 'joined'}
    end
    return {existing, 'idempotent'}
  end
  redis.call('DEL', KEYS[1])
end

if redis.call('LLEN', KEYS[3]) >= tonumber(ARGV[9]) then
  return {'', 'full'}
end

local jobKey = prefix .. ':job:' .. ARGV[2]
local subscribersKey = prefix .. ':subscribers:' .. ARGV[2]
local eventsKey = prefix .. ':events:' .. ARGV[2]
redis.call('HSET', jobKey,
  'jobId', ARGV[2],
  'kind', ARGV[3],
  'state', 'queued',
  'subscriberCount', 1,
  'createdAt', ARGV[1],
  'updatedAt', ARGV[1],
  'result', '',
  'payload', ARGV[4],
  'fingerprint', ARGV[5],
  'progress', 0
)
redis.call('SADD', subscribersKey, ARGV[6])
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[7])
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[7])
redis.call('RPUSH', KEYS[3], ARGV[2])
redis.call('RPUSH', eventsKey, cjson.encode({
  eventId = ARGV[10],
  jobId = ARGV[2],
  state = 'queued',
  message = 'Job queued.',
  occurredAt = ARGV[1],
  progress = 0
}))
redis.call('EXPIRE', jobKey, ARGV[7])
redis.call('EXPIRE', subscribersKey, ARGV[7])
redis.call('EXPIRE', eventsKey, ARGV[7])
return {ARGV[2], 'created'}
`)

func (store *Store) CreateOrJoin(ctx context.Context, request CreateRequest) (CreateResult, error) {
	if err := ValidateCreateRequest(request); err != nil {
		return CreateResult{}, err
	}
	jobID, err := NewJobID()
	if err != nil {
		return CreateResult{}, err
	}
	eventID, err := NewEventID()
	if err != nil {
		return CreateResult{}, err
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	ttlSeconds := max(int64(store.config.JobTTL/time.Second), 1)
	result, err := createOrJoinScript.Run(ctx, store.client, []string{
		store.coalesceKey(request.Fingerprint),
		store.idempotencyKey(request.Fingerprint, request.IdempotencyKey),
		store.queueKey(),
	},
		now,
		jobID,
		string(request.Kind),
		string(request.Payload),
		request.Fingerprint,
		subscriberID(request.IdempotencyKey),
		ttlSeconds,
		ttlSeconds,
		store.config.MaxQueued,
		eventID,
		store.config.Prefix,
	).Slice()
	if err != nil {
		return CreateResult{}, fmt.Errorf("create Redis job: %w", err)
	}
	if len(result) != 2 {
		return CreateResult{}, errors.New("unexpected Redis create response")
	}
	returnedID, _ := result[0].(string)
	outcome, _ := result[1].(string)
	if outcome == "full" {
		return CreateResult{}, ErrQueueFull
	}
	job, err := store.Get(ctx, returnedID)
	if err != nil {
		return CreateResult{}, err
	}
	return CreateResult{Job: job, Outcome: CreateOutcome(outcome)}, nil
}

func (store *Store) Get(ctx context.Context, jobID string) (Job, error) {
	values, err := store.client.HGetAll(ctx, store.jobKey(jobID)).Result()
	if err != nil {
		return Job{}, fmt.Errorf("read Redis job: %w", err)
	}
	if len(values) == 0 {
		return Job{}, ErrNotFound
	}
	return decodeJob(values)
}

func decodeJob(values map[string]string) (Job, error) {
	createdAt, err := time.Parse(time.RFC3339Nano, values["createdAt"])
	if err != nil {
		return Job{}, fmt.Errorf("decode job createdAt: %w", err)
	}
	updatedAt, err := time.Parse(time.RFC3339Nano, values["updatedAt"])
	if err != nil {
		return Job{}, fmt.Errorf("decode job updatedAt: %w", err)
	}
	subscriberCount, err := strconv.Atoi(values["subscriberCount"])
	if err != nil {
		return Job{}, fmt.Errorf("decode subscriberCount: %w", err)
	}
	var result json.RawMessage
	if raw := values["result"]; raw != "" {
		if !json.Valid([]byte(raw)) {
			return Job{}, errors.New("stored job result is invalid JSON")
		}
		result = json.RawMessage(raw)
	}
	payload := json.RawMessage(values["payload"])
	if !json.Valid(payload) {
		return Job{}, errors.New("stored job payload is invalid JSON")
	}
	job := Job{
		JobID:           values["jobId"],
		Kind:            Kind(values["kind"]),
		State:           State(values["state"]),
		SubscriberCount: subscriberCount,
		CreatedAt:       createdAt,
		UpdatedAt:       updatedAt,
		Result:          result,
		Payload:         payload,
		Fingerprint:     values["fingerprint"],
	}
	if job.JobID == "" || !job.Kind.Valid() || !job.State.Valid() {
		return Job{}, errors.New("stored job contains invalid identifiers or state")
	}
	return job, nil
}

func (store *Store) Events(ctx context.Context, jobID string) ([]Event, error) {
	if _, err := store.Get(ctx, jobID); err != nil {
		return nil, err
	}
	rows, err := store.client.LRange(ctx, store.eventsKey(jobID), 0, -1).Result()
	if err != nil {
		return nil, fmt.Errorf("read Redis job events: %w", err)
	}
	events := make([]Event, 0, len(rows))
	for _, row := range rows {
		var event Event
		if err := json.Unmarshal([]byte(row), &event); err != nil {
			return nil, fmt.Errorf("decode Redis job event: %w", err)
		}
		if event.JobID != jobID || !event.State.Valid() || event.Progress < 0 || event.Progress > 100 {
			return nil, errors.New("stored job event is invalid")
		}
		events = append(events, event)
	}
	return events, nil
}

var unsubscribeScript = redis.NewScript(`
local function terminal(state)
  return state == 'verified' or state == 'partial' or state == 'blocked' or state == 'failed' or state == 'cancelled'
end

if redis.call('EXISTS', KEYS[1]) == 0 then
  return {'', 'not-found'}
end
if redis.call('GET', KEYS[3]) ~= ARGV[1] then
  return {'', 'no-subscription'}
end
if redis.call('SREM', KEYS[2], ARGV[2]) == 0 then
  redis.call('DEL', KEYS[3])
  return {'', 'no-subscription'}
end
redis.call('DEL', KEYS[3])
local count = redis.call('SCARD', KEYS[2])
local state = redis.call('HGET', KEYS[1], 'state')
redis.call('HSET', KEYS[1], 'subscriberCount', count, 'updatedAt', ARGV[3])
if count == 0 and not terminal(state) then
  state = 'cancelled'
  redis.call('HSET', KEYS[1], 'state', state, 'updatedAt', ARGV[3], 'progress', 100)
  redis.call('SET', KEYS[5], '1', 'EX', ARGV[5])
  if redis.call('GET', KEYS[4]) == ARGV[1] then
    redis.call('DEL', KEYS[4])
  end
  redis.call('RPUSH', KEYS[6], cjson.encode({
    eventId = ARGV[4],
    jobId = ARGV[1],
    state = 'cancelled',
    message = 'Job cancelled after the final subscriber left.',
    occurredAt = ARGV[3],
    progress = 100
  }))
end
redis.call('EXPIRE', KEYS[1], ARGV[5])
redis.call('EXPIRE', KEYS[2], ARGV[5])
redis.call('EXPIRE', KEYS[6], ARGV[5])
return {tostring(count), state}
`)

func (store *Store) Unsubscribe(ctx context.Context, jobID, idempotencyKey string) (Job, error) {
	if err := ValidateIdempotencyKey(idempotencyKey); err != nil {
		return Job{}, err
	}
	job, err := store.Get(ctx, jobID)
	if err != nil {
		return Job{}, err
	}
	eventID, err := NewEventID()
	if err != nil {
		return Job{}, err
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	ttlSeconds := max(int64(store.config.TerminalTTL/time.Second), 1)
	result, err := unsubscribeScript.Run(ctx, store.client, []string{
		store.jobKey(jobID),
		store.subscribersKey(jobID),
		store.idempotencyKey(job.Fingerprint, idempotencyKey),
		store.coalesceKey(job.Fingerprint),
		store.cancelKey(jobID),
		store.eventsKey(jobID),
	}, jobID, subscriberID(idempotencyKey), now, eventID, ttlSeconds).Slice()
	if err != nil {
		return Job{}, fmt.Errorf("unsubscribe Redis job: %w", err)
	}
	if len(result) != 2 {
		return Job{}, errors.New("unexpected Redis unsubscribe response")
	}
	outcome, _ := result[1].(string)
	switch outcome {
	case "not-found":
		return Job{}, ErrNotFound
	case "no-subscription":
		return Job{}, ErrSubscriptionNotFound
	}
	return store.Get(ctx, jobID)
}

var transitionScript = redis.NewScript(`
local function terminal(state)
  return state == 'verified' or state == 'partial' or state == 'blocked' or state == 'failed' or state == 'cancelled'
end

if redis.call('EXISTS', KEYS[1]) == 0 then
  return 'not-found'
end
local current = redis.call('HGET', KEYS[1], 'state')
if current == 'cancelled' or redis.call('EXISTS', KEYS[5]) == 1 then
  return 'cancelled'
end
if terminal(current) then
  return 'terminal'
end
redis.call('HSET', KEYS[1],
  'state', ARGV[1],
  'updatedAt', ARGV[2],
  'progress', ARGV[3],
  'result', ARGV[4]
)
redis.call('RPUSH', KEYS[2], ARGV[5])
if terminal(ARGV[1]) then
  if redis.call('GET', KEYS[3]) == ARGV[6] then
    redis.call('DEL', KEYS[3])
  end
  redis.call('EXPIRE', KEYS[1], ARGV[8])
  redis.call('EXPIRE', KEYS[2], ARGV[8])
  redis.call('EXPIRE', KEYS[4], ARGV[8])
  redis.call('EXPIRE', KEYS[5], ARGV[8])
else
  redis.call('EXPIRE', KEYS[1], ARGV[7])
  redis.call('EXPIRE', KEYS[2], ARGV[7])
  redis.call('EXPIRE', KEYS[4], ARGV[7])
  redis.call('EXPIRE', KEYS[3], ARGV[7])
end
return 'ok'
`)

func (store *Store) Transition(ctx context.Context, jobID string, state State, message string, progress int, result json.RawMessage) (Job, error) {
	if !state.Valid() || state == StateQueued || progress < 0 || progress > 100 {
		return Job{}, ErrInvalidInput
	}
	if len(result) > 0 && !json.Valid(result) {
		return Job{}, fmt.Errorf("%w: transition result must be valid JSON", ErrInvalidInput)
	}
	job, err := store.Get(ctx, jobID)
	if err != nil {
		return Job{}, err
	}
	if !allowedTransition(job.State, state) {
		return Job{}, fmt.Errorf("%w: %s to %s", ErrInvalidInput, job.State, state)
	}
	eventID, err := NewEventID()
	if err != nil {
		return Job{}, err
	}
	now := time.Now().UTC()
	event := Event{
		EventID: eventID, JobID: jobID, State: state,
		Message: SafeMessage(message), OccurredAt: now, Progress: progress,
	}
	eventJSON, err := json.Marshal(event)
	if err != nil {
		return Job{}, err
	}
	jobTTL := max(int64(store.config.JobTTL/time.Second), 1)
	terminalTTL := max(int64(store.config.TerminalTTL/time.Second), 1)
	status, err := transitionScript.Run(ctx, store.client, []string{
		store.jobKey(jobID),
		store.eventsKey(jobID),
		store.coalesceKey(job.Fingerprint),
		store.subscribersKey(jobID),
		store.cancelKey(jobID),
	}, string(state), now.Format(time.RFC3339Nano), progress, string(result), string(eventJSON), jobID, jobTTL, terminalTTL).Text()
	if err != nil {
		return Job{}, fmt.Errorf("transition Redis job: %w", err)
	}
	switch status {
	case "not-found":
		return Job{}, ErrNotFound
	case "cancelled":
		return Job{}, ErrJobCancelled
	case "terminal":
		return Job{}, ErrTerminalJob
	case "ok":
		return store.Get(ctx, jobID)
	default:
		return Job{}, errors.New("unexpected Redis transition response")
	}
}

func allowedTransition(current, next State) bool {
	if current.Terminal() {
		return false
	}
	switch current {
	case StateQueued:
		return next == StateCheckingCache || next == StateSearching || next == StateFailed || next == StateCancelled
	case StateCheckingCache:
		return next == StateSearching || next == StateCheckingPreferredSource || next == StatePartial || next == StateFailed || next == StateCancelled
	case StateSearching:
		return next == StateCheckingPreferredSource || next == StateCheckingBackupSource || next == StateBrowserFallback || next == StatePartial || next == StateFailed || next == StateCancelled
	case StateCheckingPreferredSource:
		return next == StateCheckingBackupSource || next == StateBrowserFallback || next == StateVerified || next == StatePartial || next == StateBlocked || next == StateFailed || next == StateCancelled
	case StateCheckingBackupSource:
		return next == StateBrowserFallback || next == StateVerified || next == StatePartial || next == StateBlocked || next == StateFailed || next == StateCancelled
	case StateBrowserFallback:
		return next == StateVerified || next == StatePartial || next == StateBlocked || next == StateFailed || next == StateCancelled
	default:
		return false
	}
}

func (store *Store) CancelRequested(ctx context.Context, jobID string) (bool, error) {
	count, err := store.client.Exists(ctx, store.cancelKey(jobID)).Result()
	if err != nil {
		return false, fmt.Errorf("read Redis cancellation flag: %w", err)
	}
	return count > 0, nil
}

func (store *Store) Claim(ctx context.Context) (string, error) {
	jobID, err := store.client.BRPopLPush(ctx, store.queueKey(), store.processingKey(), store.config.BlockTime).Result()
	if errors.Is(err, redis.Nil) {
		return "", ErrNoJobAvailable
	}
	if err != nil {
		return "", fmt.Errorf("claim Redis job: %w", err)
	}
	return jobID, nil
}

func (store *Store) Ack(ctx context.Context, jobID string) error {
	if err := store.client.LRem(ctx, store.processingKey(), 1, jobID).Err(); err != nil {
		return fmt.Errorf("acknowledge Redis job: %w", err)
	}
	return nil
}

func (store *Store) Recover(ctx context.Context) (int, error) {
	recovered := 0
	for {
		jobID, err := store.client.RPop(ctx, store.processingKey()).Result()
		if errors.Is(err, redis.Nil) {
			return recovered, nil
		}
		if err != nil {
			return recovered, fmt.Errorf("recover Redis job: %w", err)
		}
		job, err := store.Get(ctx, jobID)
		if errors.Is(err, ErrNotFound) {
			continue
		}
		if err != nil {
			return recovered, err
		}
		if job.State.Terminal() {
			continue
		}
		if err := store.client.LPush(ctx, store.queueKey(), jobID).Err(); err != nil {
			return recovered, fmt.Errorf("requeue Redis job: %w", err)
		}
		recovered++
	}
}

func (store *Store) QueueDepth(ctx context.Context) (int64, error) {
	depth, err := store.client.LLen(ctx, store.queueKey()).Result()
	if err != nil {
		return 0, fmt.Errorf("read Redis queue depth: %w", err)
	}
	return depth, nil
}

func (store *Store) DeleteNamespace(ctx context.Context) error {
	var cursor uint64
	for {
		keys, next, err := store.client.Scan(ctx, cursor, store.config.Prefix+":*", 100).Result()
		if err != nil {
			return fmt.Errorf("scan Redis test namespace: %w", err)
		}
		if len(keys) > 0 {
			if err := store.client.Del(ctx, keys...).Err(); err != nil {
				return fmt.Errorf("delete Redis test namespace: %w", err)
			}
		}
		cursor = next
		if cursor == 0 {
			return nil
		}
	}
}
