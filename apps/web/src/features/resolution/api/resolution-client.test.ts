import { describe, expect, it, vi } from "vitest";

import type { Job } from "../../../types/contracts";
import {
  ResolutionClient,
  ResolutionClientError,
  TERMINAL_JOB_STATES,
  resolutionStatusMessage,
} from "./resolution-client";

const jobId = "job_0123456789abcdef0123456789abcdef";
const now = "2026-08-05T00:00:00Z";

type FetchCall = readonly [input: RequestInfo | URL, init?: RequestInit];

function job(overrides: Partial<Job> = {}): Job {
  return {
    jobId,
    kind: "resolution",
    state: "queued",
    subscriberCount: 1,
    createdAt: now,
    updatedAt: now,
    result: null,
    ...overrides,
  };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function trackedFetch(handler: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) {
  const calls: FetchCall[] = [];
  const implementation: typeof fetch = async (input, init) => {
    calls.push([input, init]);
    return handler(input, init);
  };
  return { calls, fetch: vi.fn(implementation) as typeof fetch };
}

describe("ResolutionClient", () => {
  it("creates a strict same-origin resolution job", async () => {
    const mock = trackedFetch(async () => jsonResponse(job(), 202));
    const client = new ResolutionClient(mock.fetch);
    const result = await client.create(
      { contentId: "content_12345678", variantId: "variant_12345678", quality: "1080p" },
      "resolution-request-0001",
    );

    expect(result.jobId).toBe(jobId);
    expect(mock.calls).toHaveLength(1);
    const [requestPath, init] = mock.calls[0]!;
    expect(requestPath).toBe("/api/v1/jobs/resolution");
    expect(init).toMatchObject({
      method: "POST",
      cache: "no-store",
      credentials: "same-origin",
      redirect: "error",
    });
    expect(init?.headers).toMatchObject({
      Accept: "application/json",
      "Content-Type": "application/json",
      "Idempotency-Key": "resolution-request-0001",
    });
    expect(JSON.parse(String(init?.body))).toEqual({
      contentId: "content_12345678",
      variantId: "variant_12345678",
      quality: "1080p",
    });
  });

  it("gets and unsubscribes using encoded validated job identifiers", async () => {
    const mock = trackedFetch(async () => jsonResponse(job()));
    const client = new ResolutionClient(mock.fetch);
    await client.get(jobId);
    await client.unsubscribe(jobId, "resolution-request-0001");
    expect(mock.calls[0]?.[0]).toBe(`/api/v1/jobs/${jobId}`);
    expect(mock.calls[1]?.[1]).toMatchObject({
      method: "DELETE",
      headers: expect.objectContaining({ "Idempotency-Key": "resolution-request-0001" }),
    });
    await expect(client.get("../../secret")).rejects.toMatchObject({ code: "invalid_job_id" });
  });

  it("validates terminal resolution results", () => {
    const client = new ResolutionClient(vi.fn() as unknown as typeof fetch);
    const verified = job({
      state: "verified",
      result: {
        ok: true,
        success: true,
        code: "ok",
        status: "verified",
        contentId: "content_12345678",
        variantId: "variant_12345678",
        deliveryLinks: [{
          url: "https://delivery.example/file.mkv",
          filename: "file.mkv",
          size: "1 GB",
          quality: "1080p",
          sourceId: "source_12345678",
          verifiedAt: now,
        }],
        attempts: [{
          sourceId: "source_12345678",
          status: "verified",
          failureReason: null,
          durationMs: 10,
        }],
        message: "Verified delivery links are ready.",
      },
    });
    expect(client.parseResult(verified)?.deliveryLinks[0]?.filename).toBe("file.mkv");
    expect(() => client.parseResult(job({ state: "verified", result: { unsafe: true } })))
      .toThrow(ResolutionClientError);
  });

  it("uses safe server errors and rejects non-JSON responses", async () => {
    const safeClient = new ResolutionClient(trackedFetch(async () => jsonResponse({
      ok: false,
      error: "Select a quality before resolving links.",
      code: "quality_required",
    }, 400)).fetch);
    await expect(safeClient.get(jobId)).rejects.toMatchObject({
      code: "quality_required",
      status: 400,
      message: "Select a quality before resolving links.",
    });

    const htmlClient = new ResolutionClient(trackedFetch(async () => new Response("<html>secret</html>", {
      status: 502,
      headers: { "Content-Type": "text/html" },
    })).fetch);
    await expect(htmlClient.get(jobId)).rejects.toMatchObject({
      code: "invalid_content_type",
      status: 502,
    });
  });

  it("classifies terminal states and public status text", () => {
    expect(TERMINAL_JOB_STATES).toEqual(new Set(["verified", "partial", "blocked", "failed", "cancelled"]));
    expect(resolutionStatusMessage(job({ state: "checking-preferred-source" }))).toMatch(/preferred source/i);
    expect(resolutionStatusMessage(job({ state: "verified" }))).toMatch(/ready/i);
    expect(resolutionStatusMessage(null)).toBe("");
  });
});
