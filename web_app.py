#!/usr/bin/env python3
"""
Small web UI for report-link evidence collection.

The app reuses the CLI finder logic and serves a dependency-free single page UI.
It inspects pages and redirect headers only; it does not download file bodies.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen
from custom_authorized_source import (detect_selectors, parse_html as parse_authorized_sample,
                                      preview_selectors, search_authorized_source,
                                      guided_source_setup, live_auto_detect, live_preview, LiveFetchError,
                                      validate_config as validate_authorized_config,
                                      resolve_authorized_result, auto_setup_from_site_url)
from local_ai import LocalAIError, review_source_html

from evidence_link_finder import LinkParser, fetch_html, final_or_next_url, follow_redirects
from movie_report_finder import Candidate, build_evidence, find_listing_links, search_movie, search_movie_on_site
from library_backend import LibraryService
from adapter_analyzer import analyze as analyze_adapter, discover_search_results
from adapter_storage import AdapterStorage
from adapter_models import adapter_id, enable_workflow_fallback_for_verified_onboarding
from adapter_runtime import SiteAdapter, normalized_title
from workflow_analyzer import analyze_movie_workflow


DEFAULT_QUALITIES = ("480p", "720p", "1080p", "2160p", "4k")
DIRECT_HOST_MARKERS = (
    "video-downloads.googleusercontent.com",
)
COPYABLE_HOST_MARKERS = DIRECT_HOST_MARKERS + (
    "cloud-dl.",
    "quick.cloudpaglu",
)
INTERMEDIATE_HOST_MARKERS = (
    "fxlinks.rest",
    "dl.fastdlserver.site",
    "gdflix",
    "instant.busycdn.xyz",
    "fastcdn-dl.pages.dev",
)
ACCESS_TOKEN = ""
TMDB_API_KEY = ""
APP_BASE_PATH = "/index"
MEDIA_LIBRARY_PATHS: list[str] = []
JELLYFIN_URL = ""
JELLYFIN_API_KEY = ""
JELLYFIN_LIBRARY_IDS: set[str] = set()
LIBRARY: LibraryService | None = None
APP_DATA_DIR = Path(os.environ.get("APP_DATA_DIR", str(Path(__file__).resolve().parent)))
ADAPTERS = AdapterStorage(APP_DATA_DIR / "adapters")
ENABLED_ADAPTERS: list[dict[str, Any]] = []
ADMIN_TOKEN = ""
ANALYZER_ENABLED_DOMAINS: set[str] = set()
TMDB_POSTER_CACHE: dict[str, tuple[float, str]] = {}
TMDB_BACKDROP_CACHE: tuple[float, list[str]] = (0, [])
GDFLIX_LINK_CACHE: dict[str, tuple[float, str]] = {}
DFAST_LINK_CACHE: dict[str, tuple[float, str]] = {}
FXLINKS_LINK_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}
FXLINKS_LISTING_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}
RESOLVED_SIZE_CACHE: dict[str, str] = {}
FIND_RESULT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
WORKFLOW_PREFETCH_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
WORKFLOW_PREFETCH_INFLIGHT: set[str] = set()
WORKFLOW_PREFETCH_LOCK = threading.Lock()
MEDIA_LIBRARY_CACHE: tuple[float, dict[str, list[dict[str, str]]]] = (0, {})
JELLYFIN_LIBRARY_CACHE: tuple[float, dict[str, list[dict[str, str]]]] = (0, {})
JELLYFIN_SHOW_DETAIL_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
AUTO_SYNC_LAST_RUN = 0.0
TMDB_CACHE_SECONDS = 86400
GDFLIX_CACHE_SECONDS = 21600
FIND_CACHE_SECONDS = 3600
WORKFLOW_PREFETCH_CACHE_SECONDS = 1800
MEDIA_LIBRARY_CACHE_SECONDS = 300
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w154"
TMDB_BACKDROP_BASE = "https://image.tmdb.org/t/p/w1280"
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".ts"}
DEFAULT_MEDIA_LIBRARY_PATHS = (
    "/home/ubuntu/Cloud/movies",
    "/home/ubuntu/Cloud/movies1",
    "/home/ubuntu/Cloud/tvshows",
    "/home/ubuntu/Cloud/kdrama",
    "/home/ubuntu/Cloud/anime",
)
DEFAULT_JELLYFIN_API_KEY_DB = "/home/ubuntu/Config/Jellyfin/data/data/jellyfin.db"


def configure_analyzer_domains(value: str) -> None:
    """Load the explicit domain allowlist for internal workflow inspection.

    Deep analysis is never attempted for arbitrary candidate URLs.  A saved
    adapter can also opt in with ``workflow_analyzer.enabled: true``.
    """
    ANALYZER_ENABLED_DOMAINS.clear()
    for item in value.split(","):
        host = item.strip().lower().removeprefix("https://").removeprefix("http://").split("/", 1)[0]
        if host and re.fullmatch(r"[a-z0-9.-]+", host):
            ANALYZER_ENABLED_DOMAINS.add(host)


def analyzer_enabled_for(url: str, adapter: dict[str, Any] | None = None) -> bool:
    host = (urlparse(url).hostname or "").lower()
    adapter_enabled = bool((adapter or {}).get("workflow_analyzer", {}).get("enabled"))
    return adapter_enabled or any(host == domain or host.endswith("." + domain) for domain in ANALYZER_ENABLED_DOMAINS)


def normalize_workflow_result(workflow: dict[str, Any], source_url: str, source_name: str, include_debug: bool) -> dict[str, Any]:
    """Convert internal analyzer output to the established Find Links payload."""
    links = [
        enrich_delivery_link({
            "quality": item.get("quality") or "Unknown", "quality_label": item.get("quality") or "Unknown",
            "size": item.get("size") or "verified", "url": item["final_url"], "source": source_url,
            "source_name": source_name, "variant": item.get("source") or "Verified file",
            "filename": item.get("file_name") or "", "content_length": item.get("content_length") or "",
            "content_type": item.get("content_type") or "",
            "temporary": True,
        })
        for item in workflow.get("results", [])
        if item.get("is_final_file") and item.get("final_url")
    ]
    message = "" if links else "No verified final link is available for this source."
    return {"ok": True, "links": links, "message": message, "debug": workflow.get("execution_log", []) if include_debug else [], "cached": False, "temporary": True, "workflow_status": workflow.get("status", "partial")}


def workflow_prefetch_key(adapter_id: str, page_url: str, quality: str) -> str:
    return f"{adapter_id}|{page_url}|{quality.lower()}"


def prefetch_adapter_workflow(adapter: dict[str, Any], page_url: str, quality: str = "1080p") -> None:
    """Warm a verified public-link result while the user reviews search cards."""
    key = workflow_prefetch_key(str(adapter["id"]), page_url, quality)
    with WORKFLOW_PREFETCH_LOCK:
        cached = WORKFLOW_PREFETCH_CACHE.get(key)
        if key in WORKFLOW_PREFETCH_INFLIGHT or (cached and time.time() - cached[0] < WORKFLOW_PREFETCH_CACHE_SECONDS):
            return
        WORKFLOW_PREFETCH_INFLIGHT.add(key)

    def run() -> None:
        try:
            workflow = analyze_movie_workflow("https://" + str((adapter.get("domains") or [urlparse(page_url).hostname or ""])[0]), page_url, quality)
            payload = normalize_workflow_result(workflow, f"adapter:{adapter['id']}", adapter["name"], False)
            if payload["links"]:
                WORKFLOW_PREFETCH_CACHE[key] = (time.time(), payload)
        except Exception:
            pass
        finally:
            with WORKFLOW_PREFETCH_LOCK:
                WORKFLOW_PREFETCH_INFLIGHT.discard(key)

    threading.Thread(target=run, name=f"workflow-prefetch-{adapter['id']}", daemon=True).start()


def combined_sources() -> list[dict[str, Any]]:
    """Present runtime sources and JSON adapters as one user-facing inventory."""
    rows: list[dict[str, Any]] = []
    for item in (LIBRARY.list_sources() if LIBRARY else []):
        rows.append({"id": item["id"], "kind": "runtime", "name": item["name"], "domain": urlparse(item["base_url"]).hostname or item["base_url"], "enabled": item["enabled"], "search_status": item.get("last_status") or "Not tested", "resolver_status": item.get("last_status") or "Not tested", "last_tested_at": item.get("last_checked_at") or ""})
    for item in ENABLED_ADAPTERS:
        health = item.get("health", {})
        rows.append({"id": item["id"], "kind": "adapter", "name": item.get("name") or item["id"], "domain": (item.get("domains") or [""])[0], "enabled": bool(item.get("enabled")), "search_status": health.get("status") or "Not tested", "resolver_status": health.get("last_test_status") or "Not tested", "last_tested_at": health.get("last_tested_at") or ""})
    return sorted(rows, key=lambda row: (not row["enabled"], row["name"].lower()))


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FREEMIUM INDEX</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0c1117;
      --panel: #151d26;
      --panel-2: #202b36;
      --line: #30404f;
      --text: #edf4fa;
      --muted: #9eafbf;
      --accent: #37a7e7;
      --accent-2: #63d6ff;
      --danger: #ff6b6b;
      --shadow: rgba(0, 0, 0, 0.28);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background: radial-gradient(circle at 82% -12%, #173449 0, transparent 30%), var(--bg);
      color: var(--text);
      font: 15px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    .wallpaper-bg {
      position: fixed;
      inset: 0;
      z-index: 0;
      pointer-events: none;
      background-position: center;
      background-size: cover;
      background-repeat: no-repeat;
      opacity: 0.58;
      transition: background-image 600ms ease, opacity 600ms ease;
    }

    .wallpaper-bg::after {
      content: "";
      position: absolute;
      inset: 0;
      background:
        linear-gradient(180deg, rgba(12, 17, 23, 0.64), rgba(12, 17, 23, 0.92)),
        radial-gradient(circle at 50% 10%, rgba(55, 167, 231, 0.12), rgba(0, 0, 0, 0.55));
    }

    button, input, select, textarea {
      font: inherit;
    }

    .shell {
      position: relative;
      z-index: 1;
      width: min(1240px, calc(100% - 32px));
      margin: 0 auto;
      padding: 14px 0 28px;
    }

    .topbar {
      position: sticky;
      top: 0;
      z-index: 30;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
      padding: 13px 0;
      border-bottom: 1px solid rgba(111, 145, 168, .2);
      background: rgba(12, 17, 23, 0.88);
      backdrop-filter: blur(10px);
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 0;
    }

    .mark {
      width: 38px;
      height: 38px;
      display: grid;
      place-items: center;
      border: 1px solid #397fa8;
      background: linear-gradient(145deg, #183d56, #1d2933);
      border-radius: 10px;
      color: #77d8ff;
      box-shadow: 0 8px 24px var(--shadow);
    }

    .brand h1 {
      margin: 0;
      font-size: clamp(18px, 3vw, 24px);
      line-height: 1.1;
      letter-spacing: .045em;
    }

    .brand p {
      margin: 4px 0 0;
      color: var(--muted);
      font-size: 13px;
    }

    .status {
      min-height: 28px;
      padding: 5px 10px;
      border: 1px solid #345268;
      border-radius: 999px;
      color: var(--muted);
      background: rgba(22, 34, 44, 0.9);
      backdrop-filter: blur(8px);
      white-space: nowrap;
    }

    .status::before { content: ""; display: inline-block; width: 7px; height: 7px; margin: 0 7px 1px 0; border-radius: 50%; background: #48dd92; box-shadow: 0 0 10px rgba(72, 221, 146, .8); }

    .workspace-bar {
      display: flex;
      align-items: center;
      gap: 10px;
      margin: -7px 0 18px;
      color: var(--muted);
      font-size: 12px;
    }
    .workspace-bar strong { color: #dbeefa; font-size: 12px; font-weight: 700; }
    .workspace-divider { width: 1px; height: 14px; background: var(--line); }
    .workspace-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent-2); }
    .panel-head .head-note { color: var(--muted); font-size: 12px; font-weight: 600; text-transform: none; letter-spacing: 0; }

    .search-intro { margin: -2px 0 18px; color: var(--muted); font-size: 13px; line-height: 1.5; }
    .search-intro strong { color: #d8effb; }
    .quality-label { display: flex; align-items: center; justify-content: space-between; }
    .quality-label em { color: #63d6ff; font-size: 11px; font-style: normal; font-weight: 750; }
    .actions { padding-top: 2px; border-top: 1px solid rgba(158, 175, 191, .13); }
    .actions .btn { flex: 1; }
    .poster-card:hover { border-color: #5596bd; transform: translateY(-3px); box-shadow: 0 12px 22px rgba(0,0,0,.22); }
    .poster-card:hover .poster { transform: scale(1.045); }
    .poster-card.active:hover { transform: translateY(-3px); }
    .empty { position: relative; min-height: 220px; border-color: #34495c; background: linear-gradient(145deg, rgba(26, 42, 54, .4), rgba(12, 18, 25, .32)); }
    .empty::before { content: "⌕"; display: grid; place-items: center; width: 38px; height: 38px; margin-bottom: 9px; border: 1px solid #41657d; border-radius: 50%; color: #75d2fa; font-size: 24px; }
    .empty { align-content: center; justify-items: center; }
    .link-card { border-radius: 10px; border-left: 3px solid #3d9bca; padding: 13px; }
    .link-card:hover { border-color: #579fca; background: #16232d; }
    .pill { background: #277fae; }
    .tag { background: #17232d; }

    .progress-wrap {
      display: none;
      margin: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      overflow: hidden;
      box-shadow: 0 12px 24px var(--shadow);
    }

    .progress-wrap.active {
      display: block;
      position: fixed;
      left: 50%;
      bottom: 14px;
      z-index: 60;
      width: min(1120px, calc(100% - 28px));
      transform: translateX(-50%);
    }

    .progress-info {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      padding: 8px 10px 7px;
      color: var(--muted);
      font-size: 13px;
    }

    .progress-info strong {
      color: var(--text);
      font-size: 13px;
    }

    .progress-track {
      height: 8px;
      background: #090912;
      border-top: 1px solid var(--line);
    }

    .progress-bar {
      width: 0%;
      height: 100%;
      background: linear-gradient(90deg, var(--accent), var(--accent-2));
      transition: width 220ms ease;
    }

    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 18px;
      align-items: start;
    }

    .search-panel {
      position: static;
      grid-column: 1;
    }

    .poster-panel,
    .links-panel {
      grid-column: 1;
    }

    .panel.is-hidden { display: none; }

    .search-panel .panel-body {
      display: grid;
      grid-template-columns: minmax(320px, 1.65fr) minmax(260px, 1fr) 110px;
      gap: 16px;
      align-items: end;
      padding: 16px 17px;
    }
    .search-panel .search-intro { display: none; }
    .search-panel .row,
    .search-panel .field,
    .search-panel .actions { margin: 0; }
    .search-panel .actions { border: 0; padding: 0; }
    .search-panel .actions .btn { min-height: 42px; }

    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: 0 14px 36px rgba(0, 0, 0, .26);
      overflow: hidden;
    }

    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 57px;
      padding: 14px 17px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(90deg, rgba(26, 37, 48, .98), rgba(21, 29, 38, .94));
    }

    .panel-head h2 {
      margin: 0;
      font-size: 15px;
      letter-spacing: .02em;
      text-transform: uppercase;
      font-size: 13px;
    }

    .panel-body {
      padding: 16px;
    }

    .field {
      display: grid;
      gap: 8px;
      margin-bottom: 14px;
    }

    label {
      color: var(--muted);
      font-size: 13px;
    }

    input, select {
      width: 100%;
      min-height: 42px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #0f161e;
      color: var(--text);
      padding: 9px 11px;
      outline: none;
    }

    input:focus, select:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(55, 167, 231, 0.18);
    }

    .row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: end;
    }

    .quality-grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 8px;
    }

    .quality {
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #1b2631;
      color: var(--text);
      cursor: pointer;
    }

    .quality.active {
      border-color: var(--accent);
      background: rgba(55, 167, 231, 0.18);
      color: #d9f4ff;
    }

    .actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 16px;
    }

    .btn {
      min-height: 42px;
      border: 1px solid transparent;
      border-radius: 7px;
      padding: 9px 13px;
      color: #ffffff;
      background: linear-gradient(135deg, #258dcc, #41b5e8);
      box-shadow: 0 6px 16px rgba(37, 141, 204, .2);
      font-weight: 700;
      cursor: pointer;
    }

    .btn.secondary {
      background: #202b36;
      color: var(--text);
      border-color: var(--line);
      font-weight: 600;
    }

    .btn:disabled {
      cursor: wait;
      opacity: 0.62;
    }

    .results {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(132px, 1fr));
      gap: 12px;
      max-height: min(54vh, 560px);
      overflow: auto;
      padding-right: 2px;
    }

    .candidate {
      width: 100%;
      text-align: left;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #111a23;
      color: var(--text);
      padding: 11px;
      cursor: pointer;
    }

    .poster-card {
      padding: 0;
      overflow: hidden;
      display: grid;
      grid-template-rows: auto 38px;
      transition: transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease;
    }

    .poster-card.active {
      border-color: var(--accent-2);
      background: rgba(55, 167, 231, 0.13);
      box-shadow: 0 0 0 2px rgba(99, 214, 255, 0.18), 0 12px 22px rgba(0,0,0,.22);
      transform: translateY(-3px);
    }

    .poster-frame {
      position: relative;
      aspect-ratio: 2 / 3;
      overflow: hidden;
      background: #0b1117;
    }

    .poster {
      width: 100%;
      height: 100%;
      display: block;
      border: 0;
      border-radius: 0;
      background: #0b1117;
      transition: transform 240ms ease;
      object-fit: cover;
    }

    .poster.empty-poster {
      display: grid;
      place-items: center;
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
    }

    .poster-language {
      min-height: 38px;
      display: grid;
      place-items: center;
      padding: 5px 6px;
      border-top: 1px solid var(--line);
      color: var(--text);
      background: #17212b;
      font-size: 12px;
      font-weight: 750;
      line-height: 1.15;
      text-align: center;
      overflow-wrap: anywhere;
    }

    .poster-badge {
      position: absolute;
      top: 7px;
      left: 7px;
      min-width: 24px;
      height: 24px;
      display: grid;
      place-items: center;
      border: 1px solid rgba(255, 255, 255, 0.24);
      border-radius: 999px;
      color: var(--text);
      background: rgba(7, 13, 18, 0.78);
      font-size: 12px;
      font-weight: 800;
    }

    .library-badge {
      position: absolute;
      left: 7px;
      bottom: 7px;
      z-index: 3;
      max-width: calc(100% - 14px);
      border: 1px solid rgba(244, 241, 255, 0.22);
      border-radius: 999px;
      padding: 2px 6px;
      background: rgba(9, 9, 18, 0.82);
      color: var(--muted);
      font-size: 10px;
      font-weight: 800;
      line-height: 1.25;
      text-transform: uppercase;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .library-badge.available {
      border-color: rgba(31, 214, 132, 0.5);
      color: #b8ffd9;
      background: rgba(22, 109, 67, 0.72);
    }

    .library-badge.missing {
      border-color: rgba(255, 180, 72, 0.5);
      color: #ffe0a6;
      background: rgba(116, 73, 12, 0.72);
    }

    .selected-result {
      display: none;
      margin-bottom: 12px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: linear-gradient(135deg, rgba(31, 53, 68, 0.98), rgba(18, 28, 37, 0.98));
      padding: 13px;
    }

    .selected-result.active {
      display: grid;
      gap: 10px;
    }

    .selected-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
    .result-eyebrow { display: block; margin-bottom: 2px; color: var(--accent-2); font-size: 11px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
    .result-title { display: block; font-size: 17px !important; line-height: 1.25; }
    .result-meta { display: flex; flex-wrap: wrap; gap: 6px; }
    .meta-chip, .selected-library-note { display: inline-flex; align-items: center; width: fit-content; border: 1px solid var(--line); border-radius: 999px; padding: 4px 8px; background: rgba(9, 9, 18, .56); color: var(--muted); font-size: 12px; line-height: 1.2; }
    .selected-library-note.available { border-color: rgba(31, 214, 132, .42); color: #b8ffd9; background: rgba(22, 109, 67, .22); }
    .selected-library-note.missing { border-color: rgba(255, 180, 72, .42); color: #ffe0a6; background: rgba(116, 73, 12, .22); }

    .tv-availability { border-top: 1px solid var(--line); padding-top: 10px; }
    .tv-availability summary { display: flex; align-items: center; justify-content: space-between; gap: 10px; cursor: pointer; list-style: none; color: var(--text); font-size: 13px; font-weight: 750; }
    .tv-availability summary::-webkit-details-marker { display: none; }
    .tv-availability summary::after { content: "+"; flex: 0 0 auto; color: var(--accent-2); font-size: 20px; font-weight: 400; line-height: 1; }
    .tv-availability[open] summary::after { content: "−"; }
    .tv-summary-stats { color: var(--muted); font-size: 12px; font-weight: 600; text-align: right; }
    .season-list { display: grid; gap: 7px; margin: 10px 0 0; padding: 0; list-style: none; }
    .season-row { border: 1px solid var(--line); border-radius: 9px; padding: 9px; background: rgba(9, 9, 18, .34); }
    .season-head { display: flex; justify-content: space-between; gap: 8px; align-items: center; }
    .season-head strong { font-size: 13px; }
    .season-state { border-radius: 999px; padding: 2px 6px; font-size: 11px; font-weight: 750; white-space: nowrap; }
    .season-state.complete { color: #b8ffd9; background: rgba(22, 109, 67, .35); }
    .season-state.partial { color: #ffe0a6; background: rgba(116, 73, 12, .35); }
    .season-state.unavailable { color: #d5cfeb; background: rgba(91, 83, 121, .35); }
    .episode-summary { display: grid; gap: 3px; margin-top: 7px; color: var(--muted); font-size: 12px; }
    .episode-summary b { color: #dcd7ec; font-weight: 650; }

    .selected-actions {
      display: grid;
      margin-bottom: 12px;
    }

    .selected-actions.is-hidden { display: none; }

    .selected-result strong {
      font-size: 14px;
      overflow-wrap: anywhere;
    }

    .selected-result span {
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }

    .candidate strong {
      display: block;
      font-size: 14px;
      overflow-wrap: anywhere;
    }

    .candidate span {
      display: block;
      margin-top: 5px;
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }

    .poster-card .poster-badge {
      display: grid;
      margin-top: 0;
      color: var(--text);
      font-size: 12px;
    }

    .poster-card .poster-language {
      display: grid;
      margin-top: 0;
      color: var(--text);
      font-size: 12px;
    }

    .link-list {
      display: grid;
      gap: 8px;
      max-height: min(62vh, 680px);
      overflow: auto;
      padding-right: 2px;
    }

    .filters {
      position: sticky;
      top: 0;
      z-index: 2;
      display: grid;
      gap: 8px;
      margin-bottom: 12px;
      padding-bottom: 8px;
      background: var(--panel);
    }

    .filter-row {
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
    }

    .filter-chip {
      min-height: 32px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 5px 10px;
      background: #11101a;
      color: var(--muted);
      cursor: pointer;
      font-weight: 650;
    }

    .filter-chip.active {
      border-color: var(--accent);
      color: #f7e9ff;
      background: rgba(170, 92, 195, 0.18);
    }

    .link-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #121c25;
      padding: 10px;
    }

    .link-meta {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 6px;
      color: var(--muted);
      font-size: 13px;
    }

    .link-main {
      min-width: 0;
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 6px;
      color: var(--muted);
    }

    .link-actions {
      display: flex;
      gap: 7px;
      flex: 0 0 auto;
    }

    .link-actions .btn {
      min-height: 36px;
      padding: 7px 10px;
    }

    .icon-btn {
      width: 38px;
      min-width: 38px;
      display: inline-grid;
      place-items: center;
      padding: 0;
    }

    .icon-btn svg {
      width: 18px;
      height: 18px;
      stroke: currentColor;
    }

    .toast {
      position: fixed;
      left: 50%;
      bottom: 18px;
      z-index: 80;
      max-width: min(420px, calc(100% - 28px));
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--text);
      background: rgba(33, 31, 51, 0.96);
      box-shadow: 0 14px 30px var(--shadow);
      transform: translate(-50%, 12px);
      opacity: 0;
      pointer-events: none;
      transition: opacity 180ms ease, transform 180ms ease;
    }

    .toast.active {
      opacity: 1;
      transform: translate(-50%, 0);
    }

    .link-variant {
      margin: -1px 0 6px;
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }

    .link-tags {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin: -2px 0 8px;
    }

    .tag {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 7px;
      color: var(--muted);
      font-size: 12px;
      background: #18232d;
    }

    .pill {
      color: #ffffff;
      background: var(--accent-2);
      border-radius: 999px;
      padding: 2px 8px;
      font-weight: 700;
    }

    .url-box {
      width: 100%;
      min-height: 40px;
      max-height: 72px;
      resize: none;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #0d151d;
      color: var(--text);
      padding: 8px;
      overflow-wrap: anywhere;
    }

    .pending-note {
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 8px;
      color: var(--muted);
      background: #0d151d;
      font-size: 12px;
    }

    .empty {
      min-height: 180px;
      display: grid;
      place-items: center;
      border: 1px dashed var(--line);
      border-radius: 8px;
      color: var(--muted);
      text-align: center;
      padding: 18px;
    }

    .error {
      color: #ffd3d3;
      border-color: rgba(255, 107, 107, 0.45);
      background: rgba(255, 107, 107, 0.08);
    }

    @media (max-width: 860px) {
      .layout { grid-template-columns: 1fr; }
      .search-panel,
      .poster-panel,
      .links-panel {
        grid-column: auto;
        position: static;
      }
      .topbar { align-items: flex-start; flex-direction: column; }
      .status { white-space: normal; }
      .progress-info { flex-direction: column; gap: 2px; }
      .link-list { max-height: 70vh; }
      .search-panel .panel-body { display: block; padding: 16px; }
      .search-panel .search-intro { display: block; }
      .search-panel .row, .search-panel .field { margin-bottom: 14px; }
      .search-panel .actions { border-top: 1px solid rgba(158, 175, 191, .13); padding-top: 10px; }
    }

    @media (max-width: 520px) {
      .shell { width: min(100% - 18px, 1120px); padding-top: 14px; }
      .brand p { display: none; }
      .topbar { gap: 8px; }
      .row { grid-template-columns: 1fr; }
      .quality-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .results { grid-template-columns: repeat(3, minmax(0, 1fr)); max-height: 52vh; }
      .btn { width: 100%; }
      .actions { gap: 8px; }
      .link-meta { align-items: stretch; }
      .link-actions { gap: 6px; }
      .link-actions .btn { width: auto; min-width: 68px; }
      .link-actions .icon-btn { width: 36px; min-width: 36px; }
      .panel-body, .panel-head { padding: 12px; }
      .result-title { font-size: 16px !important; }
      .selected-heading { flex-direction: column; gap: 7px; }
      .selected-library-note { max-width: 100%; }
      .tv-availability summary { align-items: flex-start; }
      .tv-summary-stats { max-width: 56%; }
      .results { gap: 8px; }
      .poster-language { min-height: 31px; font-size: 11px; }
      .workspace-bar { overflow: hidden; white-space: nowrap; }
      .workspace-bar .workspace-divider:nth-of-type(n+2), .workspace-bar .workspace-divider:nth-of-type(n+2) + * { display: none; }
    }

    @media (max-width: 520px) {
      .results { display: flex; max-height: none; overflow-x: auto; overflow-y: hidden; padding: 0 2px 8px; scroll-snap-type: x mandatory; }
      .poster-card { flex: 0 0 122px; scroll-snap-align: start; }
      .poster-panel .panel-body { padding: 12px; }
      .link-list { max-height: none; }
      .selected-result { margin-bottom: 10px; }
    }

    /* Dense media-manager layout: information first, long lists only on demand. */
    .topbar { min-height: 58px; margin-bottom: 10px; padding: 8px 0; }
    .brand { gap: 9px; }
    .mark { width: 34px; height: 34px; border-radius: 50%; }
    .brand h1 { font-size: 16px; letter-spacing: .075em; }
    .brand p, .workspace-bar { display: none; }
    .status { font-size: 12px; }
    .layout { gap: 12px; }
    .panel { border-radius: 8px; }
    .search-panel { overflow: visible; }
    .search-panel .panel-head { display: none; }
    .search-panel .panel-body { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 8px; padding: 9px; background: rgba(19, 29, 38, .94); }
    .search-panel .search-intro { display: none; }
    .search-panel .row { display: contents; }
    .search-panel .row .field { min-width: 0; }
    .search-panel label { display: none; }
    .search-panel input { min-height: 40px; border-radius: 4px; }
    .search-panel #searchBtn { min-width: 96px; }
    .search-panel .field:has(.quality-grid) { width: 245px; }
    .search-panel .quality-label { display: none; }
    .quality-grid { grid-template-columns: repeat(5, 1fr); gap: 3px; }
    .quality { min-height: 40px; padding: 4px 7px; border-radius: 4px; font-size: 12px; }
    .search-panel .actions .btn { min-height: 40px; width: 42px; padding: 0; font-size: 0; }
    .search-panel .actions .btn::before { content: "×"; font-size: 24px; font-weight: 400; }
    .panel-head { min-height: 42px; padding: 10px 13px; background: #202020; }
    .panel-head h2 { font-size: 12px; }
    .panel-body { padding: 12px; }
    .poster-panel .panel-body { display: grid; gap: 11px; }
    .selected-result { order: 2; margin: 0; padding: 0; border: 0; border-radius: 7px; background: #111a21; }
    .selected-result.active { display: block; }
    .media-hero { display: grid; grid-template-columns: 86px minmax(0, 1fr); min-height: 140px; overflow: hidden; border: 1px solid #3b5160; border-radius: 7px; background: linear-gradient(100deg, rgba(12,17,22,.97), rgba(23,37,47,.86)); }
    .media-hero-poster { width: 86px; height: 140px; object-fit: cover; background: #0c1319; }
    .media-hero-copy { display: grid; align-content: start; gap: 8px; padding: 12px; min-width: 0; }
    .result-eyebrow { color: #89d9ff; }
    .result-title { font-size: 20px !important; font-weight: 500; }
    .result-meta { gap: 5px; }
    .meta-chip, .selected-library-note { border-radius: 3px; padding: 3px 6px; font-size: 11px; }
    .selected-library-note { justify-self: start; }
    .tv-availability { margin: 0; border-top: 0; padding: 0; }
    .tv-availability > summary { min-height: 42px; padding: 0 12px; border: 1px solid #40515d; border-radius: 5px; background: #2b2b2b; }
    .tv-availability > summary::after { content: "⌄"; color: #d8e1e6; font-size: 22px; }
    .tv-availability[open] > summary::after { content: "⌃"; }
    .season-list { gap: 4px; margin-top: 5px; }
    .season-row { padding: 0; border: 1px solid #46545e; border-radius: 5px; background: #303030; }
    .season-row summary { min-height: 46px; display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 10px; cursor: pointer; list-style: none; }
    .season-row summary::-webkit-details-marker { display: none; }
    .season-row summary::before { content: "▮"; color: #d7d7d7; font-size: 20px; line-height: 1; }
    .season-row .season-head { flex: 1; }
    .season-row .episode-summary { margin: 0; padding: 0 10px 10px 38px; }
    .season-state { border-radius: 3px; color: white !important; }
    .season-state.complete { background: #168a51; }
    .season-state.partial { background: #bc7b12; }
    .season-state.unavailable { background: #65636b; }
    .selected-actions { order: 3; margin: 0; }
    .selected-actions .btn { min-height: 42px; border-radius: 4px; background: #247faa; box-shadow: none; }
    .results { order: 1; grid-template-columns: repeat(auto-fill, minmax(112px, 1fr)); max-height: none; overflow: visible; gap: 8px; }
    .poster-card { grid-template-rows: auto 38px; border-radius: 5px; }
    .poster-language { justify-content: start; padding: 5px 7px; text-align: left; }
    .poster-language::before { content: "Select"; color: #7890a0; font-size: 10px; font-weight: 600; margin-right: 5px; text-transform: uppercase; }
    .links-panel .panel-body { padding: 0; }
    .filters { margin: 0; padding: 9px 12px; border-bottom: 1px solid var(--line); background: #17232d; }
    .link-list { gap: 0; max-height: none; }
    .link-card { border-width: 0 0 1px; border-radius: 0; border-left: 3px solid #3d9bca; padding: 10px 12px; }
    .link-card:last-child { border-bottom: 0; }
    .url-box { min-height: 32px; max-height: 48px; font-size: 12px; }
    @media (max-width: 650px) {
      .shell { width: min(100% - 12px, 1240px); padding-top: 0; }
      .topbar { position: sticky; top: 0; margin: 0 -6px 8px; padding: 9px 6px; background: rgba(15, 15, 15, .97); }
      .brand h1 { font-size: 14px; }
      .status { padding: 4px 7px; }
      .search-panel .panel-body { grid-template-columns: 1fr auto; }
      .search-panel .field:has(.quality-grid) { grid-column: 1 / -1; width: auto; }
      .search-panel #searchBtn { grid-column: 1 / -1; width: 100%; }
      .search-panel .actions { display: none; }
      .search-panel #searchBtn { min-width: 78px; }
      .results { display: flex; overflow-x: auto; overflow-y: hidden; padding: 0 0 3px; scroll-snap-type: x mandatory; }
      .poster-card { flex: 0 0 108px; scroll-snap-align: start; }
      .media-hero { grid-template-columns: 76px minmax(0, 1fr); min-height: 126px; }
      .media-hero-poster { width: 76px; height: 126px; }
      .media-hero-copy { padding: 10px; gap: 6px; }
      .result-title { font-size: 18px !important; }
      .tv-summary-stats { font-size: 11px; }
    }

    /* Library views: deliberately separate from the link-search workspace. */
    .app-nav { display:flex; gap:5px; margin:0 0 10px; overflow-x:auto; scrollbar-width:none; }
    .app-nav button { flex:0 0 auto; border:1px solid var(--line); border-radius:5px; color:var(--muted); background:#17212b; padding:8px 11px; font-weight:700; cursor:pointer; }
    .app-nav button.active { color:#fff; border-color:#8b5cf6; background:#6d3bc4; }
    .library-view { display:none; }
    .library-view.active { display:block; }
    .search-workspace.hidden { display:none; }
    .library-toolbar { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:12px; }
    .library-toolbar h2 { margin:0 auto 0 0; font-size:18px; }
    .admin-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
    .admin-panel { border:1px solid var(--line); border-radius:8px; background:rgba(21,29,38,.94); overflow:hidden; }
    .admin-panel h3 { margin:0; padding:10px 12px; font-size:13px; border-bottom:1px solid var(--line); color:#d8effb; }
    .admin-list { display:grid; gap:0; }
    .admin-row { padding:10px 12px; border-bottom:1px solid rgba(158,175,191,.13); font-size:13px; }
    .admin-row:last-child { border-bottom:0; }
    .admin-row strong,.admin-row small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .admin-row small { color:var(--muted); margin-top:3px; }
    .admin-kpi { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; margin-bottom:12px; }
    .admin-kpi span { padding:10px; border:1px solid var(--line); border-radius:7px; background:rgba(21,29,38,.94); color:var(--muted); font-size:12px; }
    .admin-kpi b { display:block; color:var(--text); font-size:20px; margin-top:2px; }
    .library-stats { display:flex; gap:7px; flex-wrap:wrap; color:var(--muted); font-size:12px; }
    .library-stat { padding:4px 7px; border:1px solid var(--line); border-radius:4px; background:#17212b; }
    .library-filters { display:flex; gap:7px; margin-bottom:12px; overflow-x:auto; padding-bottom:2px; }
    .library-filters input, .library-filters select { min-height:36px; flex:0 0 auto; width:auto; max-width:180px; padding:6px 8px; }
    .library-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:10px; }
    .library-card { min-width:0; overflow:hidden; border:1px solid var(--line); border-radius:7px; background:#151e27; cursor:pointer; text-align:left; color:var(--text); padding:0; }
    .library-card:hover { border-color:#8b5cf6; transform:translateY(-1px); }
    .library-card img, .library-placeholder { width:100%; aspect-ratio:2/3; display:block; object-fit:cover; background:#202630; }
    .library-placeholder { display:grid; place-items:center; color:var(--muted); font-size:12px; }
    .library-card-copy { padding:8px; display:grid; gap:5px; }
    .library-card-title { overflow:hidden; white-space:nowrap; text-overflow:ellipsis; font-size:13px; font-weight:750; }
    .library-card-meta { display:flex; gap:4px; flex-wrap:wrap; color:var(--muted); font-size:11px; }
    .status-badge { display:inline-flex; width:max-content; border-radius:3px; padding:2px 5px; font-size:10px; font-weight:800; letter-spacing:.04em; }
    .status-badge.available,.status-badge.complete { background:#187348; color:#d3ffe7; }.status-badge.partial { background:#a8670d; color:#fff0ce; }.status-badge.missing { background:#9b3036; color:#ffe0e3; }.status-badge.match { background:#545764; color:#e5e7eb; }
    .library-empty { padding:45px 18px; text-align:center; border:1px dashed var(--line); color:var(--muted); border-radius:7px; }
    .library-detail { position:fixed; inset:0; z-index:90; display:none; background:rgba(4,7,11,.82); padding:18px; overflow:auto; }.library-detail.open { display:block; }
    .detail-sheet { width:min(960px,100%); margin:0 auto; border:1px solid var(--line); border-radius:9px; overflow:hidden; background:#121b23; box-shadow:0 24px 80px #000; }
    .detail-hero { position:relative; min-height:260px; display:grid; grid-template-columns:160px 1fr; gap:16px; align-items:end; padding:18px; background:#18232d center/cover; }.detail-hero:before { content:""; position:absolute; inset:0; background:linear-gradient(90deg,rgba(10,15,21,.96),rgba(10,15,21,.58)); }.detail-hero>* { position:relative; }.detail-poster { width:142px; border-radius:5px; box-shadow:0 10px 30px #000; }.detail-title { margin:0; font-size:28px; }.detail-overview { max-width:650px; color:#d0dbe5; }.detail-actions { display:flex; gap:7px; flex-wrap:wrap; }.detail-actions .btn { width:auto; min-height:36px; padding:7px 10px; }
    .detail-body { padding:16px; }.detail-section { margin:0 0 16px; }.detail-section h3 { margin:0 0 8px; font-size:14px; }.file-list, .episode-list { display:grid; gap:6px; }.episode-entry { display:grid; gap:5px; }.file-row,.episode-row { display:flex; align-items:center; justify-content:space-between; gap:8px; padding:8px; border:1px solid var(--line); border-radius:5px; background:#17212b; font-size:12px; }.episode-row.available { border-color:rgba(48,190,108,.7); background:linear-gradient(90deg,rgba(20,119,68,.48),rgba(23,33,43,.94)); }.episode-row.missing { border-color:rgba(231,183,50,.78); background:linear-gradient(90deg,rgba(132,93,13,.48),rgba(23,33,43,.94)); }.episode-status { flex:0 0 auto; border-radius:3px; padding:2px 5px; font-size:10px; font-weight:800; letter-spacing:.04em; }.episode-row.available .episode-status { color:#d3ffe7; background:#187348; }.episode-row.missing .episode-status { color:#fff3bd; background:#9b7012; }.episode-row-actions { display:flex; gap:4px; }.episode-row-actions button { min-height:30px; padding:4px 7px; font-size:11px; }.episode-link-result { margin:0 0 2px; }.episode-link-result:empty { display:none; }.episode-link-result .library-link-list { margin:0; }.link-quality { color:#9be8ff; font-weight:800; letter-spacing:.015em; }
    .import-match { border:1px solid #36557d; border-radius:7px; background:#111d2b; padding:12px; }.import-match h3 { color:#dcecff; }.import-match-copy { margin:0 0 10px; color:var(--muted); font-size:12px; }.match-search { display:flex; gap:7px; }.match-search input { min-width:0; flex:1; }.tmdb-results,.rename-preview { display:grid; gap:7px; margin-top:10px; }.tmdb-choice { display:grid; grid-template-columns:34px 1fr auto; align-items:center; gap:8px; width:100%; min-height:48px; padding:6px; border:1px solid var(--line); border-radius:6px; color:var(--text); background:#17212b; text-align:left; cursor:pointer; }.tmdb-choice:hover { border-color:var(--accent-2); background:#1a2a3c; }.tmdb-choice img,.tmdb-poster-placeholder { width:32px; height:42px; border-radius:3px; object-fit:cover; background:#253446; }.tmdb-choice small { color:var(--muted); }.rename-preview { padding:10px; border:1px solid rgba(96,165,250,.4); border-radius:6px; background:#0d1722; }.rename-preview strong { font-size:12px; }.rename-move { display:grid; grid-template-columns:1fr auto 1fr; gap:7px; align-items:center; color:#c8d8e9; font-size:11px; word-break:break-word; }.rename-move b { color:#7db5ff; }.rename-confirm { display:flex; gap:7px; align-items:flex-start; margin-top:4px; color:#f5d591; font-size:12px; }.rename-confirm input { width:auto; min-height:18px; margin-top:1px; }.rename-preview .btn { justify-self:start; }
    .season-accordion { border:1px solid var(--line); border-radius:5px; background:#17212b; margin-bottom:6px; }.season-accordion summary { padding:10px; cursor:pointer; font-weight:700; }.season-accordion .episode-list { padding:0 9px 9px; }.season-zip-tools { display:grid; gap:6px; padding:0 9px 9px; border-bottom:1px solid var(--line); }.season-zip-find { justify-self:start; min-height:32px; padding:5px 8px; font-size:11px; }.season-zip-result:empty { display:none; }.season-zip-result .library-link-list { margin:0; }
    .back-to-top { position:fixed; right:16px; bottom:72px; z-index:35; display:none; border:0; border-radius:50%; width:42px; height:42px; color:#fff; background:#6d3bc4; box-shadow:0 6px 16px #000; }.back-to-top.visible { display:block; }
    .bottom-nav { display:none; }
    @media (max-width:650px) { .app-nav { display:none; }.bottom-nav { position:fixed; display:grid; grid-template-columns:repeat(4,1fr); gap:1px; left:0; right:0; bottom:0; z-index:40; padding:5px; background:#121923; border-top:1px solid var(--line); }.bottom-nav button { border:0; background:transparent; color:var(--muted); padding:7px 2px; font-size:11px; }.bottom-nav button.active { color:#c6a6ff; }.shell { padding-bottom:65px; }.library-grid { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }.library-toolbar h2 { flex-basis:100%; }.library-filters { margin-left:-2px; margin-right:-2px; }.detail-hero { grid-template-columns:92px 1fr; min-height:220px; padding:12px; gap:11px; }.detail-poster { width:92px; }.detail-title { font-size:21px; }.detail-overview { font-size:13px; }.library-detail { padding:0; }.detail-sheet { min-height:100%; border-radius:0; }.file-row,.episode-row { align-items:flex-start; flex-direction:column; }.episode-row-actions { width:100%; }.episode-row-actions .btn { flex:1; }.back-to-top { bottom:68px; }.admin-grid { grid-template-columns:1fr; }.admin-kpi { grid-template-columns:repeat(2,1fr); }.match-search { flex-direction:column; }.match-search .btn { width:100%; }.tmdb-choice { grid-template-columns:34px 1fr; }.tmdb-choice > span:last-child { grid-column:2; }.rename-move { grid-template-columns:1fr; gap:3px; }.rename-move b { display:none; } }

    /* Product refresh: a quieter, task-first workspace rather than a media wallpaper. */
    :root { --bg: #0b1018; --panel: #111923; --panel-2: #192434; --line: #27364a; --text: #edf3fb; --muted: #9babc0; --accent: #3b82f6; --accent-2: #60a5fa; }
    body { background: var(--bg); }
    .wallpaper-bg { display: none; }
    .shell { width: min(1360px, calc(100% - 48px)); padding: 0 0 40px; }
    .topbar { min-height: 64px; margin: 0 -24px 16px; padding: 0 24px; background: rgba(11,16,24,.92); border-bottom-color: rgba(155,171,192,.18); }
    .mark { width: 32px; height: 32px; border-radius: 8px; border-color: #315a92; box-shadow: none; }
    .brand h1 { font-size: 15px; letter-spacing: .06em; }
    .status { border-color: #294461; background: #101c2a; color: #b9c9dd; }
    .app-nav { gap: 4px; margin-bottom: 16px; padding: 4px; width: max-content; max-width: 100%; border: 1px solid var(--line); border-radius: 9px; background: #101721; }
    .app-nav button { border: 0; border-radius: 6px; background: transparent; padding: 7px 10px; color: #a8b8cb; }
    .app-nav button:hover { background: #1a2737; color: #fff; }
    .app-nav button.active { border: 0; background: #243b5a; color: #f5f9ff; }
    .layout { gap: 16px; }
    .panel { border-radius: 10px; box-shadow: 0 10px 30px rgba(0,0,0,.18); }
    .search-panel .panel-body { grid-template-columns: minmax(300px, 1fr) 210px 112px 230px 40px; gap: 10px; padding: 12px; border-radius: 10px; }
    .search-panel .field:has(.quality-grid) { width: 230px; }
    .search-panel #searchBtn { min-width: 112px; }
    .search-panel .actions { display: block; width: 40px; }
    .search-panel .actions .btn { width: 40px; min-width: 40px; background: transparent; border-color: transparent; color: #9babc0; box-shadow: none; }
    .search-panel .actions .btn:hover { background: #1a2737; color: #fff; }
    input, select { border-radius: 6px; border-color: #314156; background: #0c141e; }
    .quality-grid { gap: 4px; padding: 3px; border: 1px solid #314156; border-radius: 7px; background: #0c141e; }
    .quality { min-height: 34px; border: 0; border-radius: 4px; background: transparent; color: #aab9ca; }
    .quality:hover { background: #1a2737; color: #fff; }
    .quality.active { background: #294a72; color: #fff; }
    .btn { border-radius: 6px; background: #3182ce; box-shadow: none; }
    .btn:hover { background: #4295e1; }
    .btn.secondary { background: #172434; }
    .welcome-panel { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(260px, .75fr); gap: 24px; align-items: center; min-height: 280px; padding: 34px; border: 1px solid var(--line); border-radius: 10px; background: linear-gradient(135deg, #111c29, #0d141e); }
    .welcome-panel.is-hidden { display: none; }
    .welcome-copy h2 { margin: 0 0 8px; font-size: 24px; letter-spacing: -.025em; }
    .welcome-copy p { max-width: 540px; margin: 0; color: var(--muted); }
    .workflow-steps { display: grid; gap: 10px; }
    .workflow-step { display: grid; grid-template-columns: 28px 1fr; gap: 10px; align-items: center; padding: 10px; border: 1px solid #2a3a4e; border-radius: 7px; background: rgba(13,20,30,.72); color: #c5d2e1; font-size: 13px; }
    .workflow-step b { display: grid; width: 28px; height: 28px; place-items: center; border-radius: 6px; background: #243b5a; color: #93c5fd; }
    .poster-panel .panel-head, .links-panel .panel-head { background: #111923; }
    .poster-panel .panel-body { padding: 16px; }
    .media-hero { border-color: #31465e; }
    .link-card:hover { background: #152130; }
    .library-toolbar { padding: 2px 0; }
    .library-filters { padding: 8px; border: 1px solid var(--line); border-radius: 8px; background: #111923; }
    .library-card { border-radius: 8px; }
    .source-list { display: grid; gap: 10px; }
    .source-card { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 16px; align-items: center; padding: 16px; border: 1px solid var(--line); border-radius: 9px; background: #111923; }
    .source-card:hover { border-color: #385474; }
    .source-title { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
    .source-title strong { font-size: 15px; }
    .source-state { border-radius: 999px; padding: 3px 7px; background: rgba(30, 150, 90, .16); color: #91e6b7; font-size: 11px; font-weight: 750; }
    .source-state.off { background: rgba(155, 171, 192, .12); color: #b5c0cf; }
    .source-details { display: flex; flex-wrap: wrap; gap: 5px 14px; color: var(--muted); font-size: 12px; }
    .source-details span { white-space: nowrap; }
    .source-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
    .source-actions .btn { width: auto; min-height: 34px; padding: 6px 9px; font-size: 12px; }
    .source-actions .source-delete { border-color: rgba(248,113,113,.35); color: #fecaca; }
    @media (max-width: 760px) {
      .shell { width: min(100% - 24px, 1360px); padding-bottom: 76px; }
      .topbar { min-height: 56px; margin: 0 -12px 12px; padding: 0 12px; flex-direction: row; align-items: center; }
      .status { font-size: 11px; }
      .app-nav { display: none; }
      .search-panel .panel-body { grid-template-columns: 1fr auto; padding: 10px; }
      .search-panel .field:has(.quality-grid), .search-panel #searchBtn { grid-column: 1 / -1; width: 100%; }
      .search-panel #searchBtn { min-height: 44px; }
      .search-panel .actions { display: none; }
      .quality-grid { grid-template-columns: repeat(5, 1fr); }
      .welcome-panel { grid-template-columns: 1fr; min-height: 0; padding: 22px 18px; gap: 18px; }
      .welcome-copy h2 { font-size: 21px; }
      .bottom-nav { padding: 6px 8px calc(6px + env(safe-area-inset-bottom)); background: rgba(13,20,30,.97); }
      .bottom-nav button { min-height: 40px; border-radius: 6px; }
      .bottom-nav button.active { background: #1b3657; color: #dbeafe; }
      .source-card { grid-template-columns: 1fr; gap: 12px; padding: 14px; }
      .source-actions { justify-content: flex-start; }
    }
  </style>
</head>
<body>
  <div class="wallpaper-bg" id="wallpaperBg" aria-hidden="true"></div>
  <div class="toast" id="toast" role="status" aria-live="polite"></div>
  <main class="shell">
    <div class="topbar">
      <div class="brand">
        <div class="mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M10 13a5 5 0 0 0 7.07 0l2.12-2.12a5 5 0 0 0-7.07-7.07L11 4.93"/>
            <path d="M14 11a5 5 0 0 0-7.07 0L4.81 13.12a5 5 0 0 0 7.07 7.07L13 19.07"/>
          </svg>
        </div>
        <div>
          <h1>FREEMIUM INDEX</h1>
          <p>Quality, size, source, final link</p>
        </div>
      </div>
      <div class="status" id="status">Ready</div>
    </div>
    <div class="workspace-bar" aria-label="Application status">
      <span class="workspace-dot"></span><strong>MEDIA DISCOVERY</strong><span class="workspace-divider"></span>
      <span>Search → choose → retrieve</span><span class="workspace-divider"></span>
      <span>Jellyfin library aware</span>
    </div>
    <div class="progress-wrap" id="progressWrap" aria-live="polite">
      <div class="progress-info">
        <strong id="progressLabel">Working</strong>
        <span id="progressMeta">0% • estimating...</span>
      </div>
      <div class="progress-track">
        <div class="progress-bar" id="progressBar"></div>
      </div>
    </div>

    <nav class="app-nav" aria-label="Main navigation" id="appNav">
      <button data-view="search" class="active">Search</button><button data-view="movies">Movies</button><button data-view="tv">TV Shows</button><button data-view="missing">Missing</button><button data-view="recent">Recently Added</button><button data-view="admin">Admin</button><button data-view="sources">Sources</button>
    </nav>

    <section class="layout search-workspace" id="searchWorkspace">
      <div class="panel search-panel">
        <div class="panel-head">
          <h2>Search</h2>
          <span class="head-note">New lookup</span>
        </div>
        <div class="panel-body">
          <p class="search-intro">Find a title, choose its <strong>exact release</strong>, then retrieve the final link.</p>
          <div class="row">
            <div class="field">
              <label for="query">Name</label>
              <input id="query" autocomplete="off" placeholder="Type name">
            </div>
            <select id="sourceSelector" aria-label="Source"><option value="all">All Sources</option><option value="existing">Existing Working Site</option></select><button class="btn" id="searchBtn">Search</button>
          </div>

          <div class="field">
            <label class="quality-label">Quality <em>Preferred: <span id="selectedQualityLabel">1080p</span></em></label>
            <div class="quality-grid" id="qualityGrid">
              <button class="quality" data-quality="480p">480p</button>
              <button class="quality" data-quality="720p">720p</button>
              <button class="quality active" data-quality="1080p">1080p</button>
              <button class="quality" data-quality="2160p">4K</button>
              <button class="quality" data-quality="all">All</button>
            </div>
          </div>

          <div class="actions">
            <button class="btn secondary" id="clearBtn">Clear</button>
          </div>
        </div>
      </div>

      <div class="panel poster-panel is-hidden" id="posterPanel">
        <div class="panel-head">
          <h2>Matches</h2>
          <span class="head-note">Target <span id="selectedQuality">1080p</span></span>
        </div>
        <div class="panel-body">
          <div class="selected-result" id="selectedResult"></div>
          <div class="selected-actions is-hidden">
            <button class="btn" id="findBtn" disabled>Find Links</button>
          </div>
          <div class="results" id="candidates">
            <div class="empty">Search for a title to load matching releases.</div>
          </div>
        </div>
      </div>

      <div class="panel links-panel is-hidden" id="linksPanel">
        <div class="panel-head">
          <h2>Delivery Links</h2>
          <span class="head-note">Copy, open, or send</span>
        </div>
        <div class="panel-body">
          <div class="filters" id="linkFilters"></div>
          <div class="link-list" id="links">
            <div class="empty">Choose a release, then retrieve the available links.</div>
          </div>
        </div>
      </div>

      <section class="welcome-panel" id="welcomePanel" aria-labelledby="welcomeTitle">
        <div class="welcome-copy">
          <h2 id="welcomeTitle">Find the right release, quickly.</h2>
          <p>Search across your configured sources, choose the exact title, then retrieve available links. Your preferred quality is already selected.</p>
        </div>
        <div class="workflow-steps" aria-label="How search works">
          <div class="workflow-step"><b>1</b><span><strong>Search</strong><br>Start with a movie or series title.</span></div>
          <div class="workflow-step"><b>2</b><span><strong>Choose</strong><br>Confirm the language and release.</span></div>
          <div class="workflow-step"><b>3</b><span><strong>Retrieve</strong><br>Get the verified links that are available.</span></div>
        </div>
      </section>

    </section>
    <section class="library-view" id="libraryView" aria-live="polite"></section>
  </main>
  <div class="library-detail" id="libraryDetail" aria-modal="true" role="dialog"></div>
  <button class="back-to-top" id="backToTop" aria-label="Back to top">↑</button>
  <nav class="bottom-nav" aria-label="Mobile navigation" id="bottomNav"><button data-view="search">Search</button><button data-view="movies">Movies</button><button data-view="tv">TV Shows</button><button data-view="missing">Missing</button></nav>

  <script>
    const state = {
      quality: "1080p",
      candidates: [],
      linkMessage: "",
      links: [],
      selected: -1,
      busy: false,
      seasonFilter: "all",
      typeFilter: "all",
      showLinks: false,
      progressTimer: null,
      progressStartedAt: 0,
      progressEstimateMs: 0,
      libraryDetail: null,
      libraryDetailId: "",
      episodeTarget: null,
      episodeFallback: false,
      hasSearched: false,
    };

    const $ = (id) => document.getElementById(id);
    const statusEl = $("status");
    const queryEl = $("query");
    const candidatesEl = $("candidates");
    const selectedResultEl = $("selectedResult");
    const linksEl = $("links");
    const linkFiltersEl = $("linkFilters");
    const progressWrapEl = $("progressWrap");
    const progressBarEl = $("progressBar");
    const progressLabelEl = $("progressLabel");
    const progressMetaEl = $("progressMeta");
    const findBtn = $("findBtn");
    const searchBtn = $("searchBtn");
    const sourceSelector = $("sourceSelector");
    const posterPanelEl = $("posterPanel");
    const welcomePanelEl = $("welcomePanel");
    const linksPanelEl = $("linksPanel");
    const selectedActionsEl = findBtn.closest(".selected-actions");
    const wallpaperEl = $("wallpaperBg");
    const toastEl = $("toast");
    let toastTimer = null;

    function setStatus(text, isError = false) {
      statusEl.textContent = text;
      statusEl.classList.toggle("error", isError);
    }

    function showToast(text) {
      if (!toastEl) return;
      toastEl.textContent = text;
      toastEl.classList.add("active");
      if (toastTimer) clearTimeout(toastTimer);
      toastTimer = setTimeout(() => {
        toastEl.classList.remove("active");
      }, 2800);
    }

    function setBusy(value) {
      state.busy = value;
      searchBtn.disabled = value;
      findBtn.disabled = value || state.selected < 0;
    }

    function renderProgress(percent, label, meta) {
      progressWrapEl.classList.add("active");
      progressBarEl.style.width = `${Math.max(0, Math.min(100, percent))}%`;
      progressLabelEl.textContent = label;
      progressMetaEl.textContent = meta;
    }

    function startProgress(label, estimatedSeconds) {
      stopProgress(false);
      state.progressStartedAt = Date.now();
      state.progressEstimateMs = Math.max(1, estimatedSeconds) * 1000;
      renderProgress(0, label, "0% • estimating...");
      state.progressTimer = setInterval(() => {
        const elapsedMs = Date.now() - state.progressStartedAt;
        const ratio = Math.min(0.95, elapsedMs / state.progressEstimateMs);
        const percent = Math.max(1, Math.round(ratio * 95));
        const leftMs = Math.max(0, state.progressEstimateMs - elapsedMs);
        const leftSec = Math.ceil(leftMs / 1000);
        const elapsedSec = Math.floor(elapsedMs / 1000);
        const leftText = ratio >= 0.95 ? "almost done" : `~${leftSec}s left`;
        renderProgress(percent, label, `${percent}% • ${leftText} • ${elapsedSec}s elapsed`);
      }, 300);
    }

    function stopProgress(done = true, label = "Done") {
      if (state.progressTimer) {
        clearInterval(state.progressTimer);
        state.progressTimer = null;
      }
      if (done) {
        renderProgress(100, label, "100% • complete");
        setTimeout(() => {
          if (!state.progressTimer) progressWrapEl.classList.remove("active");
        }, 1200);
      }
    }

    function failProgress(label = "Stopped") {
      if (state.progressTimer) {
        clearInterval(state.progressTimer);
        state.progressTimer = null;
      }
      const elapsedSec = state.progressStartedAt ? Math.floor((Date.now() - state.progressStartedAt) / 1000) : 0;
      renderProgress(100, label, `stopped after ${elapsedSec}s`);
      setTimeout(() => {
        if (!state.progressTimer) progressWrapEl.classList.remove("active");
      }, 1800);
    }

    async function loadWallpapers() {
      if (!wallpaperEl) return;
      try {
        const body = await api("/api/wallpapers");
        const images = (body.images || []).filter(Boolean);
        if (!images.length) return;
        let index = 0;
        const apply = () => {
          wallpaperEl.style.backgroundImage = `url("${images[index % images.length]}")`;
          index += 1;
        };
        apply();
        setInterval(apply, 9000);
      } catch (_) {}
    }

    function escapeHtml(value) {
      return String(value || "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[char]));
    }

    function episodeTargetFromQuery(value) {
      const text = String(value || "");
      const patterns = [/\bS(?:eason)?\s*0*(\d{1,2})\s*[-_. ]*E(?:pisode)?\s*0*(\d{1,3})\b/i, /\b(\d{1,2})\s*[xX]\s*0*(\d{1,3})\b/, /\bSeason\s*0*(\d{1,2})\s*Episode\s*0*(\d{1,3})\b/i];
      for (const pattern of patterns) { const match = text.match(pattern); if (match) return {season:Number(match[1]), episode:Number(match[2])}; }
      return null;
    }

    function downloadIconSvg() {
      return `
        <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
          <path d="M7 10l5 5 5-5"></path>
          <path d="M12 15V3"></path>
        </svg>
      `;
    }

    function telegramIconSvg() {
      return `
        <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M21.5 4.5 3 11.8l7.2 2.1 2.1 6.8 3.1-4.5 5.3-9.9c.5-.9-.2-2-1.2-1.8Z"></path>
          <path d="m10.2 13.9 5.9-4.7"></path>
        </svg>
      `;
    }

    const accessToken = new URLSearchParams(location.search).get("token") || localStorage.getItem("accessToken") || "";
    if (accessToken) localStorage.setItem("accessToken", accessToken);
    let adminPassword = sessionStorage.getItem("adminPassword") || "";

    async function api(path, options = {}) {
      const appBase = location.pathname.startsWith("/index") ? "/index" : "";
      const response = await fetch(`${appBase}${path}`, {
        ...options,
        headers: {
          "content-type": "application/json",
          "x-app-token": accessToken,
          "x-admin-password": adminPassword,
          ...(options.headers || {}),
        },
      });
      const isSuccess = response.status >= 200 && response.status < 300;
      const contentType = response.headers.get("content-type") || "";
      let body;
      if (contentType.includes("application/json")) {
        body = await response.json().catch(() => ({success:false, message:"Invalid response format"}));
      } else {
        body = await response.text();
      }
      if (!isSuccess) {
        const message = typeof body === "object" ? (body.message || body.error) : body;
        throw new Error(message || `Request failed: ${response.status}`);
      }
      if (typeof body !== "object" || body === null) {
        throw new Error("Invalid response format");
      }
      if (body.success === false || body.ok === false) {
        throw new Error(body.message || body.error || "Request failed");
      }
      if ((location.hostname === "localhost" || location.hostname === "127.0.0.1") && body) {
        const safeDebug = {...body};
        if (typeof safeDebug.html === "string") safeDebug.html = `[redacted HTML: ${safeDebug.html.length} chars]`;
        console.debug("API response", response.status, safeDebug);
      }
      body.apiHttpStatus = response.status;
      return body;
    }
    async function loadSearchSources() { try { const body=await api("/api/search/sources"); sourceSelector.innerHTML=(body.sources||[]).map(s=>`<option value="${escapeHtml(s.id)}">${escapeHtml(s.name)}</option>`).join(""); } catch (_) {} }
    loadSearchSources();

    function candidateLanguage(candidate) {
      if (candidate?.language) return candidate.language;
      const text = String(candidate?.title || "");
      const lower = text.toLowerCase();
      const languages = [
        ["Hindi", /\\bhindi\\b/i],
        ["English", /\\benglish\\b/i],
        ["Tamil", /\\btamil\\b/i],
        ["Telugu", /\\btelugu\\b/i],
        ["Malayalam", /\\bmalayalam\\b/i],
        ["Kannada", /\\bkannada\\b/i],
        ["Punjabi", /\\bpunjabi\\b/i],
        ["Bengali", /\\bbengali\\b/i],
        ["Marathi", /\\bmarathi\\b/i],
      ].filter(([, pattern]) => pattern.test(text)).map(([name]) => name);
      if (/\\bmulti[\\s-]*audio\\b/i.test(text)) return languages.length ? languages.join("-") : "Multi Audio";
      if (/\\bdual[\\s-]*audio\\b/i.test(text) || /\\bdualaudio\\b/i.test(text)) return languages.length ? languages.join("-") : "Hindi-English";
      return languages.length ? languages.join("-") : "Language unknown";
    }

    function libraryBadge(candidate) {
      const status = candidate?.library_status || "";
      if (status !== "available" && status !== "missing") return "";
      const label = status === "available" ? "Available" : "Not available";
      const title = candidate?.library_path ? `${label}: ${candidate.library_path}` : label;
      return `<span class="library-badge ${status}" title="${escapeHtml(title)}">${label}</span>`;
    }

    function episodeRanges(values) {
      const numbers = [...new Set((values || []).map(Number).filter(Number.isFinite))].sort((a, b) => a - b);
      const labels = [];
      for (let index = 0; index < numbers.length;) {
        const start = numbers[index];
        let end = start;
        while (numbers[index + 1] === end + 1) end = numbers[++index];
        const episode = (number) => `E${String(number).padStart(2, "0")}`;
        labels.push(start === end ? episode(start) : `${episode(start)}–${episode(end)}`);
        index += 1;
      }
      return labels.join(", ");
    }

    function tvAvailabilityMarkup(detail) {
      if (!detail) return "";
      if (detail.loading) return '<span>Loading season and episode availability…</span>';
      if (detail.error) return `<span>${escapeHtml(detail.error)}</span>`;
      const summary = detail.summary || {};
      const seasonText = summary.expected_seasons
        ? `${summary.available_seasons || 0}/${summary.expected_seasons} seasons available`
        : `${summary.available_seasons || 0} seasons available`;
      const episodeText = summary.expected_episodes
        ? `${summary.available_episodes || 0}/${summary.expected_episodes} episodes available`
        : `${summary.available_episodes || 0} episodes found`;
      const seasons = (detail.seasons || []).map((season) => {
        const expected = Number(season.expected_episodes || 0);
        const available = Number(season.available_count || 0);
        const state = expected && available >= expected ? "Complete" : available ? "Partial" : "Not available";
        const stateClass = state === "Complete" ? "complete" : state === "Partial" ? "partial" : "unavailable";
        const availableList = episodeRanges(season.available_episodes);
        const missingList = expected ? episodeRanges(season.missing_episodes) : "Official episode count unavailable";
        return `<li><details class="season-row"><summary><span class="season-head"><strong>Season ${escapeHtml(String(season.season))}</strong></span><span class="season-state ${stateClass}">${available}${expected ? ` / ${expected}` : ""}</span></summary><div class="episode-summary"><span><b>Available</b> ${escapeHtml(availableList || "None")}</span>${expected ? `<span><b>Missing</b> ${escapeHtml(missingList || "None")}</span>` : ""}</div></details></li>`;
      }).join("");
      return `<details class="tv-availability"><summary><span>Episode availability</span><span class="tv-summary-stats">${escapeHtml(seasonText)} · ${escapeHtml(episodeText)}</span></summary>${seasons ? `<ul class="season-list">${seasons}</ul>` : "<span>No season data found.</span>"}</details>`;
    }

    async function loadTvAvailability(candidate) {
      const jellyfinId = String(candidate?.jellyfin_id || "");
      if (candidate?.library_status !== "available" || !jellyfinId) return;
      state.libraryDetailId = jellyfinId;
      state.libraryDetail = { loading: true };
      renderSelectedCandidate();
      try {
        const body = await api(`/api/library-details?id=${encodeURIComponent(jellyfinId)}`);
        if (state.libraryDetailId !== jellyfinId) return;
        state.libraryDetail = body.detail || { error: "TV availability unavailable." };
      } catch (error) {
        if (state.libraryDetailId !== jellyfinId) return;
        state.libraryDetail = { error: error.message || "TV availability unavailable." };
      }
      renderSelectedCandidate();
    }

    function renderSelectedCandidate() {
      const candidate = state.candidates[state.selected];
      if (!candidate) {
        selectedResultEl.classList.remove("active");
        selectedResultEl.innerHTML = "";
        selectedActionsEl.classList.add("is-hidden");
        return;
      }
      selectedResultEl.classList.add("active");
      selectedActionsEl.classList.remove("is-hidden");
      const libraryLabel = candidate.library_status === "available"
        ? "Available in Jellyfin"
        : candidate.library_status === "missing"
          ? "Not in Jellyfin"
          : "";
      const isTvCandidate = candidate.library_type === "Series" || /\\b(season|web\\s*series|series|episode|s\\d{1,2})\\b/i.test(candidate.title || "");
      const tvDetail = isTvCandidate ? tvAvailabilityMarkup(state.libraryDetail) : "";
      selectedResultEl.innerHTML = `
        <article class="media-hero">
          ${candidate.poster_url
            ? `<img class="media-hero-poster" src="${escapeHtml(candidate.poster_url)}" alt="">`
            : `<div class="media-hero-poster" aria-hidden="true"></div>`}
          <div class="media-hero-copy">
            <div><span class="result-eyebrow">Selected result ${state.selected + 1}</span><strong class="result-title">${escapeHtml(candidate.title || "Untitled result")}</strong></div>
            <div class="result-meta"><span class="meta-chip">${escapeHtml(candidateLanguage(candidate))}</span><span class="meta-chip">${escapeHtml(candidate.source_name||"Existing Site")}</span>${candidate.quality?`<span class="meta-chip">${escapeHtml(candidate.quality)}</span>`:""}${isTvCandidate ? '<span class="meta-chip">TV Series</span>' : '<span class="meta-chip">Movie</span>'}</div>
            ${libraryLabel ? `<span class="selected-library-note ${escapeHtml(candidate.library_status)}">${escapeHtml(libraryLabel)}</span>` : ""}
          </div>
        </article>
        ${tvDetail}
      `;
    }

    function renderCandidates() {
      findBtn.disabled = state.busy || state.selected < 0;
      findBtn.textContent = state.episodeTarget ? "Find Episode Link" : "Find Links";
      renderSelectedCandidate();
      if (!state.candidates.length) {
        posterPanelEl.classList.add("is-hidden");
        welcomePanelEl.classList.remove("is-hidden");
        if (state.hasSearched) {
          welcomePanelEl.innerHTML = `<div class="welcome-copy"><h2>No matching releases.</h2><p>Try a shorter title, a different spelling, or choose another source.</p></div><div class="workflow-steps"><div class="workflow-step"><b>↗</b><span><strong>Try another query</strong><br>Remove year, language, or extra words.</span></div><div class="workflow-step"><b>⌘</b><span><strong>Change source</strong><br>Some titles are only available on one source.</span></div></div>`;
        }
        return;
      }
      welcomePanelEl.classList.add("is-hidden");
      posterPanelEl.classList.remove("is-hidden");
      candidatesEl.innerHTML = state.candidates.map((candidate, index) => `
        <button class="candidate poster-card ${index === state.selected ? "active" : ""}" data-index="${index}" title="${escapeHtml(candidate.title)}">
          <span class="poster-frame">
            ${candidate.poster_url
              ? `<img class="poster" src="${escapeHtml(candidate.poster_url)}" alt="" loading="lazy">`
              : `<span class="poster empty-poster">NO IMG</span>`}
            <span class="poster-badge">${index + 1}</span>
            ${libraryBadge(candidate)}
          </span>
          <span class="poster-language">${escapeHtml(candidateLanguage(candidate))}</span>
          <span class="poster-language">${escapeHtml(candidate.source_name || "Existing Site")}</span>
        </button>
      `).join("");
      candidatesEl.querySelectorAll(".candidate").forEach((button) => {
        button.addEventListener("click", () => {
          state.selected = Number(button.dataset.index);
          state.links = [];
          state.showLinks = false;
          state.seasonFilter = "all";
          state.typeFilter = "all";
          state.libraryDetail = null;
          state.libraryDetailId = "";
          linkFiltersEl.innerHTML = "";
          linksEl.innerHTML = '<div class="empty">Find links will appear here.</div>';
          renderLinks([]);
          renderCandidates();
          loadTvAvailability(state.candidates[state.selected]);
          setStatus(`Selected result ${state.selected + 1}`);
        });
      });
    }

    function linkType(item) {
      const kind = String(item.kind || "");
      if (kind === "Season Zip") return "zip";
      if (kind.startsWith("Episode")) return "episode";
      return "file";
    }

    function renderLinkFilters(items) {
      const seasons = [...new Set(items.map((item) => item.season).filter(Boolean))];
      const types = [...new Set(items.map(linkType).filter((type) => type !== "file" || items.length > 1))];
      if (!seasons.length && types.length <= 1) {
        linkFiltersEl.innerHTML = "";
        return;
      }
      const seasonButtons = seasons.length ? `
        <div class="filter-row">
          ${["all", ...seasons].map((season) => `
            <button class="filter-chip ${state.seasonFilter === season ? "active" : ""}" data-filter="season" data-value="${escapeHtml(season)}">
              ${escapeHtml(season === "all" ? "All Seasons" : season)}
            </button>
          `).join("")}
        </div>
      ` : "";
      const typeLabels = { all: "All Types", zip: "Season Zip", episode: "Episodes", file: "Files" };
      const typeValues = ["all", ...types.filter((value, index, list) => list.indexOf(value) === index)];
      const typeButtons = types.length > 1 ? `
        <div class="filter-row">
          ${typeValues.map((type) => `
            <button class="filter-chip ${state.typeFilter === type ? "active" : ""}" data-filter="type" data-value="${escapeHtml(type)}">
              ${escapeHtml(typeLabels[type] || type)}
            </button>
          `).join("")}
        </div>
      ` : "";
      linkFiltersEl.innerHTML = seasonButtons + typeButtons;
      linkFiltersEl.querySelectorAll(".filter-chip").forEach((button) => {
        button.addEventListener("click", () => {
          if (button.dataset.filter === "season") state.seasonFilter = button.dataset.value || "all";
          if (button.dataset.filter === "type") state.typeFilter = button.dataset.value || "all";
          renderLinks(state.links);
        });
      });
    }

    function filteredLinks(items) {
      return items.filter((item) => {
        const seasonOk = state.seasonFilter === "all" || item.season === state.seasonFilter;
        const typeOk = state.typeFilter === "all" || linkType(item) === state.typeFilter;
        return seasonOk && typeOk;
      });
    }

    function renderLinks(items) {
      linksPanelEl.classList.toggle("is-hidden", !state.showLinks);
      if (!state.showLinks) return;
      renderLinkFilters(items);
      const visibleItems = filteredLinks(items);
      if (!items.length) {
        linkFiltersEl.innerHTML = "";
        linksEl.innerHTML = `<div class="empty">${escapeHtml(state.linkMessage || "Direct final link not found.")}</div>`;
        return;
      }
      if (!visibleItems.length) {
        linksEl.innerHTML = '<div class="empty">No links for this filter.</div>';
        return;
      }
      const targetNote = state.episodeTarget
        ? (state.episodeFallback
          ? `<div class="pending-note">S${String(state.episodeTarget.season).padStart(2,"0")}E${String(state.episodeTarget.episode).padStart(2,"0")} ka individual link source par nahi mila. Neeche show/season fallback links hain.</div>`
          : `<div class="pending-note">Direct episode result: S${String(state.episodeTarget.season).padStart(2,"0")}E${String(state.episodeTarget.episode).padStart(2,"0")}</div>`)
        : "";
      linksEl.innerHTML = targetNote + visibleItems.map((item, index) => {
        const pending = Boolean(item.pending);
        const fileName = String(item.filename || "");
        const displayUrl = pending ? "Direct link not loaded yet." : item.url;
        const buttonText = pending ? "Get Link" : "Copy";
        const itemKey = item.resolve_url || item.url || `${item.quality}-${item.variant}-${index}`;
        return `
        <div class="link-card">
          <div class="link-meta">
            <span class="link-main">
              <span>${index + 1}.</span>
              <span class="pill">${escapeHtml(item.quality)}</span>
              <span class="link-size">${escapeHtml(item.size)}</span>
            </span>
            <span class="link-actions">
              <button
                class="btn secondary copy"
                data-key="${escapeHtml(itemKey)}"
                data-url="${escapeHtml(item.url)}"
                data-resolve="${escapeHtml(item.resolve_url || "")}"
                data-pending="${pending ? "1" : "0"}"
              >${buttonText}</button>
              <button
                class="btn secondary open-link icon-btn"
                data-key="${escapeHtml(itemKey)}"
                data-url="${escapeHtml(item.url)}"
                data-resolve="${escapeHtml(item.resolve_url || "")}"
                data-pending="${pending ? "1" : "0"}"
                aria-label="Open direct link"
                title="Open direct link"
              >
                ${downloadIconSvg()}
              </button>
              <button
                class="btn secondary telegram-link icon-btn"
                data-key="${escapeHtml(itemKey)}"
                data-url="${escapeHtml(item.url)}"
                data-resolve="${escapeHtml(item.resolve_url || "")}"
                data-pending="${pending ? "1" : "0"}"
                aria-label="Copy Telegram message and open bot"
                title="Copy Telegram message and open bot"
              >
                ${telegramIconSvg()}
              </button>
            </span>
          </div>
          ${(item.source_name || item.provider || item.confidence || item.season || item.kind) ? `
            <div class="link-tags">
              ${item.source_name ? `<span class="tag">Source: ${escapeHtml(item.source_name)}</span>` : ""}
              ${item.provider ? `<span class="tag">Provider: ${escapeHtml(item.provider)}</span>` : ""}
              ${item.confidence ? `<span class="tag">Confidence: ${escapeHtml(item.confidence)}</span>` : ""}
              ${item.season ? `<span class="tag">${escapeHtml(item.season)}</span>` : ""}
              ${item.kind ? `<span class="tag">${escapeHtml(item.kind)}</span>` : ""}
              ${fileName ? `<span class="tag" title="${escapeHtml(fileName)}">File: ${escapeHtml(fileName)}</span>` : ""}
            </div>
          ` : ""}
          ${item.variant ? `<div class="link-variant">${escapeHtml(item.variant)}</div>` : ""}
          ${item.page_url ? `<div class="link-variant">Page: ${escapeHtml(item.page_url)}</div>` : ""}
          ${pending
            ? `<div class="pending-note">${escapeHtml(displayUrl)}</div>`
            : `<textarea class="url-box" readonly>${escapeHtml(displayUrl)}</textarea>`}
        </div>
      `;
      }).join("");
      async function ensureDirectLink(button) {
          let url = button.dataset.url || "";
          const isPending = button.dataset.pending === "1";
          const card = button.closest(".link-card");
          const urlBox = card?.querySelector(".url-box");
          const pendingNote = card?.querySelector(".pending-note");
          if (isPending) {
            const resolveUrl = button.dataset.resolve || "";
            if (!resolveUrl) {
              setStatus("Direct link source missing", true);
              return "";
            }
            button.disabled = true;
            if (button.classList.contains("copy")) {
              button.textContent = "Getting...";
            } else {
              button.setAttribute("aria-busy", "true");
            }
            setStatus("Getting direct link...");
            try {
              const body = await api("/api/resolve", {
                method: "POST",
                body: JSON.stringify({
                  url: resolveUrl,
                  quality: state.quality,
                  label: button.closest(".link-card")?.querySelector(".link-variant")?.textContent || "",
                  kind: button.closest(".link-card")?.querySelector(".link-tags .tag:last-child")?.textContent || "",
                }),
              });
              url = body.url || "";
              const key = button.dataset.key || resolveUrl;
              state.links = state.links.map((item) => {
                const itemKey = item.resolve_url || item.url || "";
                if (itemKey !== key && item.resolve_url !== resolveUrl) return item;
                return {
                  ...item,
                  url,
                  size: body.size || item.size,
                  pending: false,
                };
              });
              card?.querySelectorAll("[data-resolve]").forEach((action) => {
                action.dataset.url = url;
                action.dataset.pending = "0";
              });
              const copyButton = card?.querySelector(".copy");
              const openButton = card?.querySelector(".open-link");
              const telegramButton = card?.querySelector(".telegram-link");
              if (copyButton) copyButton.textContent = "Copy";
              if (openButton) openButton.innerHTML = downloadIconSvg();
              if (telegramButton) telegramButton.innerHTML = telegramIconSvg();
              if (urlBox) urlBox.value = url;
              const sizeEl = card?.querySelector(".link-size");
              if (sizeEl && body.size) sizeEl.textContent = body.size;
              if (pendingNote) pendingNote.textContent = "Direct link ready.";
            } catch (error) {
              button.disabled = false;
              button.removeAttribute("aria-busy");
              if (button.classList.contains("copy")) {
                button.textContent = "Get Link";
              } else if (button.classList.contains("telegram-link")) {
                button.innerHTML = telegramIconSvg();
              } else {
                button.innerHTML = downloadIconSvg();
              }
              setStatus(error.message, true);
              return "";
            }
            button.disabled = false;
            button.removeAttribute("aria-busy");
          }
          return url;
      }
      linksEl.querySelectorAll(".copy").forEach((button) => {
        button.addEventListener("click", async () => {
          const url = await ensureDirectLink(button);
          if (!url) return;
          const urlBox = button.closest(".link-card")?.querySelector(".url-box");
          const copied = await copyText(url, urlBox);
          if (copied) {
            button.textContent = "Copied";
            setStatus("Copied");
            setTimeout(() => {
              button.textContent = "Copy";
            }, 1400);
            return;
          }
          if (urlBox) {
            urlBox.focus();
            urlBox.select();
            urlBox.setSelectionRange(0, urlBox.value.length);
          }
          setStatus("Selected, long-press copy", true);
        });
      });
      linksEl.querySelectorAll(".open-link").forEach((button) => {
        button.addEventListener("click", async () => {
          const pending = button.dataset.pending === "1";
          const popup = pending ? window.open("", "_blank") : null;
          const url = await ensureDirectLink(button);
          if (!url) {
            if (popup) popup.close();
            return;
          }
          if (popup) {
            popup.location.href = url;
          } else {
            window.open(url, "_blank");
          }
          setStatus("Opened direct link");
        });
      });
      linksEl.querySelectorAll(".telegram-link").forEach((button) => {
        button.addEventListener("click", async () => {
          const url = await ensureDirectLink(button);
          if (!url) {
            setStatus("No link available.", true);
            showToast("No link available.");
            return;
          }
          const card = button.closest(".link-card");
          const isZip = [...(card?.querySelectorAll(".tag") || [])].some((tag) => tag.textContent.trim() === "Season Zip");
          const message = isZip ? `/m ${url} -e -up rcl` : `/m ${url} -up rcl`;
          const copied = await copyText(message);
          if (copied) {
            const message = "Message copied. Paste it in Telegram and press Send.";
            setStatus(message);
            showToast(message);
            window.open("https://t.me/HEROKU8267BOT", "_blank");
            return;
          }
          setStatus("Selected, long-press copy", true);
        });
      });
    }

    async function copyText(text, sourceElement) {
      if (!text) return false;
      if (navigator.clipboard && window.isSecureContext) {
        try {
          await navigator.clipboard.writeText(text);
          return true;
        } catch (_) {}
      }

      const textarea = sourceElement || document.createElement("textarea");
      const created = !sourceElement;
      if (created) {
        textarea.value = text;
        textarea.setAttribute("readonly", "");
        textarea.style.position = "fixed";
        textarea.style.top = "0";
        textarea.style.left = "0";
        textarea.style.width = "1px";
        textarea.style.height = "1px";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
      }
      textarea.focus({ preventScroll: true });
      textarea.select();
      textarea.setSelectionRange(0, textarea.value.length);
      let copied = false;
      try {
        copied = document.execCommand("copy");
      } catch (_) {
        copied = false;
      } finally {
        if (created) {
          document.body.removeChild(textarea);
        }
      }
      return copied;
    }

    async function search() {
      const query = queryEl.value.trim();
      if (!query) {
        setStatus("Name required", true);
        return;
      }
      setBusy(true);
      setStatus("Searching...");
      startProgress("Searching titles", 10);
      state.links = [];
      state.showLinks = false;
      linkFiltersEl.innerHTML = "";
      linksEl.innerHTML = '<div class="empty">Choose a result and find a link.</div>';
      try {
        const body = await api(`/api/search?q=${encodeURIComponent(query)}&source=${encodeURIComponent(sourceSelector.value)}`);
        state.candidates = body.candidates || [];
        state.hasSearched = true;
        state.episodeTarget = body.episodeTarget || episodeTargetFromQuery(query);
        state.episodeFallback = false;
        state.selected = -1;
        renderCandidates();
        const sourceCounts=(body.sources||[]).filter(source=>source.enabled).map(source=>`${source.name}: ${source.results||0}`).join(" • ");
        const adapterFailures=(body.adapterFailures||[]).map(item=>`⚠ ${item.name}: ${item.reason||"adapter failed"}`).join(" • ");
        setStatus(
          state.candidates.length
            ? `${state.candidates.length} results${sourceCounts?` • ${sourceCounts}`:""}${adapterFailures?` • ${adapterFailures}`:""}${body.searchedAsShow ? " • show page matched for episode" : ""}`
            : `No results${adapterFailures?` • ${adapterFailures}`:""}`
        );
        stopProgress(true, "Search complete");
      } catch (error) {
        state.candidates = [];
        state.hasSearched = true;
        state.selected = -1;
        renderCandidates();
        setStatus(error.message, true);
        failProgress("Search failed");
      } finally {
        setBusy(false);
      }
    }

    async function findLink() {
      const query = queryEl.value.trim();
      const candidate = state.candidates[state.selected];
      if (!query || !candidate) {
        setStatus("Select a result", true);
        return;
      }
      setBusy(true);
      setStatus(state.quality === "all" ? "Scanning qualities..." : `Scanning ${state.quality}...`);
      const isSeries = /\\b(season|web\\s*series|series|episode|s\\d{1,2})\\b/i.test(candidate.title || "");
      const estimateSeconds = state.quality === "all" ? (isSeries ? 12 : 30) : (isSeries ? 4 : 8);
      startProgress(
        state.quality === "all" ? "Finding links across qualities" : `Finding ${state.quality} links`,
        estimateSeconds
      );
      state.links = [];
      state.showLinks = true;
      linkFiltersEl.innerHTML = "";
      linksEl.innerHTML = '<div class="empty">Scanning...</div>';
      linksPanelEl.classList.remove("is-hidden");
      try {
        const body = await api("/api/find", {
          method: "POST",
          body: JSON.stringify({ query, candidate, quality: state.quality, episodeTarget: state.episodeTarget || episodeTargetFromQuery(query) }),
        });
        state.links = body.links || [];
        state.linkMessage = body.message || "";
        state.episodeTarget = body.episodeTarget || state.episodeTarget;
        state.episodeFallback = Boolean(body.episodeFallback);
        state.seasonFilter = "all";
        state.typeFilter = "all";
        renderLinks(state.links);
        setStatus((body.links || []).length ? (body.cached ? "Done (cached)" : "Done") : "No final link");
        stopProgress(true, (body.links || []).length ? (body.cached ? "Links ready from cache" : "Links ready") : "No final link");
      } catch (error) {
        renderLinks([]);
        setStatus(error.message, true);
        failProgress("Find failed");
      } finally {
        setBusy(false);
      }
    }

    $("qualityGrid").querySelectorAll(".quality").forEach((button) => {
      button.addEventListener("click", () => {
        state.quality = button.dataset.quality;
        $("qualityGrid").querySelectorAll(".quality").forEach((item) => item.classList.remove("active"));
        button.classList.add("active");
        $("selectedQuality").textContent = button.textContent;
        $("selectedQualityLabel").textContent = button.textContent;
      });
    });

    searchBtn.addEventListener("click", search);
    findBtn.addEventListener("click", findLink);
    $("clearBtn").addEventListener("click", () => {
      queryEl.value = "";
      state.candidates = [];
      state.hasSearched = false;
      state.links = [];
      state.showLinks = false;
      state.selected = -1;
      state.episodeTarget = null;
      state.episodeFallback = false;
      renderCandidates();
      renderLinks([]);
      setStatus("Ready");
      stopProgress(false);
      progressWrapEl.classList.remove("active");
    });
    queryEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter") search();
    });
    /* Separate local-library SPA. Search/link functions above remain intact. */
    const libraryState = { view: "search", page: 1, loading: false, scroll: {}, stats: null, detail: null };
    const libraryViewEl = $("libraryView"), searchWorkspaceEl = $("searchWorkspace"), libraryDetailEl = $("libraryDetail"), backToTopEl = $("backToTop");
    const basePrefix = () => location.pathname.startsWith("/index") ? "/index" : "";
    const libraryImage = (path) => path ? `${basePrefix()}${path}` : "";
    const bytes = (size) => { const n=Number(size||0); if(!n) return "—"; const units=["B","KB","MB","GB","TB"]; const i=Math.min(units.length-1,Math.floor(Math.log(n)/Math.log(1024))); return `${(n/1024**i).toFixed(i?1:0)} ${units[i]}`; };
    const statusBadge = (value) => { const text=String(value||"NEEDS MATCH").toUpperCase(); const key=text.toLowerCase().replace("needs match","match"); return `<span class="status-badge ${escapeHtml(key)}">${escapeHtml(text)}</span>`; };
    const isLibraryRoute = () => location.pathname.includes("/library/") || /\/admin(?:\/sources)?$/.test(location.pathname);
    function libraryRoute(view) { return { search: "/index/", movies:"/index/library/movies", tv:"/index/library/tv", missing:"/index/library/missing", recent:"/index/library/recent", admin:"/index/admin", sources:"/index/admin/sources" }[view] || "/index/"; }
    function libraryViewFromPath() { const parts=location.pathname.split("/").filter(Boolean); return parts.at(-1)==="sources"?"sources":({movies:"movies",tv:"tv",missing:"missing",recent:"recent",admin:"admin"})[parts.at(-1)] || "search"; }
    function libraryDetailFromUrl() { const params=new URLSearchParams(location.search), id=params.get("item"), kind=params.get("kind"), season=Number(params.get("season")); return id && (kind==="movie" || kind==="tv") ? {id,kind,season:Number.isFinite(season)&&season>0?season:null} : null; }
    function libraryDetailUrl(kind,id,season=null) { const params=new URLSearchParams({item:id,kind}); if(season) params.set("season",String(season)); return `${libraryRoute(kind==="movie"?"movies":"tv")}?${params}`; }
    function saveLibraryScroll() { try { sessionStorage.setItem(`jobinfo:library-scroll:${location.pathname}${location.search}`, String(window.scrollY)); } catch (_) {} }
    function restoreLibraryScroll() { try { const saved=Number(sessionStorage.getItem(`jobinfo:library-scroll:${location.pathname}${location.search}`)||0); if(saved) requestAnimationFrame(()=>window.scrollTo({top:saved,behavior:"instant"})); } catch (_) {} }
    function setActiveNav(view) { document.querySelectorAll("[data-view]").forEach(b=>b.classList.toggle("active", b.dataset.view===view)); }
    function navigate(view, push=true) {
      libraryState.view=view; setActiveNav(view);
      const library = view !== "search";
      searchWorkspaceEl.classList.toggle("hidden", library); libraryViewEl.classList.toggle("active", library);
      if(push && `${location.pathname}${location.search}` !== libraryRoute(view)) history.pushState({view}, "", libraryRoute(view));
      if(library) return renderLibrary();
      libraryViewEl.innerHTML=""; window.scrollTo({top:0,behavior:"instant"}); return Promise.resolve();
    }
    async function libraryStats() { try { libraryState.stats=await api("/api/library/stats"); return libraryState.stats; } catch(error) { return {movies:{count:0,available:0,totalSize:0},tv:{count:0,available:0,totalSize:0},configurationErrors:[{error:error.message}]}; } }
    function libraryToolbar(title, stats, admin=true) { const errors=(stats.configurationErrors||[]).map(e=>e.error).filter(Boolean); return `<div class="library-toolbar"><h2>${title}</h2><div class="library-stats"><span class="library-stat">${stats.count||0} items</span><span class="library-stat">${stats.available||0} available</span><span class="library-stat">${bytes(stats.totalSize)}</span></div>${admin?'<button class="btn secondary" id="scanLibrary">Scan Library</button>':''}</div>${errors.length?`<div class="pending-note">${escapeHtml(errors[0])} Configure MOVIES_PATHS / TV_SHOWS_PATHS on the server, then scan.</div>`:""}`; }
    function card(item, kind) { const status=item.needsMatch?"NEEDS MATCH":(item.available?"AVAILABLE":"MISSING"); const title=escapeHtml(item.title); const poster=libraryImage(item.posterUrl); const tvMeta=kind==="tv"?(item.totalEpisodes?`${item.availableSeasons||0}/${item.totalSeasons||"?"} seasons · ${item.availableEpisodes||0}/${item.totalEpisodes} episodes${item.progress!=null?` · ${item.progress}%`:""}`:`${item.total_files||0} files`):bytes(item.total_size); return `<button class="library-card" data-item="${escapeHtml(item.id)}" data-kind="${kind}">${poster?`<img src="${escapeHtml(poster)}" loading="lazy" alt="">`:'<div class="library-placeholder">NO POSTER</div>'}<span class="library-card-copy"><span class="library-card-title">${title}</span><span class="library-card-meta"><span>${escapeHtml(item.year||"—")}</span><span>${escapeHtml((item.local_languages||[]).join(" · ")||item.original_language||"Unknown")}</span></span><span class="library-card-meta">${statusBadge(status)} <span>${escapeHtml((item.qualities||[]).join(" "))}</span><span>${tvMeta}</span></span></span></button>`; }
    async function renderCollection(kind) {
      const statsAll=await libraryStats(), stats=kind==="movie"?statsAll.movies:statsAll.tv;
      const endpoint=kind==="movie"?"/api/library/movies":"/api/library/tv";
      libraryViewEl.innerHTML=libraryToolbar(kind==="movie"?"Movies":"TV Shows",stats)+`<div class="library-filters"><input id="librarySearch" aria-label="Search ${kind==="movie"?"movies":"shows"}" placeholder="Search ${kind==="movie"?"movies":"shows"}"><select id="libraryStatus" aria-label="Filter by availability"><option value="">All statuses</option><option value="available">Available</option><option value="missing">Missing</option><option value="unmatched">Needs Match</option></select><select id="librarySort" aria-label="Sort library"><option value="title">Title</option><option value="year">Year</option><option value="dateAdded">Recently added</option><option value="fileSize">File size</option><option value="rating">Rating</option></select></div><div class="library-grid" id="libraryGrid"><div class="library-empty">Loading library…</div></div>`;
      const load=async(page=1)=>{ const search=$("librarySearch")?.value||"", status=$("libraryStatus")?.value||"", sort=$("librarySort")?.value||"title"; const result=await api(`${endpoint}?page=${page}&limit=30&search=${encodeURIComponent(search)}&status=${status}&sort=${sort}`); const grid=$("libraryGrid"); grid.innerHTML=result.items.length?result.items.map(i=>card(i,kind)).join(""):'<div class="library-empty">No items yet. Run Scan Library after configuring the media folders.</div>'; if(result.page<result.totalPages) grid.insertAdjacentHTML("beforeend",`<button class="btn secondary" id="loadMore">Load more (${result.total-result.page*result.limit} remaining)</button>`); grid.querySelectorAll(".library-card").forEach(b=>b.onclick=()=>openLibraryDetail(b.dataset.kind,b.dataset.item)); $("loadMore")?.addEventListener("click",()=>load(result.page+1)); };
      $("scanLibrary")?.addEventListener("click",startLibraryScan); $("librarySearch").addEventListener("input",debounce(()=>load(),300)); $("libraryStatus").addEventListener("change",()=>load()); $("librarySort").addEventListener("change",()=>load()); load().catch(e=>libraryError(e));
    }
    function debounce(fn,wait){let t;return(...args)=>{clearTimeout(t);t=setTimeout(()=>fn(...args),wait)}}
    function libraryError(error) { libraryViewEl.innerHTML=`<div class="library-empty">${escapeHtml(error.message||"Library temporarily unavailable.")}</div>`; setStatus(error.message||"Library temporarily unavailable",true); }
    async function renderMissing() { const stats=await libraryStats(); libraryViewEl.innerHTML=libraryToolbar("Missing",{count:0,available:0,totalSize:0},false)+`<div class="library-filters"><select id="missingKind"><option value="movies">Missing Movies</option><option value="unmatched">Unmatched Files</option></select></div><div class="library-grid" id="libraryGrid"><div class="library-empty">Loading…</div></div>`; const load=async()=>{const result=await api(`/api/library/missing?type=${$("missingKind").value}`); $("libraryGrid").innerHTML=result.items.length?result.items.map(i=>card(i,i.type)).join(""):'<div class="library-empty">Nothing to show.</div>'; $("libraryGrid").querySelectorAll(".library-card").forEach(b=>b.onclick=()=>openLibraryDetail(b.dataset.kind,b.dataset.item));}; $("missingKind").onchange=load; load().catch(libraryError); }
    async function renderRecent() { const stats=await libraryStats(); libraryViewEl.innerHTML=libraryToolbar("Recently Added",{count:0,available:0,totalSize:0},false)+`<div class="library-grid" id="libraryGrid"><div class="library-empty">Loading…</div></div>`; try {const result=await api("/api/library/recent?limit=40"); $("libraryGrid").innerHTML=result.items.length?result.items.map(i=>card(i,i.type)).join(""):'<div class="library-empty">No recently detected files.</div>'; $("libraryGrid").querySelectorAll(".library-card").forEach(b=>b.onclick=()=>openLibraryDetail(b.dataset.kind,b.dataset.item));}catch(e){libraryError(e)} }
    const displayTime=(value)=>{if(!value)return "—"; const date=new Date(value);return Number.isNaN(date.valueOf())?String(value):date.toLocaleString();};
    function adminRow(title, meta="") { return `<div class="admin-row"><strong>${escapeHtml(title||"Unknown")}</strong>${meta?`<small>${escapeHtml(meta)}</small>`:""}</div>`; }
    async function renderAdmin() { try { const data=await api("/api/admin/library/dashboard"); const stats=data.stats||{}, movies=stats.movies||{}, tv=stats.tv||{}; const events=data.events||[], scans=data.scans||[], unmatched=data.unmatched||[], duplicates=data.duplicates||[]; libraryViewEl.innerHTML=`<div class="library-toolbar"><h2>Admin Dashboard</h2><button class="btn secondary" id="scanLibrary">Scan Library</button><button class="btn secondary" id="refreshAdmin">Refresh</button></div><div class="admin-kpi"><span>Movies<b>${Number(movies.count||0)}</b></span><span>TV Shows<b>${Number(tv.count||0)}</b></span><span>Needs Match<b>${unmatched.length}</b></span><span>Possible Duplicates<b>${duplicates.length}</b></span></div><div class="admin-grid"><section class="admin-panel"><h3>Broken links & resolver failures</h3><div class="admin-list">${events.length?events.map(e=>adminRow(e.message,`${e.category.replace("_"," ")} · ${displayTime(e.created_at)}`)).join(""):'<div class="admin-row"><small>No failures logged yet. New resolver failures will appear here.</small></div>'}</div></section><section class="admin-panel"><h3>Recent scans</h3><div class="admin-list">${scans.length?scans.map(s=>{const p=s.progress||{};return adminRow(`${String(s.kind||"scan").toUpperCase()} · ${s.status||"unknown"}`,`${displayTime(s.started_at)} · ${p.filesChecked||0} files · ${p.errors?.length||0} errors`)}).join(""):'<div class="admin-row"><small>No scan history yet.</small></div>'}</div></section><section class="admin-panel"><h3>Unmatched titles</h3><div class="admin-list">${unmatched.length?unmatched.map(i=>adminRow(`${i.title}${i.year?` (${i.year})`:""}`,`${i.type} · ${i.total_files||0} file(s) · ${bytes(i.total_size)}`)).join(""):'<div class="admin-row"><small>All scanned titles are matched.</small></div>'}</div></section><section class="admin-panel"><h3>Possible duplicates</h3><div class="admin-list">${duplicates.length?duplicates.map(d=>adminRow(`${d.title}${d.year?` (${d.year})`:""}`,`${d.copies} copies across library roots · ${d.type}`)).join(""):'<div class="admin-row"><small>No same-title copies detected across configured library roots.</small></div>'}</div></section></div>`; $("scanLibrary").onclick=startLibraryScan; $("refreshAdmin").onclick=renderAdmin; } catch(error) { libraryViewEl.innerHTML=`<div class="library-empty">${escapeHtml(error.message||"Admin dashboard unavailable.")}</div>`; setStatus(error.message||"Admin dashboard unavailable",true); } }
    async function renderSources() { try { const body=await api("/api/admin/sources"), rows=body.sources||[]; const card=row=>`<div class="admin-row"><strong>${escapeHtml(row.name)}</strong><small>Domain: ${escapeHtml(row.domain)} · ${row.enabled?"Enabled":"Disabled"}<br>Search: ${escapeHtml(row.search_status)} · Link resolver: ${escapeHtml(row.resolver_status)}<br>Last test: ${escapeHtml(row.last_tested_at?displayTime(row.last_tested_at):"Never")}</small><span class="episode-row-actions"><button class="btn secondary source-test" data-id="${escapeHtml(row.id)}" data-kind="${escapeHtml(row.kind)}">Test</button><button class="btn secondary source-edit" data-id="${escapeHtml(row.id)}" data-kind="${escapeHtml(row.kind)}">Edit</button><button class="btn secondary source-toggle" data-id="${escapeHtml(row.id)}" data-kind="${escapeHtml(row.kind)}" data-enabled="${row.enabled?"0":"1"}">${row.enabled?"Disable":"Enable"}</button><button class="btn secondary source-advanced" data-id="${escapeHtml(row.id)}" data-kind="${escapeHtml(row.kind)}">Advanced Diagnostics</button><button class="btn secondary source-delete" data-id="${escapeHtml(row.id)}" data-kind="${escapeHtml(row.kind)}">Delete</button></span></div>`; libraryViewEl.innerHTML=`<div class="library-toolbar"><h2>Sources</h2><button class="btn" id="addSource">Add New Source</button></div><div class="pending-note">Configured sources and saved adapters are managed together.</div><div class="admin-list" id="sourceRows">${rows.map(card).join("")||"<div class='pending-note'>No sources configured.</div>"}</div><section class="admin-panel" id="sourceWizard" hidden></section>`; const request=(action,data)=>api(`/api/admin/sources/${action}`,{method:"POST",body:JSON.stringify(data)}); $("addSource").onclick=()=>{const panel=$("sourceWizard");panel.hidden=false;panel.innerHTML=`<h3>Add New Source</h3><div class="field"><label>Website URL</label><input id="newSourceUrl" placeholder="https://example.com"></div><div class="field"><label>Example search title</label><input id="newSourceQuery" placeholder="Example title"></div><button class="btn" id="newSourceSearch">1. Detect and Test Search</button><div id="newSourceResult" class="pending-note"></div>`; $("newSourceSearch").onclick=async()=>{const out=$("newSourceResult");out.textContent="Checking search…";try{const r=await request("wizard/search",{siteUrl:$("newSourceUrl").value,query:$("newSourceQuery").value});out.innerHTML=`<strong>2. Select a verified result</strong><br>${(r.candidates||[]).map((x,i)=>`<button class="btn secondary wizard-result" data-i="${i}">${escapeHtml(x.title)}</button>`).join("")||"No verified results."}`;out.querySelectorAll(".wizard-result").forEach(b=>b.onclick=async()=>{const item=r.candidates[Number(b.dataset.i)];out.textContent="Testing link workflow…";const a=await request("wizard/analyze",{siteUrl:$("newSourceUrl").value,query:$("newSourceQuery").value,candidate:item});out.innerHTML=`<strong>3. Result summary</strong><br>Search: ${a.report?.search_test_successful?"Verified":"Needs review"}<br>Link workflow: ${a.report?.final_link_detected?"Verified":"No final link verified"}<br><button class="btn" id="saveNewSource">Save and Enable Source</button>`;$("saveNewSource").onclick=async()=>{await api("/api/adapters/save",{method:"POST",body:JSON.stringify({adapter:a.adapter,saveMode:"new"})});renderSources();};});}catch(e){out.textContent=e.message}};}; libraryViewEl.querySelectorAll(".source-toggle,.source-delete,.source-test").forEach(b=>b.onclick=async()=>{if(b.classList.contains("source-delete")&&!confirm("Delete this source?"))return;await request(b.classList.contains("source-toggle")?"toggle":b.classList.contains("source-delete")?"delete":"test",{id:b.dataset.id,kind:b.dataset.kind,enabled:b.dataset.enabled==="1"});renderSources();}); libraryViewEl.querySelectorAll(".source-edit").forEach(b=>b.onclick=()=>{setStatus("Edit uses the Add New Source guided flow; existing runtime settings are preserved until saved.");}); libraryViewEl.querySelectorAll(".source-advanced").forEach(b=>b.onclick=()=>{const panel=$("sourceWizard");panel.hidden=false;panel.innerHTML=`<h3>Advanced Diagnostics</h3><p class="pending-note">Enter a page URL to run a read-only workflow check for this source.</p><div class="field"><input id="diagPage" placeholder="https://example.com/title"></div><button class="btn" id="diagRun">Run diagnostics</button><pre id="diagOutput"></pre>`;$("diagRun").onclick=async()=>{const page=$("diagPage").value;const row=rows.find(x=>x.id===b.dataset.id&&x.kind===b.dataset.kind);const r=await api("/api/admin/workflow-analyzer",{method:"POST",body:JSON.stringify({siteUrl:`https://${row.domain}`,movieUrl:page})});$("diagOutput").textContent=JSON.stringify(r,null,2);};}); } catch(error) { libraryError(error); } }
    // Sources deliberately replaces the old overlapping setup views.  Keep
    // workflow detail in this admin-only panel and make the save gate visible.
    renderSources=async function(){try{const body=await api("/api/admin/sources"),rows=body.sources||[];
      const card=row=>`<article class="source-card"><div><div class="source-title"><strong>${escapeHtml(row.name)}</strong><span class="source-state ${row.enabled?"":"off"}">${row.enabled?"Enabled":"Disabled"}</span></div><div class="source-details"><span>${escapeHtml(row.domain)}</span><span>Search: ${escapeHtml(row.search_status)}</span><span>Resolver: ${escapeHtml(row.resolver_status)}</span><span>Last test: ${escapeHtml(row.last_tested_at?displayTime(row.last_tested_at):"Never")}</span></div></div><div class="source-actions"><button class="btn secondary source-test" aria-label="Test ${escapeHtml(row.name)}" data-id="${escapeHtml(row.id)}" data-kind="${escapeHtml(row.kind)}">Test</button><button class="btn secondary source-edit" aria-label="Edit ${escapeHtml(row.name)}" data-id="${escapeHtml(row.id)}" data-kind="${escapeHtml(row.kind)}">Edit</button><button class="btn secondary source-toggle" aria-label="${row.enabled?"Disable":"Enable"} ${escapeHtml(row.name)}" data-id="${escapeHtml(row.id)}" data-kind="${escapeHtml(row.kind)}" data-enabled="${row.enabled?"0":"1"}">${row.enabled?"Disable":"Enable"}</button><button class="btn secondary source-advanced" aria-label="Run advanced diagnostics for ${escapeHtml(row.name)}" data-id="${escapeHtml(row.id)}" data-kind="${escapeHtml(row.kind)}">Diagnostics</button><button class="btn secondary source-delete" aria-label="Delete ${escapeHtml(row.name)}" data-id="${escapeHtml(row.id)}" data-kind="${escapeHtml(row.kind)}">Delete</button></div></article>`;
      libraryViewEl.innerHTML=`<div class="library-toolbar"><h2>Sources</h2><button class="btn" id="addSource">Add New Source</button></div><div class="pending-note">Configured sources and saved adapters are managed together.</div><div class="source-list">${rows.map(card).join("")||"<div class='pending-note'>No sources configured.</div>"}</div><section class="admin-panel" id="sourceWizard" hidden></section>`;
      const request=(action,data)=>api(`/api/admin/sources/${action}`,{method:"POST",body:JSON.stringify(data)}), panel=()=>$("sourceWizard");
      $("addSource").onclick=()=>{const box=panel();box.hidden=false;box.innerHTML=`<h3>Add New Source</h3><div class="field"><label>Website URL</label><input id="newSourceUrl" placeholder="https://example.com"></div><div class="field"><label>Example search title</label><input id="newSourceQuery" placeholder="Example title"></div><button class="btn" id="newSourceSearch">1. Detect and Test Search</button><div id="newSourceResult" class="pending-note"></div>`;
        $("newSourceSearch").onclick=async()=>{const out=$("newSourceResult");out.textContent="Checking search…";try{const found=await request("wizard/search",{siteUrl:$("newSourceUrl").value,query:$("newSourceQuery").value});out.innerHTML=`<strong>2. Select a verified result</strong><br>${(found.candidates||[]).map((item,index)=>`<button class="btn secondary wizard-result" data-index="${index}">${escapeHtml(item.title)}</button>`).join("")||"No verified results."}`;
          out.querySelectorAll(".wizard-result").forEach(button=>button.onclick=async()=>{const candidate=found.candidates[Number(button.dataset.index)];out.textContent="Opening movie page and testing every quality branch…";try{const analyzed=await request("wizard/analyze",{siteUrl:$("newSourceUrl").value,query:$("newSourceQuery").value,candidate});const workflow=analyzed.workflow||{},steps=workflow.workflow_steps||[];const visual=[{label:"Search",state:"passed"},...steps].map(step=>`${step.state==="passed"?"✓":"✗"} ${escapeHtml(step.label)}`).join("<br>↓<br>");const verified=Boolean(analyzed.report?.ready_to_save);out.innerHTML=`<strong>3. Result summary</strong><br>Search: Verified<br>Link workflow: ${verified?"Verified final file":"No final link verified"}<div class="pending-note" style="margin-top:.75rem">${visual}</div><div class="pending-note">${escapeHtml(workflow.message||"")}</div><button class="btn" id="saveNewSource" ${verified?"":"disabled"}>Save and Enable Source</button>${verified?"":"<div class='pending-note'>The source was not saved: no real downloadable response was verified.</div>"}`;if(verified)$("saveNewSource").onclick=async()=>{await api("/api/adapters/save",{method:"POST",body:JSON.stringify({adapter:analyzed.adapter,saveMode:"new"})});renderSources();};}catch(error){out.textContent=error.message;}});}catch(error){out.textContent=error.message;}};};
      libraryViewEl.querySelectorAll(".source-toggle,.source-delete,.source-test").forEach(button=>button.onclick=async()=>{if(button.classList.contains("source-delete")&&!confirm("Delete this source?"))return;await request(button.classList.contains("source-toggle")?"toggle":button.classList.contains("source-delete")?"delete":"test",{id:button.dataset.id,kind:button.dataset.kind,enabled:button.dataset.enabled==="1"});renderSources();});
      libraryViewEl.querySelectorAll(".source-edit").forEach(button=>button.onclick=()=>setStatus("Edit uses the Add New Source guided flow; existing runtime settings are preserved until saved."));
      libraryViewEl.querySelectorAll(".source-advanced").forEach(button=>button.onclick=()=>{const box=panel(),row=rows.find(item=>item.id===button.dataset.id&&item.kind===button.dataset.kind);box.hidden=false;box.innerHTML=`<h3>Advanced Diagnostics</h3><p class="pending-note">Enter a page URL to run a read-only workflow check.</p><div class="field"><input id="diagPage" placeholder="https://example.com/title"></div><button class="btn" id="diagRun">Run diagnostics</button><pre id="diagOutput"></pre>`;$("diagRun").onclick=async()=>{$("diagOutput").textContent=JSON.stringify(await api("/api/admin/workflow-analyzer",{method:"POST",body:JSON.stringify({siteUrl:`https://${row.domain}`,movieUrl:$("diagPage").value})}),null,2);};});
    }catch(error){libraryError(error);}};
    const linesValue=(value)=>Array.isArray(value)?value.join("\\n"):String(value||"");
    const splitLines=(value)=>String(value||"").split(/\\n|,/).map(x=>x.trim()).filter(Boolean);
    async function renderSetup() {
      try {
        const body=await api("/api/admin/setup"), setup=body.setup||{};
        const sources=setup.sources||[];
        setTimeout(()=>{const grid=libraryViewEl.querySelector('.admin-grid');if(!grid||$('setupAdminPassword'))return;grid.insertAdjacentHTML('afterbegin',`<section class='admin-panel'><h3>Admin access</h3><div class='panel-body'><div class='field'><label>Admin password</label><input id='setupAdminPassword' type='password' placeholder='${setup.adminPasswordConfigured?"Enter a new password to replace it":"Create a password (minimum 10 characters)"}'></div><button class='btn secondary' id='saveAdminPassword'>Save admin password</button><div class='pending-note'>The password is stored as a secure hash and protects future setup changes.</div></div></section>`);$('saveAdminPassword').onclick=async()=>{const password=$('setupAdminPassword').value;try{await api('/api/admin/setup/security',{method:'POST',body:JSON.stringify({password})});adminPassword=password;sessionStorage.setItem('adminPassword',password);showToast('Admin password saved');renderSetup();}catch(e){setStatus(e.message,true);}};},0);
        const sourceRows=sources.map(s=>`<div class="file-row"><span><b>${escapeHtml(s.name)}</b><small>${escapeHtml(s.base_url)} · ${escapeHtml(s.last_status||"unknown")}</small></span><span class="episode-row-actions"><button class="btn secondary source-test" data-id="${escapeHtml(s.id)}">Test</button><button class="btn secondary source-toggle" data-id="${escapeHtml(s.id)}" data-enabled="${s.enabled?"0":"1"}">${s.enabled?"Disable":"Enable"}</button><button class="btn secondary source-delete" data-id="${escapeHtml(s.id)}">Remove</button></span></div>`).join("")||'<div class="pending-note">No sources configured yet.</div>';
        libraryViewEl.innerHTML=`<div class="library-toolbar"><h2>Admin Setup</h2><span class="library-stat">Changes apply immediately</span></div><div class="admin-grid"><section class="admin-panel"><h3>1. Jellyfin connection</h3><div class="panel-body"><div class="field"><label>Jellyfin URL</label><input id="setupJellyfinUrl" value="${escapeHtml(setup.jellyfinUrl||"")}" placeholder="http://server:8096"></div><div class="field"><label>Jellyfin API key</label><input id="setupJellyfinKey" type="password" placeholder="${setup.jellyfinApiKeyConfigured?"Configured — enter only to replace":"Paste API key"}"></div><div class="actions"><button class="btn secondary" id="testJellyfin">Test connection</button><button class="btn secondary" id="loadJellyfinLibraries">Load libraries</button></div><div id="jellyfinSetupResult" class="pending-note"></div><div id="jellyfinLibraries"></div></div></section><section class="admin-panel"><h3>2. Local library paths</h3><div class="panel-body"><div class="field"><label>Movie folders — one per line</label><textarea id="setupMoviePaths" rows="4">${escapeHtml(linesValue(setup.moviePaths))}</textarea></div><div class="field"><label>TV / Anime folders — one per line</label><textarea id="setupTvPaths" rows="4">${escapeHtml(linesValue(setup.tvPaths))}</textarea></div><div class="field"><label>Minimum video file size (MB)</label><input id="setupMinSize" type="number" min="0" value="${escapeHtml(setup.minFileSizeMb||30)}"></div><button class="btn secondary" id="validatePaths">Validate paths</button><div id="pathResult" class="pending-note"></div></div></section><section class="admin-panel"><h3>3. Metadata & sync</h3><div class="panel-body"><div class="field"><label>TMDB API key</label><input id="setupTmdbKey" type="password" placeholder="${setup.tmdbApiKeyConfigured?"Configured — enter only to replace":"Optional, for posters and metadata"}"></div><div class="field"><label>Automatic sync interval (minutes, 0 = manual)</label><input id="setupAutoSync" type="number" min="0" max="10080" value="${escapeHtml(setup.autoSyncMinutes||0)}"></div><button class="btn" id="saveSetup">Save setup</button><div class="pending-note">Saving does not expose stored API keys in this browser.</div></div></section><section class="admin-panel"><h3>4. Content sources</h3><div class="panel-body"><div id="sourceRows">${sourceRows}</div><div class="field"><label>Source name</label><input id="sourceName" placeholder="My compatible source"></div><div class="field"><label>Source URL</label><input id="sourceUrl" placeholder="https://example.com"></div><div class="field"><label>Priority (lower runs first)</label><input id="sourcePriority" type="number" value="100"></div><button class="btn secondary" id="saveSource">Add source</button><div class="pending-note">Only sources using the compatible search layout work without a custom adapter.</div></div></section></div>`;
        const renderLibraryChoices=(items)=>{const selected=new Set(setup.jellyfinLibraryIds||[]); const mappings=setup.pathMappings||[]; $("jellyfinLibraries").innerHTML=items.length?`<div class="episode-list">${items.map(i=>{const mapped=mappings.find(m=>m.libraryId===i.id)||{}; const type=/movie/i.test(i.type||"")?"movie":"tv"; return `<div class="file-row"><span><label><input type="checkbox" class="jellyfin-library-choice" value="${escapeHtml(i.id)}" ${selected.has(i.id)?"checked":""}> <b>${escapeHtml(i.name)}</b></label><small>${escapeHtml(i.type||"other")} · ${(i.paths||[]).map(escapeHtml).join(", ")}</small><input class="path-mapping" data-library-id="${escapeHtml(i.id)}" data-jellyfin-path="${escapeHtml((i.paths||[])[0]||"")}" data-media-type="${type}" value="${escapeHtml(mapped.localPath||"")}" placeholder="App/server path for this library"></span></div>`;}).join("")}</div>`:'<div class="pending-note">No selectable Jellyfin libraries found.</div>';};
        $("testJellyfin").onclick=async()=>{const target=$("jellyfinSetupResult");target.textContent="Testing…";try{const r=await api("/api/admin/setup/test-jellyfin",{method:"POST",body:JSON.stringify({url:$("setupJellyfinUrl").value,apiKey:$("setupJellyfinKey").value})});target.textContent=`Connected: ${r.server}${r.version?` · ${r.version}`:""}`;}catch(e){target.textContent=e.message;}};
        $("loadJellyfinLibraries").onclick=async()=>{const target=$("jellyfinSetupResult");target.textContent="Loading libraries…";try{const key=$("setupJellyfinKey").value;const r=key?await api("/api/admin/setup/load-jellyfin-libraries",{method:"POST",body:JSON.stringify({url:$("setupJellyfinUrl").value,apiKey:key})}):await api("/api/admin/setup/jellyfin/libraries");renderLibraryChoices(r.items||[]);target.textContent="Select libraries and enter each app/server path, then Save setup.";}catch(e){target.textContent=e.message;}};
        $("validatePaths").onclick=async()=>{const target=$("pathResult");target.textContent="Checking…";try{const r=await api("/api/admin/setup/validate-paths",{method:"POST",body:JSON.stringify({paths:[...splitLines($("setupMoviePaths").value),...splitLines($("setupTvPaths").value)]})});target.innerHTML=(r.items||[]).map(x=>`${escapeHtml(x.path)} — ${x.readable?"ready":"not accessible"}`).join("<br>");}catch(e){target.textContent=e.message;}};
        $("saveSetup").onclick=async()=>{const selected=[...document.querySelectorAll(".jellyfin-library-choice:checked")].map(x=>x.value);const pathMappings=[...document.querySelectorAll(".path-mapping")].map(x=>({libraryId:x.dataset.libraryId,jellyfinPath:x.dataset.jellyfinPath,localPath:x.value,mediaType:x.dataset.mediaType})).filter(x=>x.localPath.trim());try{await api("/api/admin/setup/save",{method:"POST",body:JSON.stringify({jellyfinUrl:$("setupJellyfinUrl").value,jellyfinApiKey:$("setupJellyfinKey").value,tmdbApiKey:$("setupTmdbKey").value,moviePaths:splitLines($("setupMoviePaths").value),tvPaths:splitLines($("setupTvPaths").value),jellyfinLibraryIds:selected,minFileSizeMb:$("setupMinSize").value,autoSyncMinutes:$("setupAutoSync").value,pathMappings})});showToast("Setup saved. Run Scan Library to sync now.");renderSetup();}catch(e){setStatus(e.message,true);}};
        $("saveSource").onclick=async()=>{try{await api("/api/admin/setup/sources/save",{method:"POST",body:JSON.stringify({name:$("sourceName").value,baseUrl:$("sourceUrl").value,priority:$("sourcePriority").value,enabled:true})});showToast("Source saved");renderSetup();}catch(e){setStatus(e.message,true);}};
        libraryViewEl.querySelectorAll(".source-test").forEach(b=>b.onclick=async()=>{b.disabled=true;b.textContent="Testing…";try{const r=await api("/api/admin/setup/sources/test",{method:"POST",body:JSON.stringify({id:b.dataset.id})});showToast(`Source ${r.status||"Working"}`);const panel=$("customPreview");if(panel&&r.diagnostics)panel.textContent=JSON.stringify({status:r.status,diagnostics:r.diagnostics},null,2);b.textContent=r.status||"Test";b.disabled=false;}catch(e){setStatus(e.message,true);b.disabled=false;b.textContent="Test";}});
        libraryViewEl.querySelectorAll(".source-toggle").forEach(b=>b.onclick=async()=>{const source=sources.find(s=>s.id===b.dataset.id);if(!source)return;try{await api("/api/admin/setup/sources/save",{method:"POST",body:JSON.stringify({id:source.id,name:source.name,baseUrl:source.base_url,priority:source.priority,enabled:b.dataset.enabled==="1"})});renderSetup();}catch(e){setStatus(e.message,true);}});
        libraryViewEl.querySelectorAll(".source-delete").forEach(b=>b.onclick=async()=>{try{await api("/api/admin/setup/sources/delete",{method:"POST",body:JSON.stringify({id:b.dataset.id})});renderSetup();}catch(e){setStatus(e.message,true);}});
      } catch(error) { if(/Admin authorization required/i.test(error.message||"")){const password=window.prompt("Enter admin password");if(password){adminPassword=password;sessionStorage.setItem("adminPassword",password);return renderSetup();}} libraryError(error); }
    }
    async function renderAdapterMaker() { libraryViewEl.innerHTML=`<div class="library-toolbar"><h2>Adapter Maker</h2><span class="library-stat">Simple guided setup</span></div><div class="admin-grid"><section class="admin-panel"><div class="panel-body"><div class="field"><label>Site name</label><input id="adapterName" placeholder="Example Site"></div><div class="field"><label>Main site URL</label><input id="adapterMain" type="url" placeholder="https://example.com"></div><div class="field"><label>Example content/page URL</label><input id="adapterPage" type="url" placeholder="https://example.com/title/example"></div><div class="field"><label>Optional search query</label><input id="adapterQuery" placeholder="Example title"></div><div class="field"><label>Optional expected quality</label><select id="adapterQuality"><option value="">Any quality</option><option>480p</option><option>720p</option><option>1080p</option><option value="2160p">4K / 2160p</option></select></div><div class="field"><label>Optional example final URL</label><input id="adapterFinal" type="url" placeholder="https://host.example/file"></div><div class="actions"><button class="btn" id="adapterAnalyze">Analyze Site</button><button class="btn secondary" id="adapterTest" disabled>Test Adapter</button><button class="btn secondary" id="adapterSave" disabled>Save Adapter</button></div><label class="pending-note" id="adapterOverrideWrap" hidden><input id="adapterOverride" type="checkbox"> I understand this adapter needs review, but save it anyway.</label><div id="adapterResult" class="pending-note">Enter the two website URLs, then choose Analyze Site.</div></div></section><section class="admin-panel"><h3>Adapter result</h3><div id="adapterCards" class="admin-list">The status cards will appear here.</div><details class="pending-note"><summary>Advanced Debug</summary><pre id="adapterDebug"></pre></details><details class="pending-note"><summary>Generated adapter JSON</summary><pre id="adapterPreview"></pre></details></section></div>`;let result=null;const payload=()=>({siteName:$("adapterName").value,mainSiteUrl:$("adapterMain").value,examplePageUrl:$("adapterPage").value,searchQuery:$("adapterQuery").value,expectedQuality:$("adapterQuality").value,exampleFinalUrl:$("adapterFinal").value});const scoreLabel=n=>n>=95?"🟢 Excellent":n>=80?"🟢 Good":n>=60?"🟡 Needs Review":"🔴 Failed";const simpleReason=(checks,js)=>js?"❌ This site needs a browser-based adapter.":!checks.search_working?"❌ Search not detected":!checks.quality_detected?"❌ Quality not found":!checks.download_button_detected?"❌ Download button not found":!checks.redirect_chain_working?"❌ Redirect chain not working":!checks.final_link_detected?"❌ Final link could not be identified":"❌ Adapter needs review";const show=r=>{const report=r.report||{},checks=report.simple_checks||{};const names={main_site_reachable:"Main site reachable",search_working:"Search working",example_page_valid:"Example page valid",quality_detected:"Quality detected",download_button_detected:"Download button detected",redirect_chain_working:"Redirect chain working",final_link_detected:"Final link detected"};$("adapterCards").innerHTML=Object.entries(names).map(([key,label])=>`<div class="admin-row"><strong>${checks[key]?"🟢":"🔴"} ${label}</strong><small>${checks[key]?"Working":"Not found"}</small></div>`).join("");const ready=Boolean(report.ready_to_save);$("adapterResult").innerHTML=`<strong>${ready?"✅ READY TO USE":"❌ NOT READY"}</strong><br>${ready?"Your adapter passed all automatic checks.":simpleReason(checks,report.javascript_required)}<br><br>Adapter Confidence: <strong>${Number(report.adapter_confidence||0)}% — ${scoreLabel(Number(report.adapter_confidence||0))}</strong>`;$("adapterPreview").textContent=JSON.stringify(r.adapter,null,2);$("adapterDebug").textContent=JSON.stringify({report:r.report,selector_candidates:r.selector_candidates,debug:r.debug},null,2);$("adapterTest").disabled=false;$("adapterOverrideWrap").hidden=ready;$("adapterSave").disabled=!ready&&!$("adapterOverride").checked;};$("adapterOverride").onchange=()=>{$("adapterSave").disabled=!result||!(result.report?.ready_to_save||$("adapterOverride").checked)};$("adapterAnalyze").onclick=async()=>{const b=$("adapterAnalyze");b.disabled=true;$("adapterResult").textContent="Checking the website…";try{result=await api("/api/adapters/analyze",{method:"POST",body:JSON.stringify(payload())});show(result)}catch(e){$("adapterResult").textContent=`❌ ${e.message}`}finally{b.disabled=false}};$("adapterTest").onclick=async()=>{if(!result)return;$("adapterResult").textContent="Testing the adapter…";try{result=await api("/api/adapters/test",{method:"POST",body:JSON.stringify({adapter:result.adapter,examplePageUrl:$("adapterPage").value,expectedQuality:$("adapterQuality").value})});show(result)}catch(e){$("adapterResult").textContent=`❌ ${e.message}`}};$("adapterSave").onclick=async()=>{if(!result)return;try{const r=await api("/api/adapters/save",{method:"POST",body:JSON.stringify({adapter:result.adapter})});showToast(`Adapter ${r.adapter.id} saved`)}catch(e){$("adapterResult").textContent=`❌ ${e.message}`}}; }
    async function enhanceAdminAdapters(){try{const body=await api("/api/adapters");const grid=libraryViewEl.querySelector(".admin-grid");if(!grid||$("savedAdapters"))return;grid.insertAdjacentHTML("beforeend",`<section class="admin-panel" id="savedAdapters"><h3>Adapters</h3><div class="admin-list">${(body.adapters||[]).map(a=>`<div class="admin-row"><strong>${escapeHtml(a.name)} · ${a.enabled?(a.health?.status==="Working"?"🟢 Working":"🟡 Needs retest"):"⚪ Disabled"}</strong><small>${escapeHtml(a.health?.last_tested_at||"Never tested")}</small><span class="episode-row-actions"><button class="btn secondary adapter-toggle" data-id="${escapeHtml(a.id)}" data-enabled="${a.enabled?"0":"1"}">${a.enabled?"Disable":"Enable"}</button><button class="btn secondary adapter-retest" data-id="${escapeHtml(a.id)}">Retest</button><button class="btn secondary adapter-delete" data-id="${escapeHtml(a.id)}">Delete</button></span></div>`).join("")||"No saved adapters."}</div></section>`);grid.querySelectorAll(".adapter-toggle").forEach(b=>b.onclick=async()=>{await api("/api/adapters/toggle",{method:"POST",body:JSON.stringify({adapterId:b.dataset.id,enabled:b.dataset.enabled==="1"})});renderAdmin()});grid.querySelectorAll(".adapter-retest").forEach(b=>b.onclick=async()=>{const a=(body.adapters||[]).find(x=>x.id===b.dataset.id);if(!a)return;await api("/api/adapters/retest",{method:"POST",body:JSON.stringify({adapterId:a.id,adapter:a,examplePageUrl:`https://${a.domains[0]}`})});renderAdmin()});grid.querySelectorAll(".adapter-delete").forEach(b=>b.onclick=async()=>{if(!confirm("Delete this adapter?"))return;await api("/api/adapters/delete",{method:"POST",body:JSON.stringify({adapterId:b.dataset.id})});renderAdmin()});}catch(_){}}
    function enhanceAdapterMaker(){const wrap=$("adapterOverrideWrap"),save=$("adapterSave");if(!wrap||!save||$("adapterUseInSearch"))return;wrap.insertAdjacentHTML("beforebegin",'<label class="pending-note"><input id="adapterUseInSearch" type="checkbox" checked> Use this adapter in main search</label>');save.addEventListener("click",async event=>{event.stopImmediatePropagation();const preview=$("adapterPreview"),result=$("adapterResult");try{const adapter=JSON.parse(preview.textContent);adapter.enabled=$("adapterUseInSearch").checked;let mode="";for(;;){try{const body=await api("/api/adapters/save",{method:"POST",body:JSON.stringify({adapter,saveMode:mode})});result.innerHTML=`<strong>✅ Adapter saved successfully</strong><br>Name: ${escapeHtml(body.adapter.name)}<br>File: ${escapeHtml(body.storage_location)}<br>Main search: ${body.main_search_enabled?"Enabled":"Disabled — retest needed"}`;showToast(`Adapter ${body.adapter.name} saved`);await loadSearchSources();break;}catch(error){if(!/already exists/i.test(error.message||""))throw error;const choice=window.prompt("An adapter with this ID already exists. Type UPDATE to replace it, NEW to save a separate copy, or leave blank to cancel.","");if(!choice)break;mode=/^update$/i.test(choice)?"update":/^new$/i.test(choice)?"new":"";if(!mode){result.textContent="Save cancelled. Type UPDATE or NEW when asked.";break;}}}}catch(error){result.textContent=`❌ ${error.message}`;}},true);const edit=sessionStorage.getItem("adapter-edit");if(edit){try{const a=JSON.parse(edit);$("adapterName").value=a.name||"";$("adapterMain").value=`https://${(a.domains||[])[0]||""}`;$("adapterPage").value=a.maker?.example_page_url||"";$("adapterQuery").value=a.maker?.search_query||"";$("adapterQuality").value=a.maker?.expected_quality||"";$("adapterUseInSearch").checked=Boolean(a.enabled);$("adapterPreview").textContent=JSON.stringify(a,null,2);$("adapterResult").textContent="Editing saved adapter. Click Analyze Site to retest it before saving.";sessionStorage.removeItem("adapter-edit");}catch(_){}}}
    async function renderSavedAdapters(){try{const body=await api("/api/adapters"),summary=body.summary||{},adapters=body.adapters||[],invalid=body.invalid_files||[];const grid=libraryViewEl.querySelector(".admin-grid");if(!grid||$("savedAdapters"))return;const statusIcon=s=>({Working:"🟢",Failed:"🔴",Disabled:"⚪","Needs retest":"🟡"}[s]||"🟡");grid.insertAdjacentHTML("beforeend",`<section class="admin-panel" id="savedAdapters"><div class="library-toolbar"><h3>Saved Adapters</h3><button class="btn secondary" id="refreshAdapters">Refresh List</button></div><div class="admin-kpi"><span>Total Saved<b>${Number(summary.total||0)}</b></span><span>Enabled in Search<b>${Number(summary.enabled||0)}</b></span><span>Working<b>${Number(summary.working||0)}</b></span><span>Failed<b>${Number(summary.failed||0)}</b></span><span>Disabled<b>${Number(summary.disabled||0)}</b></span></div><div class="pending-note">Adapter storage: <strong>${escapeHtml(body.storage_directory||"adapters/")}</strong></div><div class="row"><div class="field"><label>Find adapter</label><input id="adapterFilter" placeholder="Name or domain"></div><div class="field"><label>Status</label><select id="adapterStatus"><option value="">All</option><option>Working</option><option>Needs retest</option><option>Failed</option><option>Disabled</option><option>Enabled</option></select></div></div><div class="admin-list" id="savedAdapterRows"></div></section>`);const rows=$("savedAdapterRows");const draw=()=>{const term=$("adapterFilter").value.toLowerCase(),filter=$("adapterStatus").value;const visible=adapters.filter(a=>{const text=`${a.name} ${(a.domains||[]).join(" ")}`.toLowerCase();return (!term||text.includes(term))&&(!filter||(filter==="Enabled"?a.enabled:a.display_status===filter));});rows.innerHTML=[...visible.map(a=>`<div class="admin-row"><strong>${statusIcon(a.display_status)} ${escapeHtml(a.name)}</strong><small>ID: ${escapeHtml(a.id)} · Domain: ${escapeHtml((a.domains||[])[0]||"—")}<br>Status: ${escapeHtml(a.display_status)} · Main Search: ${a.enabled?"Enabled":"No"}<br>Last tested: ${escapeHtml(a.health?.last_tested_at?displayTime(a.health.last_tested_at):"Never")} · ${escapeHtml(a.health?.last_test_status||"Not tested")}<br>File: ${escapeHtml(a.storage_location||a.file_name||"")}</small><span class="episode-row-actions"><button class="btn secondary saved-retest" data-id="${escapeHtml(a.id)}">Test</button><button class="btn secondary saved-toggle" data-id="${escapeHtml(a.id)}" data-enabled="${a.enabled?"0":"1"}">${a.enabled?"Disable":"Enable"}</button><button class="btn secondary saved-edit" data-id="${escapeHtml(a.id)}">Edit</button><details><summary>View details</summary><pre>${escapeHtml(JSON.stringify(a,null,2))}</pre></details><button class="btn secondary saved-delete" data-id="${escapeHtml(a.id)}">Delete</button></span></div>`),...invalid.map(f=>`<div class="admin-row"><strong>🔴 Invalid adapter file</strong><small>${escapeHtml(f.storage_location)} · ${escapeHtml(f.error||"Invalid JSON")}</small></div>`)].join("")||'<div class="pending-note">No saved adapters match this filter.</div>';rows.querySelectorAll(".saved-toggle").forEach(b=>b.onclick=async()=>{await api("/api/adapters/toggle",{method:"POST",body:JSON.stringify({adapterId:b.dataset.id,enabled:b.dataset.enabled==="1"})});renderAdmin();});rows.querySelectorAll(".saved-retest").forEach(b=>b.onclick=async()=>{const a=adapters.find(x=>x.id===b.dataset.id);if(!a)return;b.disabled=true;b.textContent="Testing…";try{await api("/api/adapters/retest",{method:"POST",body:JSON.stringify({adapterId:a.id,adapter:a,examplePageUrl:a.maker?.example_page_url||`https://${a.domains[0]}`})});renderAdmin();}catch(e){setStatus(e.message,true);b.disabled=false;b.textContent="Test";}});rows.querySelectorAll(".saved-edit").forEach(b=>b.onclick=()=>{const a=adapters.find(x=>x.id===b.dataset.id);if(a){sessionStorage.setItem("adapter-edit",JSON.stringify(a));libraryState.view="adapters";history.pushState({view:"adapters"},"",libraryRoute("adapters"));renderLibrary();}});rows.querySelectorAll(".saved-delete").forEach(b=>b.onclick=async()=>{if(!confirm("Delete this adapter file?"))return;await api("/api/adapters/delete",{method:"POST",body:JSON.stringify({adapterId:b.dataset.id})});renderAdmin();});};$("adapterFilter").oninput=draw;$("adapterStatus").onchange=draw;$("refreshAdapters").onclick=renderAdmin;draw();}catch(error){setStatus(error.message||"Saved Adapters unavailable",true);}}
    async function renderLibrary() {
      if(libraryState.view==="movies") return renderCollection("movie");
      if(libraryState.view==="tv") return renderCollection("tv");
      if(libraryState.view==="missing") return renderMissing();
      if(libraryState.view==="sources") return renderSources();
      if(libraryState.view==="admin") {
        return renderAdmin();
      }
      return renderRecent();
    }
    async function startLibraryScan() { try { const body=await api("/api/admin/library/scan",{method:"POST",body:JSON.stringify({kind:"full"})}); const jellyfin=body.jellyfin||{}; const jellyfinNote=jellyfin.started?" + Jellyfin refresh started":(jellyfin.configured?" (Jellyfin refresh unavailable)":" (Jellyfin not configured)"); setStatus(`Library scan started${jellyfinNote}`); const timer=setInterval(async()=>{try{const state=await api(`/api/library/scan/status/${body.jobId}`); const p=state.job?.progress||{}; setStatus(`Scanning local library${jellyfin.started?" + Jellyfin":""} • ${p.filesChecked||0} files • ${p.matchingItems||0} matched`); if(state.job?.status!=="running"){clearInterval(timer); setStatus(`Local scan ${state.job?.status||"completed"}${jellyfin.started?"; Jellyfin is refreshing in background":""}`); renderLibrary();}}catch(_){clearInterval(timer)}},1500);}catch(e){setStatus(e.message,true)} }
    function libraryLinkRows(links) {
      if (!links.length) return '<div class="pending-note">Direct link nahi mila. Source par show/season page available ho sakta hai.</div>';
      return `<div class="library-link-list">${links.map((link, index) => {
        const pending=Boolean(link.pending), key=link.resolve_url||link.url||"";
        const data=`data-url="${escapeHtml(link.url||"")}" data-resolve="${escapeHtml(link.resolve_url||"")}" data-pending="${pending?"1":"0"}" data-key="${escapeHtml(key)}" data-kind="${escapeHtml(link.kind||"")}"`;
        return `<div class="file-row library-link-row"><span><b class="link-quality">${index+1}. ${escapeHtml(link.quality_label||link.quality||"Link")}</b>${link.variant?` · ${escapeHtml(link.variant)}`:""}<small>${escapeHtml(link.size||"")}</small></span><span class="episode-row-actions"><button class="btn secondary library-copy" ${data}>${pending?"Get Link":"Copy"}</button><button class="btn secondary library-open" ${data}>Open</button><button class="btn secondary library-telegram icon-btn" ${data} aria-label="Copy Telegram message and open bot" title="Copy Telegram message and open bot">${telegramIconSvg()}</button></span></div>`;
      }).join("")}</div>`;
    }
    async function resolveLibraryLink(button) {
      let url=button.dataset.url||"";
      if (button.dataset.pending !== "1") return url;
      const resolveUrl=button.dataset.resolve||"";
      if (!resolveUrl) throw new Error("Direct link source missing");
      button.disabled=true; button.textContent="Getting…";
      try {
        const body=await api("/api/resolve",{method:"POST",body:JSON.stringify({url:resolveUrl})});
        url=body.url||"";
        if (!url) throw new Error("Direct link not found");
        const row=button.closest(".library-link-row");
        row?.querySelectorAll("[data-resolve]").forEach(action=>{action.dataset.url=url;action.dataset.pending="0"});
        button.textContent="Copy";
        return url;
      } finally { button.disabled=false; }
    }
    function bindLibraryLinks(panel) {
      panel.querySelectorAll(".library-copy").forEach(button=>button.onclick=async()=>{try {const url=await resolveLibraryLink(button);if(!url) return;const copied=await copyText(url);button.textContent=copied?"Copied":"Select";setTimeout(()=>button.textContent="Copy",1200);}catch(error){setStatus(error.message,true);}});
      panel.querySelectorAll(".library-open").forEach(button=>button.onclick=async()=>{const popup=button.dataset.pending==="1"?window.open("","_blank"):null;try {const url=await resolveLibraryLink(button);if(!url) throw new Error("Direct link not found");if(popup) popup.location.href=url;else window.open(url,"_blank");}catch(error){popup?.close();setStatus(error.message,true);}});
      panel.querySelectorAll(".library-telegram").forEach(button=>button.onclick=async()=>{try {const url=await resolveLibraryLink(button);if(!url) return;const message=button.dataset.kind==="Season Zip"?`/m ${url} -e -up rcl`:`/m ${url} -up rcl`;const copied=await copyText(message);if(!copied) throw new Error("Telegram message copy nahi hua");setStatus("Telegram message copied");showToast("Message copied. Paste it in Telegram and press Send.");window.open("https://t.me/HEROKU8267BOT","_blank");}catch(error){setStatus(error.message||"Telegram action failed",true);}});
    }
    async function findFromLibrary(item, episode=null, targetPanel=null, season=null) {
      const panel=targetPanel || $("libraryLinkResults");
      if (!panel) return;
      const suffix=episode?` S${String(episode.seasonNumber||0).padStart(2,"0")}E${String(episode.episodeNumber||0).padStart(2,"0")} ${episode.quality||""} ${(episode.languages||[]).join(" ")}`:"";
      const query=`${item.title}${item.year?` ${item.year}`:""}${suffix}`.trim();
      // Series indexes frequently omit the year. A season ZIP lookup should
      // start with the canonical show title, while episode search keeps its
      // existing fallback-aware query path.
      const searchQuery=season ? item.title : query;
      const lookupLabel=episode?`Finding direct link for S${String(episode.seasonNumber).padStart(2,"0")}E${String(episode.episodeNumber).padStart(2,"0")}…`:season?`Finding Season ${season} ZIP links…`:"Finding links…";
      panel.innerHTML=`<div class="pending-note">${lookupLabel}</div>`;
      try {
        const searchResult=await api(`/api/search?q=${encodeURIComponent(searchQuery)}`);
        const candidates=searchResult.candidates||[];
        // Search indexes often put an older season ahead of the show page.
        // Never hand a clearly different season to the resolver just because it
        // happens to be the first result. Prefer the requested season, then an
        // unqualified show page; reject a results page made only of other seasons.
        const seasonValue=(candidate)=>`${candidate?.title||""} ${candidate?.url||""}`;
        const explicitSeason=(candidate)=>{
          const match=seasonValue(candidate).match(/\b(?:season\s*|s)0*(\d{1,2})\b/i);
          return match?Number(match[1]):null;
        };
        const candidate=season?candidates.find(value=>explicitSeason(value)===Number(season)) || candidates.find(value=>explicitSeason(value)===null):candidates[0];
        if (!candidate) throw new Error("Source par title nahi mila.");
        if (season && explicitSeason(candidate)!==null && explicitSeason(candidate)!==Number(season)) {
          throw new Error(`Source par Season ${season} ka result nahi mila; doosre season ke links hide kar diye gaye.`);
        }
        const result=await api("/api/find",{method:"POST",body:JSON.stringify({query:searchQuery,candidate,quality:state.quality,episodeTarget:episode?{season:Number(episode.seasonNumber),episode:Number(episode.episodeNumber)}:null,seasonTarget:season?Number(season):null})});
        const target=episode?`<div class="pending-note">${result.episodeFallback?"Exact episode nahi mila — season/show fallback shown.":`Direct episode result: S${String(episode.seasonNumber).padStart(2,"0")}E${String(episode.episodeNumber).padStart(2,"0")}`}</div>`:season?`<div class="pending-note">Season ${season} ZIP links only</div>`:"";
        panel.innerHTML=target+libraryLinkRows(result.links||[]);
        bindLibraryLinks(panel);
      } catch(error) { panel.innerHTML=`<div class="pending-note">${escapeHtml(error.message||"Link lookup failed.")}</div>`; setStatus(error.message||"Link lookup failed",true); }
    }
    function libraryImportPanel(item) { if (!item.needsMatch && !item.tmdb_id) return ""; const match= item.needsMatch ? `<p class="import-match-copy">Search TMDB, choose the correct title, then preview the exact folder and filenames before any change is made.</p><div class="match-search"><input id="tmdbQuery" aria-label="Search TMDB" value="${escapeHtml(item.title)}"><button class="btn secondary" id="tmdbSearch">Search TMDB</button></div><div class="tmdb-results" id="tmdbResults" aria-live="polite"></div>` : `<p class="import-match-copy">Matched to TMDB. File renaming is optional and always shows a preview first.</p><button class="btn secondary" id="renamePreview">Preview safe rename</button><div id="renameReview" aria-live="polite"></div>`; return `<section class="detail-section import-match" id="libraryImport"><h3>${item.needsMatch?"Identify this title":"Organize local files"}</h3>${match}</section>`; }
    function bindLibraryImport(item, kind, id) {
      $("tmdbSearch")?.addEventListener("click", async () => {
        const output=$("tmdbResults"), query=$("tmdbQuery").value.trim();
        if (!query) return;
        output.textContent="Searching TMDB…";
        try {
          const body=await api("/api/admin/library/tmdb-search", {method:"POST",body:JSON.stringify({type:kind,query})});
          const rows=body.items||[];
          output.innerHTML=rows.length ? rows.map(row => {
            const poster=row.posterPath ? libraryImage(`/api/tmdb-image?path=w92${row.posterPath}`) : "";
            return `<button class="tmdb-choice" data-id="${Number(row.id)||0}">${poster?`<img src="${escapeHtml(poster)}" alt="">`:'<span class="tmdb-poster-placeholder" aria-hidden="true"></span>'}<span><strong>${escapeHtml(row.title||"Untitled")}</strong><br><small>${escapeHtml(row.year||"Year unknown")}</small></span><span>Match</span></button>`;
          }).join("") : '<div class="pending-note">No TMDB results. Try a shorter title or add the year.</div>';
          output.querySelectorAll(".tmdb-choice").forEach(button => button.onclick=async () => {
            button.disabled=true; button.lastElementChild.textContent="Matching…";
            try {
              await api("/api/admin/library/manual-match", {method:"POST",body:JSON.stringify({itemId:item.id,tmdbId:Number(button.dataset.id)})});
              showToast("TMDB match saved — review the rename preview when ready");
              await openLibraryDetail(kind,id,{push:false,season:libraryState.detail?.season});
            } catch(error) { button.disabled=false; button.lastElementChild.textContent="Match"; showToast(error.message||"TMDB match failed"); }
          });
        } catch(error) { output.innerHTML=`<div class="pending-note">${escapeHtml(error.message||"TMDB search failed.")}</div>`; }
      });
      $("renamePreview")?.addEventListener("click", async () => {
        const review=$("renameReview"), button=$("renamePreview");
        button.disabled=true; button.textContent="Building preview…";
        try {
          const body=await api("/api/admin/library/rename-preview", {method:"POST",body:JSON.stringify({itemId:item.id})});
          const plan=body.plan, moves=plan.moves||[];
          review.innerHTML=`<div class="rename-preview"><strong>${plan.writable?"Nothing has been renamed yet. Review the planned changes:":"This media folder is read-only; preview only:"}</strong>${moves.map(move=>`<div class="rename-move"><span>${escapeHtml(move.from)}</span><b>→</b><span>${escapeHtml(move.to)}</span></div>`).join("")}${plan.writable?'<label class="rename-confirm"><input type="checkbox" id="renameConfirm">I checked this preview. Rename these local files now.</label><button class="btn" id="applyRename" disabled>Rename files</button>':"<div class='pending-note'>Enable write access to the media folder before applying this preview.</div>"}</div>`;
          $("renameConfirm")?.addEventListener("change", event => { $("applyRename").disabled=!event.target.checked; });
          $("applyRename")?.addEventListener("click", async () => {
            const apply=$("applyRename"); apply.disabled=true; apply.textContent="Renaming…";
            try {
              const result=await api("/api/admin/library/rename", {method:"POST",body:JSON.stringify({itemId:item.id,confirmed:true})});
              showToast(`${result.renamed||0} file(s) renamed`);
              await openLibraryDetail(kind,id,{push:false,season:libraryState.detail?.season});
            } catch(error) { apply.disabled=false; apply.textContent="Rename files"; showToast(error.message||"Rename failed"); }
          });
        } catch(error) { review.innerHTML=`<div class="pending-note">${escapeHtml(error.message||"Could not prepare rename preview.")}</div>`; }
        finally { button.disabled=false; button.textContent="Preview safe rename"; }
      });
    }
    async function openLibraryDetail(kind,id,options={}) { try { const {push=true,season=null}=options; if(push){ saveLibraryScroll(); history.pushState({view:kind==="movie"?"movies":"tv",detail:{kind,id,season},scrollY:window.scrollY},"",libraryDetailUrl(kind,id,season)); } libraryState.detail={kind,id,season}; const body=await api(`/api/library/${kind==="movie"?"movies":"tv"}/${id}`), item=body.item; if(!item) throw new Error("Item unavailable"); let seasons=[]; if(kind==="tv") seasons=(await api(`/api/library/tv/${id}/seasons`)).items||[]; const poster=libraryImage(item.posterUrl), backdrop=libraryImage(item.backdropUrl); const files=(item.files||[]).slice(0,12).map(f=>`<div class="file-row"><span>${escapeHtml(f.filename)}</span><span>${escapeHtml(f.resolution||"")} · ${bytes(f.size)}</span></div>`).join(""); const seasonHtml=seasons.map(s=>`<details class="season-accordion" data-season="${s.seasonNumber}" ${Number(season)===Number(s.seasonNumber)?"open":""}><summary>Season ${s.seasonNumber} · ${s.availableEpisodes}/${s.totalEpisodes||"?"} ${statusBadge(s.status)}</summary><div class="episode-list">${(s.episodes||[]).map(e=>{const state=e.available?"available":"missing"; const label=e.available?(e.quality||"Available"):"Missing"; return `<div class="episode-entry"><div class="episode-row ${state}" data-available="${e.available?"true":"false"}"><span>E${String(e.episodeNumber||0).padStart(2,"0")} · ${escapeHtml(e.title||e.quality||"Episode")} · ${escapeHtml(label)}</span><span class="episode-row-actions"><button class="btn secondary episode-find" data-season="${s.seasonNumber}" data-episode="${e.episodeNumber}" data-quality="${escapeHtml(e.quality||"")}" data-lang="${escapeHtml((e.languages||[]).join(" "))}">Find Link</button></span></div><div class="episode-link-result" aria-live="polite"></div></div>`}).join("")||'<div class="pending-note">No local episode files.</div>'}</div></details>`).join(""); libraryDetailEl.innerHTML=`<div class="detail-sheet"><div class="detail-hero" style="background-image:${backdrop?`url('${escapeHtml(backdrop)}')`:"none"}">${poster?`<img class="detail-poster" src="${escapeHtml(poster)}" alt="">`:""}<div><h2 class="detail-title">${escapeHtml(item.title)}</h2><div class="library-card-meta">${escapeHtml(item.year||"")} · ${escapeHtml(item.originalTitle||item.original_language||"Unknown")} · ${escapeHtml((item.genres||[]).join(", "))} ${item.runtime?`· ${escapeHtml(item.runtime)} min`:""} ${item.rating?`· ★ ${Number(item.rating).toFixed(1)}`:""}</div><p class="detail-overview">${escapeHtml(item.overview||"No overview available.")}</p><div class="detail-actions">${kind==="movie"?'<button class="btn" id="detailFind">Find Links</button>':""}<button class="btn secondary" id="detailClose">Close</button><button class="btn secondary" id="detailRefresh">Refresh Library</button></div></div></div><div class="detail-body"><section class="detail-section"><h3>Local availability</h3><div class="library-card-meta">${statusBadge(item.needsMatch?"NEEDS MATCH":item.available?"AVAILABLE":"MISSING")} <span>${item.total_files||0} files</span><span>${bytes(item.total_size)}</span><span>${escapeHtml((item.qualities||[]).join(" · ")||"Unknown quality")}</span></div></section>${libraryImportPanel(item)}${kind==="movie"?'<section class="detail-section" id="libraryLinkResults"></section>':""}${kind==="tv"?`<section class="detail-section"><h3>Seasons</h3>${seasonHtml||'<div class="pending-note">Season metadata will appear after matching.</div>'}</section>`:`<section class="detail-section"><h3>Files</h3><div class="file-list">${files||'<div class="pending-note">No active local files.</div>'}</div></section>`}</div></div>`; libraryDetailEl.classList.add("open"); bindLibraryImport(item,kind,id); $("detailClose").onclick=closeLibraryDetail; $("detailFind")?.addEventListener("click",()=>findFromLibrary(item)); $("detailRefresh").onclick=async()=>{const button=$("detailRefresh"); button.disabled=true; button.textContent="Checking files…"; try { const scan=await api(`/api/admin/library/refresh/${item.id}`,{method:"POST",body:"{}"}); const wait=async()=>{const state=await api(`/api/library/scan/status/${scan.jobId}`); if(state.job?.status==="running") return setTimeout(wait,500); if(state.job?.status!=="completed") throw new Error(state.job?.error||"Library refresh finished with warnings"); await openLibraryDetail(kind,id,{push:false,season:libraryState.detail?.season}); showToast("Library refreshed — episode availability updated"); }; await wait(); } catch(error) { button.disabled=false; button.textContent="Refresh Library"; showToast(error.message||"Library refresh failed"); }}; libraryDetailEl.querySelectorAll(".season-accordion").forEach(detail=>detail.addEventListener("toggle",()=>{if(!detail.open)return; libraryState.detail={kind,id,season:Number(detail.dataset.season)}; history.replaceState({...history.state,detail:libraryState.detail},"",libraryDetailUrl(kind,id,libraryState.detail.season));})); libraryDetailEl.querySelectorAll(".episode-find").forEach(b=>b.onclick=()=>{libraryDetailEl.querySelectorAll(".episode-link-result").forEach(panel=>{if(panel!==b.closest(".episode-entry")?.querySelector(".episode-link-result")) panel.innerHTML="";});findFromLibrary(item,{seasonNumber:b.dataset.season,episodeNumber:b.dataset.episode,quality:b.dataset.quality,languages:b.dataset.lang.split(" ")},b.closest(".episode-entry")?.querySelector(".episode-link-result"));}); } catch(e){setStatus(e.message,true)} }
    function attachSeasonZipActions() {
      libraryDetailEl.querySelectorAll(".season-accordion").forEach(detail => {
        if (detail.dataset.seasonZipBound === "1") return;
        const seasonNumber=Number(detail.dataset.season), episodeList=detail.querySelector(".episode-list");
        if (!seasonNumber || !episodeList) return;
        detail.dataset.seasonZipBound="1";
        const tools=document.createElement("div");
        tools.className="season-zip-tools";
        tools.innerHTML=`<button class="btn secondary season-zip-find">Find Season ${seasonNumber} ZIP Links</button><div class="season-zip-result" aria-live="polite"></div>`;
        episodeList.before(tools);
        tools.querySelector(".season-zip-find").onclick=async()=>{
          const current=libraryState.detail;
          if (!current?.id) return;
          try {
            const body=await api(`/api/library/tv/${current.id}`), item=body.item;
            if (!item) throw new Error("Show unavailable");
            findFromLibrary(item,null,tools.querySelector(".season-zip-result"),seasonNumber);
          } catch(error) {
            tools.querySelector(".season-zip-result").innerHTML=`<div class="pending-note">${escapeHtml(error.message||"Season ZIP lookup failed.")}</div>`;
          }
        };
      });
    }
    new MutationObserver(attachSeasonZipActions).observe(libraryDetailEl,{childList:true,subtree:true});
    function closeLibraryDetail(){ const wasDeepLink=Boolean(libraryDetailFromUrl()); libraryDetailEl.classList.remove("open");libraryDetailEl.innerHTML="";libraryState.detail=null; if(wasDeepLink){ history.replaceState({view:libraryState.view},"",libraryRoute(libraryState.view)); restoreLibraryScroll(); } }
    document.querySelectorAll("#appNav [data-view],#bottomNav [data-view]").forEach(button=>button.addEventListener("click",()=>navigate(button.dataset.view)));
    window.addEventListener("popstate",async()=>{const view=libraryViewFromPath(), detail=libraryDetailFromUrl(); await navigate(view,false); if(detail) openLibraryDetail(detail.kind,detail.id,{push:false,season:detail.season}); else {closeLibraryDetail();restoreLibraryScroll();}}); libraryDetailEl.addEventListener("click",e=>{if(e.target===libraryDetailEl)closeLibraryDetail()}); window.addEventListener("scroll",()=>backToTopEl.classList.toggle("visible",window.scrollY>700)); backToTopEl.onclick=()=>window.scrollTo({top:0,behavior:"smooth"});
    if(isLibraryRoute()) { const detail=libraryDetailFromUrl(); navigate(libraryViewFromPath(),false).then(()=>{ if(detail) openLibraryDetail(detail.kind,detail.id,{push:false,season:detail.season}); else restoreLibraryScroll(); }); }
    loadWallpapers();
  </script>
</body>
</html>
"""


