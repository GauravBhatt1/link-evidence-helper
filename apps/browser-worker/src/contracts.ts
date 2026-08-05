const identifierPattern = /^[a-z0-9][a-z0-9._:-]{7,127}$/;
const sourceIdentifierPattern = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const hostPattern = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;

export const MAX_QUERY_RUNES = 120;
export const MAX_RESULTS = 100;
export const MAX_ALLOWED_HOSTS = 32;
export const MIN_TIMEOUT_MS = 1_000;
export const MAX_TIMEOUT_MS = 15_000;
export const MAX_SETTLE_MS = 3_000;

export type BrowserSelectors = {
  readonly result: string;
  readonly title: string;
  readonly link: string;
};

export type BrowserSearchTask = {
  readonly taskId: string;
  readonly sourceId: string;
  readonly sourceName: string;
  readonly endpoint: string;
  readonly queryParameter: string;
  readonly query: string;
  readonly selectors: BrowserSelectors;
  readonly allowedResourceHosts: readonly string[];
  readonly allowedResultHosts: readonly string[];
  readonly timeoutMs: number;
  readonly settleMs: number;
  readonly maxResults: number;
};

export type BrowserSearchResult = {
  readonly sourceId: string;
  readonly sourceName: string;
  readonly title: string;
  readonly url: string;
};

export type BrowserSearchOutput = {
  readonly ok: true;
  readonly taskId: string;
  readonly sourceId: string;
  readonly results: readonly BrowserSearchResult[];
  readonly droppedResults: number;
};

export type BrowserWorkerErrorCode =
  | "invalid_task"
  | "unsafe_endpoint"
  | "navigation_failed"
  | "selector_failed"
  | "browser_failed"
  | "cancelled";

export class BrowserWorkerError extends Error {
  readonly code: BrowserWorkerErrorCode;

  constructor(code: BrowserWorkerErrorCode, message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "BrowserWorkerError";
    this.code = code;
  }
}

export function parseBrowserSearchTask(input: unknown): BrowserSearchTask {
  const object = strictObject(input, [
    "taskId",
    "sourceId",
    "sourceName",
    "endpoint",
    "queryParameter",
    "query",
    "selectors",
    "allowedResourceHosts",
    "allowedResultHosts",
    "timeoutMs",
    "settleMs",
    "maxResults",
  ]);

  const taskId = requiredString(object.taskId, "taskId", 128);
  if (!identifierPattern.test(taskId)) {
    invalid("taskId must be 8-128 safe lowercase ASCII characters");
  }
  const sourceId = requiredString(object.sourceId, "sourceId", 64);
  if (!sourceIdentifierPattern.test(sourceId)) {
    invalid("sourceId is invalid");
  }
  const sourceName = requiredString(object.sourceName, "sourceName", 120);
  const endpoint = parseEndpoint(requiredString(object.endpoint, "endpoint", 2_048));
  const queryParameter = requiredString(object.queryParameter, "queryParameter", 64);
  if (!sourceIdentifierPattern.test(queryParameter)) {
    invalid("queryParameter is invalid");
  }
  const query = normalizeQuery(requiredString(object.query, "query", 500));
  const selectorsObject = strictObject(object.selectors, ["result", "title", "link"]);
  const selectors: BrowserSelectors = {
    result: selector(selectorsObject.result, "selectors.result"),
    title: selector(selectorsObject.title, "selectors.title"),
    link: selector(selectorsObject.link, "selectors.link"),
  };
  const endpointHost = canonicalHost(endpoint.hostname);
  const allowedResourceHosts = uniqueHosts(object.allowedResourceHosts, "allowedResourceHosts", endpointHost);
  const allowedResultHosts = uniqueHosts(object.allowedResultHosts, "allowedResultHosts", endpointHost);

  return Object.freeze({
    taskId,
    sourceId,
    sourceName,
    endpoint: endpoint.toString(),
    queryParameter,
    query,
    selectors: Object.freeze(selectors),
    allowedResourceHosts: Object.freeze(allowedResourceHosts),
    allowedResultHosts: Object.freeze(allowedResultHosts),
    timeoutMs: boundedInteger(object.timeoutMs, "timeoutMs", MIN_TIMEOUT_MS, MAX_TIMEOUT_MS),
    settleMs: boundedInteger(object.settleMs, "settleMs", 0, MAX_SETTLE_MS),
    maxResults: boundedInteger(object.maxResults, "maxResults", 1, MAX_RESULTS),
  });
}

export function normalizeQuery(value: string): string {
  const normalized = value.trim().replace(/\s+/gu, " ");
  if (!normalized) {
    invalid("query is required");
  }
  if ([...normalized].length > MAX_QUERY_RUNES) {
    invalid(`query must contain at most ${MAX_QUERY_RUNES} Unicode code points`);
  }
  return normalized;
}

function strictObject(value: unknown, allowedKeys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    invalid("task must be a JSON object");
  }
  const object = value as Record<string, unknown>;
  const allowed = new Set(allowedKeys);
  for (const key of Object.keys(object)) {
    if (!allowed.has(key)) {
      invalid(`unknown field: ${key}`);
    }
  }
  for (const key of allowedKeys) {
    if (!(key in object)) {
      invalid(`missing field: ${key}`);
    }
  }
  return object;
}

function requiredString(value: unknown, field: string, maximum: number): string {
  if (typeof value !== "string") {
    invalid(`${field} must be a string`);
  }
  const normalized = value.trim();
  if (!normalized || normalized.length > maximum || normalized.includes("\u0000")) {
    invalid(`${field} is invalid`);
  }
  return normalized;
}

function selector(value: unknown, field: string): string {
  const result = requiredString(value, field, 300);
  if (/\r|\n/u.test(result)) {
    invalid(`${field} must be one line`);
  }
  return result;
}

function parseEndpoint(value: string): URL {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    invalid("endpoint must be an absolute URL");
  }
  if ((parsed.protocol !== "http:" && parsed.protocol !== "https:") || parsed.username || parsed.password) {
    invalid("endpoint must be credential-free HTTP or HTTPS");
  }
  if (parsed.search || parsed.hash) {
    invalid("endpoint must not contain a query or fragment");
  }
  return parsed;
}

function uniqueHosts(value: unknown, field: string, requiredHost: string): string[] {
  if (!Array.isArray(value) || value.length > MAX_ALLOWED_HOSTS) {
    invalid(`${field} must be an array with at most ${MAX_ALLOWED_HOSTS} hosts`);
  }
  const hosts = new Set<string>([requiredHost]);
  for (const entry of value) {
    const host = canonicalHost(requiredString(entry, field, 253));
    if (!hostPattern.test(host) && !isIPLiteral(host)) {
      invalid(`${field} contains an invalid host`);
    }
    hosts.add(host);
  }
  if (hosts.size > MAX_ALLOWED_HOSTS) {
    invalid(`${field} contains too many unique hosts`);
  }
  return [...hosts];
}

function boundedInteger(value: unknown, field: string, minimum: number, maximum: number): number {
  if (!Number.isInteger(value) || (value as number) < minimum || (value as number) > maximum) {
    invalid(`${field} must be an integer between ${minimum} and ${maximum}`);
  }
  return value as number;
}

function canonicalHost(value: string): string {
  return value.trim().toLowerCase().replace(/\.$/u, "").replace(/^\[|\]$/gu, "");
}

function isIPLiteral(value: string): boolean {
  return /^(?:\d{1,3}\.){3}\d{1,3}$/u.test(value) || value.includes(":");
}

function invalid(message: string): never {
  throw new BrowserWorkerError("invalid_task", message);
}
