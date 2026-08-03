import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { createAppQueryClient } from "../app/query-client";
import { FixtureSearchTransport } from "../features/search/api/fixture-search-transport";
import { fixtureResponseForScenario } from "../features/search/api/search-fixture-catalog";
import type { SearchRequest, SearchTransport } from "../features/search/api/search-transport";
import type { SearchResponse } from "../types/contracts";
import { SearchPage } from "./SearchPage";

function renderSearch(transport: SearchTransport = new FixtureSearchTransport(0)) {
  return render(
    <QueryClientProvider client={createAppQueryClient()}>
      <SearchPage transport={transport} />
    </QueryClientProvider>,
  );
}

async function submitAlias(user: ReturnType<typeof userEvent.setup>, alias: string) {
  const input = screen.getByRole("searchbox", { name: "Movie or TV title" });
  await user.clear(input);
  await user.type(input, alias);
  await user.click(screen.getByRole("button", { name: /search/i }));
}

describe("SearchPage", () => {
  it("keeps the fixture disclosure visible in idle, loading, result, partial, empty, error, selection, and local-intent states", async () => {
    const user = userEvent.setup();
    renderSearch(new FixtureSearchTransport(20));
    const notice = () => screen.getByText("Development fixture search — no live sources are contacted.");
    expect(notice()).toBeVisible();

    await submitAlias(user, "Example Film");
    expect(notice()).toBeVisible();
    expect(screen.getByText("Searching development fixtures…")).toBeVisible();
    await screen.findByText(/Example Film 2024/);
    expect(notice()).toBeVisible();
    await user.click(screen.getByRole("button", { name: /choose releases for example film/i }));
    expect(notice()).toBeVisible();
    await user.click(screen.getAllByRole("radio", { name: /Hindi/ })[0]!);
    await user.click(screen.getByRole("button", { name: "Find Links" }));
    expect(notice()).toBeVisible();
    expect(screen.getByText("Selection is ready. Link resolution is not connected in Milestone 3.")).toBeVisible();

    await submitAlias(user, "Partial Search");
    await screen.findByText(/Results may be incomplete/);
    expect(notice()).toBeVisible();
    await submitAlias(user, "Unknown title");
    await screen.findByRole("heading", { name: "No development fixture matches this search." });
    expect(notice()).toBeVisible();
    await submitAlias(user, "Fixture Error");
    await screen.findByRole("heading", { name: "Development search unavailable" });
    expect(notice()).toBeVisible();
  });

  it("uses one active card and native release and quality radio groups", async () => {
    const user = userEvent.setup();
    renderSearch();
    await submitAlias(user, "Fixture Collection");
    await screen.findByText("2 unified content items");
    const openButtons = screen.getAllByRole("button", { name: /choose releases for/i });
    await user.click(openButtons[0]!);
    expect(screen.getByRole("group", { name: "Select one release" })).toBeVisible();
    await user.click(openButtons[1]!);
    expect(screen.getAllByRole("group", { name: "Select one release" })).toHaveLength(1);
    expect(screen.getAllByRole("radio").length).toBeGreaterThan(0);

    await submitAlias(user, "Multi Quality");
    await screen.findByText("1 unified content item");
    await user.click(screen.getByRole("button", { name: /choose releases for/i }));
    const findButton = screen.getByRole("button", { name: "Find Links" });
    expect(findButton).toBeDisabled();
    expect(screen.getByText("Select a release to continue.")).toBeVisible();
    await user.click(screen.getByRole("radio", { name: /Hindi/ }));
    expect(screen.getByRole("group", { name: "Select one quality" })).toBeVisible();
    expect(findButton).toBeDisabled();
    expect(screen.getByText("Select a quality to continue.")).toBeVisible();
    await user.click(screen.getByRole("radio", { name: "1080p" }));
    expect(findButton).toBeEnabled();
  });

  it("auto-selects one quality after release selection and Find Links never submits search or renders resolver UI", async () => {
    const user = userEvent.setup();
    renderSearch();
    await submitAlias(user, "Single Release");
    await screen.findByText("1 unified content item");
    await user.click(screen.getByRole("button", { name: /choose releases for/i }));
    const findButton = screen.getByRole("button", { name: "Find Links" });
    expect(findButton).toBeDisabled();
    await user.click(screen.getByRole("radio", { name: /Hindi/ }));
    expect(screen.getByRole("radio", { name: "1080p" })).toBeChecked();
    expect(findButton).toBeEnabled();
    await user.click(findButton);
    expect(screen.getByText("1 unified content item")).toBeVisible();
    expect(screen.getByText("Selection is ready. Link resolution is not connected in Milestone 3.")).toBeVisible();
    expect(screen.queryByText(/Delivery Links|Checking source|Download|Copy/i)).not.toBeInTheDocument();
  });

  it("does not clear displayed results when only the draft changes", async () => {
    const user = userEvent.setup();
    renderSearch();
    await submitAlias(user, "Example Film");
    const result = await screen.findByText(/Example Film 2024/);
    await user.type(screen.getByRole("searchbox"), " edited");
    expect(result).toBeVisible();
  });

  it("aborts the observed request and prevents an older response replacing the latest query", async () => {
    const aborted: string[] = [];
    const started: string[] = [];
    const transport: SearchTransport = {
      search({ query }: SearchRequest, signal: AbortSignal) {
        started.push(query);
        signal.addEventListener("abort", () => aborted.push(query), { once: true });
        const delay = query === "Example Film" ? 80 : 5;
        const scenario = query === "Example Film" ? "movie-several" : "tv-episode";
        return new Promise<SearchResponse>((resolve) => {
          window.setTimeout(() => resolve(fixtureResponseForScenario(scenario, query)), delay);
        });
      },
    };
    const user = userEvent.setup();
    renderSearch(transport);
    await submitAlias(user, "Example Film");
    await waitFor(() => expect(started).toContain("Example Film"));
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Example Show S02E03" } });
    fireEvent.click(screen.getByRole("button", { name: /search/i }));
    await screen.findByRole("heading", { level: 2, name: /Example Show/ });
    await waitFor(() => expect(aborted).toContain("Example Film"));
    await new Promise((resolve) => window.setTimeout(resolve, 100));
    expect(screen.getByRole("heading", { level: 2, name: /Example Show/ })).toBeVisible();
    expect(screen.queryByRole("heading", { level: 2, name: /Example Film 2024/ })).not.toBeInTheDocument();
  });

  it("contains no source internals in text or any serialized DOM attribute and never renders an image", async () => {
    const user = userEvent.setup();
    const { container } = renderSearch();
    await submitAlias(user, "Multiple Sources");
    await screen.findByText("1 unified content item");
    await user.click(screen.getByRole("button", { name: /choose releases for/i }));
    const html = container.outerHTML;
    for (const forbidden of ["Source One", "Source Two", "source_205", "verificationState", "priority=", "cookie", "selector", "authorization"]) {
      expect(html.toLocaleLowerCase("en-US")).not.toContain(forbidden.toLocaleLowerCase("en-US"));
    }
    for (const element of Array.from(container.querySelectorAll("*"))) {
      for (const attribute of Array.from(element.attributes)) {
        expect(attribute.value).not.toMatch(/Source One|Source Two|source_205|verificationState|cookie|selector|authorization/i);
      }
    }
    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(within(container).getByText("2 sources")).toBeVisible();
  });
});