def human_size(content_length: str) -> str:
    try:
        size = int(content_length)
    except (TypeError, ValueError):
        return "size unknown"
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.2f} {units[unit_index]}"


ARCHIVE_EXTENSIONS = (".zip", ".rar", ".7z")
MEDIA_FILENAME_RE = re.compile(r"[^/?#]+\.(?:mkv|mp4|webm|avi|m4v|mov|ts|zip|rar|7z)(?:$|[?#])", re.IGNORECASE)


def delivery_filename(value: str) -> str:
    """Extract a readable final filename; opaque delivery paths stay hidden."""
    text = str(value or "").strip()
    match = MEDIA_FILENAME_RE.search(text)
    return match.group(0).split("?", 1)[0].split("#", 1)[0] if match else ""


def delivery_kind_metadata(*values: str) -> dict[str, str]:
    """Use the same episode/archive labels for every source response shape."""
    text = " ".join(str(value or "") for value in values)
    combined_match = re.search(r"\bs0*(\d{1,2})[-_. ]*e0*(\d{1,3})\b", text, re.IGNORECASE)
    season_match = re.search(r"\b(?:season|s)[\s._-]*0*(\d{1,2})\b", text, re.IGNORECASE)
    episode_match = re.search(r"\b(?:episode|ep|e)[\s._-]*0*(\d{1,3})\b", text, re.IGNORECASE)
    filename = delivery_filename(text)
    lower = text.lower()
    season_number = combined_match.group(1) if combined_match else (season_match.group(1) if season_match else "")
    episode_number = combined_match.group(2) if combined_match else (episode_match.group(1) if episode_match else "")
    season = f"Season {int(season_number)}" if season_number else ""
    if episode_number:
        return {"season": season, "kind": f"Episode {int(episode_number):02d}"}
    if filename.lower().endswith(ARCHIVE_EXTENSIONS) or re.search(r"\b(?:season\s*(?:zip|pack)|complete\s+season|batch|pack)\b", lower):
        return {"season": season, "kind": "Season Zip" if season else "Archive"}
    return {"season": season, "kind": "Video file"}


