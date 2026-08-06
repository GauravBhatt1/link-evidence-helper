package recovery

import (
	"crypto/sha256"
	"encoding/hex"
	"reflect"
	"testing"
	"time"
)

func artifact(name string, content []byte) Artifact {
	sum := sha256.Sum256(content)
	return Artifact{Name: name, Kind: ArtifactPostgreSQLData, SizeBytes: int64(len(content)), SHA256: hex.EncodeToString(sum[:])}
}

func TestNormalizeBackupPlanIsDeterministic(t *testing.T) {
	plan, err := NormalizeBackupPlan(BackupPlan{
		ID:        "backup-20260806",
		CreatedAt: time.Date(2026, 8, 6, 15, 0, 0, 0, time.FixedZone("IST", 19800)),
		Artifacts: []Artifact{artifact("z-data", []byte("z")), artifact("a-data", []byte("a"))},
	})
	if err != nil {
		t.Fatal(err)
	}
	if plan.CreatedAt.Location() != time.UTC {
		t.Fatalf("location = %v", plan.CreatedAt.Location())
	}
	if got := []string{plan.Artifacts[0].Name, plan.Artifacts[1].Name}; !reflect.DeepEqual(got, []string{"a-data", "z-data"}) {
		t.Fatalf("order = %v", got)
	}
}

func TestVerifyArtifact(t *testing.T) {
	content := []byte("bounded-backup")
	item := artifact("postgres-data", content)
	if err := VerifyArtifact(content, item); err != nil {
		t.Fatal(err)
	}
	if err := VerifyArtifact([]byte("tampered"), item); err == nil {
		t.Fatal("tampered artifact accepted")
	}
}

func TestPlanRetentionKeepsNewestAndRecent(t *testing.T) {
	now := time.Date(2026, 8, 6, 10, 0, 0, 0, time.UTC)
	makePlan := func(id string, age time.Duration) BackupPlan {
		return BackupPlan{ID: id, CreatedAt: now.Add(-age), Artifacts: []Artifact{artifact(id+"-data", []byte(id))}}
	}
	decision, err := PlanRetention([]BackupPlan{
		makePlan("backup-new", 24*time.Hour),
		makePlan("backup-mid", 10*24*time.Hour),
		makePlan("backup-old", 40*24*time.Hour),
	}, RetentionPolicy{KeepLast: 1, MaxAge: 30 * 24 * time.Hour}, now)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(decision.Keep, []string{"backup-new", "backup-mid"}) {
		t.Fatalf("keep = %v", decision.Keep)
	}
	if !reflect.DeepEqual(decision.Delete, []string{"backup-old"}) {
		t.Fatalf("delete = %v", decision.Delete)
	}
}

func TestRestorePlanIsAlwaysNonExecutable(t *testing.T) {
	plan := BackupPlan{ID: "backup-safe", CreatedAt: time.Now(), Artifacts: []Artifact{artifact("postgres-data", []byte("db"))}}
	restore, err := PlanRestore(plan, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	if restore.Executable {
		t.Fatal("restore unexpectedly executable")
	}
	restore.Artifacts[0].Name = "changed"
	normalized, _ := NormalizeBackupPlan(plan)
	if normalized.Artifacts[0].Name == "changed" {
		t.Fatal("restore plan aliases input")
	}
}

func TestRejectsUnsafeInputs(t *testing.T) {
	valid := BackupPlan{ID: "backup-safe", CreatedAt: time.Now(), Artifacts: []Artifact{artifact("postgres-data", []byte("db"))}}
	cases := []BackupPlan{valid, valid, valid, valid}
	cases[0].ID = "../backup"
	cases[1].Artifacts[0].Name = "secret?token=x"
	cases[2].Artifacts[0].SHA256 = "not-a-digest"
	cases[3].Artifacts = append(cases[3].Artifacts, cases[3].Artifacts[0])
	for _, plan := range cases {
		if _, err := NormalizeBackupPlan(plan); err == nil {
			t.Fatalf("unsafe plan accepted: %+v", plan)
		}
	}
}
