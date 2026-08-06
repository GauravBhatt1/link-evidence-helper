package sqliteimport

import (
	"bytes"
	"errors"
	"testing"
	"time"
)

func TestManifestIsDeterministicAndVerifiable(t *testing.T) {
	now := time.Date(2026, 8, 6, 5, 30, 0, 0, time.FixedZone("test", 5*60*60+30*60))
	rows := []LegacySource{
		{ID: "source-b", DisplayName: "Source B", Kind: "http-html", Endpoint: "https://b.example.test/path", Enabled: true},
		{ID: "source-a", DisplayName: "Source A", Kind: "http-html", Endpoint: "https://a.example.test/path", Enabled: false},
	}

	plan, err := NewPlan(rows, now)
	if err != nil {
		t.Fatalf("NewPlan() error = %v", err)
	}
	first, err := NewManifest(plan)
	if err != nil {
		t.Fatalf("NewManifest() error = %v", err)
	}
	second, err := NewManifest(plan.Clone())
	if err != nil {
		t.Fatalf("NewManifest(clone) error = %v", err)
	}
	if first.Checksum != second.Checksum {
		t.Fatalf("checksums differ: %q != %q", first.Checksum, second.Checksum)
	}
	if err := first.Verify(); err != nil {
		t.Fatalf("Verify() error = %v", err)
	}

	firstJSON, err := first.MarshalJSONVerified()
	if err != nil {
		t.Fatalf("MarshalJSONVerified() error = %v", err)
	}
	secondJSON, err := second.MarshalJSONVerified()
	if err != nil {
		t.Fatalf("MarshalJSONVerified(clone) error = %v", err)
	}
	if !bytes.Equal(firstJSON, secondJSON) {
		t.Fatalf("manifest JSON is not deterministic:\n%s\n%s", firstJSON, secondJSON)
	}
	if first.CreatedAt.Location() != time.UTC {
		t.Fatalf("CreatedAt location = %v, want UTC", first.CreatedAt.Location())
	}
}

func TestManifestRejectsTampering(t *testing.T) {
	plan, err := NewPlan([]LegacySource{{
		ID: "source-a", DisplayName: "Source A", Kind: "http-html",
		Endpoint: "https://a.example.test/path", Enabled: true,
	}}, time.Date(2026, 8, 6, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatalf("NewPlan() error = %v", err)
	}
	manifest, err := NewManifest(plan)
	if err != nil {
		t.Fatalf("NewManifest() error = %v", err)
	}

	manifest.Sources[0].Endpoint = "https://attacker.example.test/path"
	if err := manifest.Verify(); !errors.Is(err, ErrInvalidManifest) {
		t.Fatalf("Verify() error = %v, want ErrInvalidManifest", err)
	}
	if _, err := manifest.MarshalJSONVerified(); !errors.Is(err, ErrInvalidManifest) {
		t.Fatalf("MarshalJSONVerified() error = %v, want ErrInvalidManifest", err)
	}
}

func TestManifestRejectsInvalidStructure(t *testing.T) {
	tests := []Manifest{
		{},
		{Version: manifestVersion, CreatedAt: time.Now().UTC(), Checksum: "not-hex"},
		{Version: manifestVersion + 1, CreatedAt: time.Now().UTC(), Checksum: string(make([]byte, 64))},
	}
	for index, manifest := range tests {
		if err := manifest.Verify(); !errors.Is(err, ErrInvalidManifest) {
			t.Fatalf("case %d: Verify() error = %v, want ErrInvalidManifest", index, err)
		}
	}
}

func TestNewManifestDefensivelyCopiesPlan(t *testing.T) {
	plan, err := NewPlan([]LegacySource{{
		ID: "source-a", DisplayName: "Source A", Kind: "http-html",
		Endpoint: "https://a.example.test/path", Enabled: true,
	}}, time.Date(2026, 8, 6, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatalf("NewPlan() error = %v", err)
	}
	manifest, err := NewManifest(plan)
	if err != nil {
		t.Fatalf("NewManifest() error = %v", err)
	}

	plan.Sources[0].Draft.Endpoint = "https://changed.example.test/path"
	plan.Rollback[0].SourceID = "changed"
	if manifest.Sources[0].Endpoint != "https://a.example.test/path" {
		t.Fatalf("manifest source changed with input plan: %q", manifest.Sources[0].Endpoint)
	}
	if manifest.Rollback[0].SourceID != "source-a" {
		t.Fatalf("manifest rollback changed with input plan: %q", manifest.Rollback[0].SourceID)
	}
	if err := manifest.Verify(); err != nil {
		t.Fatalf("Verify() after input mutation error = %v", err)
	}
}
