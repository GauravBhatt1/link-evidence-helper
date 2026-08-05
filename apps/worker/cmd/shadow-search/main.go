// Command shadow-search runs the HTTP-first search and aggregation pipeline as
// an explicit development tool. It is not started by the production worker and
// cannot receive browser credentials, cookies, or arbitrary request headers.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"time"

	"github.com/GauravBhatt1/link-evidence-helper/apps/worker/internal/contentaggregate"
	"github.com/GauravBhatt1/link-evidence-helper/apps/worker/internal/httpsearch"
	"github.com/GauravBhatt1/link-evidence-helper/apps/worker/internal/sourceconfig"
)

type output struct {
	Mode            string                     `json:"mode"`
	Query           string                     `json:"query"`
	Contents        []contentaggregate.Content `json:"contents"`
	PartialFailures []httpsearch.SourceError   `json:"partialFailures"`
}

func main() {
	log.SetFlags(0)
	configPath := flag.String("config", "", "path to a credential-free source configuration JSON file")
	query := flag.String("query", "", "title to search")
	allowPrivate := flag.Bool("allow-private", false, "allow loopback/private endpoints for local tests")
	timeout := flag.Duration("source-timeout", 8*time.Second, "maximum duration for each source")
	flag.Parse()

	if *configPath == "" || *query == "" {
		flag.Usage()
		os.Exit(2)
	}
	if *timeout < 100*time.Millisecond || *timeout > 30*time.Second {
		log.Fatal("source-timeout must be between 100ms and 30s")
	}

	file, err := os.Open(*configPath)
	if err != nil {
		log.Fatalf("open source configuration: %v", err)
	}
	defer file.Close()
	config, err := sourceconfig.Load(file)
	if err != nil {
		log.Fatal(err)
	}
	sources, err := sourceconfig.Compile(config)
	if err != nil {
		log.Fatal(err)
	}

	engine := httpsearch.Engine{
		AllowPrivate:        *allowPrivate,
		SourceTimeout:       *timeout,
		MaxResponseBytes:    httpsearch.DefaultMaxResponseBytes,
		MaxResultsPerSource: httpsearch.DefaultMaxResultsPerSource,
		Backoff:             httpsearch.NewBackoff(30*time.Second, 10*time.Minute),
	}
	response, err := engine.Search(context.Background(), *query, sources)
	if err != nil {
		log.Fatal(err)
	}
	normalized, err := httpsearch.NormalizeQuery(*query)
	if err != nil {
		log.Fatal(err)
	}

	candidates := make([]contentaggregate.Candidate, 0, len(response.Results))
	for _, result := range response.Results {
		candidates = append(candidates, contentaggregate.Candidate{
			Title:      result.Title,
			URL:        result.URL,
			SourceID:   result.SourceID,
			SourceName: result.Source,
		})
	}
	encoded, err := json.MarshalIndent(output{
		Mode:            "development-shadow-http-search",
		Query:           normalized,
		Contents:        contentaggregate.Aggregate(candidates),
		PartialFailures: response.Errors,
	}, "", "  ")
	if err != nil {
		log.Fatalf("encode shadow result: %v", err)
	}
	if _, err := fmt.Fprintln(os.Stdout, string(encoded)); err != nil {
		log.Fatalf("write shadow result: %v", err)
	}
}
