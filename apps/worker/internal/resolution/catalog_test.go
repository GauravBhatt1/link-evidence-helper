package resolution

import (
	"errors"
	"strings"
	"testing"
)

func validCatalogFile() CatalogFile {
	return CatalogFile{Version: CatalogVersion, Variants: []VariantCatalog{{
		ContentID: "content_3b750f8edc77152e",
		VariantID: "variant_051fab7b083f979a",
		Qualities: []string{"720p", "1080p"},
		Sources: []SourceCatalog{
			{SourceID: "backup", Priority: 20, URL: "https://backup.example/file.mkv", Quality: "1080P"},
			{SourceID: "primary", Priority: 10, URL: "https://primary.example/file.mkv?token=opaque", AllowedOrigins: []string{"https://delivery.example"}},
		},
	}}}
}

func TestCompileCatalogSortsSourcesAndSelectsCanonicalQuality(t *testing.T) {
	catalog, err := CompileCatalog(validCatalogFile())
	if err != nil {
		t.Fatal(err)
	}
	quality := " 1080P "
	selection, err := catalog.Select(Request{
		ContentID: "content_3b750f8edc77152e",
		VariantID: "variant_051fab7b083f979a",
		Quality:   &quality,
	})
	if err != nil {
		t.Fatal(err)
	}
	if selection.Quality != "1080p" || len(selection.Sources) != 2 || selection.Sources[0].SourceID != "primary" || selection.Sources[1].SourceID != "backup" {
		t.Fatalf("selection = %#v", selection)
	}
	if selection.Sources[0].Quality != "1080p" || selection.Sources[1].Quality != "1080p" {
		t.Fatalf("source qualities = %#v", selection.Sources)
	}
}

func TestSelectAutoChoosesOnlySingleQuality(t *testing.T) {
	file := validCatalogFile()
	file.Variants[0].Qualities = []string{"1080p"}
	file.Variants[0].Sources[0].Quality = "1080p"
	catalog, err := CompileCatalog(file)
	if err != nil {
		t.Fatal(err)
	}
	selection, err := catalog.Select(Request{
		ContentID: "content_3b750f8edc77152e",
		VariantID: "variant_051fab7b083f979a",
	})
	if err != nil || selection.Quality != "1080p" {
		t.Fatalf("selection=%#v err=%v", selection, err)
	}

	catalog, err = CompileCatalog(validCatalogFile())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := catalog.Select(Request{
		ContentID: "content_3b750f8edc77152e",
		VariantID: "variant_051fab7b083f979a",
	}); !errors.Is(err, ErrQualityRequired) {
		t.Fatalf("multi-quality error = %v", err)
	}
}

func TestLoadCatalogRejectsUnknownTrailingAndUnsafeValues(t *testing.T) {
	cases := []string{
		`{"version":1,"variants":[],"cookie":"secret"}`,
		`{"version":1,"variants":[]} {}`,
		`{"version":1,"variants":[{"contentId":"content","variantId":"variant","qualities":["1080p"],"sources":[{"sourceId":"source","priority":0,"url":"file:///tmp/file"}]}]}`,
		`{"version":1,"variants":[{"contentId":"content","variantId":"variant","qualities":["1080p"],"sources":[{"sourceId":"source","priority":0,"url":"https://user:pass@example.com/file"}]}]}`,
	}
	for _, raw := range cases {
		if _, err := LoadCatalog(strings.NewReader(raw)); !errors.Is(err, ErrInvalidCatalog) {
			t.Fatalf("LoadCatalog(%s) error = %v", raw, err)
		}
	}
}

func TestCompileCatalogRejectsDuplicatesAndUndeclaredQuality(t *testing.T) {
	file := validCatalogFile()
	file.Variants = append(file.Variants, file.Variants[0])
	if _, err := CompileCatalog(file); !errors.Is(err, ErrInvalidCatalog) {
		t.Fatalf("duplicate variant error = %v", err)
	}

	file = validCatalogFile()
	file.Variants[0].Sources[0].Quality = "2160p"
	if _, err := CompileCatalog(file); !errors.Is(err, ErrInvalidCatalog) {
		t.Fatalf("undeclared quality error = %v", err)
	}

	file = validCatalogFile()
	file.Variants[0].Sources[1].SourceID = file.Variants[0].Sources[0].SourceID
	if _, err := CompileCatalog(file); !errors.Is(err, ErrInvalidCatalog) {
		t.Fatalf("duplicate source error = %v", err)
	}
}

func TestSelectFiltersQualitySpecificSources(t *testing.T) {
	catalog, err := CompileCatalog(validCatalogFile())
	if err != nil {
		t.Fatal(err)
	}
	quality := "720p"
	selection, err := catalog.Select(Request{
		ContentID: "content_3b750f8edc77152e",
		VariantID: "variant_051fab7b083f979a",
		Quality:   &quality,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(selection.Sources) != 1 || selection.Sources[0].SourceID != "primary" || selection.Sources[0].Quality != "720p" {
		t.Fatalf("selection = %#v", selection)
	}
}
