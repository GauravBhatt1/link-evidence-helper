import { afterEach, describe, expect, it, vi } from "vitest";
import { FixtureSearchTransport } from "./fixture-search-transport";

afterEach(() => vi.useRealTimers());

async function runSearch(query: string, latency = 0) {
  return new FixtureSearchTransport(latency).search({ query }, new AbortController().signal);
}

describe("FixtureSearchTransport", () => {
  it("matches only documented normalized aliases", async () => {
    const exact = await runSearch("  eXaMpLe   FiLm ");
    const substring = await runSearch("Example");
    const fuzzy = await runSearch("Example Films");
    expect(exact.contents).toHaveLength(1);
    expect(substring.contents).toEqual([]);
    expect(fuzzy.contents).toEqual([]);
  });

  it("returns the canonical empty response for unknown queries", async () => {
    await expect(runSearch("Unknown title")).resolves.toMatchObject({
      ok: true,
      success: true,
      code: "ok",
      query: "Unknown title",
      contents: [],
      partialFailures: [],
    });
  });

  it("uses deterministic abortable latency", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const transport = new FixtureSearchTransport(180);
    const result = transport.search({ query: "Example Film" }, controller.signal);
    let settled = false;
    void result.then(() => { settled = true; }, () => { settled = true; });
    await vi.advanceTimersByTimeAsync(179);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await expect(result).resolves.toMatchObject({ query: "Example Film" });

    const abortedController = new AbortController();
    const aborted = transport.search({ query: "Example Film" }, abortedController.signal);
    abortedController.abort();
    await expect(aborted).rejects.toMatchObject({ name: "AbortError" });
  });

  it("returns only the safe fixture error", async () => {
    await expect(runSearch("Fixture Error")).rejects.toMatchObject({
      name: "SearchTransportError",
      message: "The development fixture search could not be completed.",
    });
  });
});
