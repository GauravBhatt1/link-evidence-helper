import { describe, expect, it, vi } from "vitest";
import { fixtureResponseForScenario } from "./search-fixture-catalog";
import { ApiSearchTransport } from "./api-search-transport";
import { SearchTransportError } from "./search-transport-error";

describe("ApiSearchTransport", () => {
  it("uses one same-origin GET request and validates the canonical response", async () => {
    const fixture = fixtureResponseForScenario("movie-several", "Example Film");
    const fetchImpl = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(fixture), {
      status: 200,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    }));
    const transport = new ApiSearchTransport({ fetchImpl });
    const controller = new AbortController();
    const response = await transport.search({ query: "Example Film" }, controller.signal);

    expect(response).toEqual(fixture);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0]!;
    const requestURL = new URL(String(url));
    expect(requestURL.origin).toBe(window.location.origin);
    expect(requestURL.pathname).toBe("/api/v1/search");
    expect(requestURL.searchParams.get("q")).toBe("Example Film");
    expect([...requestURL.searchParams.keys()]).toEqual(["q"]);
    expect(init).toMatchObject({
      method: "GET",
      cache: "no-store",
      credentials: "same-origin",
      redirect: "error",
      signal: controller.signal,
    });
  });

  it("rejects external endpoints before any request is made", async () => {
    const fetchImpl = vi.fn<typeof fetch>();
    const transport = new ApiSearchTransport({ endpoint: "https://example.com/api/search", fetchImpl });
    await expect(transport.search({ query: "Example Film" }, new AbortController().signal))
      .rejects.toMatchObject({ code: "api-rejected" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("maps HTTP and network failures to safe transport errors", async () => {
    const unavailable = new ApiSearchTransport({
      fetchImpl: vi.fn<typeof fetch>(async () => new Response("internal secret", { status: 503 })),
    });
    await expect(unavailable.search({ query: "Example Film" }, new AbortController().signal))
      .rejects.toEqual(new SearchTransportError("api-unavailable"));

    const rejected = new ApiSearchTransport({
      fetchImpl: vi.fn<typeof fetch>(async () => new Response("bad request", { status: 400 })),
    });
    await expect(rejected.search({ query: "Example Film" }, new AbortController().signal))
      .rejects.toEqual(new SearchTransportError("api-rejected"));

    const network = new ApiSearchTransport({
      fetchImpl: vi.fn<typeof fetch>(async () => { throw new Error("connection includes secret"); }),
    });
    await expect(network.search({ query: "Example Film" }, new AbortController().signal))
      .rejects.toEqual(new SearchTransportError("api-unavailable"));
  });

  it("rejects non-JSON and invalid contract payloads", async () => {
    const html = new ApiSearchTransport({
      fetchImpl: vi.fn<typeof fetch>(async () => new Response("<html></html>", {
        status: 200,
        headers: { "Content-Type": "text/html" },
      })),
    });
    await expect(html.search({ query: "Example Film" }, new AbortController().signal))
      .rejects.toEqual(new SearchTransportError("invalid-contract"));

    const invalid = new ApiSearchTransport({
      fetchImpl: vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })),
    });
    await expect(invalid.search({ query: "Example Film" }, new AbortController().signal))
      .rejects.toEqual(new SearchTransportError("invalid-contract"));
  });

  it("preserves cancellation instead of translating it into an API error", async () => {
    const controller = new AbortController();
    controller.abort();
    const transport = new ApiSearchTransport({
      fetchImpl: vi.fn<typeof fetch>(async (_input, init) => {
        if (init?.signal?.aborted) throw new DOMException("Aborted", "AbortError");
        throw new Error("expected an aborted signal");
      }),
    });
    await expect(transport.search({ query: "Example Film" }, controller.signal))
      .rejects.toMatchObject({ name: "AbortError" });
  });
});
