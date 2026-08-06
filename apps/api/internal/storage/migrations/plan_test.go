package migrations

import (
	"errors"
	"strings"
	"testing"
	"testing/fstest"
)

func TestEmbeddedPlanIsDeterministicAndReversible(t *testing.T) {
	up, err := Plan(DirectionUp)
	if err != nil {
		t.Fatal(err)
	}
	down, err := Plan(DirectionDown)
	if err != nil {
		t.Fatal(err)
	}
	if len(up) == 0 || len(up) != len(down) {
		t.Fatalf("unexpected plan lengths: up=%d down=%d", len(up), len(down))
	}
	for index := range up {
		rollback := down[len(down)-1-index]
		if up[index].Version != rollback.Version || up[index].Name != rollback.Name {
			t.Fatalf("migration pair mismatch: %#v %#v", up[index], rollback)
		}
		if len(up[index].Checksum) != 64 || len(rollback.Checksum) != 64 {
			t.Fatal("migration checksum must be a full SHA-256 digest")
		}
		if up[index].SQL == "" || rollback.SQL == "" {
			t.Fatal("migration SQL must be present")
		}
	}
}

func TestPlanRejectsInvalidDirection(t *testing.T) {
	_, err := Plan(Direction("sideways"))
	if !errors.Is(err, ErrInvalidMigrationSet) {
		t.Fatalf("expected ErrInvalidMigrationSet, got %v", err)
	}
}

func TestPlanRejectsMissingRollback(t *testing.T) {
	filesystem := fstest.MapFS{
		"0001_example.up.sql": {Data: []byte("SELECT 1;")},
	}
	_, err := planFromFS(filesystem, DirectionUp)
	if !errors.Is(err, ErrInvalidMigrationSet) || !strings.Contains(err.Error(), "not reversible") {
		t.Fatalf("expected reversible-set error, got %v", err)
	}
}

func TestPlanRejectsVersionGaps(t *testing.T) {
	filesystem := fstest.MapFS{
		"0001_first.up.sql":   {Data: []byte("SELECT 1;")},
		"0001_first.down.sql": {Data: []byte("SELECT 1;")},
		"0003_third.up.sql":   {Data: []byte("SELECT 3;")},
		"0003_third.down.sql": {Data: []byte("SELECT 3;")},
	}
	_, err := planFromFS(filesystem, DirectionUp)
	if !errors.Is(err, ErrInvalidMigrationSet) || !strings.Contains(err.Error(), "expected version") {
		t.Fatalf("expected version-gap error, got %v", err)
	}
}

func TestPlanRejectsMismatchedPairNames(t *testing.T) {
	filesystem := fstest.MapFS{
		"0001_create.up.sql":    {Data: []byte("SELECT 1;")},
		"0001_destroy.down.sql": {Data: []byte("SELECT 1;")},
	}
	_, err := planFromFS(filesystem, DirectionUp)
	if !errors.Is(err, ErrInvalidMigrationSet) || !strings.Contains(err.Error(), "mismatched names") {
		t.Fatalf("expected name-mismatch error, got %v", err)
	}
}

func TestPlanRejectsUnsafeOrEmptyContent(t *testing.T) {
	for name, content := range map[string][]byte{
		"empty": nil,
		"nul":   []byte("SELECT 1;\x00"),
	} {
		t.Run(name, func(t *testing.T) {
			filesystem := fstest.MapFS{
				"0001_example.up.sql":   {Data: content},
				"0001_example.down.sql": {Data: []byte("SELECT 1;")},
			}
			_, err := planFromFS(filesystem, DirectionUp)
			if !errors.Is(err, ErrInvalidMigrationSet) {
				t.Fatalf("expected ErrInvalidMigrationSet, got %v", err)
			}
		})
	}
}

func TestPlanReturnsDefensiveValues(t *testing.T) {
	first, err := Plan(DirectionUp)
	if err != nil {
		t.Fatal(err)
	}
	original := first[0].SQL
	first[0].SQL = "mutated"
	second, err := Plan(DirectionUp)
	if err != nil {
		t.Fatal(err)
	}
	if second[0].SQL != original {
		t.Fatal("plan must be rebuilt from immutable embedded files")
	}
}
