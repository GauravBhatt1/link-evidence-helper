import { expect, test } from "@playwright/test";

const runtime = globalThis as typeof globalThis & {
  process?: { env?: Record<string, string | undefined> };
};
const apiMode = runtime.process?.env?.VITE_SEARCH_TRANSPORT === "api";

test.describe("development Go API search integration", () => {
  test.skip(!apiMode, "runs only in the explicit Go API integration job");

  test("uses the same-origin Go API without contacting live or external sources", async ({ page }) => {
    const requests: string[] = [];
    page.on("request", (request) => requests.push(request.url()));

    await page.goto("/");
    await expect(page.getByText("Development Go API search — sanitized fixtures only; no live sources are contacted.")).toBeVisible();

    const input = page.getByRole("searchbox", { name: "Movie or TV title" });
    await input.fill("Example Film");
    await page.getByRole("button", { name: "Search" }).click();
    await expect(page.getByText("1 unified content item")).toBeVisible();
    await expect(page.getByText(/Example Film 2024/)).toBeVisible();

    await input.fill("Unknown API title");
    await page.getByRole("button", { name: "Search" }).click();
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
  });
});
