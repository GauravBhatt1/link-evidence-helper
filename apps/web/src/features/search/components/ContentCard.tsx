import { useId } from "react";
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
  intentNotice,
  onToggle,
  onSelectVariant,
  onSelectQuality,
  onFind,
}: {
  content: ContentCardViewModel;
  active: boolean;
  selectedVariantId: string | null;
  selectedQuality: string;
  helper: string;
  findEnabled: boolean;
  intentNotice: string;
  onToggle: () => void;
  onSelectVariant: (variantId: string) => void;
  onSelectQuality: (quality: string) => void;
  onFind: () => void;
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
          <FindLinksAction enabled={findEnabled} helper={helper} onActivate={onFind} />
          {intentNotice && <p className="resolution-intent-notice" role="status">{intentNotice}</p>}
        </div>
      )}
    </article>
  );
}
