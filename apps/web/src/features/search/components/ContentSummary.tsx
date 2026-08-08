import { Film, Tv } from "lucide-react";
import type { ContentCardViewModel } from "../model/search-view-model";

export function ContentSummary({ content }: { content: ContentCardViewModel }) {
  const MediaIcon = content.mediaType === "movie" ? Film : Tv;
  return (
    <div className="content-summary">
      {content.poster
        ? <img className="content-poster" src={content.poster} alt="" loading="lazy" referrerPolicy="no-referrer" />
        : (
          <div className="poster-fallback" aria-hidden="true">
            <MediaIcon aria-hidden="true" focusable="false" />
          </div>
          )}
      <div className="content-identification">
        <div className="content-title-row">
          <h2>{content.title}</h2>
          <span className={`library-status-badge library-status-${content.libraryStatus}`}>
            {content.libraryStatusLabel}
          </span>
        </div>
        <p className="content-primary-meta">
          <span>{content.year || "Year unknown"}</span>
          <span>{content.mediaType === "movie" ? "Movie" : "TV Show"}</span>
        </p>
        <dl className="content-facts">
          <div><dt>Languages</dt><dd>{content.languages.join(", ") || "Unknown"}</dd></div>
          <div><dt>Release variants</dt><dd>{content.releaseCount}</dd></div>
          <div><dt>Total sources</dt><dd>{content.totalSources}</dd></div>
        </dl>
      </div>
    </div>
  );
}
