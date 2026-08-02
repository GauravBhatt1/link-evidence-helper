import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderRoute } from "../test/render";

describe("route focus", () => {
  it("does not focus the heading during initial navigation", () => {
    renderRoute("/");
    expect(screen.getByRole("heading", { level: 1, name: "Search" })).not.toHaveFocus();
  });

  it("updates title and focuses the heading after a shell link navigation", async () => {
    const user = userEvent.setup();
    renderRoute("/");
    const navigation = screen.getByRole("navigation", { name: "Primary navigation" });

    await user.click(within(navigation).getByRole("link", { name: "TV Shows" }));
    const heading = await screen.findByRole("heading", { level: 1, name: "TV Shows" });
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

    expect(heading).toHaveFocus();
    expect(document.title).toBe("TV Shows · FREEMIUM INDEX");
  });
});
