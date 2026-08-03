import { describe, expect, it } from "vitest";
import { fixtureResponseForScenario } from "../api/search-fixture-catalog";
import { buildResolutionIntent, selectQuality, selectRelease, selectedQuality } from "./release-selection";

describe("release and quality selection", () => {
  it("auto-selects one quality only after the release is selected", () => {
    const content = fixtureResponseForScenario("movie-one", "Single Release").contents[0]!;
    const variant = content.releaseVariants[0]!;
    const selectable = { variantId: variant.variantId, qualities: variant.availableQualities };
    const selected = selectRelease({}, content.contentId, selectable);
    expect(selected[content.contentId]?.selectedVariantId).toBe(variant.variantId);
    expect(selectedQuality(selected, content.contentId, variant.variantId)).toBe(variant.availableQualities[0]);
  });

  it("requires an explicit choice for multi-quality releases", () => {
    const content = fixtureResponseForScenario("multi-quality", "Multi Quality").contents[0]!;
    const variant = content.releaseVariants[0]!;
    const selectable = { variantId: variant.variantId, qualities: variant.availableQualities };
    const releaseSelected = selectRelease({}, content.contentId, selectable);
    expect(selectedQuality(releaseSelected, content.contentId, variant.variantId)).toBe("");
    expect(buildResolutionIntent(content.contentId, selectable, "")).toBeNull();

    const qualitySelected = selectQuality(releaseSelected, content.contentId, selectable, "1080P");
    expect(selectedQuality(qualitySelected, content.contentId, variant.variantId)).toBe("1080p");
  });

  it("builds a strict validated intent with exactly three public fields", () => {
    const content = fixtureResponseForScenario("movie-one", "Single Release").contents[0]!;
    const variant = content.releaseVariants[0]!;
    const selectable = { variantId: variant.variantId, qualities: variant.availableQualities };
    const intent = buildResolutionIntent(content.contentId, selectable, variant.availableQualities[0]!);
    expect(intent).toEqual({ contentId: content.contentId, variantId: variant.variantId, quality: variant.availableQualities[0] });
    expect(Object.keys(intent!)).toEqual(["contentId", "variantId", "quality"]);
    expect(buildResolutionIntent(content.contentId, selectable, "2160p")).toBeNull();
  });
});
