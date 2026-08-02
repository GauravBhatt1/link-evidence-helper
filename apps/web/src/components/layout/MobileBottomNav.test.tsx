import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { routeMetadata } from "../../app/route-metadata";
import { renderRoute } from "../../test/render";

describe("MobileBottomNav", () => {
  it("renders primary mobile routes plus the More disclosure trigger", () => {
    renderRoute("/");
    const navigation = screen.getByRole("navigation", { name: "Mobile navigation" });

    expect(within(navigation).getAllByRole("link")).toHaveLength(routeMetadata.filter((route) => route.mobilePrimary).length);
    expect(within(navigation).getByRole("button", { name: "More" })).toHaveAttribute("aria-expanded", "false");
    expect(within(navigation).getByRole("link", { name: "Search" })).toHaveAttribute("aria-current", "page");
  });
});
