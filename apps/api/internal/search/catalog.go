// Package search provides the development search boundary for the Go API.
// It intentionally reads only sanitized contract fixtures. Live sources are
// not contacted in this milestone.
package search

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"

	"github.com/GauravBhatt1/link-evidence-helper/apps/api/internal/contracts"
)

const MaxQueryRunes = 120

var (
	ErrEmptyQuery         = errors.New("search query is empty")
	ErrQueryTooLong       = errors.New("search query is too long")
	ErrDevelopmentFixture = errors.New("development fixture error")
)

// Searcher is the API-facing search contract. Future live search backends must
// satisfy this interface without changing the HTTP or React contracts.
type Searcher interface {
	Search(ctx context.Context, query string) (contracts.SearchResponse, error)
}

// FixtureCatalog implements Searcher with deterministic sanitized fixtures.
type FixtureCatalog struct {
	aliases   map[string]string
	responses map[string]contracts.SearchResponse
}

var fixtureFiles = map[string]string{
	"movie-several":    "movie-several-variants.json",
	"movie-one":        "movie-one-variant.json",
	"multi-quality":    "multi-quality-release.json",
	"multiple-sources": "multiple-source-candidates.json",
	"tv-episode":       "tv-episode.json",
	"season-pack":      "season-pack.json",
	"partial":          "partial-search-success.json",
}

var fixtureAliases = map[string]string{
	"example film":          "movie-several",
	"single release":        "movie-one",
	"multi quality":         "multi-quality",
	"multiple sources":      "multiple-sources",
	"example show s02e03":   "tv-episode",
	"example show season 2": "season-pack",
	"fixture collection":    "collection",
	"partial search":        "partial",
	"fixture error":         "error",
}

// NewFixtureCatalog loads and validates the fixture envelope at startup. The
// supplied directory must point at packages/testing/fixtures.
func NewFixtureCatalog(dir string) (*FixtureCatalog, error) {
	responses := make(map[string]contracts.SearchResponse, len(fixtureFiles)+1)
	for scenario, filename := range fixtureFiles {
		data, err := os.ReadFile(filepath.Join(dir, filename))
		if err != nil {
			return nil, fmt.Errorf("load fixture %s: %w", filename, err)
		}
		var response contracts.SearchResponse
		decoder := json.NewDecoder(strings.NewReader(string(data)))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&response); err != nil {
			return nil, fmt.Errorf("decode fixture %s: %w", filename, err)
		}
		if !response.OK || !response.Success || response.Code == "" {
			return nil, fmt.Errorf("fixture %s is not a successful canonical search response", filename)
		}
		if response.Contents == nil {
			response.Contents = []contracts.Content{}
		}
		if response.PartialFailures == nil {
			response.PartialFailures = []contracts.PartialFailure{}
		}
		responses[scenario] = response
	}

	movie, movieOK := firstContent(responses["movie-several"])
	show, showOK := firstContent(responses["tv-episode"])
	if !movieOK || !showOK {
		return nil, errors.New("collection fixtures must each contain one content item")
	}
	responses["collection"] = contracts.SearchResponse{
		OK:              true,
		Success:         true,
		Code:            "ok",
		Contents:        []contracts.Content{movie, show},
		PartialFailures: []contracts.PartialFailure{},
	}

	aliases := make(map[string]string, len(fixtureAliases))
	for alias, scenario := range fixtureAliases {
		aliases[alias] = scenario
	}
	return &FixtureCatalog{aliases: aliases, responses: responses}, nil
}

func firstContent(response contracts.SearchResponse) (contracts.Content, bool) {
	if len(response.Contents) == 0 {
		return contracts.Content{}, false
	}
	return response.Contents[0], true
}

// NormalizeQuery applies the only supported normalization: trim surrounding
// whitespace and collapse repeated whitespace. It does not perform fuzzy,
// substring, or similarity matching.
func NormalizeQuery(query string) (string, error) {
	normalized := strings.Join(strings.Fields(query), " ")
	if normalized == "" {
		return "", ErrEmptyQuery
	}
	if utf8.RuneCountInString(normalized) > MaxQueryRunes {
		return "", ErrQueryTooLong
	}
	return normalized, nil
}

func aliasKey(query string) string {
	return strings.ToLower(query)
}

// Search returns an exact alias match, the canonical empty response for an
// unknown alias, or a safe deterministic development error.
func (catalog *FixtureCatalog) Search(ctx context.Context, query string) (contracts.SearchResponse, error) {
	if err := ctx.Err(); err != nil {
		return contracts.SearchResponse{}, err
	}
	normalized, err := NormalizeQuery(query)
	if err != nil {
		return contracts.SearchResponse{}, err
	}

	scenario, found := catalog.aliases[aliasKey(normalized)]
	if !found {
		return contracts.SearchResponse{
			OK:              true,
			Success:         true,
			Code:            "ok",
			Query:           normalized,
			Contents:        []contracts.Content{},
			PartialFailures: []contracts.PartialFailure{},
		}, nil
	}
	if scenario == "error" {
		return contracts.SearchResponse{}, ErrDevelopmentFixture
	}
	response, ok := catalog.responses[scenario]
	if !ok {
		return contracts.SearchResponse{}, errors.New("fixture scenario is not loaded")
	}
	response.Query = normalized
	return response, nil
}
