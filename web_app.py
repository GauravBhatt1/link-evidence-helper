#!/usr/bin/env python3
"""
Small web UI for report-link evidence collection.

The app reuses the CLI finder logic and serves a dependency-free single page UI.
It inspects pages and redirect headers only; it does not download file bodies.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from movie_report_finder import Candidate, build_evidence, search_movie


DEFAULT_QUALITIES = ("480p", "720p", "1080p", "2160p", "4k")
DIRECT_HOST_MARKERS = (
    "video-downloads.googleusercontent.com",
    "instant.busycdn.xyz",
)
ACCESS_TOKEN = ""


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Evidence Link Helper</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101418;
      --panel: #171d22;
      --panel-2: #20272e;
      --line: #34404a;
      --text: #edf2f5;
      --muted: #aab6bf;
      --accent: #4fc3a1;
      --accent-2: #f4b84a;
      --danger: #ff6b6b;
      --shadow: rgba(0, 0, 0, 0.28);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font: 15px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    button, input, select {
      font: inherit;
    }

    .shell {
      width: min(1120px, calc(100% - 28px));
      margin: 0 auto;
      padding: 22px 0 36px;
    }

    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
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
      border: 1px solid var(--line);
      background: var(--panel-2);
      border-radius: 8px;
      color: var(--accent);
      box-shadow: 0 8px 24px var(--shadow);
    }

    .brand h1 {
      margin: 0;
      font-size: clamp(20px, 3vw, 28px);
      line-height: 1.1;
      letter-spacing: 0;
    }

    .brand p {
      margin: 4px 0 0;
      color: var(--muted);
      font-size: 13px;
    }

    .status {
      min-height: 28px;
      padding: 5px 10px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      background: var(--panel);
      white-space: nowrap;
    }

    .layout {
      display: grid;
      grid-template-columns: minmax(0, 0.94fr) minmax(340px, 1.06fr);
      gap: 16px;
      align-items: start;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 12px 30px var(--shadow);
      overflow: hidden;
    }

    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      background: #141a1f;
    }

    .panel-head h2 {
      margin: 0;
      font-size: 15px;
      letter-spacing: 0;
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
      border-radius: 8px;
      background: #0f1418;
      color: var(--text);
      padding: 9px 11px;
      outline: none;
    }

    input:focus, select:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(79, 195, 161, 0.14);
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
      border-radius: 8px;
      background: var(--panel-2);
      color: var(--text);
      cursor: pointer;
    }

    .quality.active {
      border-color: var(--accent);
      background: rgba(79, 195, 161, 0.16);
      color: #dffbf2;
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
      border-radius: 8px;
      padding: 9px 13px;
      color: #07110e;
      background: var(--accent);
      font-weight: 700;
      cursor: pointer;
    }

    .btn.secondary {
      background: var(--panel-2);
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
      gap: 8px;
      max-height: 430px;
      overflow: auto;
    }

    .candidate {
      width: 100%;
      text-align: left;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #11171c;
      color: var(--text);
      padding: 11px;
      cursor: pointer;
    }

    .candidate.active {
      border-color: var(--accent-2);
      background: rgba(244, 184, 74, 0.12);
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

    .link-list {
      display: grid;
      gap: 10px;
    }

    .link-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #11171c;
      padding: 12px;
    }

    .link-meta {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 13px;
    }

    .pill {
      color: #07110e;
      background: var(--accent-2);
      border-radius: 999px;
      padding: 2px 8px;
      font-weight: 700;
    }

    .url-box {
      width: 100%;
      min-height: 86px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #0b1014;
      color: var(--text);
      padding: 10px;
      overflow-wrap: anywhere;
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
      .topbar { align-items: flex-start; flex-direction: column; }
      .status { white-space: normal; }
    }

    @media (max-width: 520px) {
      .shell { width: min(100% - 18px, 1120px); padding-top: 14px; }
      .row { grid-template-columns: 1fr; }
      .quality-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .btn { width: 100%; }
      .actions { gap: 8px; }
    }
  </style>
</head>
<body>
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
          <h1>Evidence Link Helper</h1>
          <p>Quality, size, source, final link</p>
        </div>
      </div>
      <div class="status" id="status">Ready</div>
    </div>

    <section class="layout">
      <div class="panel">
        <div class="panel-head">
          <h2>Search</h2>
        </div>
        <div class="panel-body">
          <div class="row">
            <div class="field">
              <label for="query">Name</label>
              <input id="query" autocomplete="off" placeholder="Type name">
            </div>
            <button class="btn" id="searchBtn">Search</button>
          </div>

          <div class="field">
            <label>Quality</label>
            <div class="quality-grid" id="qualityGrid">
              <button class="quality" data-quality="480p">480p</button>
              <button class="quality" data-quality="720p">720p</button>
              <button class="quality active" data-quality="1080p">1080p</button>
              <button class="quality" data-quality="2160p">4K</button>
              <button class="quality" data-quality="all">All</button>
            </div>
          </div>

          <div class="actions">
            <button class="btn" id="findBtn" disabled>Find Link</button>
            <button class="btn secondary" id="clearBtn">Clear</button>
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h2>Results</h2>
          <span id="selectedQuality">1080p</span>
        </div>
        <div class="panel-body">
          <div class="results" id="candidates">
            <div class="empty">Search results will appear here.</div>
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h2>Final Links</h2>
        </div>
        <div class="panel-body">
          <div class="link-list" id="links">
            <div class="empty">Choose a result and find a link.</div>
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h2>Debug</h2>
        </div>
        <div class="panel-body">
          <div class="link-list" id="debug">
            <div class="empty">No debug rows yet.</div>
          </div>
        </div>
      </div>
    </section>
  </main>

  <script>
    const state = {
      quality: "1080p",
      candidates: [],
      selected: -1,
      busy: false,
    };

    const $ = (id) => document.getElementById(id);
    const statusEl = $("status");
    const queryEl = $("query");
    const candidatesEl = $("candidates");
    const linksEl = $("links");
    const debugEl = $("debug");
    const findBtn = $("findBtn");
    const searchBtn = $("searchBtn");

    function setStatus(text, isError = false) {
      statusEl.textContent = text;
      statusEl.classList.toggle("error", isError);
    }

    function setBusy(value) {
      state.busy = value;
      searchBtn.disabled = value;
      findBtn.disabled = value || state.selected < 0;
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

    const accessToken = new URLSearchParams(location.search).get("token") || localStorage.getItem("accessToken") || "";
    if (accessToken) localStorage.setItem("accessToken", accessToken);

    async function api(path, options = {}) {
      const response = await fetch(path, {
        ...options,
        headers: {
          "content-type": "application/json",
          "x-app-token": accessToken,
          ...(options.headers || {}),
        },
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok || body.ok === false) {
        throw new Error(body.error || `Request failed: ${response.status}`);
      }
      return body;
    }

    function renderCandidates() {
      findBtn.disabled = state.busy || state.selected < 0;
      if (!state.candidates.length) {
        candidatesEl.innerHTML = '<div class="empty">No search results.</div>';
        return;
      }
      candidatesEl.innerHTML = state.candidates.map((candidate, index) => `
        <button class="candidate ${index === state.selected ? "active" : ""}" data-index="${index}">
          <strong>${index + 1}. ${escapeHtml(candidate.title)}</strong>
          <span>${escapeHtml(candidate.url)}</span>
        </button>
      `).join("");
      candidatesEl.querySelectorAll(".candidate").forEach((button) => {
        button.addEventListener("click", () => {
          state.selected = Number(button.dataset.index);
          renderCandidates();
        });
      });
    }

    function renderLinks(items) {
      if (!items.length) {
        linksEl.innerHTML = '<div class="empty">Direct final link not found.</div>';
        return;
      }
      linksEl.innerHTML = items.map((item, index) => `
        <div class="link-card">
          <div class="link-meta">
            <span><span class="pill">${escapeHtml(item.quality)}</span> ${escapeHtml(item.size)}</span>
            <button class="btn secondary copy" data-url="${escapeHtml(item.url)}">Copy</button>
          </div>
          <textarea class="url-box" readonly>${escapeHtml(item.url)}</textarea>
        </div>
      `).join("");
      linksEl.querySelectorAll(".copy").forEach((button) => {
        button.addEventListener("click", async () => {
          await navigator.clipboard.writeText(button.dataset.url || "");
          setStatus("Copied");
        });
      });
    }

    function renderDebug(rows) {
      if (!rows.length) {
        debugEl.innerHTML = '<div class="empty">No debug rows.</div>';
        return;
      }
      debugEl.innerHTML = rows.slice(0, 8).map((row, index) => `
        <div class="link-card">
          <div class="link-meta">
            <span>${index + 1}. ${escapeHtml(row.status)}</span>
            <span>${escapeHtml(row.content_length || "")}</span>
          </div>
          <div class="candidate">
            <strong>${escapeHtml(row.section || row.title)}</strong>
            <span>${escapeHtml(row.listing_link || row.source_page)}</span>
          </div>
        </div>
      `).join("");
    }

    async function search() {
      const query = queryEl.value.trim();
      if (!query) {
        setStatus("Name required", true);
        return;
      }
      setBusy(true);
      setStatus("Searching...");
      linksEl.innerHTML = '<div class="empty">Choose a result and find a link.</div>';
      debugEl.innerHTML = '<div class="empty">No debug rows yet.</div>';
      try {
        const body = await api(`/api/search?q=${encodeURIComponent(query)}`);
        state.candidates = body.candidates || [];
        state.selected = state.candidates.length ? 0 : -1;
        renderCandidates();
        setStatus(state.candidates.length ? `${state.candidates.length} results` : "No results");
      } catch (error) {
        state.candidates = [];
        state.selected = -1;
        renderCandidates();
        setStatus(error.message, true);
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
      linksEl.innerHTML = '<div class="empty">Scanning...</div>';
      try {
        const body = await api("/api/find", {
          method: "POST",
          body: JSON.stringify({ query, candidate, quality: state.quality }),
        });
        renderLinks(body.links || []);
        renderDebug(body.debug || []);
        setStatus((body.links || []).length ? "Done" : "No final link");
      } catch (error) {
        renderLinks([]);
        setStatus(error.message, true);
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
      });
    });

    searchBtn.addEventListener("click", search);
    findBtn.addEventListener("click", findLink);
    $("clearBtn").addEventListener("click", () => {
      queryEl.value = "";
      state.candidates = [];
      state.selected = -1;
      renderCandidates();
      renderLinks([]);
      renderDebug([]);
      setStatus("Ready");
    });
    queryEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter") search();
    });
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


def find_best_link(row: Any) -> str:
    for url in (row.final_inner_url, row.final_wrapper, row.instant_link):
        if any(marker in url for marker in DIRECT_HOST_MARKERS):
            return url
    return ""


def response(handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: Any) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json; charset=utf-8")
    handler.send_header("content-length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def is_authorized(handler: BaseHTTPRequestHandler, parsed: Any | None = None) -> bool:
    if not ACCESS_TOKEN:
        return True
    query_token = ""
    if parsed is not None:
        query_token = parse_qs(parsed.query).get("token", [""])[0]
    header_token = handler.headers.get("x-app-token", "")
    return ACCESS_TOKEN in {query_token, header_token}


class AppHandler(BaseHTTPRequestHandler):
    server_version = "EvidenceLinkWeb/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            if not is_authorized(self, parsed):
                response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Token required"})
                return
            raw = HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if parsed.path == "/health":
            response(self, HTTPStatus.OK, {"ok": True})
            return
        if parsed.path == "/api/search":
            if not is_authorized(self, parsed):
                response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Token required"})
                return
            query = parse_qs(parsed.query).get("q", [""])[0].strip()
            if not query:
                response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Name required"})
                return
            try:
                candidates = search_movie(query, limit=10, timeout=20, max_html_bytes=2_000_000)
            except Exception as exc:
                response(self, HTTPStatus.BAD_GATEWAY, {"ok": False, "error": str(exc)})
                return
            response(self, HTTPStatus.OK, {"ok": True, "candidates": [asdict(candidate) for candidate in candidates]})
            return
        response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/find":
            response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return
        if not is_authorized(self):
            response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "Token required"})
            return
        length = int(self.headers.get("content-length") or "0")
        if length > 16_384:
            response(self, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "Request too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON"})
            return

        query = str(payload.get("query") or "").strip()
        quality = str(payload.get("quality") or "1080p").strip()
        candidate_payload = payload.get("candidate") or {}
        title = str(candidate_payload.get("title") or "").strip()
        url = str(candidate_payload.get("url") or "").strip()
        if not query or not title or not url.startswith("http"):
            response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Missing selected result"})
            return

        qualities = DEFAULT_QUALITIES if quality.lower() in {"all", "*"} else (quality,)
        links: list[dict[str, str]] = []
        debug: list[dict[str, str]] = []
        candidate = Candidate(title=title, url=url)
        try:
            for item_quality in qualities:
                rows = build_evidence(
                    query=query,
                    candidate=candidate,
                    quality=item_quality,
                    timeout=20,
                    max_hops=10,
                    max_html_bytes=2_000_000,
                    stop_after_direct=True,
                )
                debug.extend(asdict(row) for row in rows)
                for row in rows:
                    best = find_best_link(row)
                    if best:
                        links.append(
                            {
                                "quality": item_quality,
                                "size": human_size(row.content_length),
                                "url": best,
                                "source": row.source_page,
                                "listing": row.listing_link,
                            }
                        )
                        break
        except Exception as exc:
            response(self, HTTPStatus.BAD_GATEWAY, {"ok": False, "error": str(exc)})
            return
        response(self, HTTPStatus.OK, {"ok": True, "links": links, "debug": debug})


def main() -> int:
    global ACCESS_TOKEN
    parser = argparse.ArgumentParser(description="Run the Evidence Link Helper web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default="", help="Optional access token for public hosting.")
    args = parser.parse_args()
    ACCESS_TOKEN = args.token.strip()
    httpd = ThreadingHTTPServer((args.host, args.port), AppHandler)
    print(f"Serving on http://{args.host}:{args.port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
