import libraryFixture from "../../../../../../packages/testing/fixtures/library-response.json";
import { apiPath } from "../../../app/runtime-paths";
import {
  libraryResponseSchema,
  type LibraryItem,
  type LibraryResponse,
  type LibraryView,
} from "../../../types/contracts";

export type LibraryTransport = {
  list(view: LibraryView, signal: AbortSignal): Promise<LibraryResponse>;
};

export class LibraryTransportError extends Error {
  constructor(public readonly code: "unavailable" | "invalid-contract" | "rejected") {
    super(code);
    this.name = "LibraryTransportError";
  }
}

function include(item: LibraryItem, view: LibraryView) {
  if (view === "movies") return item.mediaType === "movie";
  if (view === "tv") return item.mediaType !== "movie";
  if (view === "missing") return item.missing || item.libraryState === "partial";
  return true;
}

function sorted(items: LibraryItem[], view: LibraryView) {
  return [...items].sort((left, right) => {
    if (view === "recent") {
      const byDate = Date.parse(right.dateAdded) - Date.parse(left.dateAdded);
      if (byDate !== 0) return byDate;
    }
    return left.title.localeCompare(right.title, "en", { sensitivity: "base" }) || left.itemId.localeCompare(right.itemId);
  });
}

const fixtureCatalog = (() => {
  const parsed = libraryResponseSchema.safeParse(libraryFixture);
  if (!parsed.success) throw new LibraryTransportError("invalid-contract");
  return parsed.data;
})();

export class FixtureLibraryTransport implements LibraryTransport {
  async list(view: LibraryView, signal: AbortSignal): Promise<LibraryResponse> {
    if (signal.aborted) throw signal.reason;
    await Promise.resolve();
    if (signal.aborted) throw signal.reason;
    return libraryResponseSchema.parse({
      ...structuredClone(fixtureCatalog),
      view,
      items: sorted(fixtureCatalog.items.filter((item) => include(item, view)), view),
    });
  }
}

export class ApiLibraryTransport implements LibraryTransport {
  constructor(
    private readonly endpoint = apiPath("/api/v1/library"),
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  async list(view: LibraryView, signal: AbortSignal): Promise<LibraryResponse> {
    const target = new URL(this.endpoint, window.location.origin);
    if (target.origin !== window.location.origin) throw new LibraryTransportError("rejected");
    target.search = "";
    target.searchParams.set("view", view);

    let response: Response;
    try {
      response = await this.fetchImpl.call(globalThis, target.toString(), {
        method: "GET",
        headers: { Accept: "application/json" },
        cache: "no-store",
        credentials: "same-origin",
        redirect: "error",
        signal,
      });
    } catch (error) {
      if (signal.aborted) throw error;
      throw new LibraryTransportError("unavailable");
    }
    if (!response.ok) throw new LibraryTransportError(response.status >= 500 ? "unavailable" : "rejected");
    if (!response.headers.get("content-type")?.toLowerCase().includes("application/json")) {
      throw new LibraryTransportError("invalid-contract");
    }

    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new LibraryTransportError("invalid-contract");
    }
    const parsed = libraryResponseSchema.safeParse(payload);
    if (!parsed.success || parsed.data.view !== view) throw new LibraryTransportError("invalid-contract");
    return parsed.data;
  }
}

export type LibraryTransportMode = "fixture" | "api";

export function createLibraryTransportConfiguration(
  libraryMode?: string,
  searchMode?: string,
): { mode: LibraryTransportMode; transport: LibraryTransport } {
  if (libraryMode === "api" || (!libraryMode && searchMode === "api")) {
    return { mode: "api", transport: new ApiLibraryTransport() };
  }
  return { mode: "fixture", transport: new FixtureLibraryTransport() };
}

export const defaultLibraryTransportConfiguration = createLibraryTransportConfiguration(
  import.meta.env.VITE_LIBRARY_TRANSPORT,
  import.meta.env.VITE_SEARCH_TRANSPORT,
);
