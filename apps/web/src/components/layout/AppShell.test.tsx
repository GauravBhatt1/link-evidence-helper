import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { notFoundMetadata, routeMetadata } from "../../app/route-metadata";
import { renderRoute } from "../../test/render";

describe("AppShell", () => {
  it.each([...routeMetadata, notFoundMetadata])("renders one heading for $path", async (metadata) => {
    const path = metadata.path === "*" ? "/not-a-real-route" : metadata.path;
    renderRoute(path);

    expect(await screen.findByRole("heading", { level: 1, name: metadata.heading })).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(document.title).toBe(metadata.documentTitle);
    if (metadata.path === "/") {
      expect(screen.getByText("Development fixture search — no live sources are contacted.")).toBeInTheDocument();
    } else {
      expect(screen.getByText("No application data or backend connection is active here.")).toBeInTheDocument();
    }
  });

  it("exposes stable landmarks and does not steal focus on initial load", async () => {
    renderRoute("/");
    const main = await screen.findByRole("main");

    expect(main).toHaveAttribute("id", "main-content");
    expect(main).toHaveAttribute("tabindex", "-1");
    expect(document.activeElement).not.toBe(screen.getByRole("heading", { level: 1 }));
    expect(screen.getByRole("navigation", { name: "Primary navigation" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Mobile navigation" })).toBeInTheDocument();
  });

  it("focuses the route heading after client-side navigation", async () => {
    const user = userEvent.setup();
    renderRoute("/");

    await user.click(within(screen.getByRole("navigation", { name: "Primary navigation" })).getByRole("link", { name: "Movies" }));

    const heading = await screen.findByRole("heading", { level: 1, name: "Movies" });
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    expect(heading).toHaveFocus();
    expect(document.title).toBe("Movies · FREEMIUM INDEX");
  });

  it("lets the skip link focus main content", async () => {
    const user = userEvent.setup();
    renderRoute("/");

    const skipLink = screen.getByRole("link", { name: "Skip to main content" });
    await user.click(skipLink);
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

    expect(screen.getByRole("main")).toHaveFocus();
  });
});
