import type { ReleaseVariant, ResolutionRequest } from "../../../types/contracts";
import { resolutionRequestSchema } from "../../../types/contracts";

export type ContentSelection = {
  selectedVariantId: string | null;
  qualityByVariantId: Record<string, string>;
};

export type SearchSelections = Record<string, ContentSelection>;
export type ResolutionIntent = ResolutionRequest & { quality: string };
export type SelectableRelease = { variantId: string; qualities: string[] };

export function selectableQualities(variant: Pick<ReleaseVariant, "availableQualities" | "quality">) {
  const qualities: string[] = [];
  const keys = new Set<string>();
  for (const value of variant.availableQualities) {
    const trimmed = value.trim();
    const key = trimmed.toLocaleLowerCase("en-US");
    if (trimmed && !keys.has(key)) {
      keys.add(key);
      qualities.push(trimmed);
    }
  }
  const fallback = variant.quality.trim();
  if (!qualities.length && fallback && fallback.toLocaleLowerCase("en-US") !== "multiple") qualities.push(fallback);
  return qualities;
}

export function selectRelease(
  selections: SearchSelections,
  contentId: string,
  variant: SelectableRelease,
) {
  const existing = selections[contentId] ?? { selectedVariantId: null, qualityByVariantId: {} };
  const qualities = variant.qualities;
  const previous = existing.qualityByVariantId[variant.variantId];
  const validPrevious = previous && qualities.some((quality) => quality.toLocaleLowerCase("en-US") === previous.toLocaleLowerCase("en-US"));
  const selectedQuality = validPrevious ? previous : qualities.length === 1 ? qualities[0] : undefined;
  return {
    ...selections,
    [contentId]: {
      selectedVariantId: variant.variantId,
      qualityByVariantId: {
        ...existing.qualityByVariantId,
        ...(selectedQuality ? { [variant.variantId]: selectedQuality } : {}),
      },
    },
  };
}

export function selectQuality(
  selections: SearchSelections,
  contentId: string,
  variant: SelectableRelease,
  requestedQuality: string,
) {
  const match = variant.qualities.find(
    (quality) => quality.toLocaleLowerCase("en-US") === requestedQuality.trim().toLocaleLowerCase("en-US"),
  );
  if (!match) return selections;
  const existing = selections[contentId] ?? { selectedVariantId: variant.variantId, qualityByVariantId: {} };
  return {
    ...selections,
    [contentId]: {
      selectedVariantId: variant.variantId,
      qualityByVariantId: { ...existing.qualityByVariantId, [variant.variantId]: match },
    },
  };
}

export function selectedQuality(selections: SearchSelections, contentId: string, variantId: string) {
  return selections[contentId]?.qualityByVariantId[variantId] ?? "";
}

export function buildResolutionIntent(
  contentId: string,
  variant: SelectableRelease,
  quality: string,
): ResolutionIntent | null {
  const valid = variant.qualities.some(
    (item) => item.toLocaleLowerCase("en-US") === quality.toLocaleLowerCase("en-US"),
  );
  if (!valid) return null;
  const parsed = resolutionRequestSchema.safeParse({ contentId, variantId: variant.variantId, quality });
  return parsed.success ? { ...parsed.data, quality } : null;
}
