import { createServer, type Server } from "node:http";
import { once } from "node:events";

import { expect, test } from "@playwright/test";

import { parseBrowserSearchTask } from "./contracts.js";
import { BrowserSearchExecutor } from "./executor.js";

let server: Server;
let endpoint = "";

test.beforeAll(async () => {
  server = createServer((request, response) => {
    const query = new URL(request.url ?? "/", "http://127.0.0.1").searchParams.get("q");
    response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    response.end(`<!doctype html>
      <html><body>
        <div class="result"><span class="title"> ${query} 1080p </span><a href="/delivery/one#x">one</a></div>
        <div class="result"><span class="title">Duplicate</span><a href="/delivery/one">duplicate</a></div>
        <div class="result"><span class="title">Blocked</span><a href="https://blocked.example/file">blocked</a></div>
      </body></html>`);
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("test server did not expose a TCP address");
  endpoint = `http://127.0.0.1:${address.port}/search`;
});

test.afterAll(async () => {
  server.close();
  await once(server, "close");
});

test("extracts allowed results, removes fragments, deduplicates, and drops blocked URLs", async () => {
  const task = parseBrowserSearchTask({
    taskId: "task-0001",
    sourceId: "source-one",
    sourceName: "Source One",
    endpoint,
    queryParameter: "q",
    query: "Example Film",
    selectors: { result: ".result", title: ".title", link: "a" },
    allowedResourceHosts: [],
    allowedResultHosts: [],
    timeoutMs: 5_000,
    settleMs: 0,
    maxResults: 10,
  });
  const output = await new BrowserSearchExecutor({ allowPrivate: true }).execute(task);
  expect(output.results).toEqual([{
    sourceId: "source-one",
    sourceName: "Source One",
    title: "Example Film 1080p",
    url: new URL("/delivery/one", endpoint).toString(),
  }]);
  expect(output.droppedResults).toBe(2);
});

test("honors cancellation before browser startup", async () => {
  const controller = new AbortController();
  controller.abort();
  const task = parseBrowserSearchTask({
    taskId: "task-0002",
    sourceId: "source-one",
    sourceName: "Source One",
    endpoint,
    queryParameter: "q",
    query: "Example Film",
    selectors: { result: ".result", title: ".title", link: "a" },
    allowedResourceHosts: [],
    allowedResultHosts: [],
    timeoutMs: 5_000,
    settleMs: 0,
    maxResults: 10,
  });
  await expect(new BrowserSearchExecutor({ allowPrivate: true }).execute(task, controller.signal))
    .rejects.toMatchObject({ code: "cancelled" });
});
