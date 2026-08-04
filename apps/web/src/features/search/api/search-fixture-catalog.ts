import { searchResponseSchema, type SearchResponse } from "../../../types/contracts";
import movieOneVariant from "../../../../../../packages/testing/fixtures/movie-one-variant.json";
import movieSeveralVariants from "../../../../../../packages/testing/fixtures/movie-several-variants.json";
import multiQualityRelease from "../../../../../../packages/testing/fixtures/multi-quality-release.json";
import multipleSourceCandidates from "../../../../../../packages/testing/fixtures/multiple-source-candidates.json";
import partialSearchSuccess from "../../../../../../packages/testing/fixtures/partial-search-success.json";
import seasonPack from "../../../../../../packages/testing/fixtures/season-pack.json";
import tvEpisode from "../../../../../../packages/testing/fixtures/tv-episode.json";
import { SearchTransportError } from "./search-transport-error";

export const FIXTURE_ALIAS_DOCUMENTATION = [
  { alias: "Example Film", scenario: "Movie with several release variants" },
  { alias: "Single Release", scenario: "Movie with one release variant" },
  { alias: "Multi Quality", scenario: "Release requiring a quality decision" },
  { alias: "Multiple Sources", scenario: "One canonical release aggregated from two sources" },
  { alias: "Example Show S02E03", scenario: "TV episode search result" },
  { alias: "Example Show Season 2", scenario: "TV season-pack search result" },
  { alias: "Fixture Collection", scenario: "Two distinct unified content cards" },
  { alias: "Partial Search", scenario: "Usable results with a partial source failure" },
  { alias: "Fixture Error", scenario: "Safe development transport error" },
] as const;

export type FixtureScenario =
  | "movie-several"
  | "movie-one"
  | "multi-quality"
  | "multiple-sources"
  | "tv-episode"
  | "season-pack"
  | "collection"
  | "partial"
  | "error";

export const FIXTURE_ALIAS_MAP: Readonly<Record<string, FixtureScenario>> = Object.freeze({
  "example film": "movie-several",
  "single release": "movie-one",
  "multi quality": "multi-quality",
  "multiple sources": "multiple-sources",
  "example show s02e03": "tv-episode",
  "example show season 2": "season-pack",
  "fixture collection": "collection",
  "partial search": "partial",
  "fixture error": "error",
});

const parsedFixtures = {
  "movie-several": parseFixture(movieSeveralVariants),
  "movie-one": parseFixture(movieOneVariant),
  "multi-quality": parseFixture(multiQualityRelease),
  "multiple-sources": parseFixture(multipleSourceCandidates),
  "tv-episode": parseFixture(tvEpisode),
  "season-pack": parseFixture(seasonPack),
  partial: parseFixture(partialSearchSuccess),
} satisfies Record<Exclude<FixtureScenario, "collection" | "error">, SearchResponse>;

function parseFixture(value: unknown) {
  const parsed = searchResponseSchema.safeParse(value);
  if (!parsed.success) throw new SearchTransportError("invalid-contract");
  return parsed.data;
}

function cloneResponse(value: SearchResponse): SearchResponse {
  return structuredClone(value);
}

function collectionResponse(query: string): SearchResponse {
  const movie = parsedFixtures["movie-several"].contents[0];
  const show = parsedFixtures["tv-episode"].contents[0];
  if (!movie || !show) throw new SearchTransportError("invalid-contract");
  return parseFixture({
    ok: true,
    success: true,
    code: "ok",
    query,
    contents: [movie, show],
    partialFailures: [],
  });
}

export function fixtureResponseForScenario(scenario: Exclude<FixtureScenario, "error">, query: string) {
  const response = scenario === "collection" ? collectionResponse(query) : cloneResponse(parsedFixtures[scenario]);
  return parseFixture({ ...response, query });
}

export function emptyFixtureResponse(query: string) {
  return parseFixture({
    ok: true,
    success: true,
    code: "ok",
    query,
    contents: [],
    partialFailures: [],
  });
}
