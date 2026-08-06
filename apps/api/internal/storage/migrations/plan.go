package migrations

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

var (
	ErrInvalidMigrationSet = errors.New("invalid migration set")
	migrationNamePattern   = regexp.MustCompile(`^([0-9]{4})_([a-z0-9_]+)\.(up|down)\.sql$`)
)

// Direction identifies whether a migration moves the schema forward or rolls it back.
type Direction string

const (
	DirectionUp   Direction = "up"
	DirectionDown Direction = "down"
)

// Step is an immutable, checksum-addressed migration unit.
type Step struct {
	Version   uint64
	Name      string
	Direction Direction
	Filename  string
	Checksum  string
	SQL       string
}

// Plan validates the embedded migration set and returns deterministic steps.
// It never opens a database or executes SQL.
func Plan(direction Direction) ([]Step, error) {
	if direction != DirectionUp && direction != DirectionDown {
		return nil, ErrInvalidMigrationSet
	}
	return planFromFS(Files, direction)
}

func planFromFS(filesystem fs.FS, direction Direction) ([]Step, error) {
	entries, err := fs.ReadDir(filesystem, ".")
	if err != nil {
		return nil, fmt.Errorf("%w: read migrations: %v", ErrInvalidMigrationSet, err)
	}

	type pair struct {
		name string
		up   string
		down string
	}
	pairs := make(map[uint64]pair)
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".sql") {
			continue
		}
		matches := migrationNamePattern.FindStringSubmatch(entry.Name())
		if matches == nil {
			return nil, fmt.Errorf("%w: invalid filename %q", ErrInvalidMigrationSet, entry.Name())
		}
		version, parseErr := strconv.ParseUint(matches[1], 10, 64)
		if parseErr != nil || version == 0 {
			return nil, fmt.Errorf("%w: invalid version in %q", ErrInvalidMigrationSet, entry.Name())
		}
		current := pairs[version]
		if current.name != "" && current.name != matches[2] {
			return nil, fmt.Errorf("%w: version %04d has mismatched names", ErrInvalidMigrationSet, version)
		}
		current.name = matches[2]
		if matches[3] == string(DirectionUp) {
			if current.up != "" {
				return nil, fmt.Errorf("%w: duplicate up migration %04d", ErrInvalidMigrationSet, version)
			}
			current.up = entry.Name()
		} else {
			if current.down != "" {
				return nil, fmt.Errorf("%w: duplicate down migration %04d", ErrInvalidMigrationSet, version)
			}
			current.down = entry.Name()
		}
		pairs[version] = current
	}
	if len(pairs) == 0 {
		return nil, fmt.Errorf("%w: no migrations", ErrInvalidMigrationSet)
	}

	versions := make([]uint64, 0, len(pairs))
	for version, migration := range pairs {
		if migration.up == "" || migration.down == "" {
			return nil, fmt.Errorf("%w: version %04d is not reversible", ErrInvalidMigrationSet, version)
		}
		versions = append(versions, version)
	}
	sort.Slice(versions, func(i, j int) bool { return versions[i] < versions[j] })
	for index, version := range versions {
		if version != uint64(index+1) {
			return nil, fmt.Errorf("%w: expected version %04d, found %04d", ErrInvalidMigrationSet, index+1, version)
		}
	}
	if direction == DirectionDown {
		sort.Slice(versions, func(i, j int) bool { return versions[i] > versions[j] })
	}

	steps := make([]Step, 0, len(versions))
	for _, version := range versions {
		migration := pairs[version]
		filename := migration.up
		if direction == DirectionDown {
			filename = migration.down
		}
		content, readErr := fs.ReadFile(filesystem, filename)
		if readErr != nil {
			return nil, fmt.Errorf("%w: read %q: %v", ErrInvalidMigrationSet, filename, readErr)
		}
		sql := string(content)
		if strings.TrimSpace(sql) == "" || strings.ContainsRune(sql, '\x00') {
			return nil, fmt.Errorf("%w: unsafe content in %q", ErrInvalidMigrationSet, filename)
		}
		digest := sha256.Sum256(content)
		steps = append(steps, Step{
			Version: version, Name: migration.name, Direction: direction,
			Filename: filename, Checksum: hex.EncodeToString(digest[:]), SQL: sql,
		})
	}
	return steps, nil
}
