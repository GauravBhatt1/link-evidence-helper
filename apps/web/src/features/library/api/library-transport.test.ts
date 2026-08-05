import { describe, expect, it, vi } from "vitest";
import libraryFixture from "../../../../../../packages/testing/fixtures/library-response.json";
import {
  ApiLibraryTransport,
  createLibraryTransportConfiguration,
  FixtureLibraryTransport,
  LibraryTransportError,
} from "./library-transport";

function response(value: unknown, status = 200, contentType = "application/json") {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": contentType },
  });
}

describe("library transports", () => {
  it("filters and deterministically sorts the shared sanitized fixture", async () => {
    const transport = new FixtureLibraryTransport();
    const movies = await transport.list("movies", new AbortController().signal);
    const tv = await transport.list("tv", new AbortController().signal);
    const missing = await transport.list("missing", new AbortController().signal);
    const recent = await transport.list("recent", new AbortController().signal);

    expect(movies.items.map((item) => item.title)).toEqual(["Archive Zero", "Horizon Gate", "Paper City"]);
    expect(tv.items).toHaveLength(3);
    expect(missing.items).toHaveLength(3);
    expect(recent.items[0]?.title).toBe("Horizon Gate");
    expect(recent.summary).toEqual({ total: 6, movies: 3, tv: 3, missing: 3 });
    expect(recent.jellyfin).toEqual({ configured: false, mode: "disabled", lastSyncedAt: null });
  });

  it("uses a same-origin no-store API request and validates the requested view", async () => {
    const fetchMock = vi.fn(async () => response({ ...libraryFixture, view: "movies", items: libraryFixture.items.filter((item) => item.mediaType === "movie") }));
    const transport = new ApiLibraryTransport("/api/v1/library", fetchMock as typeof fetch);
    const result = await transport.list("movies", new AbortController().signal);

    expect(result.view).toBe("movies");
    expect(fetchMock).toHaveBeenCalledOnce();
    const [target, init] = fetchMock.mock.calls[0]!;
    expect(String(target)).toBe(`${window.location.origin}/api/v1/library?view=movies`);
    expect(init).toMatchObject({ method: "GET", cache: "no-store", credentials: "same-origin", redirect: "error" });
  });

  it("rejects external endpoints, bad content types, and mismatched contracts", async () => {
    await expect(new ApiLibraryTransport("https://external.example/library").list("movies", new AbortController().signal))
      .rejects.toMatchObject({ code: "rejected" });
    const html = new ApiLibraryTransport("/api/v1/library", vi.fn(async () => response({}, 200, "text/html")) as typeof fetch);
    await expect(html.list("movies", new AbortController().signal)).rejects.toBeInstanceOf(LibraryTransportError);
    const mismatch = new ApiLibraryTransport("/api/v1/library", vi.fn(async () => response(libraryFixture)) as typeof fetch);
    await expect(mismatch.list("movies", new AbortController().signal)).rejects.toMatchObject({ code: "invalid-contract" });
  });

  it("keeps fixture mode safe by default and follows explicit API configuration", () => {
    expect(createLibraryTransportConfiguration().mode).toBe("fixture");
    expect(createLibraryTransportConfiguration("api").mode).toBe("api");
    expect(createLibraryTransportConfiguration(undefined, "api").mode).toBe("api");
  });
});
