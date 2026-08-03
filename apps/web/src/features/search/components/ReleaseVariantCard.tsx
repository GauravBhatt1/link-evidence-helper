import type { ReleaseVariantViewModel } from "../model/search-view-model";

export function ReleaseVariantCard({
  variant,
  name,
  checked,
  onSelect,
}: {
  variant: ReleaseVariantViewModel;
  name: string;
  checked: boolean;
  onSelect: () => void;
}) {
  return (
    <label className={`release-option${checked ? " selected" : ""}`}>
      <input type="radio" name={name} value={variant.variantId} checked={checked} onChange={onSelect} />
      <span className="release-option-body">
        <strong>{variant.language}</strong>
        <span>{variant.releaseType}</span>
        {variant.packLabel && <span>{variant.packLabel}</span>}
        <span>{variant.qualities.join(" · ") || "Quality unavailable"}</span>
        <span>{variant.sourceCount} {variant.sourceCount === 1 ? "source" : "sources"}</span>
      </span>
    </label>
  );
}
