import { useId } from "react";
import type { ReleaseVariantViewModel } from "../model/search-view-model";
import { ReleaseVariantCard } from "./ReleaseVariantCard";

export function ReleaseVariantList({
  variants,
  selectedVariantId,
  onSelect,
}: {
  variants: ReleaseVariantViewModel[];
  selectedVariantId: string | null;
  onSelect: (variantId: string) => void;
}) {
  const groupName = useId();
  return (
    <fieldset className="release-fieldset">
      <legend>Select one release</legend>
      <div className="release-list">
        {variants.map((variant) => (
          <ReleaseVariantCard
            key={variant.variantId}
            variant={variant}
            name={groupName}
            checked={variant.variantId === selectedVariantId}
            onSelect={() => onSelect(variant.variantId)}
          />
        ))}
      </div>
    </fieldset>
  );
}
