import { expect, test } from "@playwright/test";
import { libraryResponseSchema } from "../src/types/contracts";

const runtime = globalThis as typeof globalThis & {
  process?: { env?: Record<string, string | undefined> };
};
const apiMode = runtime.process?.env?.VITE_SEARCH_TRANSPORT === "api" || runtime.process?.env?.VITE_LIBRARY_TRANSPORT === "api";

test.describe("development Go API library integration", () => {
  test.skip(!apiMode, "runs only in the explicit Go API integration job");

  test("loads Movies and Missing through the same-origin library API", async ({ page }) => {
    const requests: string[] = [];
    const failedRequests: string[] = [];
    const consoleErrors: string[] = [];
    page.on("request", (request) => requests.push(request.url()));
    page.on("requestfailed", (request) => failedRequests.push(`${request.url()}: ${request.failure()?.errorText ?? "unknown failure"}`));
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("pageerror", (error) => consoleErrors.push(error.message));

    await page.goto("/library/movies");
    await expect(page.getByText("Library API")).toBeVisible();
    await expect(page.getByText("Data is loaded from the same-origin Go API.")).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "Archive Zero" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "Horizon Gate" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "Paper City" })).toBeVisible();
    await expect(page.getByText("Jellyfin not configured")).toBeVisible();

    const probe = await page.evaluate(async () => {
      const modulePath = "/src/features/library/api/library-transport.ts";
      const module = await import(modulePath);
      const configuration = module.defaultLibraryTransportConfiguration;
      const response = await configuration.transport.list("recent", new AbortController().signal);
      return {
        mode: configuration.mode,
        transportName: configuration.transport.constructor.name,
        response,
      };
    });
    expect(probe.mode).toBe("api");
    expect(probe.transportName).toBe("ApiLibraryTransport");
    const parsed = libraryResponseSchema.safeParse(probe.response);
    expect(parsed.success, parsed.success ? "" : parsed.error.message).toBe(true);
    if (!parsed.success) throw parsed.error;
    expect(parsed.data.view).toBe("recent");
    expect(parsed.data.items).toHaveLength(6);

    await page.goto("/library/missing");
    await expect(page.getByRole("heading", { level: 2, name: "Paper City" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "Signal House" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "Signal House — S01E02" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "Horizon Gate" })).toHaveCount(0);

    const libraryRequests = requests.filter((url) => new URL(url).pathname === "/api/v1/library");
    expect(libraryRequests.map((url) => new URL(url).searchParams.get("view"))).toEqual([
      "movies",
      "recent",
      "missing",
    ]);
    for (const url of requests) {
      expect(new URL(url).origin).toBe("http://127.0.0.1:5173");
    }
    expect(await page.locator("img").count()).toBe(0);
    expect(await page.locator("body").innerHTML()).not.toMatch(/serverId|contentId|tmdbId/i);
    expect(failedRequests).toEqual([]);
    expect(consoleErrors).toEqual([]);
  });
});