def enrich_delivery_link(item: dict[str, Any]) -> dict[str, Any]:
    """Make delivery cards consistent regardless of which source resolved them."""
    result = dict(item)
    filename = delivery_filename(str(result.get("filename") or "")) or delivery_filename(str(result.get("url") or ""))
    text = " ".join(str(result.get(key) or "") for key in ("filename", "variant", "page_url", "url"))
    inferred = delivery_kind_metadata(text)
    result["filename"] = filename
    result["season"] = str(result.get("season") or inferred["season"])
    result["kind"] = str(result.get("kind") or inferred["kind"])
    size = str(result.get("size") or "").strip()
    if size.lower() in {"", "verified", "unknown", "size unknown"}:
        header_size = human_size(str(result.get("content_length") or ""))
        result["size"] = header_size if header_size != "size unknown" else "Size unavailable"
    return result


def clean_movie_title_for_tmdb(title: str) -> tuple[str, str, bool]:
    cleaned = html.unescape(re.sub(r"<[^>]+>", " ", title or ""))
    is_series = bool(
        re.search(r"\b(season|episode|web\s*series|series|s\d{1,2}|e\d{1,2})\b", cleaned, flags=re.IGNORECASE)
    )
    cleaned = re.sub(r"\b(download|watch|movie|full)\b", " ", cleaned, flags=re.IGNORECASE)
    year = ""
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", cleaned)
    if year_match:
        year = year_match.group(1)
    cleaned = re.sub(r"\[[^\]]+\]", " ", cleaned)
    cleaned = re.sub(r"\((?!\s*(?:19\d{2}|20\d{2})\s*\))[^)]*\)", " ", cleaned)
    cleaned = re.sub(r"\b(?:season|series)\s*\d+(?:\s*[-–]\s*\d+)?\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bs\d{1,2}(?:\s*[-–]\s*s?\d{1,2})?\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:episode|ep)\s*\d+(?:\s*[-–]\s*\d+)?\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\be\d{1,2}(?:\s*[-–]\s*e?\d{1,2})?\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.split(
        r"\b(?:480p|720p|1080p|2160p|4k|dual\s+audio|multi\s+audio|hindi\s+dubbed|web\s+series)\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    cleaned = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", cleaned)
    cleaned = re.sub(
        r"\b(480p|720p|1080p|2160p|4k|uhd|hd|imax|extended|directors?|theatrical|uncut|cut|hdrip|webrip|web-dl|bluray|brrip|dvdrip|x264|x265|hevc|10bit|esub|msubs|subtitles|hindi|english|dual|audio|org|multi|season|series|amazon|prime|netflix|hotstar|disney|zee5|sonyliv|jio|cinema|mx|player|bollywood|tollywood|kollywood|south)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", cleaned).strip()
    return cleaned, year, is_series


