import type { ResolutionRequest } from "../../../types/contracts";
import type { ResolutionViewState } from "../../resolution/hooks/use-resolution";
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
  resolutionEnabled,
  resolutionState,
  onToggleContent,
  onSelectVariant,
  onSelectQuality,
  onFind,
  onCancelResolution,
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
  resolutionEnabled: boolean;
  resolutionState: ResolutionViewState;
  onToggleContent: (contentId: string) => void;
  onSelectVariant: (contentId: string, variantId: string) => void;
  onSelectQuality: (contentId: string, variantId: string, quality: string) => void;
  onFind: (contentId: string, variantId: string, quality: string) => void;
  onCancelResolution: () => void;
  onRetry: () => void;
}) {
  if (phase === "idle") return <p className="search-instructions">Enter a title to search.</p>;
  if (phase === "submitting") {
    return <><p className="search-status" role="status">Searching…</p><SearchLoadingSkeleton /></>;
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
        const intentMatches = requestMatches(localIntent, content.contentId, variantId, quality);
        const resolutionMatches = requestMatches(resolutionState.request, content.contentId, variantId, quality);
        const findBusy = resolutionMatches
          && (resolutionState.phase === "submitting" || resolutionState.phase === "running");
        const helper = !variant
          ? "Select a release to continue."
          : !variant.qualities.length
            ? "No quality is available for this release."
            : !quality
              ? "Select a quality to continue."
              : findBusy
                ? resolutionState.statusMessage || "Checking links…"
                : "Ready to find links.";
        const resolution = resolutionMatches && resolutionState.phase !== "idle"
          ? {
              phase: resolutionState.phase,
              statusMessage: resolutionState.statusMessage,
              error: resolutionState.error,
              result: resolutionState.result,
            }
          : null;
        const intentNotice = !resolutionEnabled && intentMatches
          ? "Selection is ready. Start the app in API mode to resolve links."
          : "";

        return (
          <ContentCard
            key={content.contentId}
            content={content}
            active={activeContentId === content.contentId}
            selectedVariantId={variantId}
            selectedQuality={quality}
            helper={helper}
            findEnabled={validIntent !== null && !findBusy}
            findBusy={findBusy}
            intentNotice={intentNotice}
            resolution={resolution}
            onToggle={() => onToggleContent(content.contentId)}
            onSelectVariant={(nextVariantId) => onSelectVariant(content.contentId, nextVariantId)}
            onSelectQuality={(nextQuality) => variant && onSelectQuality(content.contentId, variant.variantId, nextQuality)}
            onFind={() => validIntent && onFind(validIntent.contentId, validIntent.variantId, validIntent.quality)}
            onCancelResolution={onCancelResolution}
          />
        );
      })}
    </div>
  );
}

function requestMatches(
  request: ResolutionRequest | null,
  contentId: string,
  variantId: string | null,
  quality: string,
): boolean {
  return request?.contentId === contentId
    && request.variantId === variantId
    && request.quality === quality;
}
