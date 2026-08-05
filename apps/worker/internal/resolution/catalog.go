package resolution

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"regexp"
	"sort"
	"strings"
)

const (
	CatalogVersion     = 1
	MaxCatalogVariants = 10000
	MaxVariantSources  = 32
)

var (
	ErrInvalidCatalog   = errors.New("resolution: invalid catalog")
	ErrSelectionMissing = errors.New("resolution: selection not found")
	ErrQualityRequired  = errors.New("resolution: quality selection required")
	identifierPattern   = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`)
)

type CatalogFile struct {
	Version  int              `json:"version"`
	Variants []VariantCatalog `json:"variants"`
}

type VariantCatalog struct {
	ContentID string          `json:"contentId"`
	VariantID string          `json:"variantId"`
	Qualities []string        `json:"qualities"`
	Sources   []SourceCatalog `json:"sources"`
}

type SourceCatalog struct {
	SourceID       string   `json:"sourceId"`
	Priority       int      `json:"priority"`
	URL            string   `json:"url"`
	Filename       string   `json:"filename,omitempty"`
	Size           string   `json:"size,omitempty"`
	Quality        string   `json:"quality,omitempty"`
	AllowedOrigins []string `json:"allowedOrigins,omitempty"`
}

type Request struct {
	ContentID string  `json:"contentId"`
	VariantID string  `json:"variantId"`
	Quality   *string `json:"quality,omitempty"`
}

type Selection struct {
	ContentID string
	VariantID string
	Quality   string
	Sources   []SourceCatalog
}

type Catalog struct {
	variants map[string]VariantCatalog
}

func LoadCatalog(reader io.Reader) (*Catalog, error) {
	decoder := json.NewDecoder(io.LimitReader(reader, 8<<20))
	decoder.DisallowUnknownFields()
	var file CatalogFile
	if err := decoder.Decode(&file); err != nil {
		return nil, fmt.Errorf("%w: decode JSON: %v", ErrInvalidCatalog, err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return nil, fmt.Errorf("%w: catalog must contain one JSON document", ErrInvalidCatalog)
	}
	return CompileCatalog(file)
}

func CompileCatalog(file CatalogFile) (*Catalog, error) {
	if file.Version != CatalogVersion {
		return nil, fmt.Errorf("%w: version must be %d", ErrInvalidCatalog, CatalogVersion)
	}
	if len(file.Variants) == 0 || len(file.Variants) > MaxCatalogVariants {
		return nil, fmt.Errorf("%w: variants must contain 1-%d entries", ErrInvalidCatalog, MaxCatalogVariants)
	}
	catalog := &Catalog{variants: make(map[string]VariantCatalog, len(file.Variants))}
	for index, variant := range file.Variants {
		if err := validateVariant(&variant); err != nil {
			return nil, fmt.Errorf("%w: variant %d: %v", ErrInvalidCatalog, index+1, err)
		}
		key := selectionKey(variant.ContentID, variant.VariantID)
		if _, duplicate := catalog.variants[key]; duplicate {
			return nil, fmt.Errorf("%w: duplicate contentId/variantId", ErrInvalidCatalog)
		}
		sort.SliceStable(variant.Sources, func(i, j int) bool {
			return variant.Sources[i].Priority < variant.Sources[j].Priority
		})
		catalog.variants[key] = variant
	}
	return catalog, nil
}

func (catalog *Catalog) Select(request Request) (Selection, error) {
	if catalog == nil || !validIdentifier(request.ContentID) || !validIdentifier(request.VariantID) {
		return Selection{}, ErrSelectionMissing
	}
	variant, found := catalog.variants[selectionKey(request.ContentID, request.VariantID)]
	if !found {
		return Selection{}, ErrSelectionMissing
	}
	quality := ""
	if request.Quality != nil {
		quality = strings.TrimSpace(*request.Quality)
	}
	if quality == "" {
		if len(variant.Qualities) != 1 {
			return Selection{}, ErrQualityRequired
		}
		quality = variant.Qualities[0]
	} else {
		matched := canonicalMatch(variant.Qualities, quality)
		if matched == "" {
			return Selection{}, ErrSelectionMissing
		}
		quality = matched
	}

	sources := make([]SourceCatalog, 0, len(variant.Sources))
	for _, source := range variant.Sources {
		if source.Quality == "" || strings.EqualFold(strings.TrimSpace(source.Quality), quality) {
			source.Quality = quality
			sources = append(sources, source)
		}
	}
	if len(sources) == 0 {
		return Selection{}, ErrSelectionMissing
	}
	return Selection{
		ContentID: request.ContentID,
		VariantID: request.VariantID,
		Quality:   quality,
		Sources:   sources,
	}, nil
}

func validateVariant(variant *VariantCatalog) error {
	variant.ContentID = strings.TrimSpace(variant.ContentID)
	variant.VariantID = strings.TrimSpace(variant.VariantID)
	if !validIdentifier(variant.ContentID) || !validIdentifier(variant.VariantID) {
		return errors.New("contentId and variantId are invalid")
	}
	if len(variant.Qualities) == 0 || len(variant.Qualities) > 16 {
		return errors.New("qualities must contain 1-16 values")
	}
	qualityKeys := map[string]struct{}{}
	for index, quality := range variant.Qualities {
		quality = strings.TrimSpace(quality)
		if quality == "" || len(quality) > 80 {
			return errors.New("quality is invalid")
		}
		key := strings.ToLower(quality)
		if _, duplicate := qualityKeys[key]; duplicate {
			return errors.New("qualities contain a duplicate")
		}
		qualityKeys[key] = struct{}{}
		variant.Qualities[index] = quality
	}
	if len(variant.Sources) == 0 || len(variant.Sources) > MaxVariantSources {
		return fmt.Errorf("sources must contain 1-%d entries", MaxVariantSources)
	}
	sourceIDs := map[string]struct{}{}
	for index := range variant.Sources {
		source := &variant.Sources[index]
		source.SourceID = strings.TrimSpace(source.SourceID)
		if !validIdentifier(source.SourceID) || source.Priority < 0 || source.Priority > 100000 {
			return errors.New("source id or priority is invalid")
		}
		if _, duplicate := sourceIDs[source.SourceID]; duplicate {
			return errors.New("sources contain a duplicate sourceId")
		}
		sourceIDs[source.SourceID] = struct{}{}
		parsed, err := parseDeliveryURL(source.URL)
		if err != nil {
			return err
		}
		source.URL = parsed.String()
		if len(source.Filename) > 300 || len(source.Size) > 80 || len(source.Quality) > 80 || len(source.AllowedOrigins) > 32 {
			return errors.New("source metadata exceeds safe limits")
		}
		if source.Quality != "" {
			matched := canonicalMatch(variant.Qualities, source.Quality)
			if matched == "" {
				return errors.New("source quality is not declared by the variant")
			}
			source.Quality = matched
		}
		seenOrigins := map[string]struct{}{}
		for originIndex, rawOrigin := range source.AllowedOrigins {
			origin, err := parseOrigin(rawOrigin)
			if err != nil {
				return err
			}
			if _, duplicate := seenOrigins[origin]; duplicate {
				return errors.New("allowedOrigins contains a duplicate")
			}
			seenOrigins[origin] = struct{}{}
			source.AllowedOrigins[originIndex] = origin
		}
	}
	return nil
}

func parseDeliveryURL(raw string) (*url.URL, error) {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || parsed.Hostname() == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil || parsed.Fragment != "" {
		return nil, errors.New("source URL must be credential-free HTTP/HTTPS without a fragment")
	}
	return parsed, nil
}

func parseOrigin(raw string) (string, error) {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || parsed.Hostname() == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" || parsed.Path != "" && parsed.Path != "/" {
		return "", errors.New("allowed origin is invalid")
	}
	return strings.ToLower(parsed.Scheme + "://" + parsed.Host), nil
}

func validIdentifier(value string) bool {
	return identifierPattern.MatchString(value)
}

func canonicalMatch(values []string, requested string) string {
	requested = strings.TrimSpace(requested)
	for _, value := range values {
		if strings.EqualFold(value, requested) {
			return value
		}
	}
	return ""
}

func selectionKey(contentID, variantID string) string {
	return contentID + "\x00" + variantID
}
