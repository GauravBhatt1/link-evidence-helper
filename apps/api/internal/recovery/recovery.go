package recovery

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"regexp"
	"sort"
	"strings"
	"time"
)

const (
	maxArtifactNameLength = 128
	maxBackupIDLength     = 64
	maxRetentionCount     = 90
)

var safeIdentifier = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]*$`)

type ArtifactKind string

const (
	ArtifactPostgreSQLSchema ArtifactKind = "postgresql_schema"
	ArtifactPostgreSQLData   ArtifactKind = "postgresql_data"
	ArtifactSQLiteLegacy     ArtifactKind = "sqlite_legacy"
	ArtifactConfigManifest   ArtifactKind = "config_manifest"
)

type Artifact struct {
	Name      string
	Kind      ArtifactKind
	SizeBytes int64
	SHA256    string
}

type BackupPlan struct {
	ID        string
	CreatedAt time.Time
	Artifacts []Artifact
}

type RetentionPolicy struct {
	KeepLast int
	MaxAge   time.Duration
}

type RetentionDecision struct {
	Keep   []string
	Delete []string
}

type RestorePlan struct {
	BackupID   string
	PlannedAt  time.Time
	Artifacts  []Artifact
	Executable bool
}

func NormalizeBackupPlan(plan BackupPlan) (BackupPlan, error) {
	if err := validateIdentifier("backup id", plan.ID, maxBackupIDLength); err != nil {
		return BackupPlan{}, err
	}
	if plan.CreatedAt.IsZero() {
		return BackupPlan{}, errors.New("backup creation time is required")
	}
	if len(plan.Artifacts) == 0 {
		return BackupPlan{}, errors.New("at least one backup artifact is required")
	}

	normalized := BackupPlan{ID: plan.ID, CreatedAt: plan.CreatedAt.UTC(), Artifacts: make([]Artifact, len(plan.Artifacts))}
	seen := make(map[string]struct{}, len(plan.Artifacts))
	for i, artifact := range plan.Artifacts {
		item, err := normalizeArtifact(artifact)
		if err != nil {
			return BackupPlan{}, err
		}
		if _, exists := seen[item.Name]; exists {
			return BackupPlan{}, fmt.Errorf("duplicate backup artifact %q", item.Name)
		}
		seen[item.Name] = struct{}{}
		normalized.Artifacts[i] = item
	}
	sort.Slice(normalized.Artifacts, func(i, j int) bool { return normalized.Artifacts[i].Name < normalized.Artifacts[j].Name })
	return normalized, nil
}

func PlanRetention(plans []BackupPlan, policy RetentionPolicy, now time.Time) (RetentionDecision, error) {
	if policy.KeepLast < 1 || policy.KeepLast > maxRetentionCount {
		return RetentionDecision{}, fmt.Errorf("keep-last must be between 1 and %d", maxRetentionCount)
	}
	if policy.MaxAge <= 0 || policy.MaxAge > 365*24*time.Hour {
		return RetentionDecision{}, errors.New("max age must be between zero and 365 days")
	}
	if now.IsZero() {
		return RetentionDecision{}, errors.New("retention evaluation time is required")
	}

	normalized := make([]BackupPlan, len(plans))
	ids := make(map[string]struct{}, len(plans))
	for i, plan := range plans {
		item, err := NormalizeBackupPlan(plan)
		if err != nil {
			return RetentionDecision{}, err
		}
		if _, exists := ids[item.ID]; exists {
			return RetentionDecision{}, fmt.Errorf("duplicate backup id %q", item.ID)
		}
		ids[item.ID] = struct{}{}
		normalized[i] = item
	}
	sort.Slice(normalized, func(i, j int) bool {
		if normalized[i].CreatedAt.Equal(normalized[j].CreatedAt) {
			return normalized[i].ID < normalized[j].ID
		}
		return normalized[i].CreatedAt.After(normalized[j].CreatedAt)
	})

	decision := RetentionDecision{}
	cutoff := now.UTC().Add(-policy.MaxAge)
	for i, plan := range normalized {
		if i < policy.KeepLast || !plan.CreatedAt.Before(cutoff) {
			decision.Keep = append(decision.Keep, plan.ID)
		} else {
			decision.Delete = append(decision.Delete, plan.ID)
		}
	}
	return decision, nil
}

func PlanRestore(plan BackupPlan, plannedAt time.Time) (RestorePlan, error) {
	normalized, err := NormalizeBackupPlan(plan)
	if err != nil {
		return RestorePlan{}, err
	}
	if plannedAt.IsZero() {
		return RestorePlan{}, errors.New("restore planning time is required")
	}
	return RestorePlan{
		BackupID:   normalized.ID,
		PlannedAt:  plannedAt.UTC(),
		Artifacts:  append([]Artifact(nil), normalized.Artifacts...),
		Executable: false,
	}, nil
}

func VerifyArtifact(content []byte, artifact Artifact) error {
	normalized, err := normalizeArtifact(artifact)
	if err != nil {
		return err
	}
	if int64(len(content)) != normalized.SizeBytes {
		return errors.New("backup artifact size mismatch")
	}
	sum := sha256.Sum256(content)
	if hex.EncodeToString(sum[:]) != normalized.SHA256 {
		return errors.New("backup artifact checksum mismatch")
	}
	return nil
}

func normalizeArtifact(artifact Artifact) (Artifact, error) {
	if err := validateIdentifier("artifact name", artifact.Name, maxArtifactNameLength); err != nil {
		return Artifact{}, err
	}
	if !validArtifactKind(artifact.Kind) {
		return Artifact{}, errors.New("backup artifact kind is invalid")
	}
	if artifact.SizeBytes < 0 {
		return Artifact{}, errors.New("backup artifact size cannot be negative")
	}
	digest := strings.ToLower(artifact.SHA256)
	decoded, err := hex.DecodeString(digest)
	if err != nil || len(decoded) != sha256.Size {
		return Artifact{}, errors.New("backup artifact sha256 is invalid")
	}
	artifact.SHA256 = digest
	return artifact, nil
}

func validateIdentifier(name, value string, max int) error {
	if value == "" || len(value) > max || !safeIdentifier.MatchString(value) {
		return fmt.Errorf("%s must use bounded lowercase identifier syntax", name)
	}
	return nil
}

func validArtifactKind(kind ArtifactKind) bool {
	switch kind {
	case ArtifactPostgreSQLSchema, ArtifactPostgreSQLData, ArtifactSQLiteLegacy, ArtifactConfigManifest:
		return true
	default:
		return false
	}
}
