import { expect, test } from "@playwright/test";

import { BrowserWorkerError, parseBrowserSearchTask } from "./contracts.js";

const validTask = {
  taskId: "task-0001",
  sourceId: "source-one",
  sourceName: "Source One",
  endpoint: "https://search.example/path",
  queryParameter: "q",
  query: "  Example   Film  ",
  selectors: { result: ".result", title: ".title", link: "a" },
  allowedResourceHosts: ["cdn.example"],
  allowedResultHosts: ["delivery.example"],
  timeoutMs: 5_000,
  settleMs: 100,
  maxResults: 20,
};

test("normalizes a strict browser task and includes the endpoint host", () => {
  const task = parseBrowserSearchTask(validTask);
  expect(task.query).toBe("Example Film");
  expect(task.allowedResourceHosts).toEqual(["search.example", "cdn.example"]);
  expect(task.allowedResultHosts).toEqual(["search.example", "delivery.example"]);
  expect(Object.isFrozen(task)).toBe(true);
});

test("rejects unknown fields, credentials, endpoint queries, and excessive limits", () => {
  for (const candidate of [
    { ...validTask, unexpected: true },
    { ...validTask, endpoint: "https://user:pass@search.example/path" },
    { ...validTask, endpoint: "https://search.example/path?existing=1" },
    { ...validTask, maxResults: 101 },
    { ...validTask, timeoutMs: 999 },
  ]) {
    expect(() => parseBrowserSearchTask(candidate)).toThrow(BrowserWorkerError);
  }
});

test("rejects malformed IP-like hosts instead of treating them as DNS names", () => {
  expect(() => parseBrowserSearchTask({
    ...validTask,
    allowedResourceHosts: ["999.999.999.999"],
  })).toThrow(/invalid host/u);
});
