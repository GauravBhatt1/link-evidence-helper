import { describe, expect, it } from "vitest";
import { apiSearchTransport } from "./api-search-transport";
import { fixtureSearchTransport } from "./fixture-search-transport";
import { createSearchTransportConfiguration } from "./search-transport-config";

describe("search transport configuration", () => {
  it("keeps fixture transport as the safe default", () => {
    expect(createSearchTransportConfiguration()).toEqual({
      mode: "fixture",
      transport: fixtureSearchTransport,
    });
    expect(createSearchTransportConfiguration("unknown")).toEqual({
      mode: "fixture",
      transport: fixtureSearchTransport,
    });
  });

  it("enables the Go API transport only when explicitly selected", () => {
    expect(createSearchTransportConfiguration("api")).toEqual({
      mode: "api",
      transport: apiSearchTransport,
    });
  });
});
