// Package sourceconfig loads the deliberately small, credential-free source
// configuration accepted by the Go HTTP-first shadow worker. Authentication,
// cookies, arbitrary headers, scripts, and browser behavior are not supported
// by this boundary.
package sourceconfig

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strings"

	"github.com/GauravBhatt1/link-evidence-helper/apps/worker/internal/httpsearch"
)

const CurrentVersion = 1

var (
	ErrInvalidConfig = errors.New("sourceconfig: invalid configuration")
	identifier       = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,63}$`)
	fieldPath        = regexp.MustCompile(`^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$`)
)

type Config struct {
	Version int            `json:"version"`
	Sources []SourceConfig `json:"sources"`
}

type SourceConfig struct {
	ID                 string   `json:"id"`
	Name               string   `json:"name"`
	Enabled            bool     `json:"enabled"`
	Rank               int      `json:"rank"`
	Endpoint           string   `json:"endpoint"`
	QueryParameter     string   `json:"queryParameter"`
	Format             string   `json:"format"`
	ResultRoot         string   `json:"resultRoot,omitempty"`
	TitleField         string   `json:"titleField"`
	URLField           string   `json:"urlField"`
	AllowedResultHosts []string `json:"allowedResultHosts,omitempty"`
}

func Load(reader io.Reader) (Config, error) {
	decoder := json.NewDecoder(io.LimitReader(reader, 1<<20))
	decoder.DisallowUnknownFields()
	var config Config
	if err := decoder.Decode(&config); err != nil {
		return Config{}, fmt.Errorf("%w: decode JSON: %v", ErrInvalidConfig, err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return Config{}, fmt.Errorf("%w: configuration must contain one JSON document", ErrInvalidConfig)
	}
	if err := Validate(config); err != nil {
		return Config{}, err
	}
	return config, nil
}

func Validate(config Config) error {
	if config.Version != CurrentVersion {
		return fmt.Errorf("%w: version must be %d", ErrInvalidConfig, CurrentVersion)
	}
	if len(config.Sources) == 0 {
		return fmt.Errorf("%w: at least one source is required", ErrInvalidConfig)
	}
	if len(config.Sources) > httpsearch.MaxSources {
		return fmt.Errorf("%w: at most %d sources are allowed", ErrInvalidConfig, httpsearch.MaxSources)
	}
	seen := map[string]struct{}{}
	enabled := 0
	for index, source := range config.Sources {
		if err := validateSource(source); err != nil {
			return fmt.Errorf("%w: source %d: %v", ErrInvalidConfig, index+1, err)
		}
		if _, duplicate := seen[source.ID]; duplicate {
			return fmt.Errorf("%w: duplicate source id %q", ErrInvalidConfig, source.ID)
		}
		seen[source.ID] = struct{}{}
		if source.Enabled {
			enabled++
		}
	}
	if enabled == 0 {
		return fmt.Errorf("%w: at least one source must be enabled", ErrInvalidConfig)
	}
	return nil
}

func validateSource(source SourceConfig) error {
	if !identifier.MatchString(source.ID) {
		return errors.New("id must use lowercase letters, numbers, dots, underscores, or hyphens")
	}
	if strings.TrimSpace(source.Name) == "" || len(source.Name) > 120 {
		return errors.New("name must be 1-120 characters")
	}
	if source.Rank < 0 || source.Rank > 100000 {
		return errors.New("rank must be between 0 and 100000")
	}
	endpoint, err := url.Parse(source.Endpoint)
	if err != nil || endpoint.Scheme == "" || endpoint.Hostname() == "" || endpoint.RawQuery != "" || endpoint.Fragment != "" || endpoint.User != nil {
		return errors.New("endpoint must be an absolute credential-free URL without query or fragment")
	}
	if endpoint.Scheme != "http" && endpoint.Scheme != "https" {
		return errors.New("endpoint scheme must be http or https")
	}
	if !identifier.MatchString(source.QueryParameter) {
		return errors.New("queryParameter is invalid")
	}
	if source.Format != "json" {
		return errors.New("format must be json; HTML and browser sources use a separate worker boundary")
	}
	for label, path := range map[string]string{
		"titleField": source.TitleField,
		"urlField":   source.URLField,
	} {
		if !fieldPath.MatchString(path) {
			return fmt.Errorf("%s is invalid", label)
		}
	}
	if source.ResultRoot != "" && !fieldPath.MatchString(source.ResultRoot) {
		return errors.New("resultRoot is invalid")
	}
	seenHosts := map[string]struct{}{}
	for _, rawHost := range source.AllowedResultHosts {
		host := canonicalHost(rawHost)
		if host == "" || netIPLiteral(host) || strings.ContainsAny(host, "/:@?#") {
			return errors.New("allowedResultHosts must contain hostnames only")
		}
		if _, duplicate := seenHosts[host]; duplicate {
			return errors.New("allowedResultHosts contains a duplicate")
		}
		seenHosts[host] = struct{}{}
	}
	return nil
}

func Compile(config Config) ([]httpsearch.Source, error) {
	if err := Validate(config); err != nil {
		return nil, err
	}
	sources := make([]httpsearch.Source, 0, len(config.Sources))
	for _, configured := range config.Sources {
		if !configured.Enabled {
			continue
		}
		configured := configured
		allowed := make(map[string]struct{}, len(configured.AllowedResultHosts))
		for _, host := range configured.AllowedResultHosts {
			allowed[canonicalHost(host)] = struct{}{}
		}
		sources = append(sources, httpsearch.Source{
			ID:                 configured.ID,
			Name:               strings.TrimSpace(configured.Name),
			Rank:               configured.Rank,
			Endpoint:           configured.Endpoint,
			BuildURL:           buildURL(configured.QueryParameter),
			Parse:              parseJSON(configured.ResultRoot, configured.TitleField, configured.URLField),
			AllowedResultHosts: allowed,
		})
	}
	// Preserve priority semantics deterministically when configurations are
	// edited by sorting rank first and original order second via stable sort.
	sort.SliceStable(sources, func(i, j int) bool { return sources[i].Rank < sources[j].Rank })
	return sources, nil
}

func buildURL(parameter string) func(string, string) (string, error) {
	return func(endpoint, query string) (string, error) {
		parsed, err := url.Parse(endpoint)
		if err != nil {
			return "", err
		}
		values := parsed.Query()
		values.Set(parameter, query)
		parsed.RawQuery = values.Encode()
		return parsed.String(), nil
	}
}

func parseJSON(resultRoot, titleField, urlField string) func(*http.Response) ([]httpsearch.Result, error) {
	return func(response *http.Response) ([]httpsearch.Result, error) {
		decoder := json.NewDecoder(response.Body)
		decoder.UseNumber()
		var document any
		if err := decoder.Decode(&document); err != nil {
			return nil, fmt.Errorf("decode JSON response: %w", err)
		}
		if resultRoot != "" {
			value, found := lookup(document, resultRoot)
			if !found {
				return nil, fmt.Errorf("result root %q was not found", resultRoot)
			}
			document = value
		}
		rows, ok := document.([]any)
		if !ok {
			return nil, errors.New("configured JSON result root is not an array")
		}
		results := make([]httpsearch.Result, 0, len(rows))
		for index, row := range rows {
			titleValue, titleFound := lookup(row, titleField)
			urlValue, urlFound := lookup(row, urlField)
			title, titleOK := titleValue.(string)
			resultURL, urlOK := urlValue.(string)
			if !titleFound || !urlFound || !titleOK || !urlOK {
				return nil, fmt.Errorf("result %d does not contain string title and URL fields", index+1)
			}
			results = append(results, httpsearch.Result{Title: title, URL: resultURL})
		}
		return results, nil
	}
}

func lookup(document any, path string) (any, bool) {
	current := document
	for _, segment := range strings.Split(path, ".") {
		object, ok := current.(map[string]any)
		if !ok {
			return nil, false
		}
		current, ok = object[segment]
		if !ok {
			return nil, false
		}
	}
	return current, true
}

func canonicalHost(value string) string {
	return strings.ToLower(strings.TrimSuffix(strings.TrimSpace(value), "."))
}

func netIPLiteral(value string) bool {
	parsed := netParseIP(value)
	return parsed
}

// netParseIP is kept tiny so this package does not expose network policy; the
// actual DNS and IP safety enforcement remains in httpsearch.Engine.
func netParseIP(value string) bool {
	for _, character := range value {
		if (character < '0' || character > '9') && character != '.' && character != ':' &&
			(character < 'a' || character > 'f') && (character < 'A' || character > 'F') {
			return false
		}
	}
	return strings.Contains(value, ".") || strings.Contains(value, ":")
}
