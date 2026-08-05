#!/usr/bin/env python3
"""Export deterministic public fixtures from current Python behavior.

No external requests or production data are used. The only legacy import is
the pure aggregation module, which is treated as the Milestone 0 behavior
oracle.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from content_aggregation import aggregate_candidates  # noqa: E402
from sanitize_fixture import sanitize  # noqa: E402

SCHEMA_DIR = ROOT / "packages" / "contracts" / "schema"
FIXTURE_DIR = ROOT / "packages" / "testing" / "fixtures"
NOW = "2026-08-01T00:00:00Z"
LIBRARY_NOW = "2026-08-05T12:00:00Z"


def public_content(rows: list[dict[str, Any]], jellyfin: str = "unknown") -> list[dict[str, Any]]:
    contents = []
    for model in aggregate_candidates(rows):
        raw = asdict(model)
        raw["tmdbId"] = raw["tmdbId"] or None
        raw["jellyfinStatus"] = jellyfin
        for variant in raw["releaseVariants"]:
            variant["sourceCount"] = len(variant["sources"])
            variant["sources"] = [
                {
                    "sourceId": source["sourceId"],
                    "displayName": source["displayName"],
                    "priority": source["priority"],
                    "verificationState": source["verificationState"],
                }
                for source in variant["sources"]
            ]
        contents.append(raw)
    return sanitize(contents)


def search(name: str, rows: list[dict[str, Any]], failures: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "success": True,
        "code": "ok",
        "query": name,
        "contents": public_content(rows),
        "partialFailures": failures or [],
    }


def library_fixture() -> dict[str, Any]:
    disabled = {
        "configured": False,
        "present": False,
        "itemId": None,
        "serverId": None,
        "lastSyncedAt": None,
    }
    return {
        "ok": True,
        "success": True,
        "code": "ok",
        "view": "recent",
        "generatedAt": LIBRARY_NOW,
        "items": [
            {
                "itemId": "movie:horizon-gate:2026",
                "contentId": "content_horizon_gate_2026",
                "tmdbId": "100001",
                "title": "Horizon Gate",
                "year": 2026,
                "mediaType": "movie",
                "season": None,
                "episode": None,
                "poster": None,
                "libraryState": "available",
                "missing": False,
                "dateAdded": "2026-08-05T09:30:00Z",
                "updatedAt": "2026-08-05T10:00:00Z",
                "jellyfin": dict(disabled),
            },
            {
                "itemId": "episode:signal-house:s01e02",
                "contentId": "content_signal_house_s01e02",
                "tmdbId": "200102",
                "title": "Signal House — S01E02",
                "year": 2025,
                "mediaType": "episode",
                "season": 1,
                "episode": 2,
                "poster": None,
                "libraryState": "missing",
                "missing": True,
                "dateAdded": "2026-08-04T18:00:00Z",
                "updatedAt": "2026-08-04T18:00:00Z",
                "jellyfin": dict(disabled),
            },
            {
                "itemId": "series:signal-house:2025",
                "contentId": "content_signal_house_2025",
                "tmdbId": "200100",
                "title": "Signal House",
                "year": 2025,
                "mediaType": "series",
                "season": None,
                "episode": None,
                "poster": None,
                "libraryState": "partial",
                "missing": True,
                "dateAdded": "2026-08-03T14:15:00Z",
                "updatedAt": "2026-08-05T08:00:00Z",
                "jellyfin": dict(disabled),
            },
            {
                "itemId": "movie:paper-city:2024",
                "contentId": "content_paper_city_2024",
                "tmdbId": "100002",
                "title": "Paper City",
                "year": 2024,
                "mediaType": "movie",
                "season": None,
                "episode": None,
                "poster": None,
                "libraryState": "missing",
                "missing": True,
                "dateAdded": "2026-08-02T11:45:00Z",
                "updatedAt": "2026-08-02T11:45:00Z",
                "jellyfin": dict(disabled),
            },
            {
                "itemId": "season:signal-house:s01",
                "contentId": "content_signal_house_s01",
                "tmdbId": "200101",
                "title": "Signal House — Season 1",
                "year": 2025,
                "mediaType": "season",
                "season": 1,
                "episode": None,
                "poster": None,
                "libraryState": "available",
                "missing": False,
                "dateAdded": "2026-08-01T16:30:00Z",
                "updatedAt": "2026-08-05T08:00:00Z",
                "jellyfin": dict(disabled),
            },
            {
                "itemId": "movie:archive-zero:2023",
                "contentId": "content_archive_zero_2023",
                "tmdbId": "100003",
                "title": "Archive Zero",
                "year": 2023,
                "mediaType": "movie",
                "season": None,
                "episode": None,
                "poster": None,
                "libraryState": "available",
                "missing": False,
                "dateAdded": "2026-07-30T08:20:00Z",
                "updatedAt": "2026-07-30T08:20:00Z",
                "jellyfin": dict(disabled),
            },
        ],
        "summary": {"total": 6, "movies": 3, "tv": 3, "missing": 3},
        "jellyfin": {"configured": False, "mode": "disabled", "lastSyncedAt": None},
    }


def fixtures() -> dict[str, tuple[str, bool, str, Any]]:
    movie_one = [{"title": "Example Film 2024 Hindi 1080p WEB-DL 1.2 GB", "url": "https://source-one.example/item", "source_id": "source-one", "source_name": "Source One"}]
    movie_many = [
        {"title": "Example Film 2024 Hindi 1080p WEB-DL 1.2 GB", "url": "https://source-one.example/a", "source_id": "source-one", "source_name": "Source One"},
        {"title": "Example Film (2024) Hindi 1080p WEB-DL 1.4 GB", "url": "https://source-two.example/a", "source_id": "source-two", "source_name": "Source Two"},
        {"title": "Example Film 2024 Hindi English Dual Audio 720p BluRay", "url": "https://source-one.example/b", "source_id": "source-one", "source_name": "Source One"},
    ]
    tv_episode = [{"title": "Example Show 2024 Hindi S02E03 1080p WEB-DL", "mediaType": "tv", "url": "https://source-one.example/episode", "source_id": "source-one", "source_name": "Source One"}]
    season_pack = [{"title": "Example Show 2024 Hindi Season 2 Complete Pack 1080p WEB-DL", "mediaType": "tv", "url": "https://source-one.example/season", "source_id": "source-one", "source_name": "Source One"}]
    multi_quality = [{"title": "Example Film 2024 Hindi 480p 720p 1080p 2160p BluRay", "url": "https://source-one.example/multi", "source_id": "source-one", "source_name": "Source One"}]
    successful = {
        "ok": True, "success": True, "code": "ok", "status": "verified",
        "contentId": "content_example", "variantId": "variant_1080p",
        "deliveryLinks": [{"url": "https://download.example.invalid/example.mkv", "filename": "Example.Film.2024.1080p.mkv", "size": "1.2 GB", "quality": "1080p", "sourceId": "source-one", "verifiedAt": NOW}],
        "attempts": [{"sourceId": "source-one", "status": "verified", "failureReason": None, "durationMs": 420}],
        "message": "Verified downloadable response",
    }
    backup = {
        **successful,
        "attempts": [
            {"sourceId": "source-one", "status": "failed", "failureReason": "missing final link", "durationMs": 600},
            {"sourceId": "source-two", "status": "verified", "failureReason": None, "durationMs": 350},
        ],
        "deliveryLinks": [{**successful["deliveryLinks"][0], "sourceId": "source-two"}],
    }
    blocked = {
        "ok": False, "success": False, "code": "manual_verification_required", "status": "blocked",
        "contentId": "content_example", "variantId": "variant_1080p", "deliveryLinks": [],
        "attempts": [{"sourceId": "source-one", "status": "blocked", "failureReason": "Manual verification required", "durationMs": 500}],
        "message": "Manual verification required",
    }
    one_content = public_content(movie_one)[0]
    one_variant = one_content["releaseVariants"][0]
    one_source = one_variant["sources"][0]
    events = [
        ("job-event-queued.json", "queued", 0),
        ("job-event-checking.json", "checking-preferred-source", 45),
        ("job-event-verified.json", "verified", 100),
    ]
    result: dict[str, tuple[str, bool, str, Any]] = {
        "source-candidate.json": ("source-candidate.schema.json", True, "sanitized content_aggregation.SourceCandidate projection", one_source),
        "release-variant.json": ("release-variant.schema.json", True, "sanitized content_aggregation.ReleaseVariant projection", one_variant),
        "content.json": ("content.schema.json", True, "sanitized content_aggregation.Content projection", one_content),
        "movie-one-variant.json": ("search-response.schema.json", True, "content_aggregation.aggregate_candidates", search("Example Film", movie_one)),
        "movie-several-variants.json": ("search-response.schema.json", True, "content_aggregation.aggregate_candidates", search("Example Film", movie_many)),
        "tv-episode.json": ("search-response.schema.json", True, "content_aggregation.aggregate_candidates", search("Example Show S02E03", tv_episode)),
        "season-pack.json": ("search-response.schema.json", True, "content_aggregation.aggregate_candidates", search("Example Show Season 2", season_pack)),
        "multi-quality-release.json": ("search-response.schema.json", True, "content_aggregation.aggregate_candidates", search("Example Film", multi_quality)),
        "multiple-source-candidates.json": ("search-response.schema.json", True, "content_aggregation.aggregate_candidates", search("Example Film", movie_many[:2])),
        "partial-search-success.json": ("search-response.schema.json", True, "current success envelope plus deterministic aggregate", search("Example Film", movie_one, [{"sourceId": "source-two", "reason": "timed out"}])),
        "library-response.json": ("library-response.schema.json", True, "sanitized deterministic Movies, TV, Missing, Recently Added, and disabled Jellyfin projection", library_fixture()),
        "resolution-success.json": ("resolution-result.schema.json", True, "current safe resolution metadata envelope", successful),
        "resolution-backup-success.json": ("resolution-result.schema.json", True, "current failover behavior projected to safe attempt summaries", backup),
        "resolution-blocked.json": ("resolution-result.schema.json", True, "current manual-verification behavior", blocked),
        "resolution-request.json": ("resolution-request.schema.json", True, "current content/variant/quality selection", {"contentId": "content_example", "variantId": "variant_1080p", "quality": "1080p"}),
        "job.json": ("job.schema.json", True, "approved Milestone 0 job contract", {"jobId": "job_example", "kind": "resolution", "state": "queued", "subscriberCount": 1, "createdAt": NOW, "updatedAt": NOW, "result": None}),
        "error.json": ("error.schema.json", True, "current safe error envelope", {"ok": False, "success": False, "code": "request_failed", "error": "Request failed", "requestId": None}),
        "invalid/invalid-search-extra-field.json": ("search-response.schema.json", False, "negative additional-property fixture", {**search("Example Film", movie_one), "unexpectedField": "not-public"}),
        "invalid/invalid-resolution-request.json": ("resolution-request.schema.json", False, "negative required-field fixture", {"contentId": "content_example"}),
        "invalid/invalid-job-state.json": ("job.schema.json", False, "negative enum fixture", {"jobId": "job_example", "kind": "resolution", "state": "running-anything", "subscriberCount": 1, "createdAt": NOW, "updatedAt": NOW, "result": None}),
    }
    for filename, state, progress in events:
        result[filename] = ("job-event.schema.json", True, "approved Milestone 0 job lifecycle", {"eventId": filename.removesuffix(".json"), "jobId": "job_example", "state": state, "message": state.replace("-", " ").title(), "occurredAt": NOW, "progress": progress})
    return result


def schema_registry() -> tuple[dict[str, Any], Registry]:
    schemas = {path.name: json.loads(path.read_text()) for path in sorted(SCHEMA_DIR.glob("*.json"))}
    resources = [(schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()]
    return schemas, Registry().with_resources(resources)


def render(output: Path) -> list[dict[str, Any]]:
    schemas, registry = schema_registry()
    manifest = []
    for filename, (schema_name, valid, provenance, value) in fixtures().items():
        clean = sanitize(value)
        error = None
        try:
            Draft202012Validator(schemas[schema_name], registry=registry).validate(clean)
        except Exception as exc:  # expected for negative fixtures
            error = exc
        if valid and error:
            raise SystemExit(f"valid fixture {filename} failed schema: {error}")
        if not valid and error is None:
            raise SystemExit(f"invalid fixture {filename} unexpectedly passed schema")
        target = output / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n")
        manifest.append({"file": filename, "schema": schema_name, "valid": valid, "provenance": provenance})
    manifest.sort(key=lambda item: item["file"])
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="regenerate checked-in fixtures")
    parser.add_argument("--check", action="store_true", help="verify checked-in fixtures are deterministic")
    args = parser.parse_args()
    if args.write == args.check:
        parser.error("choose exactly one of --write or --check")
    if args.write:
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        render(FIXTURE_DIR)
        return
    with tempfile.TemporaryDirectory() as temporary:
        generated = Path(temporary)
        render(generated)
        expected = {path.relative_to(generated): path.read_bytes() for path in generated.rglob("*.json")}
        actual = {path.relative_to(FIXTURE_DIR): path.read_bytes() for path in FIXTURE_DIR.rglob("*.json")}
        if expected != actual:
            raise SystemExit("checked-in fixtures drifted; run export_contract_fixtures.py --write")


if __name__ == "__main__":
    main()
