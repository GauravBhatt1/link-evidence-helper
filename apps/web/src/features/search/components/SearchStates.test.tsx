import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SearchEmptyState } from "./SearchEmptyState";
import { SearchErrorState } from "./SearchErrorState";
import { SearchLoadingSkeleton } from "./SearchLoadingSkeleton";
import { SearchPartialNotice } from "./SearchPartialNotice";

describe("safe search states", () => {
  it("renders loading geometry without fake media data", () => {
    const { container } = render(<SearchLoadingSkeleton />);
    expect(container.firstChild).toHaveAttribute("aria-hidden", "true");
    expect(container).not.toHaveTextContent(/movie|show|source|delivery/i);
  });

  it("renders a truthful empty state", () => {
    render(<SearchEmptyState query="Unknown title" />);
    expect(screen.getByRole("heading", { name: "No live source matches this search." })).toBeVisible();
    expect(screen.getByText(/Unknown title/)).toBeVisible();
  });

  it("keeps partial failure internals out of the notice", () => {
    const { container } = render(<SearchPartialNotice failureCount={2} />);
    expect(container).toHaveTextContent("Results may be incomplete");
    expect(container).not.toHaveTextContent(/source-two|timed out|adapter|url/i);
  });

  it("uses only the safe error and exposes a real Retry button", () => {
    const retry = vi.fn();
    render(<SearchErrorState message="The development fixture search could not be completed." onRetry={retry} />);
    screen.getByRole("button", { name: "Retry" }).click();
    expect(retry).toHaveBeenCalledOnce();
    expect(screen.queryByText(/Zod|stack|fixture path/i)).not.toBeInTheDocument();
  });
});
