import type { ReleaseVariant, ResolutionRequest } from "../../../types/contracts";
import { resolutionRequestSchema } from "../../../types/contracts";

export type ContentSelection = {
  selectedVariantId: string | null;
  qualityByVariantId: Record<string, string>;
};

export type SearchSelections = Record<string, ContentSelection>;
export type ResolutionIntent = ResolutionRequest & { quality: string };
export type SelectableRelease = { variantId: string; qualities: string[] };

function qualityKey(value: string) {
  return value.trim().toLocaleLowerCase("en-US");
}

function matchingQuality(qualities: string[], requestedQuality: string) {
  const requestedKey = qualityKey(requestedQuality);
  if (!requestedKey) return undefined;
  return qualities.find((quality) => qualityKey(quality) === requestedKey);
}

export function selectableQualities(variant: Pick<ReleaseVariant, "availableQualities" | "quality">) {
  const qualities: string[] = [];
  const keys = new Set<string>();
  for (const value of variant.availableQualities) {
    const trimmed = value.trim();
    const key = qualityKey(trimmed);
    if (trimmed && !keys.has(key)) {
      keys.add(key);
      qualities.push(trimmed);
    }
  }
  const fallback = variant.quality.trim();
  if (!qualities.length && fallback && qualityKey(fallback) !== "multiple") qualities.push(fallback);
  return qualities;
}

export function selectRelease(
  selections: SearchSelections,
  contentId: string,
  variant: SelectableRelease,
) {
  const existing = selections[contentId] ?? { selectedVariantId: null, qualityByVariantId: {} };
  const previous = existing.qualityByVariantId[variant.variantId];
  const canonicalPrevious = previous ? matchingQuality(variant.qualities, previous) : undefined;
  const nextSelectedQuality = canonicalPrevious ?? (variant.qualities.length === 1 ? variant.qualities[0] : undefined);
  const qualityByVariantId = { ...existing.qualityByVariantId };

  if (nextSelectedQuality) {
    qualityByVariantId[variant.variantId] = nextSelectedQuality;
  } else {
    delete qualityByVariantId[variant.variantId];
  }

  return {
    ...selections,
    [contentId]: {
      selectedVariantId: variant.variantId,
      qualityByVariantId,
    },
  };
}

export function selectQuality(
  selections: SearchSelections,
  contentId: string,
  variant: SelectableRelease,
  requestedQuality: string,
) {
  const match = matchingQuality(variant.qualities, requestedQuality);
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
  const canonicalQuality = matchingQuality(variant.qualities, quality);
  if (!canonicalQuality) return null;
  const candidate = { contentId, variantId: variant.variantId, quality: canonicalQuality };
  const parsed = resolutionRequestSchema.safeParse(candidate);
  return parsed.success ? { ...parsed.data, quality: canonicalQuality } : null;
}
