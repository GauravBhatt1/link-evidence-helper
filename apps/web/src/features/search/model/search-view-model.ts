import type { Content, ReleaseVariant } from "../../../types/contracts";
import { selectableQualities } from "./release-selection";

export type ReleaseVariantViewModel = {
  variantId: string;
  language: string;
  releaseType: string;
  qualities: string[];
  sourceCount: number;
  packLabel: string;
};

export type ContentCardViewModel = {
  contentId: string;
  title: string;
  year: string;
  mediaType: "movie" | "tv";
  languages: string[];
  releaseCount: number;
  totalSources: number;
  fixtureLibraryStatus: "Available" | "Missing" | "Unknown";
  variants: ReleaseVariantViewModel[];
};

function packLabel(variant: ReleaseVariant) {
  if (variant.packType === "season") return variant.season === null ? "Season pack" : `Season ${variant.season} pack`;
  if (variant.packType === "complete-series") return "Complete series pack";
  if (variant.packType === "episode") {
    const season = variant.season === null ? "" : `S${String(variant.season).padStart(2, "0")}`;
    const episode = variant.episode === null ? "" : `E${String(variant.episode).padStart(2, "0")}`;
    return `${season}${episode}` || "Episode";
  }
  return "";
}

export function toContentCardViewModel(content: Content): ContentCardViewModel {
  const status = content.jellyfinStatus === "available"
    ? "Available"
    : content.jellyfinStatus === "missing" ? "Missing" : "Unknown";
  return {
    contentId: content.contentId,
    title: content.title,
    year: content.year,
    mediaType: content.mediaType,
    languages: [...content.languages],
    releaseCount: content.releaseVariants.length,
    totalSources: content.totalSources,
    fixtureLibraryStatus: status,
    variants: content.releaseVariants.map((variant) => ({
      variantId: variant.variantId,
      language: variant.language,
      releaseType: variant.releaseType,
      qualities: selectableQualities(variant),
      sourceCount: variant.sourceCount,
      packLabel: packLabel(variant),
    })),
  };
}
