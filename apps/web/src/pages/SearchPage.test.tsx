import { QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { createAppQueryClient } from "../app/query-client";
import { ResolutionClient } from "../features/resolution/api/resolution-client";
import { FixtureSearchTransport } from "../features/search/api/fixture-search-transport";
import { fixtureResponseForScenario } from "../features/search/api/search-fixture-catalog";
import type { SearchTransportMode } from "../features/search/api/search-transport-config";
import type { SearchRequest, SearchTransport } from "../features/search/api/search-transport";
import { useSearch } from "../features/search/hooks/use-search";
import type { Job, SearchResponse } from "../types/contracts";
import { SearchPage } from "./SearchPage";

const jobId = "job_0123456789abcdef0123456789abcdef";
const now = "2026-08-05T00:00:00Z";

function renderSearch(
  transport: SearchTransport = new FixtureSearchTransport(0),
  mode: SearchTransportMode = "fixture",
  resolutionClient?: ResolutionClient,
) {
  return render(
    <QueryClientProvider client={createAppQueryClient()}>
      <SearchPage transport={transport} mode={mode} resolutionClient={resolutionClient} />
    </QueryClientProvider>,
  );
}

async function submitAlias(user: ReturnType<typeof userEvent.setup>, alias: string) {
  const input = screen.getByRole("searchbox", { name: "Movie or TV title" });
  await user.clear(input);
  await user.type(input, alias);
  await user.click(screen.getByRole("button", { name: /search/i }));
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function queuedJob(): Job {
  return {
    jobId,
    kind: "resolution",
    state: "queued",
    subscriberCount: 1,
    createdAt: now,
    updatedAt: now,
    result: null,
  };
}

function verifiedJob(): Job {
  return {
    ...queuedJob(),
    state: "verified",
    result: {
      ok: true,
      success: true,
      code: "ok",
      status: "verified",
      contentId: "content_3b750f8edc77152e",
      variantId: "variant_051fab7b083f979a",
      deliveryLinks: [{
        url: "https://delivery.example/Example.Film.2024.1080p.mkv",
        filename: "Example.Film.2024.1080p.mkv",
        size: "1 GB",
        quality: "1080p",
        sourceId: "source_eadce85bb1968618",
        verifiedAt: now,
      }],
      attempts: [{
        sourceId: "source_eadce85bb1968618",
        status: "verified",
        failureReason: null,
        durationMs: 12,
      }],
      message: "Verified delivery links are ready.",
    },
  };
}

describe("SearchPage", () => {
  it("keeps the source mode disclosure visible in idle, loading, result, partial, empty, error, selection, and local-intent states", async () => {
    const user = userEvent.setup();
    renderSearch(new FixtureSearchTransport(20));
    const notice = () => screen.getByText("Offline preview search.");
    expect(notice()).toBeVisible();

    await submitAlias(user, "Example Film");
    expect(notice()).toBeVisible();
    expect(screen.getByText("Searching…")).toBeVisible();
    await screen.findByText(/Example Film 2024/);
    expect(notice()).toBeVisible();
    await user.click(screen.getByRole("button", { name: /choose releases for example film/i }));
    expect(notice()).toBeVisible();
    await user.click(screen.getAllByRole("radio", { name: /Hindi/ })[0]!);
    await user.click(screen.getByRole("button", { name: "Find Links" }));
    expect(notice()).toBeVisible();
    expect(screen.getByText("Selection is ready. Start the app in API mode to resolve links.")).toBeVisible();

    await submitAlias(user, "Partial Search");
    await screen.findByText(/Results may be incomplete/);
    expect(notice()).toBeVisible();
    await submitAlias(user, "Unknown title");
    await screen.findByRole("heading", { name: "No live source matches this search." });
    expect(notice()).toBeVisible();
    await submitAlias(user, "Fixture Error");
    await screen.findByRole("heading", { name: "Search unavailable" });
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

  it("auto-selects one quality after release selection and fixture Find Links never renders resolver UI", async () => {
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
    expect(screen.getByText("Selection is ready. Start the app in API mode to resolve links.")).toBeVisible();
    expect(screen.queryByText(/Delivery Links|Checking source|Open \/ Download|Copy Link/i)).not.toBeInTheDocument();
  });

  it("creates a private three-field API request and renders only verified Delivery Links", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const requestPath = String(input);
      if (requestPath === "/api/v1/jobs/resolution" && init?.method === "POST") {
        return jsonResponse(queuedJob(), 202);
      }
      if (requestPath === `/api/v1/jobs/${jobId}` && init?.method === "GET") {
        return jsonResponse(verifiedJob());
      }
      throw new Error(`Unexpected request: ${init?.method} ${requestPath}`);
    });
    renderSearch(new FixtureSearchTransport(0), "api", new ResolutionClient(fetchMock as typeof fetch));

    await submitAlias(user, "Single Release");
    await screen.findByText("1 unified content item");
    await user.click(screen.getByRole("button", { name: /choose releases for/i }));
    await user.click(screen.getByRole("radio", { name: /Hindi/ }));
    await user.click(screen.getByRole("button", { name: "Find Links" }));

    expect(await screen.findByRole("heading", { name: "Delivery Links" })).toBeVisible();
    const delivery = screen.getByRole("link", { name: "Open / Download" });
    expect(delivery).toHaveAttribute("href", "https://delivery.example/Example.Film.2024.1080p.mkv");
    expect(delivery).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("Example.Film.2024.1080p.mkv")).toBeVisible();
    expect(screen.getByText("1080p · 1 GB")).toBeVisible();

    const createCall = fetchMock.mock.calls.find(([requestPath, init]) => String(requestPath) === "/api/v1/jobs/resolution" && init?.method === "POST");
    expect(createCall).toBeDefined();
    const body = JSON.parse(String(createCall?.[1]?.body));
    expect(body).toEqual({
      contentId: "content_3b750f8edc77152e",
      variantId: "variant_051fab7b083f979a",
      quality: "1080p",
    });
    expect(JSON.stringify(body)).not.toMatch(/source|url|cookie|token/i);
    expect(screen.queryByText(/source_eadce|checking development job pipeline/i)).not.toBeInTheDocument();
  });

  it("unsubscribes an active job when the user cancels", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const requestPath = String(input);
      if (requestPath === "/api/v1/jobs/resolution" && init?.method === "POST") {
        return jsonResponse(queuedJob(), 202);
      }
      if (requestPath === `/api/v1/jobs/${jobId}` && init?.method === "DELETE") {
        return jsonResponse({ ...queuedJob(), state: "cancelled", subscriberCount: 0 });
      }
      if (requestPath === `/api/v1/jobs/${jobId}` && init?.method === "GET") {
        return new Promise<Response>(() => undefined);
      }
      throw new Error(`Unexpected request: ${init?.method} ${requestPath}`);
    });
    renderSearch(new FixtureSearchTransport(0), "api", new ResolutionClient(fetchMock as typeof fetch));

    await submitAlias(user, "Single Release");
    await screen.findByText("1 unified content item");
    await user.click(screen.getByRole("button", { name: /choose releases for/i }));
    await user.click(screen.getByRole("radio", { name: /Hindi/ }));
    await user.click(screen.getByRole("button", { name: "Find Links" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("Link request cancelled.")).toBeVisible();
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([requestPath, init]) => String(requestPath) === `/api/v1/jobs/${jobId}` && init?.method === "DELETE")).toBe(true);
    });
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
    const queryClient = createAppQueryClient();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useSearch(transport), { wrapper });

    await act(async () => {
      await result.current.submit("Example Film");
    });
    await waitFor(() => expect(started).toContain("Example Film"));

    await act(async () => {
      await result.current.submit("Example Show S02E03");
    });
    await waitFor(() => expect(aborted).toContain("Example Film"));
    await waitFor(() => expect(result.current.response?.query).toBe("Example Show S02E03"));
    await new Promise((resolve) => window.setTimeout(resolve, 100));
    expect(result.current.response?.query).toBe("Example Show S02E03");
    expect(result.current.response?.contents[0]?.mediaType).toBe("tv");
  });

  it("returns focus to the results region after an explicit Retry completes", async () => {
    const user = userEvent.setup();
    renderSearch();
    await submitAlias(user, "Fixture Error");
    await screen.findByRole("heading", { name: "Search unavailable" });
    const resultsRegion = screen.getByRole("region", { name: "Search results" });
    expect(resultsRegion).not.toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByRole("heading", { name: "Search unavailable" });
    await waitFor(() => expect(resultsRegion).toHaveFocus());
  });

  it("contains no source internals in search text or serialized search DOM and never renders a poster image", async () => {
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
