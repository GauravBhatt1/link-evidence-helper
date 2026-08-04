import { apiSearchTransport } from "./api-search-transport";
import { fixtureSearchTransport } from "./fixture-search-transport";
import type { SearchTransport } from "./search-transport";

export type SearchTransportMode = "fixture" | "api";

export type SearchTransportConfiguration = {
  mode: SearchTransportMode;
  transport: SearchTransport;
};

export function createSearchTransportConfiguration(value?: string): SearchTransportConfiguration {
  if (value === "api") return { mode: "api", transport: apiSearchTransport };
  return { mode: "fixture", transport: fixtureSearchTransport };
}

// Fixture mode remains the safe default. API mode requires the explicit Vite
// environment value VITE_SEARCH_TRANSPORT=api.
export const defaultSearchTransportConfiguration = createSearchTransportConfiguration(
  import.meta.env.VITE_SEARCH_TRANSPORT,
);
