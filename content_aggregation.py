"""Normalize adapter search results into stable content and release variants.

This module deliberately has no network or workflow code.  It is the boundary
between source-specific adapters and API consumers; verification stays owned by
the existing workflow implementations.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable


def _id(prefix: str, *parts: object) -> str:
    value = "|".join(str(part or "").strip().lower() for part in parts)
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:16]}"


def normalized_title(value: str) -> str:
    """Return the source-independent title portion of a release label.

    Providers frequently add harmless labels such as ``Movie`` and ``ESub``.
    Those must not turn an otherwise identical title/year into a second
    Content record when a source has no TMDB id.
    """
    text = re.sub(r"\b(?:download|movie|esub|480p|720p|1080p|2160p|4k|uhd|fhd|web[- ]?dl|web[- ]?rip|bluray|brrip|hdrip|x26[45]|hevc|10bit|dual audio|multi audio|hindi dubbed|hindi|english|tamil|telugu|malayalam|kannada|season|series|episode|ep)\b", " ", value, flags=re.I)
    text = re.sub(r"\b(?:19|20)\d{2}\b|\b\d+(?:\.\d+)?\s*(?:kb|mb|gb|tb)\b", " ", text, flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _match(pattern: str, value: str) -> str:
    found = re.search(pattern, value, flags=re.I)
    return found.group(1) if found else ""


def _languages(value: str) -> list[str]:
    labels = []
    for language, pattern in (("Hindi", r"\bhindi\b"), ("English", r"\benglish\b"), ("Tamil", r"\btamil\b"), ("Telugu", r"\btelugu\b"), ("Malayalam", r"\bmalayalam\b"), ("Kannada", r"\bkannada\b")):
        if re.search(pattern, value, flags=re.I):
            labels.append(language)
    return labels or ["Unknown"]


@dataclass
class SourceCandidate:
    sourceId: str
    adapterName: str
    displayName: str
    workflowMetadata: dict[str, Any]
    priority: int
    verificationState: str = "unverified"


@dataclass
class ReleaseVariant:
    variantId: str
    language: str
    audioVariant: str
    quality: str
    releaseType: str
    packType: str
    season: int | None
    episode: int | None
    approxSize: str
    # This is intentionally separate from the display ``quality``.  A single
    # release page can publish several quality branches, while ``quality``
    # remains ``Multiple`` so older API consumers are not told it is only the
    # first branch found in the label.
    availableQualities: list[str] = field(default_factory=list)
    sources: list[SourceCandidate] = field(default_factory=list)


@dataclass
class Content:
    contentId: str
    tmdbId: str
    title: str
    year: str
    mediaType: str
    poster: str
    languages: list[str]
    releaseVariants: list[ReleaseVariant]
    totalSources: int


_QUALITY_TOKEN_PATTERN = re.compile(r"\b(?:480p|720p|1080p|2160p|4k|uhd|fhd)\b", flags=re.I)
_QUALITY_ALIASES = {"fhd": "1080p", "4k": "2160p", "uhd": "2160p"}
_QUALITY_ORDER = ("480p", "720p", "1080p", "2160p")


def _canonical_quality(value: str) -> str:
    """Return one UI/API quality spelling for aliases found in source labels."""
    normalized = str(value or "").strip().lower()
    return _QUALITY_ALIASES.get(normalized, normalized)


def _available_qualities(label: str) -> list[str]:
    detected = {
        _canonical_quality(token)
        for token in _QUALITY_TOKEN_PATTERN.findall(label)
    }
    return [quality for quality in _QUALITY_ORDER if quality in detected]


def _variant_fields(row: dict[str, Any]) -> dict[str, Any]:
    label = " ".join(str(row.get(key) or "") for key in ("title", "variant", "quality", "size"))
    # Some release pages advertise every quality on one result card, e.g.
    # ``480p | 720p | 1080p | 2160p``.  Treating the first token as the
    # release quality lies to the user: the Find request still receives the
    # selected target quality, but the card used to show an arbitrary 480p.
    # Keep a single explicit quality, otherwise identify the page honestly as
    # a multi-quality source.
    detected_tokens = list(dict.fromkeys(_QUALITY_TOKEN_PATTERN.findall(label)))
    available_qualities = _available_qualities(label)
    if len(available_qualities) > 1:
        quality = "Multiple"
    elif detected_tokens:
        quality = detected_tokens[0].upper().replace("4K", "4K")
    else:
        quality = str(row.get("quality") or "Unknown")
    season_text, episode_text = _match(r"\b(?:season\s*|s)0?(\d{1,2})\b", label), _match(r"\b(?:episode\s*|ep\s*|e)0?(\d{1,3})\b", label)
    season = int(season_text) if season_text else None
    episode = int(episode_text) if episode_text else None
    pack = "episode" if episode else "season" if season and re.search(r"\b(zip|complete|pack)\b", label, re.I) else "single"
    release = _match(r"\b(web[- ]?dl|web[- ]?rip|bluray|brrip|hdrip|dvdrip)\b", label).upper().replace(" ", "-") or "Unknown"
    languages = _languages(label)
    return {
        "language": "/".join(languages), "audioVariant": "Dual Audio" if re.search(r"\bdual\b", label, re.I) else "Multi Audio" if re.search(r"\bmulti\b", label, re.I) else languages[0],
        "quality": quality, "availableQualities": available_qualities,
        "releaseType": release, "packType": pack, "season": season, "episode": episode,
        "approxSize": _match(r"\b(\d+(?:\.\d+)?\s*(?:KB|MB|GB|TB))\b", label),
    }


def aggregate_candidates(rows: Iterable[dict[str, Any]]) -> list[Content]:
    """Aggregate already-normalized adapter rows, retaining their input order.

    Approximate size is intentionally display metadata, never part of the
    variant identity.  The opaque original candidate is retained only inside
    workflow metadata for the backend's current compatibility resolver.
    """
    contents: dict[tuple[str, ...], Content] = {}
    variants: dict[tuple[str, tuple[Any, ...]], ReleaseVariant] = {}
    for priority, original in enumerate(rows):
        row = dict(original)
        title = str(row.get("title") or "").strip()
        year = _match(r"\b((?:19|20)\d{2})\b", title) or str(row.get("year") or "")
        media_type = str(row.get("mediaType") or row.get("media_type") or ("tv" if re.search(r"\b(?:season|series|s\d{1,2})\b", title, re.I) else "movie"))
        tmdb_id = str(row.get("tmdbId") or row.get("tmdb_id") or "")
        identity = ("tmdb", tmdb_id) if tmdb_id else ("fallback", normalized_title(title), year, media_type)
        content = contents.get(identity)
        if content is None:
            content = Content(_id("content", *identity), tmdb_id, title, year, media_type, str(row.get("poster") or row.get("poster_url") or ""), [], [], 0)
            contents[identity] = content
        fields = _variant_fields(row)
        variant_key = tuple(fields[key] for key in ("language", "audioVariant", "quality", "releaseType", "packType", "season", "episode"))
        variant = variants.get((content.contentId, variant_key))
        if variant is None:
            variant = ReleaseVariant(_id("variant", content.contentId, *variant_key), **fields)
            variants[(content.contentId, variant_key)] = variant
            content.releaseVariants.append(variant)
        else:
            for quality in fields["availableQualities"]:
                if quality not in variant.availableQualities:
                    variant.availableQualities.append(quality)
        adapter_name = str(row.get("source_id") or row.get("adapter_type") or row.get("source") or "legacy")
        display_name = str(row.get("source_name") or adapter_name)
        source = SourceCandidate(_id("source", adapter_name, row.get("url")), adapter_name, display_name, {"candidate": row}, priority)
        if not any(item.sourceId == source.sourceId for item in variant.sources):
            variant.sources.append(source)
        for language in fields["language"].split("/"):
            if language and language not in content.languages:
                content.languages.append(language)
    for content in contents.values():
        # A source is an adapter/provider here, not each of its individual
        # release-page candidates.  One provider may publish several variants.
        content.totalSources = len({source.adapterName for variant in content.releaseVariants for source in variant.sources})
    return list(contents.values())


def serialize_contents(contents: Iterable[Content]) -> list[dict[str, Any]]:
    return [asdict(content) for content in contents]


def compatibility_candidates(contents: Iterable[Content]) -> list[dict[str, Any]]:
    """Temporary old-client projection: one tile for each aggregated title.

    The old poster-grid UI knows only a flat candidate list.  Project its
    first (highest-priority) variant/source for each Content so it cannot
    render the same film twice merely because two providers returned it.  The
    complete releaseVariants/sources set remains available in ``contents``
    and Get Link still receives the selected contentId/variantId.
    """
    rows: list[dict[str, Any]] = []
    for content in contents:
        if not content.releaseVariants or not content.releaseVariants[0].sources:
            continue
        variant = content.releaseVariants[0]
        source = variant.sources[0]
        candidate = dict(source.workflowMetadata["candidate"])
        candidate.update({"contentId": content.contentId, "variantId": variant.variantId, "sourceId": source.sourceId})
        rows.append(candidate)
    return rows


def find_variant(contents: Iterable[Content], content_id: str, variant_id: str) -> ReleaseVariant | None:
    for content in contents:
        if content.contentId == content_id:
            return next((variant for variant in content.releaseVariants if variant.variantId == variant_id), None)
    return None


def failover_sources(variant: ReleaseVariant, verify: Callable[[SourceCandidate], Any]) -> tuple[Any | None, SourceCandidate | None]:
    """Verify sources in preserved priority order without changing workflows."""
    for source in sorted(variant.sources, key=lambda item: item.priority):
        result = verify(source)
        if result:
            source.verificationState = "verified"
            return result, source
        source.verificationState = "failed"
    return None, None
