// Package contentaggregate converts source-specific search candidates into the
// stable content/release hierarchy used by the public contracts. It deliberately
// contains no network, persistence, or browser code.
package contentaggregate

import (
	"crypto/sha256"
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

type Candidate struct {
	Title       string `json:"title"`
	Year        string `json:"year,omitempty"`
	MediaType   string `json:"mediaType,omitempty"`
	TMDBID      string `json:"tmdbId,omitempty"`
	Poster      string `json:"poster,omitempty"`
	Variant     string `json:"variant,omitempty"`
	Quality     string `json:"quality,omitempty"`
	Size        string `json:"size,omitempty"`
	URL         string `json:"url"`
	SourceID    string `json:"sourceId,omitempty"`
	SourceName  string `json:"sourceName,omitempty"`
	AdapterType string `json:"adapterType,omitempty"`
	Source      string `json:"source,omitempty"`
}

type SourceCandidate struct {
	SourceID          string    `json:"sourceId"`
	AdapterName       string    `json:"adapterName"`
	DisplayName       string    `json:"displayName"`
	Priority          int       `json:"priority"`
	VerificationState string    `json:"verificationState"`
	Candidate         Candidate `json:"-"`
}

type ReleaseVariant struct {
	VariantID          string            `json:"variantId"`
	Language           string            `json:"language"`
	AudioVariant       string            `json:"audioVariant"`
	Quality            string            `json:"quality"`
	AvailableQualities []string          `json:"availableQualities"`
	ReleaseType        string            `json:"releaseType"`
	PackType           string            `json:"packType"`
	Season             *int              `json:"season"`
	Episode            *int              `json:"episode"`
	ApproxSize         string            `json:"approxSize"`
	Sources            []SourceCandidate `json:"sources"`
}

type Content struct {
	ContentID       string           `json:"contentId"`
	TMDBID          string           `json:"tmdbId"`
	Title           string           `json:"title"`
	Year            string           `json:"year"`
	MediaType       string           `json:"mediaType"`
	Poster          string           `json:"poster"`
	Languages       []string         `json:"languages"`
	ReleaseVariants []ReleaseVariant `json:"releaseVariants"`
	TotalSources    int              `json:"totalSources"`
}

type variantFields struct {
	language           string
	audioVariant       string
	quality            string
	availableQualities []string
	releaseType        string
	packType           string
	season             *int
	episode            *int
	approxSize         string
}

type variantLocation struct {
	content *Content
	index   int
}

var (
	metadataTokens        = regexp.MustCompile(`(?i)\b(download|movie|esub|480p|720p|1080p|2160p|4k|uhd|fhd|web[- ]?dl|web[- ]?rip|bluray|brrip|hdrip|x26[45]|hevc|10bit|dual audio|multi audio|hindi dubbed|hindi|english|tamil|telugu|malayalam|kannada|season|series|episode|ep)\b`)
	yearPattern           = regexp.MustCompile(`\b(19|20)[0-9]{2}\b`)
	sizePattern           = regexp.MustCompile(`(?i)\b[0-9]+(\.[0-9]+)?\s*(KB|MB|GB|TB)\b`)
	nonAlphaNum           = regexp.MustCompile(`[^a-z0-9]+`)
	qualityPattern        = regexp.MustCompile(`(?i)\b(480p|720p|1080p|2160p|4k|uhd|fhd)\b`)
	releasePattern        = regexp.MustCompile(`(?i)\b(web[- ]?dl|web[- ]?rip|bluray|brrip|hdrip|dvdrip)\b`)
	seasonPattern         = regexp.MustCompile(`(?i)\b(season\s*|s)0?([0-9]{1,2})\b`)
	episodePattern        = regexp.MustCompile(`(?i)\b(episode\s*|ep\s*|e)0?([0-9]{1,3})\b`)
	compactEpisodePattern = regexp.MustCompile(`(?i)\bs0?([0-9]{1,2})e0?([0-9]{1,3})\b`)
	packPattern           = regexp.MustCompile(`(?i)\b(zip|complete|pack)\b`)
	tvPattern             = regexp.MustCompile(`(?i)\b(season|series|s[0-9]{1,2}(?:e[0-9]{1,3})?)\b`)
	dualPattern           = regexp.MustCompile(`(?i)\bdual\b`)
	multiPattern          = regexp.MustCompile(`(?i)\bmulti\b`)
)

var qualityAliases = map[string]string{
	"fhd": "1080p",
	"4k":  "2160p",
	"uhd": "2160p",
}

var qualityOrder = []string{"480p", "720p", "1080p", "2160p"}

func NormalizedTitle(value string) string {
	text := strings.ToLower(value)
	text = metadataTokens.ReplaceAllString(text, " ")
	text = yearPattern.ReplaceAllString(text, " ")
	text = sizePattern.ReplaceAllString(text, " ")
	text = nonAlphaNum.ReplaceAllString(text, " ")
	return strings.Join(strings.Fields(text), " ")
}

func Aggregate(rows []Candidate) []Content {
	contents := make([]*Content, 0)
	contentsByIdentity := map[string]*Content{}
	variantsByIdentity := map[string]variantLocation{}

	for priority, row := range rows {
		title := strings.TrimSpace(row.Title)
		if title == "" || strings.TrimSpace(row.URL) == "" {
			continue
		}
		year := firstYear(title)
		if year == "" {
			year = strings.TrimSpace(row.Year)
		}
		mediaType := strings.ToLower(strings.TrimSpace(row.MediaType))
		if mediaType == "" {
			if tvPattern.MatchString(title) {
				mediaType = "tv"
			} else {
				mediaType = "movie"
			}
		}
		tmdbID := strings.TrimSpace(row.TMDBID)
		identityParts := []any{"fallback", NormalizedTitle(title), year, mediaType}
		if tmdbID != "" {
			identityParts = []any{"tmdb", tmdbID}
		}
		identity := identityKey(identityParts...)
		content := contentsByIdentity[identity]
		if content == nil {
			content = &Content{
				ContentID:       stableID("content", identityParts...),
				TMDBID:          tmdbID,
				Title:           title,
				Year:            year,
				MediaType:       mediaType,
				Poster:          strings.TrimSpace(row.Poster),
				Languages:       []string{},
				ReleaseVariants: []ReleaseVariant{},
			}
			contentsByIdentity[identity] = content
			contents = append(contents, content)
		}

		fields := deriveVariant(row)
		variantIdentity := identityKey(
			content.ContentID,
			fields.language,
			fields.audioVariant,
			fields.quality,
			fields.releaseType,
			fields.packType,
			optionalInt(fields.season),
			optionalInt(fields.episode),
		)
		location, found := variantsByIdentity[variantIdentity]
		var variant *ReleaseVariant
		if !found {
			content.ReleaseVariants = append(content.ReleaseVariants, ReleaseVariant{
				VariantID:          stableID("variant", content.ContentID, fields.language, fields.audioVariant, fields.quality, fields.releaseType, fields.packType, optionalInt(fields.season), optionalInt(fields.episode)),
				Language:           fields.language,
				AudioVariant:       fields.audioVariant,
				Quality:            fields.quality,
				AvailableQualities: append([]string(nil), fields.availableQualities...),
				ReleaseType:        fields.releaseType,
				PackType:           fields.packType,
				Season:             fields.season,
				Episode:            fields.episode,
				ApproxSize:         fields.approxSize,
				Sources:            []SourceCandidate{},
			})
			location = variantLocation{content: content, index: len(content.ReleaseVariants) - 1}
			variantsByIdentity[variantIdentity] = location
			variant = &content.ReleaseVariants[location.index]
		} else {
			variant = &location.content.ReleaseVariants[location.index]
			for _, quality := range fields.availableQualities {
				if !contains(variant.AvailableQualities, quality) {
					variant.AvailableQualities = append(variant.AvailableQualities, quality)
				}
			}
		}

		adapterName := firstNonEmpty(row.SourceID, row.AdapterType, row.Source, "legacy")
		displayName := firstNonEmpty(row.SourceName, adapterName)
		source := SourceCandidate{
			SourceID:          stableID("source", adapterName, row.URL),
			AdapterName:       adapterName,
			DisplayName:       displayName,
			Priority:          priority,
			VerificationState: "unverified",
			Candidate:         row,
		}
		if !hasSource(variant.Sources, source.SourceID) {
			variant.Sources = append(variant.Sources, source)
		}
		for _, language := range strings.Split(fields.language, "/") {
			if language != "" && !contains(content.Languages, language) {
				content.Languages = append(content.Languages, language)
			}
		}
	}

	result := make([]Content, 0, len(contents))
	for _, content := range contents {
		providers := map[string]struct{}{}
		for variantIndex := range content.ReleaseVariants {
			variant := &content.ReleaseVariants[variantIndex]
			sort.SliceStable(variant.AvailableQualities, func(i, j int) bool {
				return qualityPosition(variant.AvailableQualities[i]) < qualityPosition(variant.AvailableQualities[j])
			})
			for _, source := range variant.Sources {
				providers[source.AdapterName] = struct{}{}
			}
		}
		content.TotalSources = len(providers)
		result = append(result, *content)
	}
	return result
}

func deriveVariant(row Candidate) variantFields {
	label := strings.Join([]string{row.Title, row.Variant, row.Quality, row.Size}, " ")
	qualityTokens := qualityPattern.FindAllString(label, -1)
	available := availableQualities(qualityTokens)
	quality := "Unknown"
	if len(available) > 1 {
		quality = "Multiple"
	} else if len(qualityTokens) > 0 {
		quality = strings.ToUpper(qualityTokens[0])
	} else if strings.TrimSpace(row.Quality) != "" {
		quality = strings.TrimSpace(row.Quality)
	}
	languages := languages(label)
	language := strings.Join(languages, "/")
	audioVariant := languages[0]
	if dualPattern.MatchString(label) {
		audioVariant = "Dual Audio"
	} else if multiPattern.MatchString(label) {
		audioVariant = "Multi Audio"
	}
	season, episode := compactEpisodeNumbers(label)
	if season == nil {
		season = matchedNumber(seasonPattern, label)
	}
	if episode == nil {
		episode = matchedNumber(episodePattern, label)
	}
	packType := "single"
	if episode != nil {
		packType = "episode"
	} else if season != nil && packPattern.MatchString(label) {
		packType = "season"
	}
	releaseType := "Unknown"
	if match := releasePattern.FindString(label); match != "" {
		releaseType = strings.ToUpper(strings.ReplaceAll(match, " ", "-"))
	}
	return variantFields{
		language:           language,
		audioVariant:       audioVariant,
		quality:            quality,
		availableQualities: available,
		releaseType:        releaseType,
		packType:           packType,
		season:             season,
		episode:            episode,
		approxSize:         strings.TrimSpace(sizePattern.FindString(label)),
	}
}

func compactEpisodeNumbers(value string) (*int, *int) {
	match := compactEpisodePattern.FindStringSubmatch(value)
	if len(match) < 3 {
		return nil, nil
	}
	season, seasonErr := strconv.Atoi(match[1])
	episode, episodeErr := strconv.Atoi(match[2])
	if seasonErr != nil || episodeErr != nil {
		return nil, nil
	}
	return &season, &episode
}

func availableQualities(tokens []string) []string {
	detected := map[string]struct{}{}
	for _, token := range tokens {
		canonical := strings.ToLower(strings.TrimSpace(token))
		if alias, found := qualityAliases[canonical]; found {
			canonical = alias
		}
		detected[canonical] = struct{}{}
	}
	qualities := make([]string, 0, len(detected))
	for _, quality := range qualityOrder {
		if _, found := detected[quality]; found {
			qualities = append(qualities, quality)
		}
	}
	return qualities
}

func languages(value string) []string {
	patterns := []struct {
		name    string
		pattern *regexp.Regexp
	}{
		{"Hindi", regexp.MustCompile(`(?i)\bhindi\b`)},
		{"English", regexp.MustCompile(`(?i)\benglish\b`)},
		{"Tamil", regexp.MustCompile(`(?i)\btamil\b`)},
		{"Telugu", regexp.MustCompile(`(?i)\btelugu\b`)},
		{"Malayalam", regexp.MustCompile(`(?i)\bmalayalam\b`)},
		{"Kannada", regexp.MustCompile(`(?i)\bkannada\b`)},
	}
	result := []string{}
	for _, item := range patterns {
		if item.pattern.MatchString(value) {
			result = append(result, item.name)
		}
	}
	if len(result) == 0 {
		return []string{"Unknown"}
	}
	return result
}

func firstYear(value string) string {
	return yearPattern.FindString(value)
}

func matchedNumber(pattern *regexp.Regexp, value string) *int {
	match := pattern.FindStringSubmatch(value)
	if len(match) < 3 {
		return nil
	}
	parsed, err := strconv.Atoi(match[2])
	if err != nil {
		return nil
	}
	return &parsed
}

func stableID(prefix string, parts ...any) string {
	normalized := make([]string, len(parts))
	for index, part := range parts {
		normalized[index] = normalizedPart(part)
	}
	digest := sha256.Sum256([]byte(strings.Join(normalized, "|")))
	return fmt.Sprintf("%s_%x", prefix, digest[:8])
}

func identityKey(parts ...any) string {
	normalized := make([]string, len(parts))
	for index, part := range parts {
		normalized[index] = normalizedPart(part)
	}
	return strings.Join(normalized, "\x00")
}

func normalizedPart(value any) string {
	if value == nil {
		return ""
	}
	return strings.ToLower(strings.TrimSpace(fmt.Sprint(value)))
}

func optionalInt(value *int) any {
	if value == nil {
		return nil
	}
	return *value
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if trimmed := strings.TrimSpace(value); trimmed != "" {
			return trimmed
		}
	}
	return ""
}

func contains(values []string, candidate string) bool {
	for _, value := range values {
		if value == candidate {
			return true
		}
	}
	return false
}

func hasSource(sources []SourceCandidate, sourceID string) bool {
	for _, source := range sources {
		if source.SourceID == sourceID {
			return true
		}
	}
	return false
}

func qualityPosition(value string) int {
	for index, quality := range qualityOrder {
		if value == quality {
			return index
		}
	}
	return len(qualityOrder)
}
