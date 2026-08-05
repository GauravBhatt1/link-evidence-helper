import { useId } from "react";
import type { ResolutionResult } from "../../../types/contracts";
import type { ResolutionPhase } from "../../resolution/hooks/use-resolution";
import { ResolutionPanel } from "../../resolution/components/ResolutionPanel";
import type { ContentCardViewModel } from "../model/search-view-model";
import { ContentSummary } from "./ContentSummary";
import { FindLinksAction } from "./FindLinksAction";
import { QualitySelector } from "./QualitySelector";
import { ReleaseVariantList } from "./ReleaseVariantList";

export function ContentCard({
  content,
  active,
  selectedVariantId,
  selectedQuality,
  helper,
  findEnabled,
  findBusy,
  intentNotice,
  resolution,
  onToggle,
  onSelectVariant,
  onSelectQuality,
  onFind,
  onCancelResolution,
}: {
  content: ContentCardViewModel;
  active: boolean;
  selectedVariantId: string | null;
  selectedQuality: string;
  helper: string;
  findEnabled: boolean;
  findBusy: boolean;
  intentNotice: string;
  resolution: {
    phase: ResolutionPhase;
    statusMessage: string;
    error: string;
    result: ResolutionResult | null;
  } | null;
  onToggle: () => void;
  onSelectVariant: (variantId: string) => void;
  onSelectQuality: (quality: string) => void;
  onFind: () => void;
  onCancelResolution: () => void;
}) {
  const generatedId = useId().replace(/:/g, "");
  const safeContentId = content.contentId.replace(/[^a-zA-Z0-9_-]/g, "-");
  const workspaceId = `content-workspace-${safeContentId}-${generatedId}`;
  const selectedVariant = content.variants.find((variant) => variant.variantId === selectedVariantId);
  return (
    <article className={`content-card${active ? " active" : ""}`}>
      <div className="content-card-summary">
        <ContentSummary content={content} />
        <button
          type="button"
          className="content-disclosure"
          aria-expanded={active}
          aria-controls={workspaceId}
          aria-label={`${active ? "Close" : "Choose releases for"} ${content.title}`}
          onClick={onToggle}
        >
          {active ? "Close" : "Choose releases"}
        </button>
      </div>
      {active && (
        <div id={workspaceId} className="release-workspace">
          <ReleaseVariantList
            variants={content.variants}
            selectedVariantId={selectedVariantId}
            onSelect={onSelectVariant}
          />
          {selectedVariant && (
            <QualitySelector
              qualities={selectedVariant.qualities}
              selectedQuality={selectedQuality}
              onSelect={onSelectQuality}
            />
          )}
          <FindLinksAction
            enabled={findEnabled}
            busy={findBusy}
            helper={helper}
            onActivate={onFind}
          />
          {intentNotice && <p className="resolution-intent-notice" role="status">{intentNotice}</p>}
          {resolution && (
            <ResolutionPanel
              phase={resolution.phase}
              statusMessage={resolution.statusMessage}
              error={resolution.error}
              result={resolution.result}
              onCancel={onCancelResolution}
            />
          )}
        </div>
      )}
    </article>
  );
}
