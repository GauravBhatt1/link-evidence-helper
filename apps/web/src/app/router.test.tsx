import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderRoute } from "../test/render";
import { notFoundMetadata, routeMetadata } from "./route-metadata";

describe("shell routes", () => {
  it("uses route metadata as the complete source for public shell paths", () => {
    expect(routeMetadata.map(({ path }) => path)).toEqual([
      "/",
      "/library/movies",
      "/library/tv",
      "/library/missing",
      "/library/recent",
      "/admin",
    ]);
    expect(new Set(routeMetadata.map(({ id }) => id).values()).size).toBe(routeMetadata.length);
  });

  it.each([...routeMetadata, notFoundMetadata])("derives title and heading for $path", async (metadata) => {
    renderRoute(metadata.path === "*" ? "/unknown" : metadata.path);
    expect(await screen.findByRole("heading", { level: 1, name: metadata.heading })).toBeInTheDocument();
    expect(document.title).toBe(metadata.documentTitle);
  });
});
