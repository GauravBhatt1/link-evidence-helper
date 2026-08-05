import { expect, test } from "@playwright/test";

import { parseBrowserSearchTask } from "./contracts.js";
import {
  assertAllowedResourceURL,
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

test("pins approved resource hosts and validates result-host DNS too", async () => {
  const safe = await prepareNetworkPolicy(task, false, async (hostname) => [{
    address: hostname === "search.example" ? "8.8.8.8" : "1.1.1.1",
    family: 4,
  }]);
  const rules = safe.chromiumArguments.find((argument) => argument.startsWith("--host-resolver-rules="));
  expect(rules).toContain("MAP search.example 8.8.8.8");
  expect(rules).toContain("MAP cdn.example 1.1.1.1");
  expect(rules).not.toContain("MAP delivery.example");

  await expect(prepareNetworkPolicy(task, false, async (hostname) => [{
    address: hostname === "delivery.example" ? "127.0.0.1" : "8.8.8.8",
    family: 4,
  }])).rejects.toMatchObject({ code: "unsafe_endpoint" });
});

test("normalizes only explicitly approved result URLs", async () => {
  const policy = await prepareNetworkPolicy(task, false, async () => [{ address: "8.8.8.8", family: 4 }]);
  expect(normalizeResultURL("https://delivery.example/file#fragment", new URL(task.endpoint), policy))
    .toBe("https://delivery.example/file");
  expect(() => normalizeResultURL("https://blocked.example/file", new URL(task.endpoint), policy))
    .toThrow(/blocked by policy/u);
  expect(() => normalizeResultURL("https://search.example:8443/internal", new URL(task.endpoint), policy))
    .toThrow(/blocked by policy/u);
});

test("resource routing permits the exact endpoint origin but blocks alternate ports", async () => {
  const policy = await prepareNetworkPolicy(task, false, async () => [{ address: "8.8.8.8", family: 4 }]);
  expect(assertAllowedResourceURL("https://search.example/asset.js", policy).origin)
    .toBe("https://search.example");
  expect(() => assertAllowedResourceURL("https://search.example:8443/private", policy))
    .toThrow(/resource host policy/u);
});
