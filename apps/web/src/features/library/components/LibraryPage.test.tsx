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
    expect(screen.getByText("Offline library preview")).toBeVisible();
    expect(screen.getByText("Connect the API transport to load production library data.")).toBeVisible();
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

  it("renders production library posters when the API provides poster artwork", async () => {
    const { container } = renderLibrary("movies", {
      async list() {
        return {
          ok: true,
          success: true,
          code: "ok",
          view: "movies",
          generatedAt: "2026-08-08T00:00:00Z",
          summary: { total: 1, movies: 1, tv: 0, missing: 0 },
          jellyfin: { configured: true, mode: "connected", lastSyncedAt: "2026-08-08T00:00:00Z" },
          items: [{
            itemId: "movie-1",
            contentId: "library_movie-1",
            tmdbId: "1163258",
            title: "12th Fail",
            year: 2023,
            mediaType: "movie",
            season: null,
            episode: null,
            poster: "/api/tmdb-image?path=w342/poster.jpg",
            libraryState: "available",
            missing: false,
            dateAdded: "2026-08-08T00:00:00Z",
            updatedAt: "2026-08-08T00:00:00Z",
            jellyfin: {
              configured: true,
              present: true,
              itemId: "movie-1",
              serverId: null,
              lastSyncedAt: "2026-08-08T00:00:00Z",
            },
          }],
        };
      },
    });

    expect(await screen.findByRole("heading", { level: 2, name: "12th Fail" })).toBeVisible();
    const poster = container.querySelector("img.library-poster");
    expect(poster).toHaveAttribute("src", "/api/tmdb-image?path=w342/poster.jpg");
    expect(container.querySelector(".library-poster-fallback")).toBeNull();
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
