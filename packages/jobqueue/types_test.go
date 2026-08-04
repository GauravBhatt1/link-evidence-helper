package jobqueue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

func validCreateRequest() CreateRequest {
	payload := json.RawMessage(`{"contentId":"content","variantId":"variant","quality":"1080p"}`)
	payload = json.RawMessage(strings.ReplaceAll(string(payload), `\"`, `"`))
	digest := sha256.Sum256(payload)
	return CreateRequest{
		Kind:           KindResolution,
		Fingerprint:    hex.EncodeToString(digest[:]),
		IdempotencyKey: "request-0001",
		Payload:        payload,
	}
}

func TestValidateCreateRequest(t *testing.T) {
	if err := ValidateCreateRequest(validCreateRequest()); err != nil {
		t.Fatal(err)
	}

	tests := []CreateRequest{
		{Kind: "unknown", Fingerprint: strings.Repeat("a", 64), IdempotencyKey: "request-0001", Payload: json.RawMessage(`{}`)},
		{Kind: KindResolution, Fingerprint: "short", IdempotencyKey: "request-0001", Payload: json.RawMessage(`{}`)},
		{Kind: KindResolution, Fingerprint: strings.Repeat("z", 64), IdempotencyKey: "request-0001", Payload: json.RawMessage(`{}`)},
		{Kind: KindResolution, Fingerprint: strings.Repeat("a", 64), IdempotencyKey: "short", Payload: json.RawMessage(`{}`)},
		{Kind: KindResolution, Fingerprint: strings.Repeat("a", 64), IdempotencyKey: "request-0001", Payload: json.RawMessage(`invalid`)},
	}
	for _, request := range tests {
		if err := ValidateCreateRequest(request); !errors.Is(err, ErrInvalidInput) {
			t.Fatalf("request %#v error = %v", request, err)
		}
	}
}

func TestStatesAndSafeMessages(t *testing.T) {
	if !StateQueued.Valid() || StateQueued.Terminal() {
		t.Fatal("queued state must be valid and non-terminal")
	}
	if !StatePartial.Valid() || !StatePartial.Terminal() {
		t.Fatal("partial state must be valid and terminal")
	}
	if State("unknown").Valid() {
		t.Fatal("unknown state must be invalid")
	}

	message := SafeMessage("  a   safe\nmessage  ")
	if message != "a safe message" {
		t.Fatalf("safe message = %q", message)
	}
	if len(SafeMessage(strings.Repeat("x", 500))) != 240 {
		t.Fatal("safe messages must be bounded")
	}
}

func TestTransitionGraph(t *testing.T) {
	if !allowedTransition(StateQueued, StateCheckingCache) {
		t.Fatal("queued -> checking-cache should be allowed")
	}
	if allowedTransition(StateQueued, StateVerified) {
		t.Fatal("queued -> verified should not be allowed")
	}
	if allowedTransition(StatePartial, StateSearching) {
		t.Fatal("terminal states must reject transitions")
	}
}
