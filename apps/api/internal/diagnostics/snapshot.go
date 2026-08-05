package diagnostics

import (
	"errors"
	"sort"
	"strings"
	"time"
)

const Version = "v1"

type Status string

const (
	StatusOK       Status = "ok"
	StatusDegraded Status = "degraded"
	StatusDisabled Status = "disabled"
)

type Component string

const (
	ComponentAPI        Component = "api"
	ComponentRedis      Component = "redis"
	ComponentPostgres   Component = "postgres"
	ComponentLibrary    Component = "library"
	ComponentSearch     Component = "search"
	ComponentResolution Component = "resolution"
	ComponentBrowser    Component = "browser"
)

type Check struct {
	Component Component `json:"component"`
	Status    Status    `json:"status"`
	Code      string    `json:"code"`
}

type Snapshot struct {
	Version     string    `json:"version"`
	GeneratedAt time.Time `json:"generatedAt"`
	Overall     Status    `json:"overall"`
	Checks      []Check   `json:"checks"`
}

func NewSnapshot(now time.Time, checks []Check) (Snapshot, error) {
	if now.IsZero() {
		return Snapshot{}, errors.New("generated time is required")
	}
	if len(checks) == 0 || len(checks) > 16 {
		return Snapshot{}, errors.New("diagnostic checks must contain between 1 and 16 entries")
	}

	copyChecks := append([]Check(nil), checks...)
	seen := make(map[Component]struct{}, len(copyChecks))
	overall := StatusOK
	for i := range copyChecks {
		check := &copyChecks[i]
		if !validComponent(check.Component) {
			return Snapshot{}, errors.New("unsupported diagnostic component")
		}
		if _, exists := seen[check.Component]; exists {
			return Snapshot{}, errors.New("duplicate diagnostic component")
		}
		seen[check.Component] = struct{}{}
		if !validStatus(check.Status) {
			return Snapshot{}, errors.New("unsupported diagnostic status")
		}
		check.Code = strings.TrimSpace(check.Code)
		if !validCode(check.Code) {
			return Snapshot{}, errors.New("diagnostic code must be a bounded lowercase identifier")
		}
		if check.Status == StatusDegraded {
			overall = StatusDegraded
		}
	}

	sort.Slice(copyChecks, func(i, j int) bool { return copyChecks[i].Component < copyChecks[j].Component })
	return Snapshot{
		Version:     Version,
		GeneratedAt: now.UTC(),
		Overall:     overall,
		Checks:      copyChecks,
	}, nil
}

func validStatus(value Status) bool {
	switch value {
	case StatusOK, StatusDegraded, StatusDisabled:
		return true
	default:
		return false
	}
}

func validComponent(value Component) bool {
	switch value {
	case ComponentAPI, ComponentRedis, ComponentPostgres, ComponentLibrary, ComponentSearch, ComponentResolution, ComponentBrowser:
		return true
	default:
		return false
	}
}

func validCode(value string) bool {
	if len(value) < 2 || len(value) > 48 {
		return false
	}
	for _, r := range value {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || r == '_' {
			continue
		}
		return false
	}
	return true
}
