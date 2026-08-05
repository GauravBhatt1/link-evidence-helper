import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { createAppQueryClient } from "../../../app/query-client";
import { FixtureLibraryTransport, LibraryTransportError, type LibraryTransport } from "../api/library-transport";
import { LibraryPage } from "./LibraryPage";

function renderLibrary(view: "movies" | "tv" | "missing" | "recent", transport: LibraryTransport = new FixtureLibraryTransport()) {
  return render(
    <QueryClientProvider client={createAppQueryClient()}>
      <LibraryPage view={view} transport={transport} mode="fixture" />
    </QueryClientProvider>,
  );
}

describe("LibraryPage", () => {
  it("renders canonical movie cards with a persistent fixture disclosure and no remote images", async () => {
    const { container } = renderLibrary("movies");
    expect(screen.getByText("Development library fixtures")).toBeVisible();
    expect(screen.getByText("No live Jellyfin server or production library is contacted.")).toBeVisible();
    expect(await screen.findByRole("heading", { level: 2, name: "Archive Zero" })).toBeVisible();
    expect(screen.getByRole("heading", { level: 2, name: "Horizon Gate" })).toBeVisible();
    expect(screen.getByRole("heading", { level: 2, name: "Paper City" })).toBeVisible();
    expect(screen.queryByText("Signal House — S01E02")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Library summary")).toHaveTextContent("3 movies");
    expect(screen.getByText("Jellyfin not configured")).toBeVisible();
    expect(container.querySelector("img")).toBeNull();
    expect(container.outerHTML).not.toMatch(/serverId|itemId|tmdbId|contentId/i);
  });

  it("shows only missing and partial items in the Missing view", async () => {
    renderLibrary("missing");
    expect(await screen.findByRole("heading", { level: 2, name: "Paper City" })).toBeVisible();
    expect(screen.getByRole("heading", { level: 2, name: "Signal House" })).toBeVisible();
    expect(screen.getByRole("heading", { level: 2, name: "Signal House — S01E02" })).toBeVisible();
    expect(screen.queryByRole("heading", { level: 2, name: "Horizon Gate" })).not.toBeInTheDocument();
  });

  it("presents a safe retry state without leaking transport details", async () => {
    const user = userEvent.setup();
    const list = vi.fn(async () => {
      throw new LibraryTransportError("unavailable");
    });
    renderLibrary("tv", { list });
    expect(await screen.findByRole("heading", { level: 2, name: "Library unavailable" })).toBeVisible();
    expect(screen.getByText("The library could not be loaded safely.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /retry/i }));
    expect(list).toHaveBeenCalledTimes(2);
    expect(document.body.textContent).not.toMatch(/stack|endpoint|fetch|exception/i);
  });
});
