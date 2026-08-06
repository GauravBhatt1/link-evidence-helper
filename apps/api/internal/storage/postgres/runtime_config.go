package postgres

import (
	"errors"
	"fmt"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const (
	EnvEnabled         = "LINK_EVIDENCE_POSTGRES_ENABLED"
	EnvURL             = "LINK_EVIDENCE_POSTGRES_URL"
	EnvAllowInsecure   = "LINK_EVIDENCE_POSTGRES_ALLOW_INSECURE"
	EnvMaxOpenConns    = "LINK_EVIDENCE_POSTGRES_MAX_OPEN_CONNS"
	EnvMaxIdleConns    = "LINK_EVIDENCE_POSTGRES_MAX_IDLE_CONNS"
	EnvConnMaxLifetime = "LINK_EVIDENCE_POSTGRES_CONN_MAX_LIFETIME_SECONDS"
	EnvConnectTimeout  = "LINK_EVIDENCE_POSTGRES_CONNECT_TIMEOUT_SECONDS"
)

// LookupEnv is intentionally injected so configuration can be validated without
// reading process state during package initialization.
type LookupEnv func(string) (string, bool)

// RuntimeConfig contains only validated runtime configuration. URL is sensitive
// and must never be logged, formatted, or persisted.
type RuntimeConfig struct {
	Enabled         bool
	URL             string
	MaxOpenConns    int
	MaxIdleConns    int
	ConnMaxLifetime time.Duration
	ConnectTimeout  time.Duration
}

// Summary is safe for structured logs and diagnostics because it excludes the
// connection URL and all credentials.
type Summary struct {
	Enabled         bool
	MaxOpenConns    int
	MaxIdleConns    int
	ConnMaxLifetime time.Duration
	ConnectTimeout  time.Duration
}

func (c RuntimeConfig) SafeSummary() Summary {
	return Summary{
		Enabled:         c.Enabled,
		MaxOpenConns:    c.MaxOpenConns,
		MaxIdleConns:    c.MaxIdleConns,
		ConnMaxLifetime: c.ConnMaxLifetime,
		ConnectTimeout:  c.ConnectTimeout,
	}
}

func (c RuntimeConfig) String() string {
	return fmt.Sprintf("postgres runtime enabled=%t max_open=%d max_idle=%d conn_max_lifetime=%s connect_timeout=%s", c.Enabled, c.MaxOpenConns, c.MaxIdleConns, c.ConnMaxLifetime, c.ConnectTimeout)
}

// LoadRuntimeConfig validates an explicit disabled-by-default PostgreSQL
// boundary. It never opens a connection and never runs migrations.
func LoadRuntimeConfig(lookup LookupEnv) (RuntimeConfig, error) {
	if lookup == nil {
		return RuntimeConfig{}, errors.New("postgres runtime environment lookup is required")
	}

	enabled, err := envBool(lookup, EnvEnabled, false)
	if err != nil {
		return RuntimeConfig{}, err
	}
	config := RuntimeConfig{Enabled: enabled}
	if !enabled {
		return config, nil
	}

	rawURL := strings.TrimSpace(envValue(lookup, EnvURL))
	if rawURL == "" {
		return RuntimeConfig{}, fmt.Errorf("%s is required when PostgreSQL runtime is enabled", EnvURL)
	}
	allowInsecure, err := envBool(lookup, EnvAllowInsecure, false)
	if err != nil {
		return RuntimeConfig{}, err
	}
	if err := validateConnectionURL(rawURL, allowInsecure); err != nil {
		return RuntimeConfig{}, err
	}

	maxOpen, err := envInt(lookup, EnvMaxOpenConns, 20, 1, 200)
	if err != nil {
		return RuntimeConfig{}, err
	}
	maxIdle, err := envInt(lookup, EnvMaxIdleConns, 5, 0, maxOpen)
	if err != nil {
		return RuntimeConfig{}, err
	}
	lifetimeSeconds, err := envInt(lookup, EnvConnMaxLifetime, 300, 30, 3600)
	if err != nil {
		return RuntimeConfig{}, err
	}
	connectSeconds, err := envInt(lookup, EnvConnectTimeout, 10, 1, 60)
	if err != nil {
		return RuntimeConfig{}, err
	}

	config.URL = rawURL
	config.MaxOpenConns = maxOpen
	config.MaxIdleConns = maxIdle
	config.ConnMaxLifetime = time.Duration(lifetimeSeconds) * time.Second
	config.ConnectTimeout = time.Duration(connectSeconds) * time.Second
	return config, nil
}

func validateConnectionURL(raw string, allowInsecure bool) error {
	parsed, err := url.Parse(raw)
	if err != nil {
		return errors.New("invalid PostgreSQL connection URL")
	}
	if parsed.Scheme != "postgres" && parsed.Scheme != "postgresql" {
		return errors.New("PostgreSQL connection URL must use postgres or postgresql scheme")
	}
	if parsed.Hostname() == "" {
		return errors.New("PostgreSQL connection URL must include a host")
	}
	if parsed.User == nil || strings.TrimSpace(parsed.User.Username()) == "" {
		return errors.New("PostgreSQL connection URL must include a user")
	}
	if strings.Trim(parsed.Path, "/") == "" {
		return errors.New("PostgreSQL connection URL must include a database name")
	}
	if parsed.Fragment != "" {
		return errors.New("PostgreSQL connection URL must not include a fragment")
	}
	sslMode := strings.ToLower(strings.TrimSpace(parsed.Query().Get("sslmode")))
	if !allowInsecure && sslMode != "require" && sslMode != "verify-ca" && sslMode != "verify-full" {
		return errors.New("PostgreSQL connection URL must require TLS unless LINK_EVIDENCE_POSTGRES_ALLOW_INSECURE=true")
	}
	return nil
}

func envValue(lookup LookupEnv, name string) string {
	value, _ := lookup(name)
	return value
}

func envBool(lookup LookupEnv, name string, fallback bool) (bool, error) {
	value, ok := lookup(name)
	if !ok || strings.TrimSpace(value) == "" {
		return fallback, nil
	}
	parsed, err := strconv.ParseBool(strings.TrimSpace(value))
	if err != nil {
		return false, fmt.Errorf("%s must be true or false", name)
	}
	return parsed, nil
}

func envInt(lookup LookupEnv, name string, fallback, minimum, maximum int) (int, error) {
	value, ok := lookup(name)
	if !ok || strings.TrimSpace(value) == "" {
		return fallback, nil
	}
	parsed, err := strconv.Atoi(strings.TrimSpace(value))
	if err != nil || parsed < minimum || parsed > maximum {
		return 0, fmt.Errorf("%s must be an integer between %d and %d", name, minimum, maximum)
	}
	return parsed, nil
}
