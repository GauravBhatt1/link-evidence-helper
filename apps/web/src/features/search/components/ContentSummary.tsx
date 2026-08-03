import { Film, Tv } from "lucide-react";
import type { ContentCardViewModel } from "../model/search-view-model";

export function ContentSummary({ content }: { content: ContentCardViewModel }) {
  const MediaIcon = content.mediaType === "movie" ? Film : Tv;
  return (
    <div className="content-summary">
      <div className="poster-fallback" aria-hidden="true">
        <MediaIcon aria-hidden="true" focusable="false" />
      </div>
      <div className="content-identification">
        <h2>{content.title}</h2>
        <p className="content-primary-meta">
          <span>{content.year || "Year unknown"}</span>
          <span>{content.mediaType === "movie" ? "Movie" : "TV Show"}</span>
        </p>
        <dl className="content-facts">
          <div><dt>Languages</dt><dd>{content.languages.join(", ") || "Unknown"}</dd></div>
          <div><dt>Release variants</dt><dd>{content.releaseCount}</dd></div>
          <div><dt>Total sources</dt><dd>{content.totalSources}</dd></div>
          <div><dt>Fixture library status</dt><dd>{content.fixtureLibraryStatus}</dd></div>
        </dl>
      </div>
    </div>
  );
}
