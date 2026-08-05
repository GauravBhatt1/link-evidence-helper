import { chromium, type Browser, type BrowserType, type Page } from "@playwright/test";

import {
  BrowserWorkerError,
  type BrowserSearchOutput,
  type BrowserSearchResult,
  type BrowserSearchTask,
} from "./contracts.js";
import {
  assertAllowedResourceURL,
  buildSearchURL,
  normalizeResultURL,
  prepareNetworkPolicy,
  type DNSResolver,
} from "./network-policy.js";

type BrowserLauncher = Pick<BrowserType<Browser>, "launch">;

export type BrowserExecutorOptions = {
  readonly allowPrivate?: boolean;
  readonly chromiumSandbox?: boolean;
  readonly resolver?: DNSResolver;
  readonly launcher?: BrowserLauncher;
};

export class BrowserSearchExecutor {
  readonly #allowPrivate: boolean;
  readonly #chromiumSandbox: boolean;
  readonly #resolver?: DNSResolver;
  readonly #launcher: BrowserLauncher;

  constructor(options: BrowserExecutorOptions = {}) {
    this.#allowPrivate = options.allowPrivate ?? false;
    this.#chromiumSandbox = options.chromiumSandbox ?? true;
    this.#resolver = options.resolver;
    this.#launcher = options.launcher ?? chromium;
  }

  async execute(task: BrowserSearchTask, signal?: AbortSignal): Promise<BrowserSearchOutput> {
    if (signal?.aborted) {
      throw new BrowserWorkerError("cancelled", "Browser task was cancelled.");
    }
    const policy = await prepareNetworkPolicy(task, this.#allowPrivate, this.#resolver);
    const searchURL = buildSearchURL(task);
    assertAllowedResourceURL(searchURL.toString(), policy);

    let browser: Browser | undefined;
    let abortHandler: (() => void) | undefined;
    try {
      browser = await this.#launcher.launch({
        headless: true,
        args: [...policy.chromiumArguments],
        chromiumSandbox: this.#chromiumSandbox,
      });
      if (signal) {
        abortHandler = () => {
          void browser?.close().catch(() => undefined);
        };
        signal.addEventListener("abort", abortHandler, { once: true });
      }
      const context = await browser.newContext({
        acceptDownloads: false,
        bypassCSP: false,
        colorScheme: "light",
        extraHTTPHeaders: { Accept: "text/html,application/xhtml+xml" },
        ignoreHTTPSErrors: false,
        javaScriptEnabled: true,
        locale: "en-US",
        serviceWorkers: "block",
        userAgent: "link-evidence-helper-browser-worker/next",
        viewport: { width: 1280, height: 720 },
      });
      context.setDefaultTimeout(task.timeoutMs);
      context.setDefaultNavigationTimeout(task.timeoutMs);
      const page = await context.newPage();
      await installRequestPolicy(page, policy);
      page.on("download", (download) => {
        void download.cancel().catch(() => undefined);
      });
      page.on("popup", (popup) => {
        void popup.close().catch(() => undefined);
      });

      try {
        await page.goto(searchURL.toString(), { waitUntil: "domcontentloaded", timeout: task.timeoutMs });
        if (task.settleMs > 0) {
          await page.waitForTimeout(task.settleMs);
        }
        const output = await extractResults(page, task, searchURL, policy);
        await context.close();
        return output;
      } catch (error) {
        await context.close().catch(() => undefined);
        if (signal?.aborted) {
          throw new BrowserWorkerError("cancelled", "Browser task was cancelled.", { cause: error });
        }
        if (error instanceof BrowserWorkerError) {
          throw error;
        }
        throw new BrowserWorkerError("browser_failed", "The isolated browser task could not be completed.", {
          cause: error,
        });
      }
    } catch (error) {
      if (signal?.aborted) {
        throw new BrowserWorkerError("cancelled", "Browser task was cancelled.", { cause: error });
      }
      if (error instanceof BrowserWorkerError) {
        throw error;
      }
      throw new BrowserWorkerError("browser_failed", "The isolated browser process could not be started.", {
        cause: error,
      });
    } finally {
      if (signal && abortHandler) {
        signal.removeEventListener("abort", abortHandler);
      }
      await browser?.close().catch(() => undefined);
    }
  }
}

async function installRequestPolicy(
  page: Page,
  policy: Awaited<ReturnType<typeof prepareNetworkPolicy>>,
): Promise<void> {
  await page.route("**/*", async (route) => {
    const request = route.request();
    try {
      assertAllowedResourceURL(request.url(), policy);
    } catch {
      await route.abort("blockedbyclient");
      return;
    }
    if (["image", "media", "font"].includes(request.resourceType())) {
      await route.abort("blockedbyclient");
      return;
    }
    await route.continue();
  });
}

async function extractResults(
  page: Page,
  task: BrowserSearchTask,
  searchURL: URL,
  policy: Awaited<ReturnType<typeof prepareNetworkPolicy>>,
): Promise<BrowserSearchOutput> {
  let count: number;
  try {
    count = await page.locator(task.selectors.result).count();
  } catch (error) {
    throw new BrowserWorkerError("selector_failed", "The configured result selector could not be evaluated.", {
      cause: error,
    });
  }

  const results: BrowserSearchResult[] = [];
  const seen = new Set<string>();
  let droppedResults = Math.max(0, count - task.maxResults);
  const maximum = Math.min(count, task.maxResults);
  for (let index = 0; index < maximum; index += 1) {
    const container = page.locator(task.selectors.result).nth(index);
    try {
      const rawTitle = await container.locator(task.selectors.title).first().textContent();
      const rawURL = await container.locator(task.selectors.link).first().getAttribute("href");
      const title = rawTitle?.trim().replace(/\s+/gu, " ") ?? "";
      if (!title || [...title].length > 300 || !rawURL) {
        droppedResults += 1;
        continue;
      }
      const url = normalizeResultURL(rawURL, searchURL, policy);
      if (seen.has(url)) {
        droppedResults += 1;
        continue;
      }
      seen.add(url);
      results.push(Object.freeze({
        sourceId: task.sourceId,
        sourceName: task.sourceName,
        title,
        url,
      }));
    } catch (error) {
      if (error instanceof BrowserWorkerError && error.code === "unsafe_endpoint") {
        droppedResults += 1;
        continue;
      }
      droppedResults += 1;
    }
  }

  return Object.freeze({
    ok: true,
    taskId: task.taskId,
    sourceId: task.sourceId,
    results: Object.freeze(results),
    droppedResults,
  });
}
