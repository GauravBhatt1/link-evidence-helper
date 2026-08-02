import { expect, test, type Page } from "@playwright/test";

const routes = [
  { path: "/", heading: "Search", title: "Search · FREEMIUM INDEX" },
  { path: "/library/movies", heading: "Movies", title: "Movies · FREEMIUM INDEX" },
  { path: "/library/tv", heading: "TV Shows", title: "TV Shows · FREEMIUM INDEX" },
  { path: "/library/missing", heading: "Missing", title: "Missing · FREEMIUM INDEX" },
  { path: "/library/recent", heading: "Recently Added", title: "Recently Added · FREEMIUM INDEX" },
  { path: "/admin", heading: "Admin", title: "Admin · FREEMIUM INDEX" },
] as const;

function observeFailures(page: Page) {
  const unexpectedRequests: string[] = [];
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    const isLocalAsset = url.origin === "http://127.0.0.1:5173" && !url.pathname.startsWith("/api");
    if (!isLocalAsset) unexpectedRequests.push(request.url());
  });
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  return () => {
    expect(unexpectedRequests, "unexpected API or external requests").toEqual([]);
    expect(consoleErrors, "browser console errors").toEqual([]);
    expect(pageErrors, "uncaught page errors").toEqual([]);
  };
}

test("uses the deterministic navigation mode with no horizontal overflow", async ({ page }, testInfo) => {
  const assertNoFailures = observeFailures(page);
  await page.goto("/");
  const usesMobileNavigation = testInfo.project.use.viewport!.width < 1024;

  if (usesMobileNavigation) {
    await expect(page.getByTestId("mobile-header")).toBeVisible();
    await expect(page.getByTestId("mobile-bottom-nav")).toBeVisible();
    await expect(page.getByTestId("desktop-sidebar")).toBeHidden();
  } else {
    await expect(page.getByTestId("mobile-header")).toBeHidden();
    await expect(page.getByTestId("mobile-bottom-nav")).toBeHidden();
    await expect(page.getByTestId("desktop-sidebar")).toBeVisible();
  }
  const hasOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(hasOverflow).toBe(false);
  assertNoFailures();
});

test("supports direct navigation, refresh, one h1, and local-only placeholders", async ({ page }) => {
  const assertNoFailures = observeFailures(page);
  for (const route of routes) {
    await page.goto(route.path);
    await page.reload();
    await expect(page).toHaveTitle(route.title);
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
    await expect(page.getByRole("heading", { level: 1, name: route.heading })).toBeVisible();
    await expect(page.getByText("No application data or backend connection is active here.")).toBeVisible();
  }
  assertNoFailures();
});

test("manages route focus without stealing initial focus", async ({ page }, testInfo) => {
  const assertNoFailures = observeFailures(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Search" })).not.toBeFocused();

  if (testInfo.project.use.viewport!.width < 1024) {
    await page.getByTestId("mobile-bottom-nav").getByRole("link", { name: "Movies" }).click();
  } else {
    await page.getByTestId("desktop-sidebar").getByRole("link", { name: "Movies" }).click();
  }
  await expect(page.getByRole("heading", { level: 1, name: "Movies" })).toBeFocused();
  await expect(page).toHaveTitle("Movies · FREEMIUM INDEX");
  assertNoFailures();
});

test("skip link focuses the stable main-content target", async ({ page }) => {
  const assertNoFailures = observeFailures(page);
  await page.goto("/");
  await page.keyboard.press("Tab");
  const skipLink = page.getByRole("link", { name: "Skip to main content" });
  await expect(skipLink).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();
  assertNoFailures();
});

test("mobile More disclosure dismisses accessibly and returns focus", async ({ page }, testInfo) => {
  test.skip(testInfo.project.use.viewport!.width >= 1024, "mobile/tablet navigation only");
  const assertNoFailures = observeFailures(page);
  await page.goto("/");
  const trigger = page.getByRole("button", { name: "More" });

  await trigger.click();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("navigation", { name: "More navigation" }).getByRole("link", { name: "Admin" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await expect(trigger).toBeFocused();

  await trigger.click();
  await page.locator(".route-placeholder").click({ position: { x: 8, y: 8 } });
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await expect(trigger).toBeFocused();
  assertNoFailures();
});
