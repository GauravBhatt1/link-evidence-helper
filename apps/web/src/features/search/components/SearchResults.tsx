import type { ResolutionRequest } from "../../../types/contracts";
import type { SearchPhase } from "../hooks/use-search";
import type { SearchSelections } from "../model/release-selection";
import { buildResolutionIntent, selectedQuality } from "../model/release-selection";
import type { ContentCardViewModel } from "../model/search-view-model";
import { ContentCard } from "./ContentCard";
import { SearchEmptyState } from "./SearchEmptyState";
import { SearchErrorState } from "./SearchErrorState";
import { SearchLoadingSkeleton } from "./SearchLoadingSkeleton";
import { SearchPartialNotice } from "./SearchPartialNotice";

export function SearchResults({
  phase,
  query,
  contents,
  partialFailureCount,
  safeError,
  activeContentId,
  selections,
  localIntent,
  onToggleContent,
  onSelectVariant,
  onSelectQuality,
  onFind,
  onRetry,
}: {
  phase: SearchPhase;
  query: string;
  contents: ContentCardViewModel[];
  partialFailureCount: number;
  safeError: string;
  activeContentId: string | null;
  selections: SearchSelections;
  localIntent: ResolutionRequest | null;
  onToggleContent: (contentId: string) => void;
  onSelectVariant: (contentId: string, variantId: string) => void;
  onSelectQuality: (contentId: string, variantId: string, quality: string) => void;
  onFind: (contentId: string, variantId: string, quality: string) => void;
  onRetry: () => void;
}) {
  if (phase === "idle") return <p className="search-instructions">Enter a documented fixture alias to explore the search workflow.</p>;
  if (phase === "submitting") {
    return <><p className="search-status" role="status">Searching development fixtures…</p><SearchLoadingSkeleton /></>;
  }
  if (phase === "error") return <SearchErrorState message={safeError} onRetry={onRetry} />;
  if (phase === "empty") return <SearchEmptyState query={query} />;

  return (
    <div className="search-results-list">
      <p className="search-status" role="status">{contents.length} unified {contents.length === 1 ? "content item" : "content items"}</p>
      {phase === "partial" && <SearchPartialNotice failureCount={partialFailureCount} />}
      {contents.map((content) => {
        const selection = selections[content.contentId];
        const variantId = selection?.selectedVariantId ?? null;
        const variant = content.variants.find((item) => item.variantId === variantId);
        const quality = variantId ? selectedQuality(selections, content.contentId, variantId) : "";
        const validIntent = variant ? buildResolutionIntent(content.contentId, variant, quality) : null;
        const helper = !variant
          ? "Select a release to continue."
          : !variant.qualities.length
            ? "No quality is available for this release."
            : !quality ? "Select a quality to continue." : "Ready to find links.";
        const intentMatches = localIntent?.contentId === content.contentId
          && localIntent.variantId === variantId
          && localIntent.quality === quality;
        return (
          <ContentCard
            key={content.contentId}
            content={content}
            active={activeContentId === content.contentId}
            selectedVariantId={variantId}
            selectedQuality={quality}
            helper={helper}
            findEnabled={validIntent !== null}
            intentNotice={intentMatches ? "Selection is ready. Link resolution is not connected in Milestone 3." : ""}
            onToggle={() => onToggleContent(content.contentId)}
            onSelectVariant={(nextVariantId) => onSelectVariant(content.contentId, nextVariantId)}
            onSelectQuality={(nextQuality) => variant && onSelectQuality(content.contentId, variant.variantId, nextQuality)}
            onFind={() => validIntent && onFind(validIntent.contentId, validIntent.variantId, validIntent.quality)}
          />
        );
      })}
    </div>
  );
}
