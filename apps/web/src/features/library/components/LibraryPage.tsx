import { useQuery } from "@tanstack/react-query";
import { CircleAlert, Database, Film, RefreshCw, Tv } from "lucide-react";
import type { LibraryItem, LibraryView } from "../../../types/contracts";
import {
  defaultLibraryTransportConfiguration,
  LibraryTransportError,
  type LibraryTransport,
  type LibraryTransportMode,
} from "../api/library-transport";
import "../styles/library.css";

const viewLabels: Record<LibraryView, string> = {
  movies: "Movies",
  tv: "TV Shows",
  missing: "Missing",
  recent: "Recently Added",
};

const dateFormatter = new Intl.DateTimeFormat("en-IN", {
  dateStyle: "medium",
  timeZone: "UTC",
});

function itemInitials(title: string) {
  return title
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "LE";
}

function mediaLabel(item: LibraryItem) {
  if (item.mediaType === "movie") return "Movie";
  if (item.mediaType === "series") return "Series";
  if (item.mediaType === "season") return `Season ${item.season ?? "—"}`;
  return `S${String(item.season ?? 0).padStart(2, "0")}E${String(item.episode ?? 0).padStart(2, "0")}`;
}

function stateLabel(item: LibraryItem) {
  if (item.libraryState === "available") return "Available";
  if (item.libraryState === "missing") return "Missing";
  if (item.libraryState === "partial") return "Partially available";
  return "Status unknown";
}

function LibraryCard({ item }: { item: LibraryItem }) {
  const Icon = item.mediaType === "movie" ? Film : Tv;
  return (
    <article className="library-card">
      <div className="library-poster-fallback" aria-hidden="true">
        <span>{itemInitials(item.title)}</span>
      </div>
      <div className="library-card-body">
        <div className="library-card-title-row">
          <div>
            <h2>{item.title}</h2>
            <p className="library-card-meta">
              <Icon aria-hidden="true" focusable="false" />
              <span>{mediaLabel(item)}</span>
              {item.year && <span>{item.year}</span>}
            </p>
          </div>
          <span className={`library-state library-state-${item.libraryState}`}>{stateLabel(item)}</span>
        </div>
        <dl className="library-card-details">
          <div>
            <dt>Added</dt>
            <dd>{dateFormatter.format(new Date(item.dateAdded))}</dd>
          </div>
          <div>
            <dt>Jellyfin</dt>
            <dd>{item.jellyfin.configured ? (item.jellyfin.present ? "Available" : "Not found") : "Not configured"}</dd>
          </div>
        </dl>
      </div>
    </article>
  );
}

export function LibraryPage({
  view,
  transport = defaultLibraryTransportConfiguration.transport,
  mode = defaultLibraryTransportConfiguration.mode,
}: {
  view: LibraryView;
  transport?: LibraryTransport;
  mode?: LibraryTransportMode;
}) {
  const query = useQuery({
    queryKey: ["library", mode, view],
    queryFn: ({ signal }) => transport.list(view, signal),
  });

  const safeError = query.error instanceof LibraryTransportError
    ? "The library could not be loaded safely."
    : "The library is temporarily unavailable.";

  return (
    <section className="library-page" aria-label={`${viewLabels[view]} library`}>
      <aside className="library-mode-notice" aria-label="Library connection status">
        <Database aria-hidden="true" focusable="false" />
        <div>
          <strong>{mode === "fixture" ? "Offline library preview" : "Live library and Jellyfin"}</strong>
          <p>
            {mode === "fixture"
              ? "Connect the API transport to load production library data."
              : "Data is loaded from the production library bridge with Jellyfin availability."}
          </p>
        </div>
      </aside>

      {query.isPending && (
        <div className="library-loading" role="status">
          <span className="library-spinner" aria-hidden="true" />
          Loading {viewLabels[view].toLowerCase()}…
        </div>
      )}

      {query.isError && (
        <div className="library-error" role="alert">
          <CircleAlert aria-hidden="true" focusable="false" />
          <div>
            <h2>Library unavailable</h2>
            <p>{safeError}</p>
            <button type="button" onClick={() => void query.refetch()}>
              <RefreshCw aria-hidden="true" focusable="false" /> Retry
            </button>
          </div>
        </div>
      )}

      {query.data && (
        <>
          <div className="library-summary" aria-label="Library summary">
            <span><strong>{query.data.summary.movies}</strong> movies</span>
            <span><strong>{query.data.summary.tv}</strong> TV items</span>
            <span><strong>{query.data.summary.missing}</strong> missing or partial</span>
            <span>{query.data.jellyfin.configured ? "Jellyfin connected" : "Jellyfin not configured"}</span>
          </div>
          {query.data.items.length === 0 ? (
            <div className="library-empty">
              <h2>No {viewLabels[view].toLowerCase()} found</h2>
              <p>This view has no matching library items.</p>
            </div>
          ) : (
            <div className="library-grid">
              {query.data.items.map((item) => <LibraryCard key={item.itemId} item={item} />)}
            </div>
          )}
        </>
      )}
    </section>
  );
}