def normalized_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", html.unescape(value or "").lower()).strip()


def result_year(result: dict[str, Any]) -> str:
    value = str(result.get("release_date") or result.get("first_air_date") or "")
    match = re.match(r"(\d{4})", value)
    return match.group(1) if match else ""


def tmdb_result_score(result: dict[str, Any], query: str, year: str, prefer_tv: bool) -> float:
    poster_path = str(result.get("poster_path") or "")
    if not poster_path.startswith("/"):
        return -1

    query_norm = normalized_title(query)
    names = [
        normalized_title(str(result.get(key) or ""))
        for key in ("title", "name", "original_title", "original_name")
    ]
    names = [name for name in names if name]
    if not query_norm or not names:
        return -1

    query_tokens = set(query_norm.split())
    best_name_score = 0.0
    for name in names:
        name_tokens = set(name.split())
        overlap = len(query_tokens & name_tokens) / max(1, len(query_tokens | name_tokens))
        if name == query_norm:
            best_name_score = max(best_name_score, 100)
        elif query_norm in name or name in query_norm:
            best_name_score = max(best_name_score, 78)
        else:
            best_name_score = max(best_name_score, overlap * 70)

    media_type = str(result.get("media_type") or "")
    if not media_type:
        media_type = "tv" if result.get("name") or result.get("first_air_date") else "movie"
    media_score = 18 if (prefer_tv and media_type == "tv") or (not prefer_tv and media_type == "movie") else -8
    year_score = 0
    if year:
        candidate_year = result_year(result)
        if candidate_year == year:
            year_score = 16
        elif candidate_year:
            year_score = -6
    popularity = min(float(result.get("popularity") or 0), 120) / 12
    return best_name_score + media_score + year_score + popularity


