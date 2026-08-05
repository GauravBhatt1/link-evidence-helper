import { expect, test } from "@playwright/test";

import { parseBrowserSearchTask } from "./contracts.js";
import {
  isUnsafeAddress,
  normalizeResultURL,
  prepareNetworkPolicy,
} from "./network-policy.js";

const task = parseBrowserSearchTask({
  taskId: "task-0001",
  sourceId: "source-one",
  sourceName: "Source One",
  endpoint: "https://search.example/path",
  queryParameter: "q",
  query: "Example Film",
  selectors: { result: ".result", title: ".title", link: "a" },
  allowedResourceHosts: ["cdn.example"],
  allowedResultHosts: ["delivery.example"],
  timeoutMs: 5_000,
  settleMs: 0,
  maxResults: 20,
});

test("rejects private, loopback, documentation, and mapped private addresses", () => {
  for (const address of [
    "127.0.0.1",
    "10.0.0.1",
    "169.254.169.254",
    "192.0.2.10",
    "::1",
    "fc00::1",
    "fe80::1",
    "2001:db8::1",
    "::ffff:127.0.0.1",
  ]) {
    expect(isUnsafeAddress(address), address).toBe(true);
  }
  expect(isUnsafeAddress("8.8.8.8")).toBe(false);
  expect(isUnsafeAddress("2606:4700:4700::1111")).toBe(false);
});

test("pins every approved resource hostname and rejects unsafe DNS answers", async () => {
  const safe = await prepareNetworkPolicy(task, false, async (hostname) => [{
    address: hostname === "search.example" ? "203.1.1.10" : "203.1.1.11",
    family: 4,
  }]);
  const rules = safe.chromiumArguments.find((argument) => argument.startsWith("--host-resolver-rules="));
  expect(rules).toContain("MAP search.example 203.1.1.10");
  expect(rules).toContain("MAP cdn.example 203.1.1.11");

  await expect(prepareNetworkPolicy(task, false, async () => [{ address: "127.0.0.1", family: 4 }]))
    .rejects.toMatchObject({ code: "unsafe_endpoint" });
});

test("normalizes only explicitly approved result URLs", async () => {
  const policy = await prepareNetworkPolicy(task, false, async () => [{ address: "203.1.1.10", family: 4 }]);
  expect(normalizeResultURL("https://delivery.example/file#fragment", new URL(task.endpoint), policy))
    .toBe("https://delivery.example/file");
  expect(() => normalizeResultURL("https://blocked.example/file", new URL(task.endpoint), policy))
    .toThrow(/blocked by policy/u);
});
