// Package shadowsearch composes the credential-free source configuration,
// HTTP-first engine, and content aggregation layers. It remains separate from
// the production Redis worker until release-candidate parity is complete.
package shadowsearch

import (
	"context"

	"github.com/GauravBhatt1/link-evidence-helper/apps/worker/internal/contentaggregate"
	"github.com/GauravBhatt1/link-evidence-helper/apps/worker/internal/httpsearch"
)

type Output struct {
	Mode            string                     `json:"mode"`
	Query           string                     `json:"query"`
	Contents        []contentaggregate.Content `json:"contents"`
	PartialFailures []httpsearch.SourceError   `json:"partialFailures"`
}

func Run(ctx context.Context, engine httpsearch.Engine, query string, sources []httpsearch.Source) (Output, error) {
	normalized, err := httpsearch.NormalizeQuery(query)
	if err != nil {
		return Output{}, err
	}
	response, err := engine.Search(ctx, normalized, sources)
	if err != nil {
		return Output{}, err
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
	contents := contentaggregate.Aggregate(candidates)
	if contents == nil {
		contents = []contentaggregate.Content{}
	}
	failures := response.Errors
	if failures == nil {
		failures = []httpsearch.SourceError{}
	}
	return Output{
		Mode:            "development-shadow-http-search",
		Query:           normalized,
		Contents:        contents,
		PartialFailures: failures,
	}, nil
}
