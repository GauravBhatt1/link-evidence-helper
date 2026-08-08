import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fixtureResponseForScenario } from "../api/search-fixture-catalog";
import { toContentCardViewModel } from "../model/search-view-model";
import { ContentCard } from "./ContentCard";

function renderCard(active = false) {
  const content = fixtureResponseForScenario("multiple-sources", "Multiple Sources").contents[0]!;
  const viewModel = toContentCardViewModel(content);
  const onToggle = vi.fn();
  const result = render(
    <ContentCard
      content={viewModel}
      active={active}
      selectedVariantId={null}
      selectedQuality=""
      helper="Select a release to continue."
      findEnabled={false}
      findBusy={false}
      intentNotice=""
      resolution={null}
      onToggle={onToggle}
      onSelectVariant={vi.fn()}
      onSelectQuality={vi.fn()}
      onFind={vi.fn()}
      onCancelResolution={vi.fn()}
    />,
  );
  return { ...result, onToggle, viewModel };
}

function renderCardWithPoster() {
  const content = fixtureResponseForScenario("multiple-sources", "Multiple Sources").contents[0]!;
  const viewModel = {
    ...toContentCardViewModel(content),
    poster: "/api/tmdb-image?path=w342/poster.jpg",
  };
  const result = render(
    <ContentCard
      content={viewModel}
      active={false}
      selectedVariantId={null}
      selectedQuality=""
      helper="Select a release to continue."
      findEnabled={false}
      findBusy={false}
      intentNotice=""
      resolution={null}
      onToggle={vi.fn()}
      onSelectVariant={vi.fn()}
      onSelectQuality={vi.fn()}
      onFind={vi.fn()}
      onCancelResolution={vi.fn()}
    />,
  );
  return { ...result, viewModel };
}

describe("ContentCard", () => {
  it("renders one approved identification summary with poster and availability status", () => {
    const { container, viewModel } = renderCardWithPoster();
    expect(screen.getAllByRole("heading", { level: 2, name: viewModel.title })).toHaveLength(1);
    expect(screen.getByText(viewModel.libraryStatusLabel)).toBeVisible();
    const poster = container.querySelector("img.content-poster");
    expect(poster).toHaveAttribute("src", viewModel.poster);
    expect(container.querySelector(".poster-fallback")).toBeNull();
  });

  it("uses a local decorative fallback only when a poster is unavailable", () => {
    const { container } = renderCard();
    expect(container.querySelector("img.content-poster")).toBeNull();
    expect(container.querySelector(".poster-fallback")).toHaveAttribute("aria-hidden", "true");
  });

  it("uses disclosure semantics without making poster or metadata interactive", async () => {
    const user = userEvent.setup();
    const { onToggle, viewModel } = renderCard();
    const disclosure = screen.getByRole("button", { name: `Choose releases for ${viewModel.title}` });
    expect(disclosure).toHaveAttribute("aria-expanded", "false");
    expect(screen.getAllByRole("button")).toHaveLength(1);
    await user.click(disclosure);
    expect(onToggle).toHaveBeenCalledOnce();
  });

  it("drops all source candidate data before rendering text or attributes", () => {
    const { container, viewModel } = renderCard(true);
    expect(viewModel.totalSources).toBe(2);
    expect(viewModel.variants[0]?.sourceCount).toBe(2);
    const serialized = container.outerHTML;
    for (const forbidden of ["Source One", "Source Two", "sourceId", "displayName", "priority", "verificationState"]) {
      expect(serialized).not.toContain(forbidden);
      expect(JSON.stringify(viewModel)).not.toContain(forbidden);
    }
    expect(screen.getByText("2 sources")).toBeVisible();
  });
});
