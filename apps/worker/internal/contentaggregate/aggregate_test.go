package contentaggregate

import "testing"

func TestNormalizedTitleMatchesLegacyNoiseRemoval(t *testing.T) {
	for input, expected := range map[string]string{
		"Hockey Movie 2025 Hindi Dual Audio 1080p WEB-DL 1.5GB": "hockey",
		"Example Show Season 2 English 720p WEBRip":             "example show 2",
		"Download Ikka ESub 2160p HEVC":                         "ikka",
	} {
		if actual := NormalizedTitle(input); actual != expected {
			t.Fatalf("NormalizedTitle(%q) = %q, want %q", input, actual, expected)
		}
	}
}

func TestAggregateMergesProviderCandidatesAndIgnoresSizeForVariantIdentity(t *testing.T) {
	contents := Aggregate([]Candidate{
		{
			Title: "Hockey 2025 Hindi Dual Audio 1080p WEB-DL 1.5GB",
			URL:   "https://source-a.example/hockey", SourceID: "source-a", SourceName: "Source A",
		},
		{
			Title: "Hockey Movie 2025 Hindi Dual Audio 1080p WEB-DL 2GB",
			URL:   "https://source-b.example/hockey", SourceID: "source-b", SourceName: "Source B",
		},
	})
	if len(contents) != 1 {
		t.Fatalf("contents = %#v", contents)
	}
	content := contents[0]
	if content.ContentID != "content_2eeb1c44085d606b" || content.Year != "2025" || content.MediaType != "movie" || content.TotalSources != 2 {
		t.Fatalf("content = %#v", content)
	}
	if len(content.ReleaseVariants) != 1 {
		t.Fatalf("variants = %#v", content.ReleaseVariants)
	}
	variant := content.ReleaseVariants[0]
	if variant.VariantID != "variant_04a7d2afd20948f4" || variant.Language != "Hindi" || variant.AudioVariant != "Dual Audio" || variant.Quality != "1080P" || variant.ReleaseType != "WEB-DL" || variant.ApproxSize != "1.5GB" {
		t.Fatalf("variant = %#v", variant)
	}
	if len(variant.Sources) != 2 || variant.Sources[0].SourceID != "source_ffc0883c2a6a98d9" || variant.Sources[1].SourceID != "source_4e68b446f70dd00a" {
		t.Fatalf("sources = %#v", variant.Sources)
	}
	if len(content.Languages) != 1 || content.Languages[0] != "Hindi" {
		t.Fatalf("languages = %#v", content.Languages)
	}
}

func TestAggregateRepresentsMultiQualityReleaseHonestly(t *testing.T) {
	contents := Aggregate([]Candidate{{
		Title: "Example Film 2024 Hindi 480p 720p FHD 4K WEB-DL",
		URL:   "https://source.example/example-film", SourceID: "source",
	}})
	variant := contents[0].ReleaseVariants[0]
	if variant.Quality != "Multiple" {
		t.Fatalf("quality = %q", variant.Quality)
	}
	expected := []string{"480p", "720p", "1080p", "2160p"}
	if len(variant.AvailableQualities) != len(expected) {
		t.Fatalf("available qualities = %#v", variant.AvailableQualities)
	}
	for index := range expected {
		if variant.AvailableQualities[index] != expected[index] {
			t.Fatalf("available qualities = %#v", variant.AvailableQualities)
		}
	}
}

func TestAggregateExtractsTVSeasonAndEpisode(t *testing.T) {
	contents := Aggregate([]Candidate{{
		Title: "Example Show S02E03 English 1080p WEBRip 900MB",
		URL:   "https://source.example/show", SourceID: "source",
	}})
	if len(contents) != 1 || contents[0].MediaType != "tv" {
		t.Fatalf("contents = %#v", contents)
	}
	variant := contents[0].ReleaseVariants[0]
	if variant.Season == nil || *variant.Season != 2 || variant.Episode == nil || *variant.Episode != 3 || variant.PackType != "episode" || variant.ReleaseType != "WEBRIP" {
		t.Fatalf("variant = %#v", variant)
	}
}

func TestAggregateUsesTMDBIdentityAcrossDifferentProviderTitles(t *testing.T) {
	contents := Aggregate([]Candidate{
		{Title: "Example Film 2024 Hindi 1080p", TMDBID: "100", URL: "https://one.example/item", SourceID: "one"},
		{Title: "Example Film Extended 2024 English 720p", TMDBID: "100", URL: "https://two.example/item", SourceID: "two"},
	})
	if len(contents) != 1 || contents[0].TMDBID != "100" || contents[0].TotalSources != 2 {
		t.Fatalf("contents = %#v", contents)
	}
	if len(contents[0].ReleaseVariants) != 2 {
		t.Fatalf("variants = %#v", contents[0].ReleaseVariants)
	}
}

func TestAggregateKeepsVariantReferencesValidAcrossSliceGrowth(t *testing.T) {
	contents := Aggregate([]Candidate{
		{Title: "Example Film 2024 Hindi 1080p WEB-DL", URL: "https://one.example/hindi", SourceID: "one"},
		{Title: "Example Film 2024 English 720p WEB-DL", URL: "https://two.example/english", SourceID: "two"},
		{Title: "Example Film 2024 Hindi 1080p WEB-DL", URL: "https://three.example/hindi", SourceID: "three"},
	})
	if len(contents) != 1 || len(contents[0].ReleaseVariants) != 2 {
		t.Fatalf("contents = %#v", contents)
	}
	var hindi *ReleaseVariant
	for index := range contents[0].ReleaseVariants {
		variant := &contents[0].ReleaseVariants[index]
		if variant.Language == "Hindi" {
			hindi = variant
		}
	}
	if hindi == nil || len(hindi.Sources) != 2 || hindi.Sources[0].AdapterName != "one" || hindi.Sources[1].AdapterName != "three" {
		t.Fatalf("Hindi variant = %#v", hindi)
	}
}

func TestAggregateDeduplicatesIdenticalSourceCandidates(t *testing.T) {
	row := Candidate{Title: "Example 2024 English 720p", URL: "https://source.example/item", SourceID: "source"}
	contents := Aggregate([]Candidate{row, row})
	if len(contents) != 1 || len(contents[0].ReleaseVariants) != 1 || len(contents[0].ReleaseVariants[0].Sources) != 1 {
		t.Fatalf("contents = %#v", contents)
	}
}

func TestAggregateSkipsCandidatesWithoutTitleOrURL(t *testing.T) {
	contents := Aggregate([]Candidate{
		{Title: "", URL: "https://source.example/item"},
		{Title: "Example", URL: ""},
	})
	if len(contents) != 0 {
		t.Fatalf("contents = %#v", contents)
	}
}
