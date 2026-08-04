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
    const consoleErrors: string[] = [];
    page.on("request", (request) => requests.push(request.url()));
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("pageerror", (error) => consoleErrors.push(error.message));

    await page.goto("/");
    await expect(page.getByText("Development Go API search — sanitized fixtures only; no live sources are contacted.")).toBeVisible();

    const input = page.getByRole("searchbox", { name: "Movie or TV title" });
    await input.fill("Example Film");
    const firstResponsePromise = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === "/api/v1/search" && url.searchParams.get("q") === "Example Film";
    });
    await page.getByRole("button", { name: "Search" }).click();
    const firstResponse = await firstResponsePromise;
    expect(firstResponse.status()).toBe(200);
    expect(firstResponse.headers()["content-type"]).toContain("application/json");
    const firstPayload: unknown = await firstResponse.json();
    const firstParsed = searchResponseSchema.safeParse(firstPayload);
    expect(firstParsed.success, firstParsed.success ? "" : firstParsed.error.message).toBe(true);
    if (!firstParsed.success) throw firstParsed.error;
    expect(firstParsed.data).toMatchObject({
      ok: true,
      success: true,
      code: "ok",
      query: "Example Film",
    });
    expect(firstParsed.data.contents).toHaveLength(1);
    await expect(page.getByText("1 unified content item")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/Example Film 2024/)).toBeVisible();

    await input.fill("Unknown API title");
    const secondResponsePromise = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === "/api/v1/search" && url.searchParams.get("q") === "Unknown API title";
    });
    await page.getByRole("button", { name: "Search" }).click();
    const secondResponse = await secondResponsePromise;
    expect(secondResponse.status()).toBe(200);
    const secondPayload: unknown = await secondResponse.json();
    const secondParsed = searchResponseSchema.safeParse(secondPayload);
    expect(secondParsed.success, secondParsed.success ? "" : secondParsed.error.message).toBe(true);
    if (!secondParsed.success) throw secondParsed.error;
    expect(secondParsed.data.contents).toEqual([]);
    await expect(page.getByRole("heading", { name: "No development fixture matches this search." })).toBeVisible();

    const apiRequests = requests.filter((url) => new URL(url).pathname === "/api/v1/search");
    expect(apiRequests).toHaveLength(2);
    expect(apiRequests.map((url) => new URL(url).searchParams.get("q"))).toEqual([
      "Example Film",
      "Unknown API title",
    ]);
    for (const url of requests) {
      expect(new URL(url).origin).toBe("http://127.0.0.1:5173");
    }
    expect(consoleErrors).toEqual([]);
  });
});
