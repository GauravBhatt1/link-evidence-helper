import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { routeMetadata } from "../../app/route-metadata";
import { renderRoute } from "../../test/render";

describe("DesktopSidebar", () => {
  it("renders every metadata route as an ordinary link", () => {
    renderRoute("/library/movies");
    const navigation = screen.getByRole("navigation", { name: "Primary navigation" });
    const links = within(navigation).getAllByRole("link");

    expect(links).toHaveLength(routeMetadata.length);
    expect(within(navigation).getByRole("link", { name: "Movies" })).toHaveAttribute("aria-current", "page");
    expect(within(navigation).queryByRole("button")).not.toBeInTheDocument();
  });
});
