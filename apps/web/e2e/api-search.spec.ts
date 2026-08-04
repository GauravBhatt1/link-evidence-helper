import { expect, test } from "@playwright/test";
import { searchResponseSchema } from "../src/types/contracts";

const runtime = globalThis as typeof globalThis & {
  process?: { env?: Record<string, string | undefined> };
};
const apiMode = runtime.process?.env?.VITE_SEARCH_TRANSPORT === "api";

test.describe("development Go API search integration", () => {
  test.skip(!apiMode, "runs only in the explicit Go API integration job");

  test("uses the same-origin Go API without contacting live or external sources", async ({ page }) => {
    const requests: string[] = [];
    const failedRequests: string[] = [];
    const consoleErrors: string[] = [];
    page.on("request", (request) => requests.push(request.url()));
    page.on("requestfailed", (request) => {
      failedRequests.push(`${request.url()}: ${request.failure()?.errorText ?? "unknown failure"}`);
    });
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("pageerror", (error) => consoleErrors.push(error.message));

    await page.goto("/");
    await expect(page.getByText("Development Go API search — sanitized fixtures only; no live sources are contacted.")).toBeVisible();

    const transportProbe = await page.evaluate(async () => {
      const modulePath = "/src/features/search/api/search-transport-config.ts";
      const module = await import(modulePath);
      const configuration = module.defaultSearchTransportConfiguration;
      const response = await configuration.transport.search(
        { query: "Transport Probe" },
        new AbortController().signal,
      );
      return {
        mode: configuration.mode,
        transportName: configuration.transport.constructor.name,
        response,
      };
    });
    expect(transportProbe.mode).toBe("api");
    expect(transportProbe.transportName).toBe("ApiSearchTransport");
    const probeParsed = searchResponseSchema.safeParse(transportProbe.response);
    expect(probeParsed.success, probeParsed.success ? "" : probeParsed.error.message).toBe(true);
    if (!probeParsed.success) throw probeParsed.error;
    expect(probeParsed.data.query).toBe("Transport Probe");
    expect(probeParsed.data.contents).toEqual([]);

    const input = page.getByRole("searchbox", { name: "Movie or TV title" });
    await input.fill("Example Film");
    await page.getByRole("button", { name: "Search" }).click();
    await expect.poll(
      () => requests
        .filter((url) => new URL(url).pathname === "/api/v1/search")
        .map((url) => new URL(url).searchParams.get("q")),
      {
        message: `UI submission did not reach the API; failed requests: ${failedRequests.join(" | ")}`,
        timeout: 5_000,
      },
    ).toContain("Example Film");
    await expect(page.getByText("1 unified content item")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/Example Film 2024/)).toBeVisible();

    await input.fill("Unknown API title");
    await page.getByRole("button", { name: "Search" }).click();
    await expect.poll(
      () => requests
        .filter((url) => new URL(url).pathname === "/api/v1/search")
        .map((url) => new URL(url).searchParams.get("q")),
      { timeout: 5_000 },
    ).toContain("Unknown API title");
    await expect(page.getByRole("heading", { name: "No development fixture matches this search." })).toBeVisible();

    const apiRequests = requests.filter((url) => new URL(url).pathname === "/api/v1/search");
    expect(apiRequests.map((url) => new URL(url).searchParams.get("q"))).toEqual([
      "Transport Probe",
      "Example Film",
      "Unknown API title",
    ]);
    for (const url of requests) {
      expect(new URL(url).origin).toBe("http://127.0.0.1:5173");
    }
    expect(failedRequests).toEqual([]);
    expect(consoleErrors).toEqual([]);
  });
});
