package search

import (
	"context"
	"errors"
	"path/filepath"
	"runtime"
	"testing"
)

func fixtureDir(t *testing.T) string {
	t.Helper()
	_, filename, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate fixture catalog test")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(filename), "../../../../packages/testing/fixtures"))
}

func TestFixtureCatalogUsesOnlyExplicitAliases(t *testing.T) {
	catalog, err := NewFixtureCatalog(fixtureDir(t))
	if err != nil {
		t.Fatal(err)
	}

	response, err := catalog.Search(context.Background(), "  EXAMPLE   film  ")
	if err != nil {
		t.Fatal(err)
	}
	if response.Query != "EXAMPLE film" {
		t.Fatalf("normalized query = %q", response.Query)
	}
	if len(response.Contents) == 0 {
		t.Fatal("explicit alias should return fixture content")
	}

	for _, query := range []string{"Example Films", "Film", "Example"} {
		response, err := catalog.Search(context.Background(), query)
		if err != nil {
			t.Fatal(err)
		}
		if len(response.Contents) != 0 {
			t.Fatalf("unknown query %q must return an empty response", query)
		}
		if response.Contents == nil || response.PartialFailures == nil {
			t.Fatalf("empty response for %q must use non-nil arrays", query)
		}
	}
}

func TestFixtureCatalogCollectionAndSafeError(t *testing.T) {
	catalog, err := NewFixtureCatalog(fixtureDir(t))
	if err != nil {
		t.Fatal(err)
	}
	response, err := catalog.Search(context.Background(), "Fixture Collection")
	if err != nil {
		t.Fatal(err)
	}
	if len(response.Contents) != 2 {
		t.Fatalf("collection content count = %d", len(response.Contents))
	}

	_, err = catalog.Search(context.Background(), "Fixture Error")
	if !errors.Is(err, ErrDevelopmentFixture) {
		t.Fatalf("fixture error = %v", err)
	}
}

func TestFixtureCatalogValidatesQueriesAndCancellation(t *testing.T) {
	catalog, err := NewFixtureCatalog(fixtureDir(t))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := catalog.Search(context.Background(), "   "); !errors.Is(err, ErrEmptyQuery) {
		t.Fatalf("empty query error = %v", err)
	}
	tooLong := make([]rune, MaxQueryRunes+1)
	for index := range tooLong {
		tooLong[index] = 'x'
	}
	if _, err := catalog.Search(context.Background(), string(tooLong)); !errors.Is(err, ErrQueryTooLong) {
		t.Fatalf("long query error = %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := catalog.Search(ctx, "Example Film"); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled query error = %v", err)
	}
}
