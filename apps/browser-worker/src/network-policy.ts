import { lookup } from "node:dns/promises";
import { isIP } from "node:net";

import { BrowserWorkerError, type BrowserSearchTask } from "./contracts.js";

export type ResolvedAddress = { readonly address: string; readonly family: 4 | 6 };
export type DNSResolver = (hostname: string) => Promise<readonly ResolvedAddress[]>;

export type PreparedNetworkPolicy = {
  readonly resourceHosts: ReadonlySet<string>;
  readonly resultHosts: ReadonlySet<string>;
  readonly chromiumArguments: readonly string[];
};

const defaultResolver: DNSResolver = async (hostname) => {
  const addresses = await lookup(hostname, { all: true, verbatim: true });
  return addresses.map(({ address, family }) => ({ address, family: family as 4 | 6 }));
};

export async function prepareNetworkPolicy(
  task: BrowserSearchTask,
  allowPrivate: boolean,
  resolver: DNSResolver = defaultResolver,
): Promise<PreparedNetworkPolicy> {
  const resourceHosts = new Set(task.allowedResourceHosts.map(canonicalHost));
  const resultHosts = new Set(task.allowedResultHosts.map(canonicalHost));
  const mappings: string[] = [];

  for (const host of resourceHosts) {
    const literalFamily = isIP(host);
    const addresses = literalFamily
      ? [{ address: host, family: literalFamily as 4 | 6 }]
      : await resolveHost(host, resolver);
    if (!allowPrivate && addresses.some(({ address }) => isUnsafeAddress(address))) {
      throw new BrowserWorkerError("unsafe_endpoint", "A browser resource host resolved to a blocked network address.");
    }
    if (!literalFamily) {
      mappings.push(`MAP ${host} ${formatResolverAddress(addresses[0]!.address)}`);
    }
  }

  return Object.freeze({
    resourceHosts,
    resultHosts,
    chromiumArguments: Object.freeze([
      "--disable-background-networking",
      "--disable-component-update",
      "--disable-default-apps",
      "--disable-domain-reliability",
      "--disable-features=MediaRouter,OptimizationHints,Translate",
      "--disable-sync",
      "--metrics-recording-only",
      "--no-first-run",
      `--host-resolver-rules=${[...mappings, "EXCLUDE localhost"].join(",")}`,
    ]),
  });
}

export function buildSearchURL(task: BrowserSearchTask): URL {
  const target = new URL(task.endpoint);
  target.searchParams.set(task.queryParameter, task.query);
  return target;
}

export function assertAllowedResourceURL(rawURL: string, policy: PreparedNetworkPolicy): URL {
  return assertAllowedURL(rawURL, policy.resourceHosts, "A browser request was blocked by the resource host policy.");
}

export function normalizeResultURL(rawURL: string, baseURL: URL, policy: PreparedNetworkPolicy): string {
  let resolved: URL;
  try {
    resolved = new URL(rawURL, baseURL);
  } catch {
    throw new BrowserWorkerError("selector_failed", "A browser result contained an invalid URL.");
  }
  assertAllowedURL(resolved.toString(), policy.resultHosts, "A browser result URL was blocked by policy.");
  resolved.hash = "";
  return resolved.toString();
}

export function isUnsafeAddress(address: string): boolean {
  const family = isIP(address);
  if (family === 4) {
    const octets = address.split(".").map(Number);
    if (octets.length !== 4 || octets.some((value) => !Number.isInteger(value) || value < 0 || value > 255)) {
      return true;
    }
    const [a, b] = octets as [number, number, number, number];
    return (
      a === 0 ||
      a === 10 ||
      a === 127 ||
      (a === 100 && b >= 64 && b <= 127) ||
      (a === 169 && b === 254) ||
      (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 0) ||
      (a === 192 && b === 168) ||
      (a === 192 && b === 0 && octets[2] === 2) ||
      (a === 198 && (b === 18 || b === 19)) ||
      (a === 198 && b === 51 && octets[2] === 100) ||
      (a === 203 && b === 0 && octets[2] === 113) ||
      a >= 224
    );
  }
  if (family === 6) {
    const normalized = address.toLowerCase().replace(/^\[|\]$/gu, "");
    if (normalized === "::" || normalized === "::1") return true;
    if (normalized.startsWith("fc") || normalized.startsWith("fd") || normalized.startsWith("ff")) return true;
    const first = Number.parseInt(normalized.split(":", 1)[0] || "0", 16);
    if ((first & 0xffc0) === 0xfe80) return true;
    if (normalized.startsWith("2001:db8:")) return true;
    const mapped = normalized.match(/::ffff:(\d+\.\d+\.\d+\.\d+)$/u)?.[1];
    return mapped ? isUnsafeAddress(mapped) : false;
  }
  return true;
}

function assertAllowedURL(rawURL: string, allowedHosts: ReadonlySet<string>, message: string): URL {
  let parsed: URL;
  try {
    parsed = new URL(rawURL);
  } catch {
    throw new BrowserWorkerError("unsafe_endpoint", message);
  }
  if (
    (parsed.protocol !== "http:" && parsed.protocol !== "https:") ||
    parsed.username ||
    parsed.password ||
    !allowedHosts.has(canonicalHost(parsed.hostname))
  ) {
    throw new BrowserWorkerError("unsafe_endpoint", message);
  }
  return parsed;
}

async function resolveHost(host: string, resolver: DNSResolver): Promise<readonly ResolvedAddress[]> {
  let addresses: readonly ResolvedAddress[];
  try {
    addresses = await resolver(host);
  } catch (error) {
    throw new BrowserWorkerError("unsafe_endpoint", "A browser resource host could not be resolved safely.", {
      cause: error,
    });
  }
  if (!addresses.length || addresses.some(({ address, family }) => isIP(address) !== family)) {
    throw new BrowserWorkerError("unsafe_endpoint", "A browser resource host returned invalid DNS data.");
  }
  return addresses;
}

function canonicalHost(value: string): string {
  return value.trim().toLowerCase().replace(/\.$/u, "").replace(/^\[|\]$/gu, "");
}

function formatResolverAddress(address: string): string {
  return isIP(address) === 6 ? `[${address}]` : address;
}
