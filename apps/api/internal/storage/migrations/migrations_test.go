package migrations

import (
	"strings"
	"testing"
)

func TestInitialMigrationContainsRequiredTablesAndConstraints(t *testing.T) {
	content, err := Files.ReadFile("0001_admin_sources_audit.up.sql")
	if err != nil {
		t.Fatal(err)
	}
	sql := string(content)
	for _, required := range []string{
		"CREATE TABLE admin_sources",
		"CREATE TABLE admin_audit_events",
		"admin_sources_revision_positive",
		"admin_audit_action_allowed",
		"admin_audit_outcome_allowed",
		"timestamptz",
	} {
		if !strings.Contains(sql, required) {
			t.Fatalf("migration missing %q", required)
		}
	}
}

func TestMigrationsRemainCredentialFree(t *testing.T) {
	entries, err := Files.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		content, err := Files.ReadFile(entry.Name())
		if err != nil {
			t.Fatal(err)
		}
		lower := strings.ToLower(string(content))
		for _, forbidden := range []string{
			"password", "api_key", "apikey", "authorization", "cookie", "bearer", "secret", "request_body", "headers",
		} {
			if strings.Contains(lower, forbidden) {
				t.Fatalf("%s contains forbidden credential-shaped term %q", entry.Name(), forbidden)
			}
		}
	}
}

func TestRollbackDropsOnlyOwnedTables(t *testing.T) {
	content, err := Files.ReadFile("0001_admin_sources_audit.down.sql")
	if err != nil {
		t.Fatal(err)
	}
	sql := string(content)
	if !strings.Contains(sql, "DROP TABLE IF EXISTS admin_audit_events") || !strings.Contains(sql, "DROP TABLE IF EXISTS admin_sources") {
		t.Fatal("rollback does not drop both owned tables")
	}
	if strings.Contains(strings.ToUpper(sql), "DROP DATABASE") || strings.Contains(strings.ToUpper(sql), "DROP SCHEMA") {
		t.Fatal("rollback exceeds migration ownership boundary")
	}
}
