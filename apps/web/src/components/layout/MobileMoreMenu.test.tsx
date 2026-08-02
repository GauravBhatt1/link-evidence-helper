import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderRoute } from "../../test/render";

describe("MobileMoreMenu", () => {
  it("uses an accessible disclosure with ordinary navigation links", async () => {
    const user = userEvent.setup();
    renderRoute("/");
    const trigger = screen.getByRole("button", { name: "More" });

    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");

    const navigation = screen.getByRole("navigation", { name: "More navigation" });
    expect(navigation).toBeInTheDocument();
    expect(within(navigation).getByRole("link", { name: "Recently Added" })).toHaveAttribute("href", "/library/recent");
    expect(within(navigation).getByRole("link", { name: "Admin" })).toHaveAttribute("href", "/admin");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("dismisses on Escape and returns focus", async () => {
    const user = userEvent.setup();
    renderRoute("/");
    const trigger = screen.getByRole("button", { name: "More" });

    await user.click(trigger);
    await user.keyboard("{Escape}");
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

    expect(screen.queryByRole("navigation", { name: "More navigation" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("dismisses on an outside pointer action and returns focus", async () => {
    const user = userEvent.setup();
    renderRoute("/");
    const trigger = screen.getByRole("button", { name: "More" });

    await user.click(trigger);
    fireEvent.pointerDown(screen.getByRole("main"));
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

    expect(screen.queryByRole("navigation", { name: "More navigation" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
