package sqliteimport

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"time"
)

var ErrInvalidManifest = errors.New("invalid SQLite import manifest")

const manifestVersion = 1

// Manifest is a portable, tamper-evident representation of a reviewed dry-run
// plan. It contains only the credential-free source fields accepted by Plan.
type Manifest struct {
	Version   int            `json:"version"`
	CreatedAt time.Time      `json:"created_at"`
	Sources   []ManifestStep `json:"sources"`
	Rollback  []RollbackStep `json:"rollback"`
	Checksum  string         `json:"checksum"`
}

// ManifestStep is the stable JSON representation of one source creation.
type ManifestStep struct {
	ID          string `json:"id"`
	DisplayName string `json:"display_name"`
	Kind        string `json:"kind"`
	Endpoint    string `json:"endpoint"`
	Enabled     bool   `json:"enabled"`
}

type manifestPayload struct {
	Version   int            `json:"version"`
	CreatedAt time.Time      `json:"created_at"`
	Sources   []ManifestStep `json:"sources"`
	Rollback  []RollbackStep `json:"rollback"`
}

// NewManifest converts a validated Plan into a deterministic review artifact.
func NewManifest(plan Plan) (Manifest, error) {
	if plan.CreatedAt.IsZero() {
		return Manifest{}, ErrInvalidManifest
	}

	sources := make([]ManifestStep, len(plan.Sources))
	for index, step := range plan.Sources {
		sources[index] = ManifestStep{
			ID:          step.Draft.ID,
			DisplayName: step.Draft.DisplayName,
			Kind:        step.Draft.Kind,
			Endpoint:    step.Draft.Endpoint,
			Enabled:     step.Draft.Enabled,
		}
	}
	rollback := append([]RollbackStep(nil), plan.Rollback...)

	payload := manifestPayload{
		Version:   manifestVersion,
		CreatedAt: plan.CreatedAt.UTC(),
		Sources:   sources,
		Rollback:  rollback,
	}
	checksum, err := checksumPayload(payload)
	if err != nil {
		return Manifest{}, ErrInvalidManifest
	}

	return Manifest{
		Version:   payload.Version,
		CreatedAt: payload.CreatedAt,
		Sources:   append([]ManifestStep(nil), payload.Sources...),
		Rollback:  append([]RollbackStep(nil), payload.Rollback...),
		Checksum:  checksum,
	}, nil
}

// Verify validates structure and checksum without opening a database or
// applying any source mutation.
func (manifest Manifest) Verify() error {
	if manifest.Version != manifestVersion || manifest.CreatedAt.IsZero() {
		return ErrInvalidManifest
	}
	if len(manifest.Checksum) != sha256.Size*2 {
		return ErrInvalidManifest
	}
	if _, err := hex.DecodeString(manifest.Checksum); err != nil {
		return ErrInvalidManifest
	}

	payload := manifestPayload{
		Version:   manifest.Version,
		CreatedAt: manifest.CreatedAt.UTC(),
		Sources:   append([]ManifestStep(nil), manifest.Sources...),
		Rollback:  append([]RollbackStep(nil), manifest.Rollback...),
	}
	expected, err := checksumPayload(payload)
	if err != nil || expected != manifest.Checksum {
		return ErrInvalidManifest
	}
	return nil
}

// MarshalJSONVerified returns canonical JSON only for a valid manifest.
func (manifest Manifest) MarshalJSONVerified() ([]byte, error) {
	if err := manifest.Verify(); err != nil {
		return nil, err
	}
	encoded, err := json.Marshal(manifest)
	if err != nil {
		return nil, ErrInvalidManifest
	}
	return encoded, nil
}

func checksumPayload(payload manifestPayload) (string, error) {
	encoded, err := json.Marshal(payload)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(encoded)
	return hex.EncodeToString(sum[:]), nil
}
