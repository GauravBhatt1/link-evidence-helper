import type { SearchResponse } from "../../../types/contracts";

export type SearchRequest = {
  query: string;
};

export interface SearchTransport {
  search(request: SearchRequest, signal: AbortSignal): Promise<SearchResponse>;
}
