package adminauth

import (
	"errors"
	"strings"
	"testing"
)

func TestVerifierAcceptsOnlyExactBearerToken(t *testing.T) {
	token := strings.Repeat("aB3_", 12)
	verifier, err := NewVerifier(token)
	if err != nil {
		t.Fatalf("NewVerifier() error = %v", err)
	}
	if !verifier.VerifyAuthorization("Bearer " + token) {
		t.Fatal("valid bearer token was rejected")
	}
	for _, header := range []string{
		"", "Basic " + token, "Bearer", "Bearer ", "Bearer " + token + "x",
		"Bearer  " + token, "Bearer " + token + " ", "Bearer " + token + "\n",
	} {
		if verifier.VerifyAuthorization(header) {
			t.Fatalf("header %q was unexpectedly accepted", header)
		}
	}
}

func TestVerifierRejectsUnsafeRuntimeConfiguration(t *testing.T) {
	for _, token := range []string{
		"", "too-short", strings.Repeat("x", 513), strings.Repeat("x", 31) + " ",
		strings.Repeat("x", 31) + "\n",
	} {
		if _, err := NewVerifier(token); !errors.Is(err, ErrInvalidTokenConfiguration) {
			t.Fatalf("NewVerifier(%q) error = %v", token, err)
		}
	}
}

func TestNilVerifierAlwaysRejects(t *testing.T) {
	var verifier *Verifier
	if verifier.VerifyAuthorization("Bearer " + strings.Repeat("x", 40)) {
		t.Fatal("nil verifier accepted a token")
	}
}
