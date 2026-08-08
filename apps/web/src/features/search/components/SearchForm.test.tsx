import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { createAppQueryClient, SEARCH_QUERY_GC_TIME_MS } from "../../../app/query-client";
import { fixtureAliasKey, normalizeSearchQuery, validateSearchQuery } from "../model/search-query";
import { SearchForm } from "./SearchForm";

describe("SearchForm and query configuration", () => {
  it("normalizes only whitespace and case for explicit fixture aliases", () => {
    expect(normalizeSearchQuery("  Example   Film  ")).toBe("Example Film");
    expect(fixtureAliasKey("  EXAMPLE   film ")).toBe("example film");
    expect(fixtureAliasKey("Example Films")).toBe("example films");
    expect(fixtureAliasKey("Film")).toBe("film");
    expect(validateSearchQuery(" ")).toEqual({ ok: false, error: "Enter a title to search." });
    expect(validateSearchQuery("x".repeat(121)).ok).toBe(false);
  });

  it("uses finite, non-retrying, non-refetching, memory-only query defaults", () => {
    const options = createAppQueryClient().getDefaultOptions().queries;
    expect(options).toMatchObject({
      retry: false,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      staleTime: 0,
      gcTime: SEARCH_QUERY_GC_TIME_MS,
    });
    expect(SEARCH_QUERY_GC_TIME_MS).toBe(5 * 60 * 1000);
  });

  it("has a visible label/hint and does not search on mount or typing", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async () => true);
    render(<SearchForm busy={false} externalError="" onSubmit={onSubmit} />);
    const input = screen.getByRole("searchbox", { name: "Movie or TV title" });
    expect(input).toHaveAccessibleDescription(/configured sources/i);
    await user.type(input, "Example Film");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("uses the same native form path for Enter and the button", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async () => true);
    render(<SearchForm busy={false} externalError="" onSubmit={onSubmit} />);
    const input = screen.getByRole("searchbox");
    await user.type(input, "  Example   Film {Enter}");
    expect(onSubmit).toHaveBeenLastCalledWith("Example Film");
    await user.click(screen.getByRole("button", { name: "Search" }));
    expect(onSubmit).toHaveBeenCalledTimes(2);
  });

  it("shows local validation, keeps input editable, and blocks duplicate busy submission", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async () => true);
    const { rerender } = render(<SearchForm busy={false} externalError="" onSubmit={onSubmit} />);
    await user.type(screen.getByRole("searchbox"), "   ");
    fireEvent.submit(screen.getByRole("form", { name: "Live release search" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a title to search.");
    await user.clear(screen.getByRole("searchbox"));
    await user.type(screen.getByRole("searchbox"), "Example Film");
    rerender(<SearchForm busy externalError="The development fixture search could not be completed." onSubmit={onSubmit} />);
    const input = screen.getByRole("searchbox");
    const button = screen.getByRole("button", { name: "Search in progress…" });
    expect(input).toBeEnabled();
    expect(button).toBeDisabled();
    fireEvent.submit(screen.getByRole("form", { name: "Live release search" }));
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
