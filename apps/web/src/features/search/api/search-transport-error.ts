export type SearchTransportErrorCode =
  | "fixture-error"
  | "invalid-contract"
  | "api-unavailable"
  | "api-rejected";

const SAFE_MESSAGES: Record<SearchTransportErrorCode, string> = {
  "fixture-error": "The development fixture search could not be completed.",
  "invalid-contract": "Development search data did not match the application contract.",
  "api-unavailable": "The development Go search API is unavailable.",
  "api-rejected": "The development Go search request was rejected.",
};

export class SearchTransportError extends Error {
  readonly code: SearchTransportErrorCode;

  constructor(code: SearchTransportErrorCode) {
    super(SAFE_MESSAGES[code]);
    this.name = "SearchTransportError";
    this.code = code;
  }
}

export function safeSearchError(error: unknown) {
  if (error instanceof SearchTransportError) return error.message;
  return "The development search could not be completed.";
}