def tmdb_poster_url(title: str, timeout: float = 4.0) -> str:
    if not TMDB_API_KEY:
        return ""
    query, year, is_series = clean_movie_title_for_tmdb(title)
    if not query:
        return ""
    cache_key = f"{query.lower()}|{year}|{'tv' if is_series else 'movie'}"
    cached = TMDB_POSTER_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < TMDB_CACHE_SECONDS:
        return cached[1]

    base_params = f"api_key={quote_plus(TMDB_API_KEY)}&query={quote_plus(query)}&include_adult=false"
    movie_params = base_params + (f"&year={quote_plus(year)}" if year else "")
    tv_params = base_params + (f"&first_air_date_year={quote_plus(year)}" if year else "")
    urls = [
        f"https://api.themoviedb.org/3/search/tv?{tv_params}",
        f"https://api.themoviedb.org/3/search/movie?{movie_params}",
        f"https://api.themoviedb.org/3/search/multi?api_key={quote_plus(TMDB_API_KEY)}&query={quote_plus(query)}&include_adult=false",
    ]
    if not is_series:
        urls[0], urls[1] = urls[1], urls[0]
    poster = ""
    try:
        results: list[dict[str, Any]] = []
        for url in urls:
            request = Request(
                url,
                headers={"accept": "application/json", "user-agent": "EvidenceLinkHelper/1.0"},
            )
            with urlopen(request, timeout=timeout) as handle:
                payload = json.loads(handle.read(750_000).decode("utf-8", errors="replace"))
            endpoint_media = "tv" if "/search/tv" in url else "movie" if "/search/movie" in url else ""
            for result in payload.get("results") or []:
                if result.get("media_type") == "person":
                    continue
                item = dict(result)
                if endpoint_media and not item.get("media_type"):
                    item["media_type"] = endpoint_media
                results.append(item)
        best = max(results, key=lambda item: tmdb_result_score(item, query, year, is_series), default={})
        if best and tmdb_result_score(best, query, year, is_series) >= 45:
            poster = f"{TMDB_IMAGE_BASE}{best['poster_path']}"
    except Exception as exc:
        print(f"TMDB poster lookup failed for {title!r}: {exc}")
    TMDB_POSTER_CACHE[cache_key] = (now, poster)
    return poster


def title_matches_search_query(title: str, search_query: str) -> bool:
    """Return true only when every searched title word is present as a word.

    This is intentionally stricter than substring matching: searching ``Ikka``
    must not treat ``Nikka`` as the same film.  It lets a clean user query
    rescue a noisy release title after the title-specific TMDB lookup fails.
    """
    query_tokens = set(normalized_title(search_query).split())
    title_tokens = set(normalized_title(title).split())
    return bool(query_tokens) and query_tokens <= title_tokens


def tmdb_poster_for_candidate(title: str, search_query: str = "") -> str:
    """Prefer the release-title lookup, then safely retry the exact query.

    Source titles commonly append distributor/language words that are not part
    of the TMDB title.  The fallback is limited to candidates whose visible
    title contains every query word, so unrelated search results never inherit
    the requested movie's poster.
    """
    poster = tmdb_poster_url(title)
    if poster or not title_matches_search_query(title, search_query):
        return poster
    return tmdb_poster_url(search_query)


def tmdb_backdrop_urls(timeout: float = 4.0) -> list[str]:
    global TMDB_BACKDROP_CACHE
    if not TMDB_API_KEY:
        return []
    now = time.time()
    cached_at, cached_images = TMDB_BACKDROP_CACHE
    if cached_images and now - cached_at < TMDB_CACHE_SECONDS:
        return cached_images

    urls = [
        f"https://api.themoviedb.org/3/trending/movie/day?api_key={quote_plus(TMDB_API_KEY)}",
        f"https://api.themoviedb.org/3/movie/now_playing?api_key={quote_plus(TMDB_API_KEY)}&region=IN",
    ]
    images: list[str] = []
    seen: set[str] = set()
    try:
        for url in urls:
            request = Request(
                url,
                headers={"accept": "application/json", "user-agent": "EvidenceLinkHelper/1.0"},
            )
            with urlopen(request, timeout=timeout) as handle:
                payload = json.loads(handle.read(750_000).decode("utf-8", errors="replace"))
            for result in payload.get("results") or []:
                path = str(result.get("backdrop_path") or "")
                if not path.startswith("/") or path in seen:
                    continue
                seen.add(path)
                images.append(f"{TMDB_BACKDROP_BASE}{path}")
                if len(images) >= 10:
                    break
            if len(images) >= 10:
                break
    except Exception as exc:
        print(f"TMDB backdrop lookup failed: {exc}")
    TMDB_BACKDROP_CACHE = (now, images)
    return images


def language_label(title: str) -> str:
    text = html.unescape(title or "")
    languages = [
        name for name, pattern in (
            ("Hindi", r"\bhindi\b"),
            ("English", r"\benglish\b"),
            ("Tamil", r"\btamil\b"),
            ("Telugu", r"\btelugu\b"),
            ("Malayalam", r"\bmalayalam\b"),
            ("Kannada", r"\bkannada\b"),
            ("Punjabi", r"\bpunjabi\b"),
            ("Bengali", r"\bbengali\b"),
            ("Marathi", r"\bmarathi\b"),
        )
        if re.search(pattern, text, flags=re.IGNORECASE)
    ]
    if re.search(r"\bmulti[\s-]*audio\b", text, flags=re.IGNORECASE):
        return "-".join(languages) if languages else "Multi Audio"
    if re.search(r"\bdual[\s-]*audio\b|\bdualaudio\b", text, flags=re.IGNORECASE):
        return "-".join(languages) if languages else "Hindi-English"
    return "-".join(languages) if languages else "Language unknown"


def media_identity(title: str) -> tuple[str, str]:
    cleaned, year, _ = clean_movie_title_for_tmdb(title)
    if not year:
        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", title or "")
        year = year_match.group(1) if year_match else ""
    return normalized_title(cleaned), year


def media_key(title: str, year: str = "") -> str:
    title_norm = normalized_title(title)
    return f"{title_norm}|{year}" if year else title_norm


def jellyfin_api_key_from_db(db_path: str = DEFAULT_JELLYFIN_API_KEY_DB) -> str:
    try:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(
                "SELECT AccessToken FROM ApiKeys WHERE AccessToken IS NOT NULL AND length(AccessToken) > 10 LIMIT 1"
            ).fetchone()
    except (OSError, sqlite3.Error):
        return ""
    return str(row[0]).strip() if row else ""


def _config_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    return [str(item).strip() for item in (value or []) if str(item).strip()]


def apply_persisted_configuration() -> None:
    """Load Admin Setup values into the running process without a restart."""
    global TMDB_API_KEY, JELLYFIN_URL, JELLYFIN_API_KEY, JELLYFIN_LIBRARY_IDS, MEDIA_LIBRARY_PATHS
    if LIBRARY is None:
        return
    settings = LIBRARY.settings()
    # Existing environment-based deployments are migrated as defaults on their
    # first boot; later Admin Setup saves take precedence.
    if not settings:
        settings = {
            "jellyfin_url": JELLYFIN_URL,
            "jellyfin_api_key": JELLYFIN_API_KEY,
            "tmdb_api_key": TMDB_API_KEY,
            "movie_paths": _config_list(os.environ.get("MOVIES_PATHS", "")),
            "tv_paths": _config_list(os.environ.get("TV_SHOWS_PATHS", "")),
            "min_file_size_mb": os.environ.get("LIBRARY_MIN_FILE_SIZE_MB", "30"),
            "auto_sync_minutes": 0,
            "jellyfin_library_ids": [],
            "path_mappings": [],
        }
        LIBRARY.update_settings(settings)
    TMDB_API_KEY = str(settings.get("tmdb_api_key") or TMDB_API_KEY).strip()
    LIBRARY.tmdb_key = TMDB_API_KEY
    JELLYFIN_URL = str(settings.get("jellyfin_url") or JELLYFIN_URL).strip().rstrip("/")
    JELLYFIN_API_KEY = str(settings.get("jellyfin_api_key") or JELLYFIN_API_KEY).strip()
    JELLYFIN_LIBRARY_IDS = set(_config_list(settings.get("jellyfin_library_ids")))
    movie_paths = _config_list(settings.get("movie_paths"))
    tv_paths = _config_list(settings.get("tv_paths"))
    if movie_paths:
        os.environ["MOVIES_PATHS"] = ",".join(movie_paths)
    if tv_paths:
        os.environ["TV_SHOWS_PATHS"] = ",".join(tv_paths)
    MEDIA_LIBRARY_PATHS = [str(Path(path).expanduser()) for path in [*movie_paths, *tv_paths] if Path(path).expanduser().exists()]
    if settings.get("min_file_size_mb") not in (None, ""):
        os.environ["LIBRARY_MIN_FILE_SIZE_MB"] = str(settings["min_file_size_mb"])
    # The legacy finder only understands its original compatible layout.  Do
    # not send generic selector-configured sources through it.
    sources = [source["base_url"] for source in LIBRARY.list_sources() if source["enabled"] and source.get("parser_type") == "compatible-search"]
    if not sources:
        for url in _config_list(os.environ.get("BOLLYFLIX_SITES", "")) or ["https://bollyflix.at"]:
            try:
                LIBRARY.save_source({"name": urlparse(url).netloc or url, "base_url": url, "enabled": True})
            except ValueError:
                pass
        sources = [source["base_url"] for source in LIBRARY.list_sources() if source["enabled"]]
    if sources:
        os.environ["BOLLYFLIX_SITES"] = ",".join(sources)


def migrate_verified_onboarding_adapters() -> int:
    """Enable internal workflow fallback for already verified Sources entries."""
    migrated = 0
    for adapter in ADAPTERS.list():
        if enable_workflow_fallback_for_verified_onboarding(adapter):
            ADAPTERS.replace(adapter)
            migrated += 1
    if migrated:
        print(f"Enabled workflow fallback for {migrated} verified onboarding adapter(s)")
    ENABLED_ADAPTERS[:] = ADAPTERS.list()
    return migrated


