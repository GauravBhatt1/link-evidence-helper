import { expect, test, type Page } from "@playwright/test";

const fixtureNotice = "Development fixture search — no live sources are contacted.";

function observeIsolation(page: Page) {
  const unexpectedRequests: string[] = [];
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const consoleMessages: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    const localAsset = url.origin === "http://127.0.0.1:5173" && !url.pathname.startsWith("/api");
    if (!localAsset) unexpectedRequests.push(request.url());
  });
  page.on("console", (message) => {
    consoleMessages.push(message.text());
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  return () => {
    expect(unexpectedRequests, "unexpected API or external requests").toEqual([]);
    expect(consoleErrors, "browser console errors").toEqual([]);
    expect(pageErrors, "uncaught page errors").toEqual([]);
    expect(consoleMessages.join("\n")).not.toMatch(/Source One|Source Two|source_205|cookie|authorization|selector/i);
  };
}

async function submit(page: Page, alias: string) {
  const input = page.getByRole("searchbox", { name: "Movie or TV title" });
  await input.fill(alias);
  await page.getByRole("button", { name: /search/i }).click();
}

test("runs the multi-quality fixture workflow with persistent disclosure", async ({ page }) => {
  const assertIsolated = observeIsolation(page);
  await page.goto("/");
  await expect(page.getByText(fixtureNotice)).toBeVisible();
  await submit(page, "Multi Quality");
  await expect(page.getByText(fixtureNotice)).toBeVisible();
  await expect(page.getByText("1 unified content item")).toBeVisible();
  await expect(page.getByText(fixtureNotice)).toBeVisible();

  await page.getByRole("button", { name: /choose releases for/i }).click();
  const findLinks = page.getByRole("button", { name: "Find Links" });
  await expect(findLinks).toBeDisabled();
  await expect(page.getByText("Select a release to continue.")).toBeVisible();
  await page.getByRole("radio", { name: /Hindi/ }).check();
  await expect(page.getByRole("group", { name: "Select one quality" })).toBeVisible();
  await expect(findLinks).toBeDisabled();
  await page.getByRole("radio", { name: "1080p", exact: true }).check();
  await expect(findLinks).toBeEnabled();
  await findLinks.click();
  await expect(page.getByText("Selection is ready. Start the app in API mode to resolve links.")).toBeVisible();
  await expect(page.getByText(fixtureNotice)).toBeVisible();
  await expect(page.getByText(/Delivery Links|Checking source|Download|Copy/i)).toHaveCount(0);
  expect(await page.locator("img").count()).toBe(0);
  assertIsolated();
});

test("uses exact aliases, safe states, one active card, and no source internals", async ({ page }) => {
  const assertIsolated = observeIsolation(page);
  await page.goto("/");
  await submit(page, "Example");
  await expect(page.getByRole("heading", { name: "No development fixture matches this search." })).toBeVisible();
  await expect(page.getByText(fixtureNotice)).toBeVisible();

  await submit(page, "Partial Search");
  await expect(page.getByText(/Results may be incomplete/)).toBeVisible();
  await expect(page.getByText(fixtureNotice)).toBeVisible();

  await submit(page, "Fixture Collection");
  await expect(page.getByText("2 unified content items")).toBeVisible();
  const disclosures = page.getByRole("button", { name: /choose releases for/i });
  await disclosures.nth(0).click();
  await expect(page.getByRole("group", { name: "Select one release" })).toHaveCount(1);
  await page.getByRole("button", { name: /choose releases for example show/i }).click();
  await expect(page.getByRole("group", { name: "Select one release" })).toHaveCount(1);

  await submit(page, "Multiple Sources");
  await expect(page.getByText("1 unified content item")).toBeVisible();
  await page.getByRole("button", { name: /choose releases for/i }).click();
  await expect(page.getByText("2 sources")).toBeVisible();
  const serializedDom = await page.locator("#root").evaluate((element) => element.outerHTML);
  expect(serializedDom).not.toMatch(/Source One|Source Two|source_205|verificationState|priority=|cookie|authorization|selector/i);
  for (const attribute of ["aria-label", "title", "data-source", "data-source-id"]) {
    const values = await page.locator(`[${attribute}]`).evaluateAll((elements, name) => elements.map((element) => element.getAttribute(String(name))), attribute);
    expect(values.join("\n")).not.toMatch(/Source One|Source Two|source_205|verificationState|cookie|authorization|selector/i);
  }

  await submit(page, "Fixture Error");
  await expect(page.getByRole("heading", { name: "Development search unavailable" })).toBeVisible();
  await expect(page.getByText(fixtureNotice)).toBeVisible();
  assertIsolated();
});

test("supports keyboard radio selection and never overflows", async ({ page }) => {
  const assertIsolated = observeIsolation(page);
  await page.goto("/");
  await submit(page, "Multi Quality");
  await expect(page.getByText("1 unified content item")).toBeVisible();
  await page.getByRole("button", { name: /choose releases for/i }).focus();
  await page.keyboard.press("Enter");
  const release = page.getByRole("radio", { name: /Hindi/ });
  await release.focus();
  await page.keyboard.press("Space");
  await expect(release).toBeChecked();
  const firstQuality = page.getByRole("radio", { name: "480p", exact: true });
  await firstQuality.focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("radio", { name: "720p", exact: true })).toBeChecked();

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  assertIsolated();
});
