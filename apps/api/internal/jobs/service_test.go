package jobs

import (
	"encoding/json"
	"errors"
	"testing"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/contracts"
	"github.com/GauravBhatt1/link-evidence-helper/packages/jobqueue"
)

func TestCanonicalResolution(t *testing.T) {
	quality := " 1080p "
	normalized, payload, fingerprint, err := canonicalResolution(contracts.ResolutionRequest{
		ContentID: " content ", VariantID: " variant ", Quality: &quality,
	})
	if err != nil {
		t.Fatal(err)
	}
	if normalized.ContentID != "content" || normalized.VariantID != "variant" || normalized.Quality == nil || *normalized.Quality != "1080p" {
		t.Fatalf("normalized request = %#v", normalized)
	}
	if len(fingerprint) != 64 || !json.Valid(payload) {
		t.Fatalf("fingerprint=%q payload=%s", fingerprint, payload)
	}
	var decoded map[string]any
	if err := json.Unmarshal(payload, &decoded); err != nil {
		t.Fatal(err)
	}
	if len(decoded) != 3 || decoded["contentId"] != "content" || decoded["variantId"] != "variant" || decoded["quality"] != "1080p" {
		t.Fatalf("decoded payload = %#v", decoded)
	}

	_, payloadAgain, fingerprintAgain, err := canonicalResolution(normalized)
	if err != nil {
		t.Fatal(err)
	}
	if string(payloadAgain) != string(payload) || fingerprintAgain != fingerprint {
		t.Fatal("canonical requests must produce deterministic payloads and fingerprints")
	}
}

func TestCanonicalResolutionRejectsInvalidInput(t *testing.T) {
	blank := "   "
	for _, request := range []contracts.ResolutionRequest{
		{},
		{ContentID: "content"},
		{ContentID: "content", VariantID: "variant", Quality: &blank},
	} {
		if _, _, _, err := canonicalResolution(request); !errors.Is(err, jobqueue.ErrInvalidInput) {
			t.Fatalf("request %#v error = %v", request, err)
		}
	}
}
