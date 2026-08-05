package contracts

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"strings"
	"testing"

	jsonschema "github.com/santhosh-tekuri/jsonschema/v5"
)

type manifestEntry struct {
	File       string `json:"file"`
	Schema     string `json:"schema"`
	Valid      bool   `json:"valid"`
	Provenance string `json:"provenance"`
}

func paths(t *testing.T) (string, string) {
	t.Helper()
	_, filename, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate contract test")
	}
	root := filepath.Clean(filepath.Join(filepath.Dir(filename), "../../../.."))
	return filepath.Join(root, "packages/contracts/schema"), filepath.Join(root, "packages/testing/fixtures")
}

func compilerFor(t *testing.T, schemaDir string) *jsonschema.Compiler {
	t.Helper()
	compiler := jsonschema.NewCompiler()
	compiler.Draft = jsonschema.Draft2020
	entries, err := os.ReadDir(schemaDir)
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		data, err := os.ReadFile(filepath.Join(schemaDir, entry.Name()))
		if err != nil {
			t.Fatal(err)
		}
		id := "https://schemas.jobinfo.local/v1/" + entry.Name()
		if err := compiler.AddResource(id, bytes.NewReader(data)); err != nil {
			t.Fatal(err)
		}
	}
	return compiler
}

func typedValue(schema string) any {
	switch schema {
	case "source-candidate.schema.json":
		return &SourceCandidate{}
	case "release-variant.schema.json":
		return &ReleaseVariant{}
	case "content.schema.json":
		return &Content{}
	case "search-response.schema.json":
		return &SearchResponse{}
	case "library-item.schema.json":
		return &LibraryItem{}
	case "library-response.schema.json":
		return &LibraryResponse{}
	case "resolution-request.schema.json":
		return &ResolutionRequest{}
	case "resolution-result.schema.json":
		return &ResolutionResult{}
	case "job.schema.json":
		return &Job{}
	case "job-event.schema.json":
		return &JobEvent{}
	case "error.schema.json":
		return &ErrorResponse{}
	default:
		panic("unknown schema " + schema)
	}
}

func normalize(data []byte) (any, error) {
	var value any
	err := json.Unmarshal(data, &value)
	return value, err
}

func TestGoldenFixturesMatchCanonicalSchemasAndGoTypes(t *testing.T) {
	schemaDir, fixtureDir := paths(t)
	manifestData, err := os.ReadFile(filepath.Join(fixtureDir, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var manifest []manifestEntry
	if err := json.Unmarshal(manifestData, &manifest); err != nil {
		t.Fatal(err)
	}
	compiler := compilerFor(t, schemaDir)
	for _, entry := range manifest {
		entry := entry
		t.Run(entry.File, func(t *testing.T) {
			data, err := os.ReadFile(filepath.Join(fixtureDir, entry.File))
			if err != nil {
				t.Fatal(err)
			}
			var generic any
			if err := json.Unmarshal(data, &generic); err != nil {
				t.Fatal(err)
			}
			schema, err := compiler.Compile("https://schemas.jobinfo.local/v1/" + entry.Schema)
			if err != nil {
				t.Fatal(err)
			}
			validationErr := schema.Validate(generic)
			if entry.Valid && validationErr != nil {
				t.Fatalf("canonical schema rejected valid fixture: %v", validationErr)
			}
			if !entry.Valid {
				if validationErr == nil {
					t.Fatal("canonical schema accepted invalid fixture")
				}
				if err := json.Unmarshal(data, typedValue(entry.Schema)); err == nil {
					// Go decoding is permissive; schema rejection is the required public gate.
				}
				return
			}
			decoded := typedValue(entry.Schema)
			if err := json.Unmarshal(data, decoded); err != nil {
				t.Fatalf("Go decode failed: %v", err)
			}
			encoded, err := json.Marshal(decoded)
			if err != nil {
				t.Fatal(err)
			}
			left, _ := normalize(data)
			right, _ := normalize(encoded)
			if !reflect.DeepEqual(left, right) {
				t.Fatalf("Go round trip drift\nwant=%s\ngot=%s", data, encoded)
			}
			if err := schema.Validate(right); err != nil {
				t.Fatalf("Go output failed schema: %v", err)
			}
		})
	}
}

func TestPublicGoTypesContainNoInternalOrSecretJSONFields(t *testing.T) {
	forbidden := []string{"cookie", "authorization", "workflowMetadata", "selector", "signedQuery", "apiKey", "password", "token"}
	types := []reflect.Type{
		reflect.TypeOf(SourceCandidate{}), reflect.TypeOf(ReleaseVariant{}), reflect.TypeOf(Content{}),
		reflect.TypeOf(SearchResponse{}), reflect.TypeOf(LibraryItem{}), reflect.TypeOf(LibraryResponse{}),
		reflect.TypeOf(ResolutionRequest{}), reflect.TypeOf(ResolutionResult{}),
		reflect.TypeOf(Job{}), reflect.TypeOf(JobEvent{}), reflect.TypeOf(ErrorResponse{}),
	}
	var found []string
	for _, typ := range types {
		for i := 0; i < typ.NumField(); i++ {
			name := strings.Split(typ.Field(i).Tag.Get("json"), ",")[0]
			for _, blocked := range forbidden {
				if strings.EqualFold(name, blocked) {
					found = append(found, fmt.Sprintf("%s.%s", typ.Name(), name))
				}
			}
		}
	}
	sort.Strings(found)
	if len(found) > 0 {
		t.Fatalf("forbidden fields: %v", found)
	}
}

func TestGoTopLevelFieldsMatchCanonicalSchemas(t *testing.T) {
	schemaDir, _ := paths(t)
	types := map[string]reflect.Type{
		"source-candidate.schema.json":   reflect.TypeOf(SourceCandidate{}),
		"release-variant.schema.json":    reflect.TypeOf(ReleaseVariant{}),
		"content.schema.json":            reflect.TypeOf(Content{}),
		"search-response.schema.json":    reflect.TypeOf(SearchResponse{}),
		"library-item.schema.json":       reflect.TypeOf(LibraryItem{}),
		"library-response.schema.json":   reflect.TypeOf(LibraryResponse{}),
		"resolution-request.schema.json": reflect.TypeOf(ResolutionRequest{}),
		"resolution-result.schema.json":  reflect.TypeOf(ResolutionResult{}),
		"job.schema.json":                reflect.TypeOf(Job{}),
		"job-event.schema.json":          reflect.TypeOf(JobEvent{}),
		"error.schema.json":              reflect.TypeOf(ErrorResponse{}),
	}
	for name, typ := range types {
		data, err := os.ReadFile(filepath.Join(schemaDir, name))
		if err != nil {
			t.Fatal(err)
		}
		var schema struct {
			Properties map[string]any `json:"properties"`
		}
		if err := json.Unmarshal(data, &schema); err != nil {
			t.Fatal(err)
		}
		var goFields []string
		for i := 0; i < typ.NumField(); i++ {
			goFields = append(goFields, strings.Split(typ.Field(i).Tag.Get("json"), ",")[0])
		}
		var schemaFields []string
		for field := range schema.Properties {
			schemaFields = append(schemaFields, field)
		}
		sort.Strings(goFields)
		sort.Strings(schemaFields)
		if !reflect.DeepEqual(goFields, schemaFields) {
			t.Fatalf("%s field drift: go=%v schema=%v", name, goFields, schemaFields)
		}
	}
}
