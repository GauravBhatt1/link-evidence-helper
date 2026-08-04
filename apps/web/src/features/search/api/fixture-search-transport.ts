import { searchResponseSchema } from "../../../types/contracts";
import { fixtureAliasKey } from "../model/search-query";
import {
  emptyFixtureResponse,
  FIXTURE_ALIAS_MAP,
  fixtureResponseForScenario,
} from "./search-fixture-catalog";
import type { SearchRequest, SearchTransport } from "./search-transport";
import { SearchTransportError } from "./search-transport-error";

export const FIXTURE_LATENCY_MS = 180;

function abortError() {
  return new DOMException("The fixture search was aborted.", "AbortError");
}

export function deterministicFixtureDelay(signal: AbortSignal, latencyMs = FIXTURE_LATENCY_MS) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError());
      return;
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, latencyMs);
    const onAbort = () => {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      reject(abortError());
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export class FixtureSearchTransport implements SearchTransport {
  readonly latencyMs: number;

  constructor(latencyMs = FIXTURE_LATENCY_MS) {
    this.latencyMs = latencyMs;
  }

  async search(request: SearchRequest, signal: AbortSignal) {
    await deterministicFixtureDelay(signal, this.latencyMs);
    const scenario = FIXTURE_ALIAS_MAP[fixtureAliasKey(request.query)];
    if (scenario === "error") throw new SearchTransportError("fixture-error");
    const raw = scenario
      ? fixtureResponseForScenario(scenario, request.query)
      : emptyFixtureResponse(request.query);
    const parsed = searchResponseSchema.safeParse(raw);
    if (!parsed.success) throw new SearchTransportError("invalid-contract");
    return parsed.data;
  }
}

export const fixtureSearchTransport = new FixtureSearchTransport();