def public_setup_configuration() -> dict[str, Any]:
    if LIBRARY is None:
        return {}
    settings = LIBRARY.settings()
    return {
        "jellyfinUrl": settings.get("jellyfin_url") or JELLYFIN_URL,
        "jellyfinApiKeyConfigured": bool(settings.get("jellyfin_api_key") or JELLYFIN_API_KEY),
        "tmdbApiKeyConfigured": bool(settings.get("tmdb_api_key") or TMDB_API_KEY),
        "moviePaths": _config_list(settings.get("movie_paths") or os.environ.get("MOVIES_PATHS", "")),
        "tvPaths": _config_list(settings.get("tv_paths") or os.environ.get("TV_SHOWS_PATHS", "")),
        "jellyfinLibraryIds": _config_list(settings.get("jellyfin_library_ids")),
        "pathMappings": settings.get("path_mappings") or [],
        "minFileSizeMb": settings.get("min_file_size_mb") or os.environ.get("LIBRARY_MIN_FILE_SIZE_MB", "30"),
        "autoSyncMinutes": settings.get("auto_sync_minutes") or 0,
        "adminPasswordConfigured": bool(settings.get("admin_password_hash")),
        "sources": LIBRARY.list_sources(),
    }


def add_media_index_entry(index: dict[str, list[dict[str, str]]], title: str, year: str, path: Path, root: Path) -> None:
    title_norm = normalized_title(title)
    if not title_norm or title_norm in {"attachments", "subs", "subtitles", "sample"}:
        return
    item = {
        "title": title.strip(),
        "year": year,
        "path": str(path),
        "root": str(root),
    }
    keys = {media_key(title_norm), media_key(title_norm, year)}
    for key in keys:
        bucket = index.setdefault(key, [])
        if not any(existing.get("path") == item["path"] for existing in bucket):
            bucket.append(item)


def add_library_item_entry(index: dict[str, list[dict[str, str]]], item: dict[str, Any], source: str) -> None:
    year = str(item.get("ProductionYear") or item.get("year") or "")
    names = [
        str(item.get(key) or "").strip()
        for key in ("Name", "OriginalTitle", "SortName", "title")
    ]
    names = [name for name in names if name]
    if not names:
        return
    path = str(item.get("Path") or item.get("path") or "")
    provider_ids = item.get("ProviderIds") or {}
    library_item = {
        "title": names[0],
        "year": year,
        "path": path,
        "root": source,
        "source": source,
        "type": str(item.get("Type") or ""),
        "jellyfin_id": str(item.get("Id") or ""),
        "tmdb_id": str(provider_ids.get("Tmdb") or provider_ids.get("TmdbCollection") or ""),
        "imdb_id": str(provider_ids.get("Imdb") or ""),
    }
    for name in names:
        title_norm = normalized_title(name)
        if not title_norm:
            continue
        for key in {media_key(title_norm), media_key(title_norm, year)}:
            bucket = index.setdefault(key, [])
            if not any(existing.get("jellyfin_id") == library_item["jellyfin_id"] for existing in bucket):
                bucket.append(library_item)


def scan_jellyfin_library(timeout: float = 12.0) -> dict[str, list[dict[str, str]]]:
    global JELLYFIN_LIBRARY_CACHE
    if not JELLYFIN_URL or not JELLYFIN_API_KEY:
        return {}
    now = time.time()
    cached_at, cached_index = JELLYFIN_LIBRARY_CACHE
    if cached_index and now - cached_at < MEDIA_LIBRARY_CACHE_SECONDS:
        return cached_index

    base_url = JELLYFIN_URL.rstrip("/")
    index: dict[str, list[dict[str, str]]] = {}
    try:
        library_ids = JELLYFIN_LIBRARY_IDS or {""}
        for library_id in library_ids:
            params = {
                "Recursive": "true",
                "IncludeItemTypes": "Movie,Series",
                "Fields": "Path,ProviderIds,ProductionYear,OriginalTitle,SortName",
                "Limit": "10000",
            }
            if library_id:
                params["ParentId"] = library_id
            request = Request(
                f"{base_url}/Items?{urlencode(params)}",
                headers={
                    "accept": "application/json",
                    "user-agent": "EvidenceLinkHelper/1.0",
                    "X-Emby-Token": JELLYFIN_API_KEY,
                },
            )
            with urlopen(request, timeout=timeout) as handle:
                payload = json.loads(handle.read(20_000_000).decode("utf-8", errors="replace"))
            for item in payload.get("Items") or []:
                add_library_item_entry(index, item, "jellyfin")
    except Exception as exc:
        print(f"Jellyfin library scan failed: {exc}")
        return {}
    JELLYFIN_LIBRARY_CACHE = (now, index)
    return index


def trigger_jellyfin_library_refresh(timeout: float = 12.0) -> dict[str, Any]:
    """Start Jellyfin's own library refresh alongside a local library scan.

    Jellyfin performs this refresh asynchronously, so a successful request only
    means its scanner accepted the job; it does not wait for all media paths to
    finish indexing.  Local-library scanning deliberately remains independent
    so a temporary Jellyfin outage never blocks the dashboard's scan.
    """
    global JELLYFIN_LIBRARY_CACHE, JELLYFIN_SHOW_DETAIL_CACHE
    configured = bool(JELLYFIN_URL and JELLYFIN_API_KEY)
    if not configured:
        return {
            "configured": False,
            "started": False,
            "message": "Jellyfin API is not configured.",
        }
    request = Request(
        f"{JELLYFIN_URL.rstrip('/')}/Library/Refresh",
        data=b"",
        method="POST",
        headers={
            "accept": "application/json",
            "user-agent": "EvidenceLinkHelper/1.0",
            "X-Emby-Token": JELLYFIN_API_KEY,
        },
    )
    try:
        with urlopen(request, timeout=timeout):
            pass
    except Exception as exc:
        return {
            "configured": True,
            "started": False,
            "message": f"Jellyfin refresh could not be started: {exc}",
        }
    # The following library reads must not use a pre-refresh snapshot.
    JELLYFIN_LIBRARY_CACHE = (0, {})
    JELLYFIN_SHOW_DETAIL_CACHE.clear()
    return {
        "configured": True,
        "started": True,
        "message": "Jellyfin library refresh started.",
    }


def scan_media_library() -> dict[str, list[dict[str, str]]]:
    global MEDIA_LIBRARY_CACHE
    now = time.time()
    cached_at, cached_index = MEDIA_LIBRARY_CACHE
    if cached_index and now - cached_at < MEDIA_LIBRARY_CACHE_SECONDS:
        return cached_index

    index: dict[str, list[dict[str, str]]] = {}
    for root_text in MEDIA_LIBRARY_PATHS:
        root = Path(root_text).expanduser()
        if not root.is_dir():
            continue
        try:
            entries = list(root.iterdir())
        except OSError as exc:
            print(f"Media library scan skipped {root}: {exc}")
            continue
        for entry in entries:
            name = entry.stem if entry.is_file() else entry.name
            if entry.is_file() and entry.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            title, year, _ = clean_movie_title_for_tmdb(name)
            if not title:
                title = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", name).strip()
            if not year:
                year_match = re.search(r"\b(19\d{2}|20\d{2})\b", name)
                year = year_match.group(1) if year_match else ""
            add_media_index_entry(index, title, year, entry, root)
    MEDIA_LIBRARY_CACHE = (now, index)
    return index


def library_status_for_title(title: str) -> dict[str, str]:
    if not MEDIA_LIBRARY_PATHS and not (JELLYFIN_URL and JELLYFIN_API_KEY):
        return {}
    title_norm, year = media_identity(title)
    _, _, is_series = clean_movie_title_for_tmdb(title)
    if not title_norm:
        return {"library_status": "missing"}

    index = scan_jellyfin_library()
    source = "jellyfin" if index else "filesystem"
    if not index:
        index = scan_media_library()

    matches = index.get(media_key(title_norm, year)) if year else None
    if not matches:
        matches = index.get(media_key(title_norm))
    # A title can exist as both a movie and a TV show.  TV search cards must
    # only be matched against Jellyfin Series items, otherwise a same-named
    # movie can give a misleading availability badge.
    expected_type = "Series" if is_series else "Movie"
    if matches:
        typed_matches = [item for item in matches if not item.get("type") or item.get("type") == expected_type]
        # Search results for some TV pages omit words such as "Season" or
        # "Series". In that case an exact Jellyfin title is still reliable;
        # use it only when there is no same-titled item of the expected type.
        if typed_matches:
            matches = typed_matches
        elif is_series:
            matches = None
    if not matches and year:
        query_tokens = set(title_norm.split())
        best: dict[str, str] | None = None
        best_score = 0.0
        for bucket in index.values():
            for item in bucket:
                if item.get("type") and item.get("type") != expected_type:
                    continue
                if item.get("year") and item.get("year") != year:
                    continue
                item_norm = normalized_title(item.get("title", ""))
                item_tokens = set(item_norm.split())
                if not query_tokens or not item_tokens:
                    continue
                score = len(query_tokens & item_tokens) / max(1, len(query_tokens | item_tokens))
                if score > best_score:
                    best = item
                    best_score = score
        if best and best_score >= 0.92:
            matches = [best]
    if not matches:
        return {"library_status": "missing"}
    match = matches[0]
    return {
        "library_status": "available",
        "library_source": source,
        "library_title": match.get("title", ""),
        "library_year": match.get("year", ""),
        "library_path": match.get("path", ""),
        "library_root": match.get("root", ""),
        "jellyfin_id": match.get("jellyfin_id", ""),
        "tmdb_id": match.get("tmdb_id", ""),
        "library_type": match.get("type", ""),
    }


def jellyfin_show_availability(jellyfin_id: str, tmdb_id: str = "", timeout: float = 12.0) -> dict[str, Any]:
    """Return actual Jellyfin episode presence, compared with TMDB's episode totals."""
    if not JELLYFIN_URL or not JELLYFIN_API_KEY:
        return {"error": "Jellyfin API is unavailable."}
    cache_key = f"{jellyfin_id}|{tmdb_id}"
    cached = JELLYFIN_SHOW_DETAIL_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < MEDIA_LIBRARY_CACHE_SECONDS:
        return cached[1]

    params = urlencode(
        {
            "ParentId": jellyfin_id,
            "Recursive": "true",
            "IncludeItemTypes": "Episode",
            "Fields": "ParentIndexNumber,IndexNumber,Path",
            "Limit": "10000",
        }
    )
    request = Request(
        f"{JELLYFIN_URL.rstrip('/')}/Items?{params}",
        headers={"accept": "application/json", "user-agent": "EvidenceLinkHelper/1.0", "X-Emby-Token": JELLYFIN_API_KEY},
    )
    try:
        with urlopen(request, timeout=timeout) as handle:
            items = json.loads(handle.read(10_000_000).decode("utf-8", errors="replace")).get("Items") or []
    except Exception as exc:
        return {"error": f"Jellyfin episode lookup failed: {exc}"}

    available_by_season: dict[int, set[int]] = {}
    for item in items:
        try:
            season = int(item.get("ParentIndexNumber"))
            episode = int(item.get("IndexNumber"))
        except (TypeError, ValueError):
            continue
        if season < 1 or episode < 1:  # Skip specials; their numbering differs by metadata source.
            continue
        available_by_season.setdefault(season, set()).add(episode)

    expected_by_season: dict[int, int] = {}
    if tmdb_id and TMDB_API_KEY:
        tmdb_url = f"https://api.themoviedb.org/3/tv/{quote_plus(str(tmdb_id))}?api_key={quote_plus(TMDB_API_KEY)}"
        try:
            request = Request(tmdb_url, headers={"accept": "application/json", "user-agent": "EvidenceLinkHelper/1.0"})
            with urlopen(request, timeout=timeout) as handle:
                tmdb_show = json.loads(handle.read(750_000).decode("utf-8", errors="replace"))
            for season in tmdb_show.get("seasons") or []:
                number = season.get("season_number")
                count = season.get("episode_count")
                if isinstance(number, int) and isinstance(count, int) and number >= 1 and count >= 0:
                    expected_by_season[number] = count
        except Exception as exc:
            print(f"TMDB episode catalog lookup failed for Jellyfin show {jellyfin_id}: {exc}")

    season_numbers = sorted(set(available_by_season) | set(expected_by_season))
    seasons: list[dict[str, Any]] = []
    for season in season_numbers:
        available_episodes = sorted(available_by_season.get(season, set()))
        expected_episodes = expected_by_season.get(season, 0)
        missing_episodes = (
            [episode for episode in range(1, expected_episodes + 1) if episode not in available_by_season.get(season, set())]
            if expected_episodes
            else []
        )
        seasons.append(
            {
                "season": season,
                "available_count": len(available_episodes),
                "expected_episodes": expected_episodes,
                "available_episodes": available_episodes,
                "missing_episodes": missing_episodes,
            }
        )
    detail: dict[str, Any] = {
        "seasons": seasons,
        "summary": {
            "available_seasons": sum(1 for season in seasons if season["available_count"]),
            "expected_seasons": len(expected_by_season),
            "available_episodes": sum(season["available_count"] for season in seasons),
            "expected_episodes": sum(expected_by_season.values()),
        },
    }
    JELLYFIN_SHOW_DETAIL_CACHE[cache_key] = (now, detail)
    return detail


def jellyfin_library_item_by_id(jellyfin_id: str) -> dict[str, str] | None:
    seen: set[str] = set()
    for bucket in scan_jellyfin_library().values():
        for item in bucket:
            item_id = item.get("jellyfin_id", "")
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            if item_id == jellyfin_id:
                return item
    return None


def candidates_with_posters(candidates: list[Candidate | dict[str, Any]], search_query: str = "") -> list[dict[str, Any]]:
    rows = [dict(candidate) if isinstance(candidate, dict) else asdict(candidate) for candidate in candidates]
    for row in rows:
        # Generated adapters already attach their human-readable source name.
        # Do not overwrite it with the synthetic ``adapter:<id>`` URL.
        row["source_name"] = str(row.get("source_name") or source_name_for_url(str(row.get("source") or "")))
        row["language"] = language_label(row.get("title", ""))
        row.update(library_status_for_title(row.get("title", "")))
    if not TMDB_API_KEY or not rows:
        return rows
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(tmdb_poster_for_candidate, row["title"], search_query) for row in rows]
        for row, future in zip(rows, futures):
            try:
                poster_url = future.result(timeout=5)
                # Serve TMDB artwork through the app. This avoids posters
                # disappearing on clients/networks that cannot reach
                # image.tmdb.org directly.
                row["poster_url"] = (
                    f"{APP_BASE_PATH}/api/tmdb-image?url={quote_plus(poster_url)}"
                    if poster_url
                    else ""
                )
            except Exception:
                row["poster_url"] = ""
    return rows


def custom_authorized_search(query: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Run configured custom sources independently; a failure never affects legacy sources."""
    if LIBRARY is None:
        return [], {}
    sources = [item for item in LIBRARY.list_sources() if item["enabled"] and item.get("parser_type") == "custom-authorized"]
    results: list[dict[str, Any]] = []
    diagnostics: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(sources))) as executor:
        futures = {executor.submit(search_authorized_source, source, query): source for source in sources}
        for future in concurrent.futures.as_completed(futures):
            source = futures[future]
            try:
                rows, detail = future.result()
            except Exception as exc:
                rows, detail = [], {"sourceMatched": True, "parserStatus": "needs-adapter", "errorStage": "configuration", "rejectedResults": [{"reason": str(exc)[:300]}], "finalRenderedResults": 0}
            diagnostics[source["id"]] = detail
            for row in rows:
                # A generic source parser has already identified the actual
                # result URL in ``publicUrl``.  Passing the search page here
                # made the UI look as if the new source had no results and
                # sent Find Links back to the listing page.  Keep pageUrl as
                # provenance/evidence, but select the detected result URL.
                result_url = str(row.get("publicUrl") or row.get("pageUrl") or "")
                results.append({
                    "title": row["title"], "url": result_url, "source": source["base_url"], "source_id": source["id"],
                    "source_name": source["name"], "adapter_type": "custom-authorized", "custom_result": row,
                })
    return results, diagnostics


def enabled_saved_adapters() -> list[dict[str, Any]]:
    """Startup-loaded adapter extension layer. Invalid or untested files never enter search."""
    return [item for item in ENABLED_ADAPTERS if item.get("enabled") and item.get("health", {}).get("last_test_status") == "passed" and item.get("search", {}).get("url_template") and item.get("domains")]


def saved_adapter_search(query: str, selected_source: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    adapters = enabled_saved_adapters()
    if selected_source not in {"all", "existing"}:
        adapters = [item for item in adapters if item["id"] == selected_source]
    if selected_source == "existing":
        return [], []
    rows: list[dict[str, Any]] = []; failures: list[dict[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(4, len(adapters)))) as executor:
        futures = {executor.submit(SiteAdapter(adapter).search, query): adapter for adapter in adapters}
        for future in concurrent.futures.as_completed(futures, timeout=25 if futures else None):
            adapter = futures[future]
            try:
                for candidate in future.result(timeout=12):
                    rows.append({"title": candidate["title"], "url": candidate["url"], "source": f"adapter:{adapter['id']}", "source_id": adapter["id"], "source_name": adapter["name"], "adapter_type": "generated-adapter"})
            except Exception as exc:
                failures.append({"id": adapter["id"], "name": adapter["name"], "error": str(exc)[:160], "reason": str(exc)[:160] or "Adapter failed"})
    return rows, failures


def search_all_configured_sources(query: str, selected_source: str) -> tuple[list[Any], dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Search independent source types concurrently without changing result order.

    A slow compatible source must not postpone starting a saved adapter search.
    We still merge in the established source order, so an adapter completing
    first never changes which source is shown first in the UI.
    """
    include_existing = selected_source in {"all", "existing"}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        legacy_future = executor.submit(search_movie, query, 10, 20, 2_000_000) if include_existing else None
        custom_future = executor.submit(custom_authorized_search, query) if include_existing else None
        adapter_future = executor.submit(saved_adapter_search, query, selected_source)

        legacy_candidates = legacy_future.result() if legacy_future else []
        custom_candidates, custom_diagnostics = custom_future.result() if custom_future else ([], {})
        adapter_candidates, adapter_failures = adapter_future.result()
    return [*legacy_candidates, *custom_candidates, *adapter_candidates], custom_diagnostics, adapter_failures


def merge_search_candidates(rows: list[Any]) -> list[Any]:
    """Keep same title/source variants while removing duplicate URL/title rows."""
    seen: set[tuple[str, str, str]] = set(); merged=[]
    for row in rows:
        title = row.get("title", "") if isinstance(row, dict) else row.title
        url = row.get("url", "") if isinstance(row, dict) else row.url
        source = row.get("source", "") if isinstance(row, dict) else row.source
        key=(normalized_title(title), str(source), str(url))
        if key not in seen: seen.add(key); merged.append(row)
    return merged


def source_name_for_url(source_url: str) -> str:
    """Return the admin-facing source name for a configured source URL."""
    normalized = source_url.rstrip("/")
    source_names = {
        item["base_url"].rstrip("/"): item["name"]
        for item in (LIBRARY.list_sources() if LIBRARY else [])
    }
    return source_names.get(normalized) or urlparse(normalized).netloc or "Unknown source"


SIZE_PATTERN = re.compile(r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*(kb|mb|gb|tb)(?![a-z0-9])", re.IGNORECASE)


def normalize_size_label(value: str, unit: str, suffix: str = "") -> str:
    number = float(value)
    number_label = str(int(number)) if number.is_integer() else f"{number:g}"
    return f"{number_label} {unit.upper()}{suffix}"


def size_label_bytes(label: str) -> float:
    match = SIZE_PATTERN.search(label or "")
    if not match:
        return float("inf")
    unit_factor = {
        "kb": 1024,
        "mb": 1024**2,
        "gb": 1024**3,
        "tb": 1024**4,
    }
    return float(match.group(1)) * unit_factor[match.group(2).lower()]


def quality_terms(quality: str) -> tuple[str, ...]:
    normalized = quality.lower()
    if normalized in {"2160p", "4k"}:
        return ("2160p", "4k", "uhd")
    return (normalized,)


def size_from_text(text: str, quality: str) -> str:
    if not text:
        return ""
    matches = list(SIZE_PATTERN.finditer(text))
    if not matches:
        return ""

    lower_text = text.lower()
    preferred_windows: list[tuple[int, int]] = []
    for term in quality_terms(quality):
        start = 0
        while True:
            index = lower_text.find(term, start)
            if index < 0:
                break
            preferred_windows.append((max(0, index - 90), min(len(text), index + 140)))
            start = index + len(term)

    nearest: tuple[int, re.Match[str]] | None = None
    for window_start, window_end in preferred_windows:
        center = window_start + 90
        for match in matches:
            if window_start <= match.start() <= window_end:
                distance = abs(match.start() - center)
                if nearest is None or distance < nearest[0]:
                    nearest = (distance, match)
    if nearest is not None:
        match = nearest[1]
        suffix = "/E" if re.match(r"\s*/\s*e\b", text[match.end() : match.end() + 6], flags=re.IGNORECASE) else ""
        return normalize_size_label(match.group(1), match.group(2), suffix)

    match = matches[0]
    suffix = "/E" if re.match(r"\s*/\s*e\b", text[match.end() : match.end() + 6], flags=re.IGNORECASE) else ""
    return normalize_size_label(match.group(1), match.group(2), suffix)


def size_from_url_bytes(*urls: str) -> str:
    for url in urls:
        try:
            bytes_value = parse_qs(urlparse(url or "").query).get("bytes", [""])[0]
            if bytes_value:
                return human_size(bytes_value)
        except Exception:
            continue
    return ""


def best_size_label(row: Any, quality: str, resolved_url: str = "") -> str:
    text = " ".join(
        str(value or "")
        for value in (
            row.section,
            row.title,
            row.listing_link,
            row.landing_page,
            row.instant_link,
            row.final_wrapper,
            row.final_inner_url,
        )
    )
    url_size = size_from_url_bytes(resolved_url, row.final_inner_url, row.final_wrapper, row.instant_link, row.landing_page)
    if url_size:
        return url_size
    if resolved_url in RESOLVED_SIZE_CACHE:
        return RESOLVED_SIZE_CACHE[resolved_url]
    text_size = size_from_text(text, quality)
    try:
        content_length = int(row.content_length)
    except (TypeError, ValueError):
        content_length = 0
    if content_length >= 1024 * 1024:
        return human_size(row.content_length)
    return text_size or human_size(row.content_length)


def is_gdflix_file_url(url: str) -> bool:
    parsed = urlparse(url or "")
    return "gdflix" in parsed.netloc.lower() and parsed.path.startswith("/file/")


def is_fxlinks_url(url: str) -> bool:
    parsed = urlparse(url or "")
    return "fxlinks.rest" in parsed.netloc.lower() and parsed.path.startswith("/elinks/")


def is_intermediate_url(url: str) -> bool:
    lower_url = (url or "").lower()
    return any(marker in lower_url for marker in INTERMEDIATE_HOST_MARKERS)


def is_copyable_url(url: str) -> bool:
    lower_url = (url or "").lower()
    return bool(url) and not is_intermediate_url(url) and any(
        marker in lower_url for marker in COPYABLE_HOST_MARKERS
    )


def is_expired_quick_cloudpaglu(url: str, timeout: int = 8) -> bool:
    if "quick.cloudpaglu" not in (url or "").lower():
        return False
    headers = {"user-agent": "Mozilla/5.0", "range": "bytes=0-0"}
    try:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=timeout) as handle:
            sample = handle.read(700)
            content_type = (handle.headers.get("content-type") or "").lower()
    except Exception:
        return True
    text = sample.decode("utf-8", errors="ignore").lower()
    if "download link expired" in text or "invalid api response" in text:
        return True
    if "application/json" in content_type and ("error" in text or "expired" in text):
        return True
    return False


def is_usable_copyable_url(url: str) -> bool:
    return is_copyable_url(url) and not is_expired_quick_cloudpaglu(url)


def gdflix_inner_link(file_url: str, timeout: int = 12) -> str:
    if not is_gdflix_file_url(file_url):
        return ""
    now = time.time()
    cached = GDFLIX_LINK_CACHE.get(file_url)
    if cached and now - cached[0] < GDFLIX_CACHE_SECONDS:
        return cached[1]

    resolved = ""
    try:
        file_html = fetch_html(file_url, 1_000_000, timeout)
        file_parser = LinkParser(file_url)
        file_parser.feed(file_html)
        cloud_url = ""
        for link in file_parser.links:
            text = f"{link.text} {link.href}".lower()
            if "instant" in text and "busycdn" in text:
                resolved = busycdn_inner_link(link.href, timeout)
                break
            if "fast cloud" in text or "zipdisk" in text or "/cloud/" in urlparse(link.href).path:
                cloud_url = link.href
                break
        if not resolved and cloud_url:
            cloud_html = fetch_html(cloud_url, 1_000_000, timeout)
            cloud_parser = LinkParser(cloud_url)
            cloud_parser.feed(cloud_html)
            fallback = ""
            for link in cloud_parser.links:
                text = link.text.lower()
                if "quick download" in text:
                    resolved = quick_cloudpaglu_inner_link(link.href, timeout) if "quick.cloudpaglu" in link.href else link.href
                    if resolved:
                        break
                if not fallback and ("cloud resume" in text or "download" in text):
                    fallback = link.href
            resolved = resolved or fallback
    except Exception as exc:
        print(f"GDFlix inner lookup failed for {file_url!r}: {exc}")

    if resolved:
        GDFLIX_LINK_CACHE[file_url] = (now, resolved)
    return resolved


def dl_fast_inner_link(url: str, timeout: int = 12) -> str:
    if "dl.fastdlserver.site" not in (url or ""):
        return ""
    now = time.time()
    cached = DFAST_LINK_CACHE.get(url)
    if cached and now - cached[0] < GDFLIX_CACHE_SECONDS:
        return cached[1]

    resolved = ""
    try:
        html = fetch_html(url, 1_000_000, timeout)
        parser = LinkParser(url)
        parser.feed(html)
        cloud_url = ""
        for link in parser.links:
            text = f"{link.text} {link.href}".lower()
            if "instant" in text and "busycdn" in text:
                resolved = busycdn_inner_link(link.href, timeout)
                break
            if "foxcloud" in link.href or ("instant" in text and "foxcloud" in text):
                resolved = foxcloud_inner_link(link.href, timeout)
                if resolved:
                    break
            if "quick download" in text or "cloud-dl." in text or "quick.cloudpaglu" in text:
                resolved = quick_cloudpaglu_inner_link(link.href, timeout) if "quick.cloudpaglu" in link.href else link.href
                if resolved:
                    break
            if not cloud_url and ("fast cloud" in text or "zipdisk" in text or "/cloud/" in urlparse(link.href).path):
                cloud_url = link.href
        if not resolved and cloud_url:
            cloud_html = fetch_html(cloud_url, 1_000_000, timeout)
            cloud_parser = LinkParser(cloud_url)
            cloud_parser.feed(cloud_html)
            fallback = ""
            for link in cloud_parser.links:
                text = link.text.lower()
                if "quick download" in text:
                    resolved = quick_cloudpaglu_inner_link(link.href, timeout) if "quick.cloudpaglu" in link.href else link.href
                    if resolved:
                        break
                if not fallback and ("cloud resume" in text or "download" in text):
                    fallback = link.href
            resolved = resolved or fallback
        if resolved:
            DFAST_LINK_CACHE[url] = (now, resolved)
            return resolved

        final = final_or_next_url(follow_redirects(url, 8, timeout)) or ""
        if is_gdflix_file_url(final):
            resolved = gdflix_inner_link(final, timeout)
        elif is_usable_copyable_url(final):
            resolved = final
    except Exception as exc:
        print(f"dl.fast inner lookup failed for {url!r}: {exc}")

    if resolved:
        DFAST_LINK_CACHE[url] = (now, resolved)
    return resolved


def resolve_inner_link(url: str, timeout: int = 12) -> str:
    if not url:
        return ""
    if is_usable_copyable_url(url):
        return url

    for resolver in (busycdn_inner_link, foxcloud_inner_link, quick_cloudpaglu_inner_link, gdflix_inner_link, dl_fast_inner_link):
        resolved = resolver(url, timeout)
        if resolved and not is_intermediate_url(resolved):
            if is_usable_copyable_url(resolved):
                return resolved
            nested = resolve_inner_link(resolved, timeout)
            if nested:
                return nested

    try:
        final = final_or_next_url(follow_redirects(url, 8, timeout)) or ""
    except Exception:
        final = ""
    if final and final != url:
        if is_usable_copyable_url(final):
            return final
        if is_gdflix_file_url(final) or "instant.busycdn.xyz" in final or "dl.fastdlserver.site" in final or "quick.cloudpaglu" in final:
            return resolve_inner_link(final, timeout)
    return ""


def fxlinks_inner_options(url: str, timeout: int = 12) -> list[dict[str, str]]:
    if not is_fxlinks_url(url):
        return []
    now = time.time()
    cached = FXLINKS_LINK_CACHE.get(url)
    if cached and now - cached[0] < GDFLIX_CACHE_SECONDS:
        return cached[1]

    options: list[dict[str, str]] = []
    try:
        page_html = fetch_html(url, 2_000_000, timeout)
        parser = LinkParser(url)
        parser.feed(page_html)
        dl_links = [
            link for link in parser.links
            if "dl.fastdlserver.site" in link.href or is_gdflix_file_url(link.href)
        ]
        resolved_hrefs: set[str] = set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, max(1, len(dl_links)))) as executor:
            future_map = {
                executor.submit(resolve_inner_link, link.href, timeout): link
                for link in dl_links
            }
            for future in concurrent.futures.as_completed(future_map):
                link = future_map[future]
                try:
                    resolved = future.result()
                except Exception:
                    resolved = ""
                if not resolved or is_intermediate_url(resolved):
                    continue
                resolved_hrefs.add(link.href)
                label = compact_label(link.text) or compact_label(link.section)
                options.append(
                    {
                        "url": resolved,
                        "listing": link.href,
                        "label": label or link.text or link.section,
                        "source": url,
                    }
                )
        for link in dl_links:
            if link.href in resolved_hrefs:
                continue
            resolved = resolve_inner_link(link.href, timeout)
            if not resolved or is_intermediate_url(resolved):
                continue
            label = compact_label(link.text) or compact_label(link.section)
            options.append(
                {
                    "url": resolved,
                    "listing": link.href,
                    "label": label or link.text or link.section,
                    "source": url,
                }
            )
    except Exception as exc:
        print(f"FXLinks inner lookup failed for {url!r}: {exc}")

    if options:
        FXLINKS_LINK_CACHE[url] = (now, options)
    return options


def fxlinks_listing_options(url: str, timeout: int = 8) -> list[dict[str, str]]:
    if not is_fxlinks_url(url):
        return []
    now = time.time()
    cached = FXLINKS_LISTING_CACHE.get(url)
    if cached and now - cached[0] < GDFLIX_CACHE_SECONDS:
        return cached[1]

    options: list[dict[str, str]] = []
    try:
        page_html = fetch_html(url, 2_000_000, timeout)
        parser = LinkParser(url)
        parser.feed(page_html)
        for link in parser.links:
            if "dl.fastdlserver.site" not in link.href and not is_gdflix_file_url(link.href):
                continue
            label = compact_label(link.text) or compact_label(link.section)
            options.append(
                {
                    "url": "",
                    "listing": link.href,
                    "label": label or link.text or link.section,
                    "source": url,
                    "pending": True,
                }
            )
    except Exception as exc:
        print(f"FXLinks listing lookup failed for {url!r}: {exc}")

    if options:
        FXLINKS_LISTING_CACHE[url] = (now, options)
    return options


def busycdn_inner_link(url: str, timeout: int = 12) -> str:
    if "instant.busycdn.xyz" not in (url or ""):
        return ""
    wrapper_size = size_from_url_bytes(url)
    try:
        hops = follow_redirects(url, 8, timeout)
        for hop in hops:
            for candidate in (hop.location, hop.url):
                parsed = urlparse(candidate or "")
                inner = parse_qs(parsed.query).get("url", [""])[0]
                if inner.startswith("http"):
                    if wrapper_size:
                        RESOLVED_SIZE_CACHE[inner] = wrapper_size
                    return inner
                if "video-downloads.googleusercontent.com" in (candidate or ""):
                    if wrapper_size:
                        RESOLVED_SIZE_CACHE[candidate or ""] = wrapper_size
                    return candidate or ""
    except Exception as exc:
        print(f"BusyCDN inner lookup failed for {url!r}: {exc}")
    return ""


def foxcloud_inner_link(url: str, timeout: int = 12) -> str:
    """Resolve FoxCloud's JavaScript landing page to its direct download URL."""
    if "cdn.foxcloud.rest" not in (url or "").lower():
        return ""
    wrapper_size = size_from_url_bytes(url)
    try:
        landing_html = fetch_html(url, 750_000, timeout)
        target_match = re.search(
            r"(?:window\.)?location(?:\.href)?\s*=\s*[\"']([^\"']+)",
            landing_html,
            flags=re.IGNORECASE,
        )
        upload_url = urljoin(url, target_match.group(1)) if target_match else url
        upload_html = fetch_html(upload_url, 750_000, timeout)
        parser = LinkParser(upload_url)
        parser.feed(upload_html)
        for link in parser.links:
            direct = link.href.strip()
            if "video-downloads.googleusercontent.com" not in direct.lower():
                continue
            if wrapper_size:
                RESOLVED_SIZE_CACHE[direct] = wrapper_size
            return direct
    except Exception as exc:
        print(f"FoxCloud inner lookup failed for {url!r}: {exc}")
    return ""


def quick_cloudpaglu_inner_link(url: str, timeout: int = 12) -> str:
    if "quick.cloudpaglu" not in (url or ""):
        return ""
    wrapper_size = size_from_url_bytes(url)
    try:
        final = final_or_next_url(follow_redirects(url, 8, timeout)) or ""
        if final and final != url:
            if wrapper_size:
                RESOLVED_SIZE_CACHE[final] = wrapper_size
            if is_usable_copyable_url(final):
                return final
            if not is_intermediate_url(final):
                nested = resolve_inner_link(final, timeout)
                if nested:
                    return nested

        page_html = fetch_html(url, 750_000, timeout)
        parser = LinkParser(url)
        parser.feed(page_html)
        for link in parser.links:
            if not link.href.startswith("http"):
                continue
            if wrapper_size:
                RESOLVED_SIZE_CACHE[link.href] = wrapper_size
            if is_usable_copyable_url(link.href):
                return link.href
            if "video-downloads.googleusercontent.com" in link.href or "cloud-dl." in link.href:
                return link.href
    except Exception as exc:
        print(f"Quick CloudPaglu lookup failed for {url!r}: {exc}")
    return ""


