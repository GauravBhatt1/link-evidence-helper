import { useEffect, useMemo, useRef, useState } from "react";
import { fixtureSearchTransport } from "../features/search/api/fixture-search-transport";
import type { SearchTransport } from "../features/search/api/search-transport";
import { SearchDevelopmentNotice } from "../features/search/components/SearchDevelopmentNotice";
import { SearchForm } from "../features/search/components/SearchForm";
import { SearchResults } from "../features/search/components/SearchResults";
import { useSearch } from "../features/search/hooks/use-search";
import {
  buildResolutionIntent,
  selectQuality,
  selectRelease,
  type SearchSelections,
} from "../features/search/model/release-selection";
import { toContentCardViewModel } from "../features/search/model/search-view-model";
import "../features/search/styles/search.css";
import type { ResolutionRequest } from "../types/contracts";

export function SearchPage({ transport = fixtureSearchTransport }: { transport?: SearchTransport }) {
  const search = useSearch(transport);
  const [activeContentId, setActiveContentId] = useState<string | null>(null);
  const [selections, setSelections] = useState<SearchSelections>({});
  const [localIntent, setLocalIntent] = useState<ResolutionRequest | null>(null);
  const resultsRegionRef = useRef<HTMLDivElement>(null);
  const focusResultsAfterRetry = useRef(false);

  const resetDecisions = () => {
    setActiveContentId(null);
    setSelections({});
    setLocalIntent(null);
  };

  const submit = async (query: string) => {
    resetDecisions();
    return search.submit(query);
  };

  useEffect(() => {
    if (!focusResultsAfterRetry.current || search.phase === "submitting") return;
    focusResultsAfterRetry.current = false;
    const frame = window.requestAnimationFrame(() => {
      resultsRegionRef.current?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [search.phase]);

  const contents = search.response?.contents ?? [];
  const contentViewModels = useMemo(() => contents.map(toContentCardViewModel), [contents]);

  return (
    <section className="search-page" aria-label="Fixture search workflow">
      <SearchDevelopmentNotice />
      <SearchForm busy={search.phase === "submitting"} externalError={search.formError} onSubmit={submit} />
      <div
        ref={resultsRegionRef}
        className="search-results"
        role="region"
        aria-label="Search results"
        aria-busy={search.phase === "submitting"}
        tabIndex={-1}
      >
        <SearchResults
          phase={search.phase}
          query={search.submittedQuery}
          contents={contentViewModels}
          partialFailureCount={search.response?.partialFailures.length ?? 0}
          safeError={search.safeError}
          activeContentId={activeContentId}
          selections={selections}
          localIntent={localIntent}
          onToggleContent={(contentId) => {
            setActiveContentId((current) => current === contentId ? null : contentId);
            setLocalIntent(null);
          }}
          onSelectVariant={(contentId, variantId) => {
            const content = contentViewModels.find((item) => item.contentId === contentId);
            const variant = content?.variants.find((item) => item.variantId === variantId);
            if (!variant) return;
            setSelections((current) => selectRelease(current, contentId, variant));
            setLocalIntent(null);
          }}
          onSelectQuality={(contentId, variantId, quality) => {
            const content = contentViewModels.find((item) => item.contentId === contentId);
            const variant = content?.variants.find((item) => item.variantId === variantId);
            if (!variant) return;
            setSelections((current) => selectQuality(current, contentId, variant, quality));
            setLocalIntent(null);
          }}
          onFind={(contentId, variantId, quality) => {
            const content = contentViewModels.find((item) => item.contentId === contentId);
            const variant = content?.variants.find((item) => item.variantId === variantId);
            if (!variant) return;
            setLocalIntent(buildResolutionIntent(contentId, variant, quality));
          }}
          onRetry={() => {
            resetDecisions();
            focusResultsAfterRetry.current = true;
            void search.retry();
          }}
        />
      </div>
    </section>
  );
}
