package postgres

import (
	"strings"
	"testing"
	"time"
)

func lookup(values map[string]string) LookupEnv {
	return func(name string) (string, bool) {
		value, ok := values[name]
		return value, ok
	}
}

func TestLoadRuntimeConfigDisabledByDefault(t *testing.T) {
	config, err := LoadRuntimeConfig(lookup(map[string]string{}))
	if err != nil {
		t.Fatal(err)
	}
	if config.Enabled {
		t.Fatal("PostgreSQL runtime must be disabled by default")
	}
	if config.URL != "" {
		t.Fatal("disabled configuration must not retain a URL")
	}
}

func TestLoadRuntimeConfigEnabled(t *testing.T) {
	const secret = "top-secret-password"
	config, err := LoadRuntimeConfig(lookup(map[string]string{
		EnvEnabled:         "true",
		EnvURL:             "postgresql://app:" + secret + "@db.example.test/evidence?sslmode=verify-full",
		EnvMaxOpenConns:    "40",
		EnvMaxIdleConns:    "10",
		EnvConnMaxLifetime: "600",
		EnvConnectTimeout:  "15",
	}))
	if err != nil {
		t.Fatal(err)
	}
	if !config.Enabled || config.MaxOpenConns != 40 || config.MaxIdleConns != 10 {
		t.Fatalf("unexpected config: %#v", config.SafeSummary())
	}
	if config.ConnMaxLifetime != 10*time.Minute || config.ConnectTimeout != 15*time.Second {
		t.Fatalf("unexpected durations: %#v", config.SafeSummary())
	}
	if strings.Contains(config.String(), secret) || strings.Contains(config.String(), "db.example.test") {
		t.Fatal("String leaked connection URL material")
	}
	if strings.Contains(config.SafeSummary().ConnectTimeout.String(), secret) {
		t.Fatal("safe summary leaked secret")
	}
}

func TestLoadRuntimeConfigRejectsMissingURL(t *testing.T) {
	_, err := LoadRuntimeConfig(lookup(map[string]string{EnvEnabled: "true"}))
	if err == nil || !strings.Contains(err.Error(), EnvURL) {
		t.Fatalf("expected missing URL error, got %v", err)
	}
}

func TestLoadRuntimeConfigRequiresTLS(t *testing.T) {
	_, err := LoadRuntimeConfig(lookup(map[string]string{
		EnvEnabled: "true",
		EnvURL:     "postgres://app:secret@localhost/evidence?sslmode=disable",
	}))
	if err == nil || !strings.Contains(err.Error(), "require TLS") {
		t.Fatalf("expected TLS error, got %v", err)
	}
}

func TestLoadRuntimeConfigAllowsExplicitInsecureDevelopment(t *testing.T) {
	config, err := LoadRuntimeConfig(lookup(map[string]string{
		EnvEnabled:       "true",
		EnvURL:           "postgres://app:secret@127.0.0.1/evidence?sslmode=disable",
		EnvAllowInsecure: "true",
	}))
	if err != nil {
		t.Fatal(err)
	}
	if !config.Enabled {
		t.Fatal("expected enabled config")
	}
}

func TestLoadRuntimeConfigRejectsInvalidBounds(t *testing.T) {
	_, err := LoadRuntimeConfig(lookup(map[string]string{
		EnvEnabled:      "true",
		EnvURL:          "postgres://app:secret@db.example.test/evidence?sslmode=require",
		EnvMaxOpenConns: "4",
		EnvMaxIdleConns: "5",
	}))
	if err == nil || !strings.Contains(err.Error(), EnvMaxIdleConns) {
		t.Fatalf("expected max idle bounds error, got %v", err)
	}
}

func TestLoadRuntimeConfigNeverIncludesURLInErrors(t *testing.T) {
	const secret = "do-not-leak"
	_, err := LoadRuntimeConfig(lookup(map[string]string{
		EnvEnabled: "true",
		EnvURL:     "mysql://app:" + secret + "@db.example.test/evidence",
	}))
	if err == nil {
		t.Fatal("expected validation error")
	}
	if strings.Contains(err.Error(), secret) || strings.Contains(err.Error(), "db.example.test") {
		t.Fatalf("validation error leaked URL material: %v", err)
	}
}