def find_best_link(row: Any) -> str:
    for url in (row.final_inner_url, row.final_wrapper, row.instant_link):
        if is_usable_copyable_url(url):
            return url
    for url in (row.final_inner_url, row.final_wrapper, row.instant_link):
        resolved = resolve_inner_link(url)
        if resolved and not is_intermediate_url(resolved):
            return resolved
    return ""


def fast_listing_options(candidate: Candidate, quality: str, timeout: int = 6, max_options: int = 3) -> list[dict[str, str]]:
    try:
        listing_links = find_listing_links(candidate.url, quality, timeout, 2_000_000)
    except Exception as exc:
        print(f"Fast listing lookup failed for {candidate.url!r}: {exc}")
        return []
    if not listing_links:
        return []

    options: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(listing_links))) as executor:
        future_map = {
            executor.submit(resolve_inner_link, link.href, timeout): link
            for link in listing_links
        }
        for future in concurrent.futures.as_completed(future_map):
            link = future_map[future]
            try:
                best = future.result()
            except Exception:
                best = ""
            if not best or is_intermediate_url(best) or best in seen_urls:
                continue
            seen_urls.add(best)
            label = compact_label(link.section, link.text, link.href)
            size_label = size_from_url_bytes(best, link.href) or size_from_text(label, quality) or "unknown"
            options.append(
                {
                    "quality": quality,
                    "size": size_label,
                    "url": best,
                    "source": candidate.url,
                    "listing": link.href,
                    "variant": compact_label(link.section, link.text),
                }
            )
            if len(options) >= max_options:
                break
    return sort_links(options)


def link_resolution_score(url: str) -> int:
    if is_usable_copyable_url(url):
        return 2
    if is_intermediate_url(url):
        return 1
    return 0


def series_meta(row: Any) -> dict[str, str]:
    text = " ".join(str(value or "") for value in (row.section, row.listing_link, row.instant_link))
    season = ""
    season_match = re.search(r"\bSeason\s*0*(\d+)\b|\bS0*(\d+)\b", text, flags=re.IGNORECASE)
    if season_match:
        season = season_match.group(1) or season_match.group(2) or ""

    episode = ""
    episode_match = re.search(r"\bEpisode\s*0*(\d+)\b|\bE0*(\d+)\b", text, flags=re.IGNORECASE)
    if episode_match:
        episode = episode_match.group(1) or episode_match.group(2) or ""

    lower_text = text.lower()
    if episode:
        kind = f"Episode {int(episode):02d}"
    elif "season zip" in lower_text or "season pack" in lower_text or "batch" in lower_text:
        kind = "Season Zip"
    elif not season:
        kind = ""
    else:
        kind = "File"

    return {
        "season": f"Season {int(season)}" if season else "",
        "kind": kind,
        "episode": episode,
    }


