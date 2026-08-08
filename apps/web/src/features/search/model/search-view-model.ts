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
  poster: string;
  languages: string[];
  releaseCount: number;
  totalSources: number;
  libraryStatus: "available" | "missing" | "unknown";
  libraryStatusLabel: "Available in Jellyfin" | "Not in Jellyfin" | "Availability unknown";
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
  const statusLabel = content.jellyfinStatus === "available"
    ? "Available in Jellyfin"
    : content.jellyfinStatus === "missing" ? "Not in Jellyfin" : "Availability unknown";
  return {
    contentId: content.contentId,
    title: content.title,
    year: content.year,
    mediaType: content.mediaType,
    poster: content.poster,
    languages: [...content.languages],
    releaseCount: content.releaseVariants.length,
    totalSources: content.totalSources,
    libraryStatus: content.jellyfinStatus,
    libraryStatusLabel: statusLabel,
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
