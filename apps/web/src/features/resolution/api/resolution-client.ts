import {
  errorResponseSchema,
  jobSchema,
  resolutionRequestSchema,
  resolutionResultSchema,
  type ErrorResponse,
  type Job,
  type ResolutionRequest,
  type ResolutionResult,
} from "../../../types/contracts";

export const TERMINAL_JOB_STATES = new Set<Job["state"]>([
  "verified",
  "partial",
  "blocked",
  "failed",
  "cancelled",
]);

export class ResolutionClientError extends Error {
  readonly code: string;
  readonly status: number | null;

  constructor(code: string, message: string, status: number | null = null, options?: ErrorOptions) {
    super(message, options);
    this.name = "ResolutionClientError";
    this.code = code;
    this.status = status;
  }
}

type FetchLike = typeof fetch;

export class ResolutionClient {
  readonly #fetch: FetchLike;

  constructor(fetchImplementation: FetchLike = globalThis.fetch.bind(globalThis)) {
    this.#fetch = fetchImplementation;
  }

  async create(
    request: ResolutionRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<Job> {
    const parsedRequest = resolutionRequestSchema.parse(request);
    return this.#requestJob("/api/v1/jobs/resolution", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify(parsedRequest),
      signal,
    });
  }

  async get(jobId: string, signal?: AbortSignal): Promise<Job> {
    return this.#requestJob(`/api/v1/jobs/${encodeURIComponent(validJobId(jobId))}`, {
      method: "GET",
      signal,
    });
  }

  async unsubscribe(jobId: string, idempotencyKey: string, signal?: AbortSignal): Promise<Job> {
    return this.#requestJob(`/api/v1/jobs/${encodeURIComponent(validJobId(jobId))}`, {
      method: "DELETE",
      headers: { "Idempotency-Key": idempotencyKey },
      signal,
    });
  }

  parseResult(job: Job): ResolutionResult | null {
    if (job.result === null) return null;
    const parsed = resolutionResultSchema.safeParse(job.result);
    if (!parsed.success) {
      throw new ResolutionClientError(
        "invalid_resolution_result",
        "The server returned an invalid resolution result.",
      );
    }
    return parsed.data;
  }

  async #requestJob(path: string, init: RequestInit): Promise<Job> {
    let response: Response;
    try {
      response = await this.#fetch(path, {
        ...init,
        cache: "no-store",
        credentials: "same-origin",
        redirect: "error",
        headers: {
          Accept: "application/json",
          ...init.headers,
        },
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      throw new ResolutionClientError(
        "network_error",
        "The link service could not be reached.",
        null,
        { cause: error },
      );
    }

    const payload = await readJSON(response);
    if (!response.ok) {
      const parsedError = errorResponseSchema.safeParse(payload);
      const safe: ErrorResponse | null = parsedError.success ? parsedError.data : null;
      throw new ResolutionClientError(
        safe?.code ?? "request_failed",
        safe?.error ?? "The link request could not be completed.",
        response.status,
      );
    }
    const parsed = jobSchema.safeParse(payload);
    if (!parsed.success) {
      throw new ResolutionClientError(
        "invalid_job_response",
        "The server returned an invalid job response.",
        response.status,
      );
    }
    return parsed.data;
  }
}

export function createResolutionIdempotencyKey(): string {
  const randomUUID = globalThis.crypto?.randomUUID?.bind(globalThis.crypto);
  if (!randomUUID) {
    throw new ResolutionClientError(
      "secure_random_unavailable",
      "This browser cannot create a secure link request identifier.",
    );
  }
  return `resolution-${randomUUID()}`;
}

export function resolutionStatusMessage(job: Job | null): string {
  switch (job?.state) {
    case "queued":
      return "Link request queued.";
    case "checking-cache":
      return "Checking for an existing result.";
    case "checking-preferred-source":
      return "Checking the preferred source.";
    case "checking-backup-source":
      return "Checking backup sources.";
    case "browser-fallback":
      return "Checking a JavaScript-required source.";
    case "verified":
      return "Verified delivery links are ready.";
    case "partial":
      return "Some verified delivery links are ready.";
    case "blocked":
      return "All available sources were blocked by verification policy.";
    case "failed":
      return "No verified delivery link was found.";
    case "cancelled":
      return "Link request cancelled.";
    default:
      return "";
  }
}

async function readJSON(response: Response): Promise<unknown> {
  const contentType = response.headers.get("Content-Type")?.toLowerCase() ?? "";
  if (!contentType.includes("application/json")) {
    throw new ResolutionClientError(
      "invalid_content_type",
      "The link service returned an unsupported response.",
      response.status,
    );
  }
  try {
    return await response.json() as unknown;
  } catch (error) {
    throw new ResolutionClientError(
      "invalid_json",
      "The link service returned malformed data.",
      response.status,
      { cause: error },
    );
  }
}

function validJobId(value: string): string {
  const normalized = value.trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u.test(normalized)) {
    throw new ResolutionClientError("invalid_job_id", "The link request identifier is invalid.");
  }
  return normalized;
}

export const resolutionClient = new ResolutionClient();