def episode_target_from_query(value: str) -> dict[str, int] | None:
    """Parse a requested TV episode without treating a season pack as that episode."""
    text = str(value or "")
    patterns = (
        r"\bS(?:eason)?\s*0*(\d{1,2})\s*[-_. ]*E(?:pisode)?\s*0*(\d{1,3})\b",
        r"\b(\d{1,2})\s*[xX]\s*0*(\d{1,3})\b",
        r"\bSeason\s*0*(\d{1,2})\s*Episode\s*0*(\d{1,3})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return {"season": int(match.group(1)), "episode": int(match.group(2))}
    return None


def episode_search_fallback(value: str) -> str:
    """Sites index a show/season page, not the individual episode query."""
    cleaned = re.sub(
        r"\bS(?:eason)?\s*0*\d{1,2}\s*[-_. ]*E(?:pisode)?\s*0*\d{1,3}\b|"
        r"\b\d{1,2}\s*[xX]\s*0*\d{1,3}\b|"
        r"\bSeason\s*0*\d{1,2}\s*Episode\s*0*\d{1,3}\b",
        " ",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:480p|720p|1080p|2160p|4k|uhd|hindi|english|tamil|telugu|"
        r"malayalam|kannada|multi(?:\s*audio)?|dual(?:\s*audio)?)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def season_number_from_meta(meta: dict[str, str]) -> int | None:
    """Return the parsed season number from a listing's compact metadata."""
    match = re.search(r"\d+", str(meta.get("season") or ""))
    return int(match.group(0)) if match else None


def explicit_season_in_candidate(title: str, url: str) -> int | None:
    """Read an explicitly-labelled season from a search candidate, if any."""
    match = re.search(
        r"\b(?:season\s*|s)0*(\d{1,2})\b",
        f"{title} {url}",
        flags=re.IGNORECASE,
    )
    return int(match.group(1)) if match else None


def is_exact_episode_link(meta: dict[str, str], target: dict[str, int] | None) -> bool:
    if not target:
        return False
    try:
        return season_number_from_meta(meta) == target["season"] and int(meta.get("episode") or 0) == target["episode"]
    except (TypeError, ValueError, IndexError):
        return False


def link_variant_label(row: Any) -> str:
    text = str(row.section or row.listing_link or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > 90:
        return f"{text[:87]}..."
    return text


def compact_label(*values: str) -> str:
    text = " ".join(str(value or "") for value in values)
    text = re.sub(r"\s+", " ", text)
    return html.unescape(text).strip()


def media_quality_label(*values: str) -> str:
    """Build a compact, honest quality label from a source listing's text."""
    text = compact_label(*values).lower()
    tags: list[str] = []

    resolution = re.search(r"\b(2160p|4k|1080p|720p|480p)\b", text)
    if resolution:
        raw = resolution.group(1).lower()
        tags.append("4K" if raw in {"2160p", "4k"} else raw)

    for pattern, label in (
        (r"\bweb[ .-]?dl\b", "WEB-DL"),
        (r"\bweb[ .-]?rip\b", "WEBRip"),
        (r"\bblu[ .-]?ray\b|\bbrrip\b", "BluRay"),
        (r"\bhdtv\b", "HDTV"),
        (r"\bhd[ .-]?rip\b", "HDRip"),
        (r"\b(?:x265|h[ .-]?265|hevc)\b", "HEVC"),
        (r"\b(?:x264|h[ .-]?264|avc)\b", "x264"),
        (r"\b10[ .-]?bit\b", "10-bit"),
        (r"\bdv\b|\bdolby[ .-]?vision\b", "Dolby Vision"),
        (r"\bhdr10\+?\b", "HDR10"),
        (r"\bhdr\b", "HDR"),
    ):
        if re.search(pattern, text, flags=re.IGNORECASE) and label not in tags:
            tags.append(label)
    return " · ".join(tags)


def sort_links(links: list[dict[str, str]]) -> list[dict[str, str]]:
    quality_order = {quality: index for index, quality in enumerate(DEFAULT_QUALITIES)}
    kind_order = {"Season Zip": 0, "File": 1}

    def season_number(item: dict[str, str]) -> int:
        match = re.search(r"\d+", item.get("season", ""))
        return int(match.group(0)) if match else 999

    def episode_number(item: dict[str, str]) -> int:
        if item.get("episode"):
            return int(item["episode"])
        match = re.search(r"\d+", item.get("kind", ""))
        return int(match.group(0)) if match else 999

    return sorted(
        links,
        key=lambda item: (
            quality_order.get(item.get("quality", "").lower(), len(quality_order)),
            season_number(item),
            kind_order.get(item.get("kind", ""), 2),
            episode_number(item),
            size_label_bytes(item.get("size", "")),
            item.get("variant", ""),
        ),
    )


def is_series_candidate(candidate: Candidate) -> bool:
    text = f"{candidate.title} {candidate.url}".lower()
    return any(term in text for term in ("season", "web series", "series", "episode"))


def response(handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: Any) -> None:
    # Keep API responses backward compatible (`ok`) while providing one
    # unambiguous transport-success field for new clients. A 2xx response is
    # never turned into a failure merely because an optional parser found no
    # matches.
    if isinstance(payload, dict) and "success" not in payload:
        payload = {**payload, "success": bool(200 <= int(status) < 300 and payload.get("ok", True) is not False)}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("content-type", "application/json; charset=utf-8")
        handler.send_header("content-length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)
    except (BrokenPipeError, ConnectionResetError):
        # A browser/proxy may abandon a long-running request. The handler must
        # not try to send a second error response, which only creates a noisy
        # traceback and obscures the actual analyzer outcome.
        return


def normalized_app_path(path: str) -> str:
    if path == APP_BASE_PATH:
        return "/"
    if path.startswith(f"{APP_BASE_PATH}/"):
        stripped = path[len(APP_BASE_PATH):]
        return stripped or "/"
    return path


def is_authorized(handler: BaseHTTPRequestHandler, parsed: Any | None = None) -> bool:
    if not ACCESS_TOKEN:
        return True
    query_token = ""
    if parsed is not None:
        query_token = parse_qs(parsed.query).get("token", [""])[0]
    header_token = handler.headers.get("x-app-token", "")
    return ACCESS_TOKEN in {query_token, header_token}


def admin_password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex()
    return f"{salt.hex()}:{digest}"


def matches_admin_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest = stored.split(":", 1)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1).hex()
        return hmac.compare_digest(actual, digest)
    except (ValueError, TypeError):
        return False


def is_admin(handler: BaseHTTPRequestHandler, parsed: Any | None = None) -> bool:
    """Admin APIs are never less protected than normal app APIs.

    Deployments can set ADMIN_TOKEN for a second credential.  On the existing
    token-protected install, the application token remains the admin boundary
    until a separate token is configured.
    """
    if not is_authorized(handler, parsed):
        return False
    if ADMIN_TOKEN:
        query_token = parse_qs(parsed.query).get("admin_token", [""])[0] if parsed else ""
        if ADMIN_TOKEN in {handler.headers.get("x-admin-token", ""), query_token}:
            return True
    stored_password = str(LIBRARY.settings().get("admin_password_hash") or "") if LIBRARY else ""
    if stored_password:
        return matches_admin_password(handler.headers.get("x-admin-password", ""), stored_password)
    if not ADMIN_TOKEN:
        return True
    return False


def query_params(parsed: Any) -> dict[str, str]:
    return {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}


def library_response(handler: BaseHTTPRequestHandler, parsed: Any, route_path: str) -> bool:
    """Serve read-only library routes. Returns whether a route was handled."""
    if not route_path.startswith("/api/library"):
        return False
    if not is_authorized(handler, parsed):
        response(handler, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Token required"})
        return True
    if LIBRARY is None:
        response(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "Library service is unavailable."})
        return True
    parts = [part for part in route_path.split("/") if part]
    params = query_params(parsed)
    try:
        if route_path == "/api/library/stats":
            response(handler, HTTPStatus.OK, {"ok": True, **LIBRARY.stats()})
        elif route_path == "/api/library/movies":
            response(handler, HTTPStatus.OK, {"ok": True, **LIBRARY.list_items("movie", params)})
        elif route_path == "/api/library/tv":
            response(handler, HTTPStatus.OK, {"ok": True, **LIBRARY.list_items("tv", params)})
        elif route_path == "/api/library/missing":
            kind = params.get("type", "movies")
            mapped = "unmatched" if kind == "unmatched" else "missing"
            response(handler, HTTPStatus.OK, {"ok": True, "items": LIBRARY.missing(mapped)})
        elif route_path == "/api/library/recent":
            response(handler, HTTPStatus.OK, {"ok": True, "items": LIBRARY.recent(min(50, max(1, int(params.get("limit", "40")))))})
        elif len(parts) == 4 and parts[:3] == ["api", "library", "movies"]:
            item = LIBRARY.get_item(parts[3])
            response(handler, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND, {"ok": bool(item), "item": item} if item else {"ok": False, "error": "Movie not found"})
        elif len(parts) == 4 and parts[:3] == ["api", "library", "tv"]:
            item = LIBRARY.get_item(parts[3])
            response(handler, HTTPStatus.OK if item else HTTPStatus.NOT_FOUND, {"ok": bool(item), "item": item} if item else {"ok": False, "error": "TV show not found"})
        elif len(parts) == 5 and parts[:3] == ["api", "library", "tv"] and parts[4] == "seasons":
            response(handler, HTTPStatus.OK, {"ok": True, "items": LIBRARY.seasons(parts[3])})
        elif len(parts) == 6 and parts[:3] == ["api", "library", "tv"] and parts[4] == "seasons" and parts[5].isdigit():
            response(handler, HTTPStatus.OK, {"ok": True, "items": LIBRARY.seasons(parts[3], int(parts[5]))})
        elif len(parts) == 5 and parts[:4] == ["api", "library", "scan", "status"]:
            job = LIBRARY._jobs.get(parts[4])
            if not job:
                with LIBRARY._connection() as db:
                    row = db.execute("SELECT * FROM scan_jobs WHERE id=?", (parts[4],)).fetchone()
                job = dict(row) if row else None
                if job and isinstance(job.get("progress"), str): job["progress"] = json.loads(job["progress"])
            response(handler, HTTPStatus.OK if job else HTTPStatus.NOT_FOUND, {"ok": bool(job), "job": job} if job else {"ok": False, "error": "Scan job not found"})
        else:
            response(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Library route not found"})
    except (ValueError, TypeError) as exc:
        response(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
    return True


def library_admin_dashboard_response(handler: BaseHTTPRequestHandler, parsed: Any, route_path: str) -> bool:
    if route_path != "/api/admin/library/dashboard":
        return False
    if not is_admin(handler, parsed):
        response(handler, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Admin authorization required"})
        return True
    if LIBRARY is None:
        response(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "Library service is unavailable."})
        return True
    response(handler, HTTPStatus.OK, {"ok": True, **LIBRARY.admin_dashboard()})
    return True


def jellyfin_connection_data(base_url: str, api_key: str, endpoint: str) -> dict[str, Any]:
    base_url = base_url.strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")) or not urlparse(base_url).hostname:
        raise ValueError("Enter a valid Jellyfin URL")
    if not api_key.strip():
        raise ValueError("Jellyfin API key is required")
    request = Request(
        f"{base_url}{endpoint}",
        headers={"accept": "application/json", "user-agent": "FreemiumIndex/1.0", "X-Emby-Token": api_key.strip()},
    )
    with urlopen(request, timeout=12) as handle:
        return json.loads(handle.read(5_000_000).decode("utf-8", errors="replace"))


def jellyfin_libraries() -> list[dict[str, Any]]:
    payload = jellyfin_connection_data(JELLYFIN_URL, JELLYFIN_API_KEY, "/Library/VirtualFolders")
    return [
        {
            "id": str(item.get("ItemId") or ""), "name": str(item.get("Name") or "Unnamed library"),
            "type": str(item.get("CollectionType") or "other"), "paths": item.get("Locations") or [],
        }
        for item in payload if item.get("ItemId")
    ]


def admin_setup_get(handler: BaseHTTPRequestHandler, parsed: Any, route_path: str) -> bool:
    if route_path not in {"/api/admin/setup", "/api/admin/setup/jellyfin/libraries"}:
        return False
    if not is_admin(handler, parsed):
        response(handler, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Admin authorization required"})
        return True
    if LIBRARY is None:
        response(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "Library service is unavailable."})
        return True
    try:
        if route_path.endswith("/libraries"):
            response(handler, HTTPStatus.OK, {"ok": True, "items": jellyfin_libraries()})
        else:
            response(handler, HTTPStatus.OK, {"ok": True, "setup": public_setup_configuration()})
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        response(handler, HTTPStatus.BAD_GATEWAY, {"ok": False, "error": str(exc)})
    return True


def admin_setup_post(handler: BaseHTTPRequestHandler, parsed: Any, route_path: str, payload: dict[str, Any]) -> bool:
    if not route_path.startswith("/api/admin/setup/"):
        return False
    if not is_admin(handler, parsed):
        response(handler, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Admin authorization required"})
        return True
    if LIBRARY is None:
        response(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "Library service is unavailable."})
        return True
    try:
        if route_path == "/api/admin/setup/security":
            password = str(payload.get("password") or "")
            if len(password) < 10:
                raise ValueError("Admin password must have at least 10 characters")
            LIBRARY.update_settings({"admin_password_hash": admin_password_hash(password)})
            response(handler, HTTPStatus.OK, {"ok": True})
        elif route_path == "/api/admin/setup/test-jellyfin":
            data = jellyfin_connection_data(str(payload.get("url") or ""), str(payload.get("apiKey") or ""), "/System/Info")
            response(handler, HTTPStatus.OK, {"ok": True, "server": data.get("ServerName") or data.get("Name") or "Jellyfin", "version": data.get("Version") or ""})
        elif route_path == "/api/admin/setup/load-jellyfin-libraries":
            data = jellyfin_connection_data(str(payload.get("url") or ""), str(payload.get("apiKey") or ""), "/Library/VirtualFolders")
            items = [{"id": str(item.get("ItemId") or ""), "name": str(item.get("Name") or "Unnamed library"), "type": str(item.get("CollectionType") or "other"), "paths": item.get("Locations") or []} for item in data if item.get("ItemId")]
            response(handler, HTTPStatus.OK, {"ok": True, "items": items})
        elif route_path == "/api/admin/setup/validate-paths":
            values = _config_list(payload.get("paths"))
            checked = []
            for raw in values:
                path = Path(raw).expanduser().resolve()
                checked.append({"path": str(path), "exists": path.is_dir(), "readable": path.is_dir() and os.access(path, os.R_OK)})
            response(handler, HTTPStatus.OK, {"ok": True, "items": checked})
        elif route_path == "/api/admin/setup/save":
            mappings = payload.get("pathMappings") or []
            if not isinstance(mappings, list):
                raise ValueError("Path mappings must be a list")
            movie_paths, tv_paths = _config_list(payload.get("moviePaths")), _config_list(payload.get("tvPaths"))
            for mapping in mappings:
                if not isinstance(mapping, dict):
                    continue
                local_path = str(mapping.get("localPath") or "").strip()
                if not local_path:
                    continue
                if str(mapping.get("mediaType") or "").lower() == "movie": movie_paths.append(local_path)
                elif str(mapping.get("mediaType") or "").lower() == "tv": tv_paths.append(local_path)
            min_size = int(payload.get("minFileSizeMb") or 30)
            if not 0 <= min_size <= 100000: raise ValueError("Minimum file size must be between 0 and 100000 MB")
            auto_sync = int(payload.get("autoSyncMinutes") or 0)
            if not 0 <= auto_sync <= 10080: raise ValueError("Auto-sync must be between 0 and 10080 minutes")
            existing = LIBRARY.settings()
            LIBRARY.update_settings({
                "jellyfin_url": str(payload.get("jellyfinUrl") or existing.get("jellyfin_url") or "").strip(),
                "jellyfin_api_key": str(payload.get("jellyfinApiKey") or existing.get("jellyfin_api_key") or "").strip(),
                "tmdb_api_key": str(payload.get("tmdbApiKey") or existing.get("tmdb_api_key") or "").strip(),
                "jellyfin_library_ids": _config_list(payload.get("jellyfinLibraryIds")),
                "movie_paths": list(dict.fromkeys(movie_paths)), "tv_paths": list(dict.fromkeys(tv_paths)),
                "path_mappings": mappings, "min_file_size_mb": min_size, "auto_sync_minutes": auto_sync,
            })
            apply_persisted_configuration()
            response(handler, HTTPStatus.OK, {"ok": True, "setup": public_setup_configuration()})
        elif route_path == "/api/admin/setup/sources/save":
            raw_url = str(payload.get("baseUrl") or "").strip()
            parsed_url = urlparse(raw_url if "://" in raw_url else f"https://{raw_url}")
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
                raise ValueError("Enter a valid source URL")
            source_id = str(payload.get("id") or "")
            existing_source = next((item for item in LIBRARY.list_sources() if item["id"] == source_id), {})
            parser_type = str(payload.get("parserType") or existing_source.get("parser_type") or "compatible-search")
            config = payload.get("config", existing_source.get("config") or {})
            if parser_type == "custom-authorized":
                config = validate_authorized_config(config if isinstance(config, dict) else {})
                allowed = config["allowedDomain"]
                if parsed_url.hostname != allowed:
                    raise ValueError("Source URL host must exactly match the allowed domain")
            elif parser_type != "compatible-search":
                raise ValueError("Unknown source adapter type")
            source = LIBRARY.save_source({"id": payload.get("id"), "name": payload.get("name") or parsed_url.hostname, "base_url": parsed_url.geturl().rstrip("/"), "enabled": payload.get("enabled", True), "priority": payload.get("priority", 100), "parser_type": parser_type, "config": config})
            apply_persisted_configuration()
            response(handler, HTTPStatus.OK, {"ok": True, "source": source})
        elif route_path == "/api/admin/setup/sources/preview":
            config = validate_authorized_config(payload.get("config") if isinstance(payload.get("config"), dict) else {})
            sample_html = str(payload.get("sampleHtml") or "")
            if not sample_html or len(sample_html) > 1_000_000:
                raise ValueError("Paste between 1 and 1,000,000 characters of sample HTML")
            items, diagnostics = parse_authorized_sample(sample_html, config, "preview", str(payload.get("sourceName") or "Custom Authorized Source"), config["searchUrlTemplate"].replace("{query}", "preview"))
            response(handler, HTTPStatus.OK, {"ok": True, "items": items, "diagnostics": diagnostics, "preview": preview_selectors(sample_html, config)})
        elif route_path == "/api/admin/setup/sources/auto-detect":
            sample_html = str(payload.get("sampleHtml") or "")
            if not sample_html or len(sample_html) > 1_000_000:
                raise ValueError("Paste between 1 and 1,000,000 characters of sample HTML")
            # This endpoint deliberately operates only on text supplied in the
            # form; it does not fetch a URL or execute page JavaScript.
            response(handler, HTTPStatus.OK, {"ok": True, **detect_selectors(sample_html)})
        elif route_path == "/api/admin/setup/sources/ai-review":
            sample_html = str(payload.get("sampleHtml") or "")
            if not sample_html or len(sample_html) > 100_000:
                raise ValueError("Paste between 1 and 100,000 characters of sample HTML")
            advice = review_source_html(sample_html, payload.get("config") if isinstance(payload.get("config"), dict) else {})
            response(handler, HTTPStatus.OK, {"ok": True, **advice})
        elif route_path == "/api/admin/setup/sources/fetch-auto-detect":
            # Credentials are passed directly to the fetch service and are never
            # persisted, included in diagnostics, or written to source checks.
            live = live_auto_detect(
                payload.get("livePageUrl"), timeout=payload.get("requestTimeout"),
                allow_private_network=bool(payload.get("allowPrivateNetwork")),
                auth=payload.get("auth") if isinstance(payload.get("auth"), dict) else {},
                render_mode=str(payload.get("renderMode") or "auto"),
            )
            response(handler, HTTPStatus.OK, live)
        elif route_path == "/api/admin/setup/sources/guided-auto-setup":
            guided = guided_source_setup(
                payload.get("searchPageUrl"), payload.get("exampleResultUrl"),
                timeout=payload.get("requestTimeout"),
                allow_private_network=bool(payload.get("allowPrivateNetwork")),
                auth=payload.get("auth") if isinstance(payload.get("auth"), dict) else {},
                render_mode=str(payload.get("renderMode") or "auto"),
            )
            states = ["fetching-search-page", "inspecting-example-url", "learning-url-pattern", "detecting-containers", "detecting-fields", "validating-results"]
            if guided.get("detectionStatus") != "working" or not isinstance(guided.get("config"), dict) or int((guided.get("diagnostics") or {}).get("validResults") or 0) < 1:
                guided.update({"sourceActive": False, "states": [*states, "failed"]})
                response(handler, HTTPStatus.OK, guided)
                return True
            name = str(payload.get("sourceName") or "").strip()
            if not name:
                raise ValueError("Source Name is required")
            config = validate_authorized_config(guided["config"])
            # `base_url` is intentionally unique.  A user can first add a
            # source by its domain and later run Auto Setup for that same
            # domain; that must upgrade/update the existing source, not fail
            # with a database unique-constraint error while appearing to have
            # completed detection successfully.
            base_url = f"https://{config['allowedDomain']}"
            existing_source = next(
                (item for item in LIBRARY.list_sources()
                 if urlparse(str(item.get("base_url") or "")).hostname == config["allowedDomain"]),
                None,
            )
            source = LIBRARY.save_source({
                "id": existing_source["id"] if existing_source else None,
                "name": name,
                "base_url": base_url,
                "enabled": True,
                "priority": int(payload.get("priority") or (existing_source or {}).get("priority") or 100),
                "parser_type": "custom-authorized",
                "config": config,
            })
            # The detected cards are the source test: at least one already
            # matched the learned URL pattern and normalized required fields.
            LIBRARY.record_source_check(source["id"], "Working", json.dumps({"validResults": guided["diagnostics"]["validResults"], "mode": "guided-auto-setup"}))
            apply_persisted_configuration()
            guided.update({"source": source, "sourceActive": True, "states": [*states, "saving-source", "testing-source", "active"]})
            response(handler, HTTPStatus.OK, guided)
        elif route_path == "/api/admin/setup/sources/site-auto-setup":
            guided = auto_setup_from_site_url(
                payload.get("siteUrl"), timeout=payload.get("requestTimeout"),
                allow_private_network=bool(payload.get("allowPrivateNetwork")),
                auth=payload.get("auth") if isinstance(payload.get("auth"), dict) else {},
                render_mode=str(payload.get("renderMode") or "auto"),
            )
            states = ["finding-search-page", "fetching-search-page", "detecting-containers", "detecting-fields", "validating-results"]
            if guided.get("detectionStatus") != "working" or not isinstance(guided.get("config"), dict) or int((guided.get("diagnostics") or {}).get("validResults") or 0) < 1:
                guided.update({"sourceActive": False, "states": [*states, "failed"]})
                response(handler, HTTPStatus.OK, guided)
                return True
            name = str(payload.get("sourceName") or urlparse(str(payload.get("siteUrl") or "")).hostname or "Custom source").strip()
            config = validate_authorized_config(guided["config"])
            base_url = f"https://{config['allowedDomain']}"
            existing_source = next((item for item in LIBRARY.list_sources() if urlparse(str(item.get("base_url") or "")).hostname == config["allowedDomain"]), None)
            source = LIBRARY.save_source({"id": existing_source["id"] if existing_source else None, "name": name, "base_url": base_url, "enabled": True, "priority": int(payload.get("priority") or (existing_source or {}).get("priority") or 100), "parser_type": "custom-authorized", "config": config})
            LIBRARY.record_source_check(source["id"], "Working", json.dumps({"validResults": guided["diagnostics"]["validResults"], "mode": "site-auto-setup"}))
            apply_persisted_configuration()
            guided.update({"source": source, "sourceActive": True, "states": [*states, "saving-source", "testing-source", "active"]})
            response(handler, HTTPStatus.OK, guided)
        elif route_path == "/api/admin/setup/sources/live-preview":
            preview = live_preview(
                payload.get("livePageUrl"), payload.get("config") if isinstance(payload.get("config"), dict) else {},
                str(payload.get("sourceName") or "Custom Authorized Source"), timeout=payload.get("requestTimeout"),
                allow_private_network=bool(payload.get("allowPrivateNetwork")),
                auth=payload.get("auth") if isinstance(payload.get("auth"), dict) else {},
                render_mode=str(payload.get("renderMode") or "auto"),
            )
            response(handler, HTTPStatus.OK, preview)
        elif route_path == "/api/admin/setup/sources/delete":
            source_id = str(payload.get("id") or "")
            if not source_id: raise ValueError("Source id is required")
            LIBRARY.delete_source(source_id); apply_persisted_configuration()
            response(handler, HTTPStatus.OK, {"ok": True})
        elif route_path == "/api/admin/setup/sources/test":
            source_id = str(payload.get("id") or "")
            source = next((item for item in LIBRARY.list_sources() if item["id"] == source_id), None)
            if not source: raise ValueError("Source not found")
            if source.get("parser_type") == "custom-authorized":
                items, diagnostics = search_authorized_source(source, str(payload.get("query") or "test"))
                status_map = {"working": "Working", "parser-mismatch": "Parser mismatch", "invalid-normalized-output": "Invalid normalized output", "timed-out": "Timed out", "access-restricted": "Access restricted", "needs-adapter": "Needs adapter"}
                status = "Working" if items else status_map.get(diagnostics.get("parserStatus"), "Reachable but no results")
                LIBRARY.record_source_check(source_id, status, json.dumps(diagnostics, ensure_ascii=False))
                response(handler, HTTPStatus.OK, {"ok": bool(items), "status": status, "diagnostics": diagnostics})
            else:
                try:
                    candidates = search_movie_on_site("test", limit=1, timeout=10, max_html_bytes=1_000_000, site=source["base_url"])
                    if not candidates:
                        raise ValueError("Site is reachable, but its search-page layout is not compatible with this source adapter.")
                    LIBRARY.record_source_check(source_id, "Working")
                    response(handler, HTTPStatus.OK, {"ok": True, "status": "Working"})
                except Exception as exc:
                    LIBRARY.record_source_check(source_id, "Needs adapter", str(exc))
                    response(handler, HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "error": str(exc)})
        else:
            response(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Admin setup route not found"})
    except LiveFetchError as exc:
        status_code = HTTPStatus.GATEWAY_TIMEOUT if exc.status == "timed-out" else HTTPStatus.UNPROCESSABLE_ENTITY
        response(handler, status_code, {"ok": False, "status": exc.status, "error": str(exc)})
    except (ValueError, OSError, json.JSONDecodeError, LocalAIError) as exc:
        response(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
    return True


def library_admin_post(handler: BaseHTTPRequestHandler, parsed: Any, route_path: str, payload: dict[str, Any]) -> bool:
    if not route_path.startswith("/api/admin/library/"):
        return False
    if not is_admin(handler, parsed):
        response(handler, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Admin authorization required"})
        return True
    if LIBRARY is None:
        response(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "Library service is unavailable."})
        return True
    try:
        if route_path == "/api/admin/library/scan":
            kind = str(payload.get("kind") or "full")
            if kind not in {"full", "quick"}: raise ValueError("Invalid scan type")
            job_id = LIBRARY.start_scan(kind)
            # Launch Jellyfin's asynchronous index refresh at the same time as
            # the local scan.  It is intentionally best-effort: the dashboard
            # still has a useful local result if Jellyfin is restarting.
            jellyfin = trigger_jellyfin_library_refresh()
            response(handler, HTTPStatus.ACCEPTED, {"ok": True, "jobId": job_id, "jellyfin": jellyfin})
        elif route_path.startswith("/api/admin/library/scan/"):
            item_id = route_path.rsplit("/", 1)[-1]
            response(handler, HTTPStatus.ACCEPTED, {"ok": True, "jobId": LIBRARY.start_scan("single", item_id=item_id)})
        elif route_path.startswith("/api/admin/library/refresh/"):
            item_id = route_path.rsplit("/", 1)[-1]
            response(handler, HTTPStatus.ACCEPTED, {"ok": True, "jobId": LIBRARY.start_scan("refresh", item_id=item_id, refresh_metadata=True)})
        elif route_path == "/api/admin/library/manual-match":
            item_id, tmdb_id = str(payload.get("itemId") or ""), int(payload.get("tmdbId") or 0)
            if not item_id or not tmdb_id: raise ValueError("itemId and tmdbId are required")
            LIBRARY.manual_match(item_id, tmdb_id)
            response(handler, HTTPStatus.OK, {"ok": True})
        elif route_path == "/api/admin/library/tmdb-search":
            media_type = str(payload.get("type") or "")
            query = str(payload.get("query") or "").strip()
            if media_type not in {"movie", "tv"} or not query: raise ValueError("type and query are required")
            response(handler, HTTPStatus.OK, {"ok": True, "items": LIBRARY.search_tmdb(media_type, query)})
        elif route_path == "/api/admin/library/rename-preview":
            item_id = str(payload.get("itemId") or "")
            if not item_id: raise ValueError("itemId is required")
            response(handler, HTTPStatus.OK, {"ok": True, "plan": LIBRARY.rename_preview(item_id)})
        elif route_path == "/api/admin/library/rename":
            item_id = str(payload.get("itemId") or "")
            if not item_id or payload.get("confirmed") is not True: raise ValueError("itemId and rename confirmation are required")
            response(handler, HTTPStatus.OK, {"ok": True, **LIBRARY.rename_matched_files(item_id)})
        elif route_path.startswith("/api/admin/library/ignore/"):
            item_id = route_path.rsplit("/", 1)[-1]
            with LIBRARY._connection() as db: db.execute("UPDATE media_items SET ignored=1 WHERE id=?", (item_id,))
            response(handler, HTTPStatus.OK, {"ok": True})
        else:
            response(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Admin library route not found"})
    except (ValueError, RuntimeError) as exc:
        response(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
    return True


class AppHandler(BaseHTTPRequestHandler):
    server_version = "EvidenceLinkWeb/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route_path = normalized_app_path(parsed.path)
        # Admin setup and adapter setup are intentionally unavailable for now.
        # Redirect old bookmarks to the main index instead of rendering its SPA view.
        if route_path in {"/setup", "/adapters"}:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", f"{APP_BASE_PATH}/")
            self.send_header("cache-control", "no-store")
            self.end_headers()
            return
        if route_path == "/admin/workflow-analyzer":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", f"{APP_BASE_PATH}/admin/sources")
            self.send_header("cache-control", "no-store")
            self.end_headers()
            return
        if route_path in {"/", "/library/movies", "/library/tv", "/library/missing", "/library/recent", "/admin", "/admin/sources"}:
            if not is_authorized(self, parsed):
                response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Token required"})
                return
            raw = HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if route_path == "/health":
            response(self, HTTPStatus.OK, {"ok": True})
            return
        if route_path == "/api/admin/sources":
            if not is_admin(self, parsed):
                response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Admin authorization required"})
            else:
                response(self, HTTPStatus.OK, {"ok": True, "sources": combined_sources()})
            return
        if route_path == "/api/adapters":
            if not is_admin(self, parsed):
                response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Admin authorization required"})
            else:
                inventory = ADAPTERS.inventory()
                records = []
                for item in inventory["adapters"]:
                    adapter = dict(item["adapter"])
                    health = adapter.get("health", {})
                    if not adapter.get("enabled"):
                        status = "Disabled"
                    elif not adapter.get("maker", {}).get("ready_to_save"):
                        # Older adapters may have been saved when a page was
                        # reachable even though the final-link flow was never
                        # verified.  Keep them available for review, but do
                        # not present that as a working download adapter.
                        status = "Needs retest"
                    elif health.get("last_test_status") == "passed" and health.get("status") == "Working":
                        status = "Working"
                    elif health.get("last_test_status") == "failed":
                        status = "Failed"
                    else:
                        status = "Needs retest"
                    adapter.update({"file_name": item["file_name"], "storage_location": item["storage_location"], "display_status": status})
                    records.append(adapter)
                summary = {
                    "total": len(records),
                    "enabled": sum(bool(item.get("enabled")) for item in records),
                    "working": sum(item["display_status"] == "Working" for item in records),
                    "failed": sum(item["display_status"] == "Failed" for item in records) + len(inventory["invalid_files"]),
                    "disabled": sum(item["display_status"] == "Disabled" for item in records),
                }
                response(self, HTTPStatus.OK, {"ok": True, "summary": summary, "adapters": records, "invalid_files": inventory["invalid_files"], "storage_directory": inventory["storage_directory"]})
            return
        if route_path == "/api/wallpapers":
            if not is_authorized(self, parsed):
                response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Token required"})
                return
            response(self, HTTPStatus.OK, {"ok": True, "images": tmdb_backdrop_urls()})
            return
        if route_path == "/api/search/sources":
            if not is_authorized(self, parsed):
                response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Token required"}); return
            response(self, HTTPStatus.OK, {"ok": True, "sources": [{"id":"all","name":"All Sources"}, {"id":"existing","name":"Existing Working Site"}, *[{"id": item["id"], "name": item["name"]} for item in enabled_saved_adapters()]]})
            return
        if library_response(self, parsed, route_path):
            return
        if library_admin_dashboard_response(self, parsed, route_path):
            return
        if admin_setup_get(self, parsed, route_path):
            return
        if route_path == "/api/tmdb-image":
            if not is_authorized(self, parsed):
                response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Token required"})
                return
            image_url = parse_qs(parsed.query).get("url", [""])[0].strip()
            image_path = parse_qs(parsed.query).get("path", [""])[0].strip()
            # Library cards send only an image path.  Constructing the host
            # here keeps clients from receiving TMDB credentials or raw URLs.
            if image_path and re.fullmatch(r"w(?:154|342|500|780|1280)/[-A-Za-z0-9_.]+", image_path):
                image_url = f"https://image.tmdb.org/t/p/{image_path}"
            image_host = urlparse(image_url).hostname or ""
            if not image_url.startswith("https://") or image_host != "image.tmdb.org":
                response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid poster URL"})
                return
            try:
                request = Request(image_url, headers={"user-agent": "EvidenceLinkHelper/1.0"})
                with urlopen(request, timeout=10) as handle:
                    content_type = str(handle.headers.get("content-type") or "").split(";", 1)[0]
                    raw = handle.read(5_000_000)
                if not content_type.startswith("image/"):
                    raise ValueError("TMDB response was not an image")
            except Exception as exc:
                response(self, HTTPStatus.BAD_GATEWAY, {"ok": False, "error": f"Poster unavailable: {exc}"})
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(raw)))
            self.send_header("cache-control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(raw)
            return
        if route_path == "/api/library-details":
            if not is_authorized(self, parsed):
                response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Token required"})
                return
            jellyfin_id = parse_qs(parsed.query).get("id", [""])[0].strip()
            if not re.fullmatch(r"[A-Za-z0-9-]{8,80}", jellyfin_id):
                response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid Jellyfin show"})
                return
            item = jellyfin_library_item_by_id(jellyfin_id)
            if not item or item.get("type") != "Series":
                response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Jellyfin TV show not found"})
                return
            response(
                self,
                HTTPStatus.OK,
                {"ok": True, "detail": jellyfin_show_availability(jellyfin_id, item.get("tmdb_id", ""))},
            )
            return
        if route_path == "/api/search":
            if not is_authorized(self, parsed):
                response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Token required"})
                return
            query = parse_qs(parsed.query).get("q", [""])[0].strip()
            selected_source = parse_qs(parsed.query).get("source", ["all"])[0].strip() or "all"
            if not query:
                response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Name required"})
                return
            episode_target = episode_target_from_query(query)
            # Index sites commonly only expose the show page, not an SxxExx result.
            # A title year is useful for matching, but many such indexes omit it from
            # their search index (for example: "The Boys", not "The Boys 2019").
            search_query = episode_search_fallback(query) if episode_target else query
            try:
                candidates: list[Any] = []; custom_diagnostics: dict[str, dict[str, Any]] = {}; adapter_failures: list[dict[str, str]] = []
                candidates, custom_diagnostics, adapter_failures = search_all_configured_sources(search_query, selected_source)
                candidates = merge_search_candidates(candidates)
                # Most index sites have one show page, not a page per episode. Retry
                # the title alone if its year was not indexed by the source.
                if not candidates and episode_target:
                    title_only = re.sub(r"\b(?:19|20)\d{2}\b", " ", search_query)
                    title_only = re.sub(r"\s+", " ", title_only).strip()
                    if title_only and title_only != search_query:
                        candidates, custom_diagnostics, adapter_failures = search_all_configured_sources(title_only, selected_source)
                        candidates = merge_search_candidates(candidates)
                        if candidates:
                            search_query = title_only
            except Exception as exc:
                response(self, HTTPStatus.BAD_GATEWAY, {"ok": False, "error": str(exc)})
                return
            first_url = (candidates[0].get("url", "") if candidates and isinstance(candidates[0], dict) else (candidates[0].url if candidates else ""))
            source_host = urlparse(first_url).netloc if first_url else ""
            source_rows = LIBRARY.list_sources() if LIBRARY else []
            source_summary = [
                {
                    "name": source["name"], "url": source["base_url"], "enabled": source["enabled"],
                    "results": sum(1 for candidate in candidates if (candidate.get("source", "") if isinstance(candidate, dict) else candidate.source).rstrip("/") == source["base_url"].rstrip("/")),
                    "diagnostics": custom_diagnostics.get(source["id"]) if source.get("parser_type") == "custom-authorized" else None,
                }
                for source in source_rows
            ]
            source_summary.extend([{"id": adapter["id"], "name": adapter["name"], "url": "https://"+adapter["domains"][0], "enabled": adapter["enabled"], "results": sum(1 for candidate in candidates if isinstance(candidate, dict) and candidate.get("source_id") == adapter["id"]), "health": adapter.get("health", {}).get("status", "Working")} for adapter in ENABLED_ADAPTERS])
            # hdmovie2r's public host needs several seconds to generate its
            # visible Direct link. Start its normal, already-authorized
            # 1080p workflow while the user is reviewing the search cards.
            # The result is only cached after the final response is verified.
            if selected_source in {"all", "hdmovie2r_ltd"}:
                candidate = next((item for item in candidates if isinstance(item, dict) and item.get("source_id") == "hdmovie2r_ltd"), None)
                adapter = next((item for item in enabled_saved_adapters() if item["id"] == "hdmovie2r_ltd"), None)
                if candidate and adapter:
                    prefetch_adapter_workflow(adapter, str(candidate["url"]), "1080p")
            response(
                self,
                HTTPStatus.OK,
                {
                    "ok": True,
                    "candidates": candidates_with_posters(candidates, query),
                    "source": source_host,
                    "sources": source_summary,
                    "adapterFailures": adapter_failures,
                    "episodeTarget": episode_target,
                    "searchedAsShow": bool(episode_target and search_query != query),
                },
            )
            return
        response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        route_path = normalized_app_path(parsed.path)
        length = int(self.headers.get("content-length") or "0")
        if length > 16_384:
            response(self, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "Request too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON"})
            return

        if admin_setup_post(self, parsed, route_path, payload):
            return
        if library_admin_post(self, parsed, route_path, payload):
            return
        if route_path.startswith("/api/admin/sources/"):
            if not is_admin(self, parsed):
                response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Admin authorization required"}); return
            action = route_path[len("/api/admin/sources/"):]
            try:
                if action == "wizard/search":
                    site_url, query = str(payload.get("siteUrl") or "").strip(), str(payload.get("query") or "").strip()
                    if not site_url or not query: raise ValueError("Website URL and example search title are required")
                    candidates, detail = discover_search_results(site_url, query)
                    print(f"Source onboarding search: site={urlparse(site_url).hostname} query={query!r} status={detail.get('status')} results={len(candidates)} attempts={detail.get('attempts')}")
                    response(self, HTTPStatus.OK, {"ok": True, "candidates": candidates})
                elif action == "wizard/analyze":
                    candidate = payload.get("candidate") or {}
                    result = analyze_adapter({"siteName": urlparse(str(payload.get("siteUrl") or "")).hostname or "New Source", "mainSiteUrl": payload.get("siteUrl"), "examplePageUrl": candidate.get("url"), "searchQuery": payload.get("query") or "", "expectedQuality": ""})
                    workflow = analyze_movie_workflow(str(payload.get("siteUrl") or ""), str(candidate.get("url") or ""))
                    verified_workflow = bool(any(item.get("is_final_file") and item.get("final_url") for item in workflow.get("results", [])))
                    # Onboarding has a stricter contract than the legacy
                    # adapter maker: a new source is saved only after this
                    # exact selected result reached a real file response.
                    result["report"]["final_link_detected"] = verified_workflow
                    result["report"]["ready_to_save"] = bool(result["report"].get("search_test_successful")) and verified_workflow
                    result["adapter"].setdefault("maker", {})["ready_to_save"] = result["report"]["ready_to_save"]
                    result["adapter"]["maker"]["workflow_verified"] = verified_workflow
                    result["adapter"]["maker"]["workflow_status"] = workflow.get("status")
                    # Sources onboarding has already run the workflow engine;
                    # save this explicit opt-in so Find Links can use the same
                    # engine if the normal adapter later finds no final file.
                    if verified_workflow:
                        enable_workflow_fallback_for_verified_onboarding(result["adapter"])
                    print(f"Source onboarding workflow: site={urlparse(str(payload.get('siteUrl') or '')).hostname} candidate={urlparse(str(candidate.get('url') or '')).path} status={workflow.get('status')} results={len(workflow.get('results', []))}")
                    response(self, HTTPStatus.OK, {"ok": True, **result, "workflow": workflow})
                else:
                    source_id, kind = str(payload.get("id") or ""), str(payload.get("kind") or "")
                    if not source_id or kind not in {"runtime", "adapter"}: raise ValueError("Invalid source")
                    if kind == "runtime":
                        source = next((item for item in (LIBRARY.list_sources() if LIBRARY else []) if item["id"] == source_id), None)
                        if not source: raise ValueError("Source not found")
                        if action == "toggle":
                            source["enabled"] = bool(payload.get("enabled")); LIBRARY.save_source(source); apply_persisted_configuration()
                        elif action == "delete": LIBRARY.delete_source(source_id)
                        elif action == "test":
                            try: search_movie_on_site("test", limit=1, timeout=10, max_html_bytes=1_000_000, site=source["base_url"]); LIBRARY.record_source_check(source_id, "Working")
                            except Exception as exc: LIBRARY.record_source_check(source_id, "Failed", str(exc))
                        else: raise ValueError("Unknown source action")
                    else:
                        adapter = next((item for item in ENABLED_ADAPTERS if item["id"] == source_id), None)
                        if not adapter: raise ValueError("Adapter not found")
                        if action == "toggle": adapter["enabled"] = bool(payload.get("enabled")); ADAPTERS.replace(adapter); ENABLED_ADAPTERS[:] = ADAPTERS.list()
                        elif action == "delete": ADAPTERS.delete(source_id); ENABLED_ADAPTERS[:] = ADAPTERS.list()
                        elif action == "test":
                            result = analyze_adapter({"siteName": adapter["name"], "mainSiteUrl": "https://" + adapter["domains"][0], "examplePageUrl": adapter.get("maker", {}).get("example_page_url") or "https://" + adapter["domains"][0], "searchQuery": adapter.get("maker", {}).get("search_query") or "test", "expectedQuality": adapter.get("maker", {}).get("expected_quality") or ""})
                            adapter["health"] = {"status": "Working" if result["report"]["ready_to_save"] else "Needs review", "last_test_status": "passed" if result["report"]["ready_to_save"] else "failed", "last_tested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}; ADAPTERS.replace(adapter); ENABLED_ADAPTERS[:] = ADAPTERS.list()
                        else: raise ValueError("Unknown source action")
                    response(self, HTTPStatus.OK, {"ok": True, "sources": combined_sources()})
            except (ValueError, RuntimeError) as exc:
                print(f"Source onboarding failed: action={action} error={exc}")
                response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        if route_path == "/api/admin/workflow-analyzer":
            if not is_admin(self, parsed):
                response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Admin authorization required"})
                return
            try:
                site_url = str(payload.get("siteUrl") or "").strip()
                movie_url = str(payload.get("movieUrl") or "").strip()
                quality = str(payload.get("quality") or "").strip() or None
                if not site_url or not movie_url:
                    raise ValueError("Site URL and movie page URL are required")
                response(self, HTTPStatus.OK, analyze_movie_workflow(site_url, movie_url, quality))
            except ValueError as exc:
                response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            except Exception:
                # Do not let an analyzer failure crash the production handler
                # or disclose session/transport details to the browser.
                response(self, HTTPStatus.BAD_GATEWAY, {"ok": False, "error": "Workflow analysis failed"})
            return
        if route_path.startswith("/api/adapters/"):
            if not is_admin(self, parsed):
                response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Admin authorization required"})
                return
            try:
                if route_path == "/api/adapters/analyze":
                    response(self, HTTPStatus.OK, {"ok": True, **analyze_adapter(payload)})
                elif route_path in {"/api/adapters/test", "/api/adapters/retest"}:
                    adapter = payload.get("adapter")
                    if not isinstance(adapter, dict): raise ValueError("Adapter JSON required")
                    # Retest reuses the supplied example URL but does not persist or contact any adapter domain not explicitly supplied.
                    test_payload = {"siteName": adapter.get("name", "Adapter"), "mainSiteUrl": "https://" + str((adapter.get("domains") or [""])[0]), "examplePageUrl": payload.get("examplePageUrl"), "expectedQuality": payload.get("expectedQuality", "")}
                    result = analyze_adapter(test_payload)
                    if route_path == "/api/adapters/retest" and payload.get("adapterId"):
                        stored = ADAPTERS.get(str(payload["adapterId"]))
                        if stored:
                            passed = bool(result.get("report", {}).get("ready_to_save"))
                            stored["maker"] = result.get("adapter", {}).get("maker", stored.get("maker", {}))
                            stored["health"] = {"status": "Working" if passed else "Failed", "last_test_status": "passed" if passed else "failed", "last_tested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                            ADAPTERS.replace(stored); ENABLED_ADAPTERS[:] = ADAPTERS.list()
                    response(self, HTTPStatus.OK, {"ok": True, **result})
                elif route_path == "/api/adapters/save":
                    adapter = payload.get("adapter")
                    if not isinstance(adapter, dict): raise ValueError("Adapter JSON required")
                    mode = str(payload.get("saveMode") or "")
                    ident = adapter_id(str(adapter.get("id") or adapter.get("name") or ""))
                    exists = ADAPTERS.get(ident)
                    if exists and not mode:
                        response(self, HTTPStatus.CONFLICT, {"ok": False, "error": "Adapter ID already exists", "adapterId": ident, "existing": {"name": exists.get("name"), "file_name": f"{ident}.json"}})
                        return
                    if mode == "new" and exists:
                        base, suffix = ident, 2
                        while ADAPTERS.get(f"{base}_{suffix}"):
                            suffix += 1
                        adapter["id"] = f"{base}_{suffix}"
                    ready = bool(adapter.get("maker", {}).get("ready_to_save"))
                    if adapter.get("maker", {}).get("workflow_verified") is False:
                        raise ValueError("A verified final file workflow is required before saving this source")
                    requested_enabled = bool(adapter.get("enabled", True))
                    enabled = requested_enabled and ready
                    adapter["enabled"] = enabled
                    adapter["health"] = {
                        "status": "Working" if enabled else ("Disabled" if not requested_enabled else "Needs retest"),
                        "last_test_status": "passed" if ready else "needs_retest",
                        "last_tested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                    saved = ADAPTERS.replace(adapter) if exists and mode == "update" else ADAPTERS.save(adapter)
                    ENABLED_ADAPTERS[:] = ADAPTERS.list()
                    response(self, HTTPStatus.CREATED, {"ok": True, "adapter": saved, "file_name": f"{saved['id']}.json", "storage_location": f"adapters/{saved['id']}.json", "main_search_enabled": bool(saved.get("enabled"))})
                elif route_path == "/api/adapters/toggle":
                    stored = ADAPTERS.get(str(payload.get("adapterId") or ""))
                    if not stored: raise ValueError("Adapter not found")
                    stored["enabled"] = bool(payload.get("enabled"))
                    health = stored.setdefault("health", {}); health["status"] = "Working" if stored["enabled"] and health.get("last_test_status") == "passed" else "Disabled"
                    ADAPTERS.replace(stored); ENABLED_ADAPTERS[:] = ADAPTERS.list()
                    response(self, HTTPStatus.OK, {"ok": True, "adapter": stored})
                elif route_path == "/api/adapters/delete":
                    ADAPTERS.delete(str(payload.get("adapterId") or "")); ENABLED_ADAPTERS[:] = ADAPTERS.list()
                    response(self, HTTPStatus.OK, {"ok": True})
                else:
                    response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Adapter route not found"})
            except (ValueError, RuntimeError) as exc:
                response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        if route_path not in {"/api/find", "/api/resolve"}:
            response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return
        if not is_authorized(self, parsed):
            response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Token required"})
            return

        if route_path == "/api/resolve":
            url = str(payload.get("url") or payload.get("resolve_url") or "").strip()
            quality = str(payload.get("quality") or "").strip()
            label = str(payload.get("label") or payload.get("variant") or "").strip()
            kind = str(payload.get("kind") or "").strip()
            if not url.startswith("http"):
                response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Missing link source"})
                return
            try:
                best = resolve_inner_link(url, timeout=12)
            except Exception as exc:
                if LIBRARY is not None:
                    LIBRARY.log_admin_event("resolver_failure", "Resolver request failed", url, str(exc))
                response(self, HTTPStatus.BAD_GATEWAY, {"ok": False, "error": str(exc)})
                return
            if not best or is_intermediate_url(best):
                if LIBRARY is not None:
                    LIBRARY.log_admin_event("broken_link", "Direct link could not be resolved", url, "Direct link not found")
                response(self, HTTPStatus.BAD_GATEWAY, {"ok": False, "error": "Direct link not found"})
                return
            size_label = size_from_url_bytes(best, url) or size_from_text(label, quality) or "unknown"
            # A "GB/E" label describes an individual episode, not the ZIP
            # archive.  Do not relabel a season pack as an episode-sized file
            # when its host does not expose a total byte count.
            if kind == "Season Zip" and size_label.endswith("/E"):
                size_label = "ZIP size unavailable"
            response(self, HTTPStatus.OK, {"ok": True, "url": best, "size": size_label})
            return

        query = str(payload.get("query") or "").strip()
        quality = str(payload.get("quality") or "1080p").strip()
        requested_episode = payload.get("episodeTarget") or episode_target_from_query(query)
        requested_season = payload.get("seasonTarget")
        if not isinstance(requested_episode, dict):
            requested_episode = None
        elif not isinstance(requested_episode.get("season"), (int, float, str)) or not isinstance(requested_episode.get("episode"), (int, float, str)):
            requested_episode = None
        else:
            try:
                requested_episode = {"season": int(requested_episode["season"]), "episode": int(requested_episode["episode"])}
            except (TypeError, ValueError):
                requested_episode = None
        if not isinstance(requested_season, (int, float, str)):
            requested_season = None
        else:
            try:
                requested_season = int(requested_season)
                if not 1 <= requested_season <= 99:
                    requested_season = None
            except (TypeError, ValueError):
                requested_season = None
        candidate_payload = payload.get("candidate") or {}
        title = str(candidate_payload.get("title") or "").strip()
        url = str(candidate_payload.get("url") or "").strip()
        if not query or not title or not url.startswith("http"):
            response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Missing selected result"})
            return
        if candidate_payload.get("adapter_type") == "generated-adapter":
            adapter = next((item for item in enabled_saved_adapters() if item["id"] == candidate_payload.get("source_id")), None)
            if not adapter:
                response(self, HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "error": "Adapter is disabled, invalid, or needs retesting"}); return
            try:
                prefetch_key = workflow_prefetch_key(adapter["id"], url, quality)
                cached_workflow = WORKFLOW_PREFETCH_CACHE.get(prefetch_key)
                if cached_workflow and time.time() - cached_workflow[0] < WORKFLOW_PREFETCH_CACHE_SECONDS:
                    cached_payload = dict(cached_workflow[1])
                    cached_payload["cached"] = True
                    response(self, HTTPStatus.OK, cached_payload)
                    return
                adapter_runtime = SiteAdapter(adapter)
                links = adapter_runtime.find_links(url, quality)
                for link in links:
                    link.update({"source": f"adapter:{adapter['id']}", "source_name": adapter["name"], "temporary": True})
                # Preserve the existing configuration-driven adapter response
                # whenever it produced verified links.  The internal workflow
                # engine is an explicit, configured fallback—not a replacement
                # for the working Find Links path and never a local HTTP call.
                if not links and analyzer_enabled_for(url, adapter):
                    workflow = analyze_movie_workflow(
                        "https://" + str((adapter.get("domains") or [urlparse(url).hostname or ""])[0]),
                        url,
                        None if quality.lower() in {"all", "*"} else quality,
                    )
                    response(self, HTTPStatus.OK, normalize_workflow_result(
                        workflow, f"adapter:{adapter['id']}", adapter["name"], is_admin(self, parsed)
                    ))
                    return
                response(self, HTTPStatus.OK, {
                    "ok": True,
                    "links": [enrich_delivery_link(link) for link in links],
                    "message": "" if links else adapter_runtime.last_find_reason,
                    "debug": [],
                    "cached": False,
                    "temporary": True,
                })
            except Exception as exc:
                response(self, HTTPStatus.BAD_GATEWAY, {"ok": False, "error": f"{adapter['name']} adapter failed: {exc}"})
            return
        # Generic authorized sources already normalize a public URL during
        # search.  Their result must not be sent through the legacy resolver.
        custom_result = candidate_payload.get("custom_result")
        if candidate_payload.get("adapter_type") == "custom-authorized":
            if not isinstance(custom_result, dict):
                response(self, HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "error": "Invalid normalized output"})
                return
            public_url = str(custom_result.get("publicUrl") or "")
            parsed_public = urlparse(public_url)
            if parsed_public.scheme not in {"http", "https"} or not parsed_public.hostname:
                response(self, HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "error": "Invalid URL in custom source result"})
                return
            source_name = str(custom_result.get("sourceBadge") or candidate_payload.get("source_name") or "Custom Authorized Source")
            source_id = str(candidate_payload.get("source_id") or custom_result.get("sourceId") or "")
            source = next((item for item in (LIBRARY.list_sources() if LIBRARY else []) if str(item.get("id")) == source_id), None)
            if not source:
                response(self, HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "error": "Configured source was not found"})
                return
            resolved = resolve_authorized_result(source, public_url)
            if not resolved.get("success"):
                if LIBRARY:
                    LIBRARY.log_admin_event("resolver_failure", "Authorized source delivery path was not found", public_url, json.dumps({"sourceId": source_id, "trail": resolved.get("trail", [])})[:2000])
                response(self, HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "error": resolved.get("message") or "Delivery path could not be resolved"})
                return
            public_url = str(resolved["url"])
            response(self, HTTPStatus.OK, {"ok": True, "links": [enrich_delivery_link({
                "quality": str(custom_result.get("quality") or "Unknown"), "quality_label": str(custom_result.get("quality") or "Unknown"),
                "size": str(custom_result.get("fileSize") or "Unknown"), "url": public_url,
                "source": str(candidate_payload.get("source") or ""), "source_name": source_name,
                "variant": str(custom_result.get("filename") or ""), "provider": str(custom_result.get("providerName") or ""),
                "page_url": str(custom_result.get("pageUrl") or ""), "confidence": str(custom_result.get("confidence") or "low"),
            })], "debug": [], "cached": False, "episodeTarget": requested_episode, "seasonTarget": requested_season, "episodeFallback": False})
            return
        # The Season ZIP action is scoped to one season. A source result that
        # explicitly names another season must never be resolved as a fallback.
        # This also protects direct API callers from bypassing the UI filter.
        candidate_season = explicit_season_in_candidate(title, url)
        if requested_season and candidate_season is not None and candidate_season != requested_season:
            response(
                self,
                HTTPStatus.CONFLICT,
                {
                    "ok": False,
                    "error": (
                        f"Source result is Season {candidate_season}, not requested "
                        f"Season {requested_season}."
                    ),
                },
            )
            return

        cache_key = json.dumps(
            {
                "query": query.lower(),
                "quality": quality.lower(),
                "title": title,
                "url": url,
                "episode": requested_episode,
                "season": requested_season,
            },
            sort_keys=True,
        )
        cached = FIND_RESULT_CACHE.get(cache_key)
        now = time.time()
        if cached and now - cached[0] < FIND_CACHE_SECONDS:
            payload = dict(cached[1])
            payload["cached"] = True
            response(self, HTTPStatus.OK, payload)
            return

        qualities = DEFAULT_QUALITIES if quality.lower() in {"all", "*"} else (quality,)
        links_by_key: dict[str, dict[str, str]] = {}
        debug: list[dict[str, str]] = []
        # ``candidate.source`` is the configured index site, while ``url`` is
        # its individual result page.  Keep both: the former is what a user
        # needs to identify in the Delivery Links panel.
        candidate_source = str(candidate_payload.get("source") or "").strip().rstrip("/")
        candidate = Candidate(title=title, url=url, source=candidate_source)
        delivery_source_url = candidate.source or candidate.url
        delivery_source_name = source_name_for_url(delivery_source_url)
        series_mode = is_series_candidate(candidate)
        try:
            for item_quality in qualities:
                if series_mode:
                    if is_fxlinks_url(candidate.url):
                        listing_links = [
                            type(
                                "ListingLink",
                                (),
                                {"href": candidate.url, "section": title, "text": title},
                            )()
                        ]
                    else:
                        listing_links = find_listing_links(candidate.url, item_quality, 12, 2_000_000)
                    for link in listing_links:
                        listing_options = fxlinks_listing_options(link.href, timeout=8)
                        if not listing_options:
                            listing_options = [
                                {
                                    "url": "",
                                    "listing": link.href,
                                    "label": compact_label(link.section, link.text),
                                    "source": delivery_source_url,
                                    "pending": True,
                                }
                            ]
                        for option in listing_options:
                            row = type(
                                "MetaRow",
                                (),
                                {
                                    "section": compact_label(link.section, option.get("label", "")),
                                    "listing_link": option.get("listing") or link.href,
                                    "landing_page": link.href,
                                    "instant_link": option.get("listing", ""),
                                    "final_wrapper": option.get("listing", ""),
                                    "final_inner_url": option.get("url", ""),
                                    "content_length": "",
                                    "title": title,
                                },
                            )()
                            best = option.get("url", "")
                            meta = series_meta(row)
                            size_label = size_from_text(
                                compact_label(link.section, option.get("label", "")),
                                item_quality,
                            ) or "unknown"
                            if size_label.endswith("/E"):
                                if meta.get("kind") == "Season Zip":
                                    size_label = "tap for size"
                                else:
                                    size_label = f"approx {size_label}"
                            link_key = "|".join(
                                str(value)
                                for value in (
                                    item_quality.lower(),
                                    meta.get("season") or "",
                                    meta.get("kind") or "",
                                    size_label,
                                    option.get("listing") or link.href,
                                )
                            )
                            links_by_key[link_key] = {
                                "quality": item_quality,
                                "size": size_label,
                                "url": best,
                                "resolve_url": option.get("listing") or link.href,
                                "pending": True,
                                "source": delivery_source_url,
                                "listing": option.get("listing") or link.href,
                                "variant": compact_label(link.section, option.get("label", "")),
                                "episode_exact": is_exact_episode_link(meta, requested_episode),
                                **meta,
                            }
                    continue
                fast_options = fast_listing_options(candidate, item_quality, timeout=6, max_options=3)
                if fast_options:
                    for item in fast_options:
                        item["source"] = delivery_source_url
                        link_key = item["url"]
                        links_by_key[link_key] = item
                    continue
                rows = build_evidence(
                    query=query,
                    candidate=candidate,
                    quality=item_quality,
                    timeout=5,
                    max_hops=8,
                    max_html_bytes=2_000_000,
                    max_direct_links=0 if series_mode else 3,
                    max_listing_workers=0 if series_mode else 8,
                    listing_delay=2.5 if series_mode else 0,
                )
                debug.extend(asdict(row) for row in rows)
                for row in rows:
                    best = find_best_link(row)
                    if not best:
                        continue
                    if is_intermediate_url(best):
                        continue
                    size_label = best_size_label(row, item_quality, best)
                    meta = series_meta(row)
                    link_key = "|".join(
                        str(value)
                        for value in (
                            item_quality.lower(),
                            meta.get("season") or "",
                            meta.get("kind") or "",
                            size_label,
                        )
                    )
                    if not meta.get("season") and not meta.get("kind"):
                        link_key = best
                    item = {
                        "quality": item_quality,
                        "size": size_label,
                        "url": best,
                        "source": delivery_source_url,
                        "listing": row.listing_link,
                        "variant": link_variant_label(row),
                        "episode_exact": is_exact_episode_link(meta, requested_episode),
                        **meta,
                    }
                    existing = links_by_key.get(link_key)
                    if existing and link_resolution_score(existing["url"]) >= link_resolution_score(best):
                        continue
                    links_by_key[link_key] = item
        except Exception as exc:
            response(self, HTTPStatus.BAD_GATEWAY, {"ok": False, "error": str(exc)})
            return
        links = list(links_by_key.values())
        for item in links:
            # Do not use the final file host (for example Google Drive) here:
            # this badge identifies the content source selected before Find
            # Links was pressed.
            item["source"] = delivery_source_url
            item["source_name"] = delivery_source_name
            item["quality_label"] = media_quality_label(
                item.get("quality", ""), item.get("variant", ""), title
            ) or item.get("quality", "")
        exact_episode_links = [item for item in links if item.get("episode_exact")]
        # Never label a season zip/show page as a direct episode result. If the
        # source does not publish individual episode entries, retain its usable
        # pack/show links as an explicitly labelled fallback.
        episode_fallback = bool(requested_episode and not exact_episode_links)
        if exact_episode_links:
            links = exact_episode_links
        if requested_season:
            # A season button must never return a mixed bundle of episode links,
            # whole-show packs, or another season's ZIP.
            links = [
                item for item in links
                if item.get("kind") == "Season Zip"
                and season_number_from_meta(item) == requested_season
            ]
        payload = {
            "ok": True,
            "links": [enrich_delivery_link(item) for item in sort_links(links)],
            "debug": debug,
            "cached": False,
            "episodeTarget": requested_episode,
            "seasonTarget": requested_season,
            "episodeFallback": episode_fallback,
        }
        FIND_RESULT_CACHE[cache_key] = (time.time(), payload)
        response(self, HTTPStatus.OK, payload)


def main() -> int:
    global ACCESS_TOKEN, TMDB_API_KEY, MEDIA_LIBRARY_PATHS, JELLYFIN_URL, JELLYFIN_API_KEY, LIBRARY, ADMIN_TOKEN
    parser = argparse.ArgumentParser(description="Run the Evidence Link Helper web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default="", help="Optional access token for public hosting.")
    parser.add_argument(
        "--tmdb-api-key",
        default=os.environ.get("TMDB_API_KEY", ""),
        help="Optional TMDB API key for search result posters.",
    )
    parser.add_argument(
        "--media-paths",
        default=os.environ.get("JELLYFIN_MEDIA_PATHS", ",".join(DEFAULT_MEDIA_LIBRARY_PATHS)),
        help="Comma-separated Jellyfin/rclone media roots used to mark search results available or missing.",
    )
    parser.add_argument(
        "--jellyfin-url",
        default=os.environ.get("JELLYFIN_URL", "http://127.0.0.1:8096"),
        help="Jellyfin server URL used for accurate library availability checks.",
    )
    parser.add_argument(
        "--jellyfin-api-key",
        default=os.environ.get("JELLYFIN_API_KEY", ""),
        help="Jellyfin API key. If omitted, the app tries the local Jellyfin ApiKeys database.",
    )
    args = parser.parse_args()
    migrate_verified_onboarding_adapters()
    ACCESS_TOKEN = args.token.strip()
    ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()
    configure_analyzer_domains(os.environ.get("WORKFLOW_ANALYZER_DOMAINS", ""))
    TMDB_API_KEY = args.tmdb_api_key.strip()
    JELLYFIN_URL = args.jellyfin_url.strip().rstrip("/")
    JELLYFIN_API_KEY = args.jellyfin_api_key.strip() or jellyfin_api_key_from_db()
    MEDIA_LIBRARY_PATHS = [
        path.strip()
        for path in args.media_paths.split(",")
        if path.strip() and Path(path.strip()).expanduser().exists()
    ]
    LIBRARY = LibraryService(TMDB_API_KEY)
    apply_persisted_configuration()
    def auto_sync_loop() -> None:
        global AUTO_SYNC_LAST_RUN
        while True:
            try:
                interval = int((LIBRARY.settings().get("auto_sync_minutes") if LIBRARY else 0) or 0)
                if interval and time.time() - AUTO_SYNC_LAST_RUN >= interval * 60:
                    if LIBRARY is not None:
                        LIBRARY.start_scan("quick")
                        trigger_jellyfin_library_refresh()
                        AUTO_SYNC_LAST_RUN = time.time()
            except RuntimeError:
                # An admin-started scan is already running; try again on the
                # next interval without creating a second concurrent scan.
                pass
            except Exception as exc:
                print(f"Scheduled library sync failed: {exc}")
            time.sleep(30)
    threading.Thread(target=auto_sync_loop, daemon=True).start()
    httpd = ThreadingHTTPServer((args.host, args.port), AppHandler)
    print(f"Serving on http://{args.host}:{args.port}")
    if MEDIA_LIBRARY_PATHS:
        print("Media library paths: " + ", ".join(MEDIA_LIBRARY_PATHS))
    if JELLYFIN_URL and JELLYFIN_API_KEY:
        print(f"Jellyfin API enabled: {JELLYFIN_URL}")
    if os.environ.get("LIBRARY_AUTO_SCAN", "false").lower() in {"1", "true", "yes"}:
        try:
            LIBRARY.start_scan("quick")
            print("Started scheduled startup library scan")
        except RuntimeError:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
