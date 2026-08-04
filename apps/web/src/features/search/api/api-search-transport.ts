import { searchResponseSchema, type SearchResponse } from "../../../types/contracts";
import type { SearchRequest, SearchTransport } from "./search-transport";
import { SearchTransportError } from "./search-transport-error";

export type ApiSearchTransportOptions = {
  endpoint?: string;
  fetchImpl?: typeof fetch;
};

/**
 * Same-origin client for the versioned Go development API. It never adds
 * credentials, tokens, source fields, or external endpoints.
 */
export class ApiSearchTransport implements SearchTransport {
  private readonly endpoint: string;
  private readonly fetchImpl: typeof fetch;

  constructor({ endpoint = "/api/v1/search", fetchImpl = fetch }: ApiSearchTransportOptions = {}) {
    this.endpoint = endpoint;
    this.fetchImpl = fetchImpl;
  }

  async search({ query }: SearchRequest, signal: AbortSignal): Promise<SearchResponse> {
    const target = new URL(this.endpoint, window.location.origin);
    if (target.origin !== window.location.origin) {
      throw new SearchTransportError("api-rejected");
    }
    target.search = "";
    target.searchParams.set("q", query);

    let response: Response;
    try {
      response = await this.fetchImpl(target.toString(), {
        method: "GET",
        headers: { Accept: "application/json" },
        cache: "no-store",
        credentials: "same-origin",
        redirect: "error",
        signal,
      });
    } catch (error) {
      if (signal.aborted) throw error;
      throw new SearchTransportError("api-unavailable");
    }

    if (!response.ok) {
      throw new SearchTransportError(response.status >= 500 ? "api-unavailable" : "api-rejected");
    }
    if (!response.headers.get("content-type")?.toLowerCase().includes("application/json")) {
      throw new SearchTransportError("invalid-contract");
    }

    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new SearchTransportError("invalid-contract");
    }
    const parsed = searchResponseSchema.safeParse(payload);
    if (!parsed.success) throw new SearchTransportError("invalid-contract");
    return parsed.data;
  }
}

export const apiSearchTransport = new ApiSearchTransport();
