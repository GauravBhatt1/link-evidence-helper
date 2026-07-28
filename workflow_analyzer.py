"""Bounded, verification-only workflow analysis for configured movie sources.

The module follows public, server-visible navigation only.  It never executes
JavaScript, submits POST forms, solves challenges, or treats an HTML page as a
file.  Each request is logged so callers can show exactly why a branch moved
forward or stopped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from network_safety import REDIRECT_CODES, SafeSession, redact_url, validate_public_url

QUALITY_RE = re.compile(r"\b(?:2160|1440|1080|720|480)p\b|\b4k\b", re.I)
DOWNLOAD_RE = re.compile(r"\b(?:download|direct|instant|mirror|server|drive|get\s*(?:link|file)|continue)\b", re.I)
BLOCKER_RE = re.compile(r"cloudflare\s*turnstile|cf-turnstile|g-recaptcha|hcaptcha|captcha|verify\s+(?:that\s+)?you(?:'re| are)\s+human", re.I)
LOGIN_RE = re.compile(r"\b(?:sign in|log in|login required|authentication required)\b", re.I)
FILE_RE = re.compile(r"\.(?:mkv|mp4|webm|avi|zip)(?:$|[?#])", re.I)
FINAL_TYPES = ("video/", "audio/", "application/octet-stream", "application/zip")
MAX_QUALITY_LINKS, MAX_ACTIONS_PER_PAGE, MAX_DEPTH = 12, 18, 8
STATIC_LOCATION_RE = re.compile(r"(?:window\.)?(?:location(?:\.href)?\s*=|location\.(?:assign|replace)\s*\()\s*['\"]([^'\"]+)['\"]", re.I)
META_REFRESH_RE = re.compile(r"\s*\d+(?:\.\d+)?\s*;\s*url\s*=\s*(.+)\s*", re.I)


@dataclass(frozen=True)
class Action:
    url: str
    label: str
    source_page: str
    reason: str
    quality: str = ""
    method: str = "GET"


class WorkflowParser(HTMLParser):
    """Extract visible and static navigation without executing page scripts."""
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url, self.actions, self._heading = base_url, [], ""
        self._open_actions: list[tuple[str, dict[str, str], list[str], str]] = []
        self._open_headings: list[list[str]] = []
        self._forms: list[dict[str, str]] = []
        self._script: list[str] | None = None

    def _add(self, target: str, label: str, reason: str, quality_text: str = "", method: str = "GET") -> None:
        if not target:
            return
        try:
            url = validate_public_url(urljoin(self.base_url, target.strip().strip("'\"")))
        except ValueError:
            return
        combined = " ".join((self._heading, label, url, quality_text))
        match = QUALITY_RE.search(combined)
        quality = match.group(0).upper() if match else ""
        self.actions.append(Action(url, label[:180] or "Unnamed action", self.base_url, reason, quality, method.upper()))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._open_headings.append([])
        if tag in {"a", "button"}:
            self._open_actions.append((tag, attr, [], self._heading))
        if tag == "form":
            self._forms.append(attr)
        if tag == "meta" and attr.get("http-equiv", "").lower() == "refresh":
            match = META_REFRESH_RE.fullmatch(attr.get("content", ""))
            if match:
                self._add(match.group(1), "Meta refresh", "static meta refresh navigation")
        if tag == "script":
            self._script = []

    def handle_data(self, data: str) -> None:
        for _, _, chunks, _ in self._open_actions:
            chunks.append(data)
        for chunks in self._open_headings:
            chunks.append(data)
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self._open_headings:
            self._heading = " ".join("".join(self._open_headings.pop()).split())[:220]
        if self._open_actions and self._open_actions[-1][0] == tag:
            _, attrs, chunks, heading = self._open_actions.pop()
            label = " ".join("".join(chunks).split()) or attrs.get("aria-label") or attrs.get("title") or "Unnamed action"
            target = attrs.get("href") or attrs.get("data-url") or attrs.get("data-href") or ""
            if target and not target.lower().startswith(("javascript:", "#")):
                self._add(target, label, "visible link or button", heading + " " + attrs.get("class", ""))
            onclick = attrs.get("onclick", "")
            match = STATIC_LOCATION_RE.search(onclick)
            if match:
                self._add(match.group(1), label, "static onclick navigation", heading)
        if tag == "form" and self._forms:
            form = self._forms.pop()
            method = form.get("method", "GET").upper()
            self._add(form.get("action", self.base_url), f"Form ({method})", "HTML form action detected", self._heading, method)
        if tag == "script" and self._script is not None:
            script = "".join(self._script)
            for match in STATIC_LOCATION_RE.finditer(script):
                self._add(match.group(1), "JavaScript redirect", "static JavaScript redirect")
            self._script = None


def _parse(url: str, body: str) -> list[Action]:
    parser = WorkflowParser(url)
    parser.feed(body)
    return parser.actions


def _is_download(action: Action) -> bool:
    return bool(DOWNLOAD_RE.search(" ".join((action.label, action.url, action.reason))))


def _pick(actions: list[Action], predicate: Any, maximum: int) -> list[Action]:
    seen: set[tuple[str, str]] = set()
    chosen: list[Action] = []
    for action in actions:
        key = (action.url, action.method)
        if key not in seen and predicate(action):
            seen.add(key)
            chosen.append(action)
            if len(chosen) >= maximum:
                break
    return chosen


def _log(log: list[dict[str, Any]], response: Any, reason: str, action: Action | None, next_step: str, extracted: str = "") -> dict[str, Any]:
    row = {
        "request_url": response.url,
        "http_status": response.status,
        "redirect_location": response.location or "",
        "content_type": response.content_type or "",
        "link_button_label": action.label if action else "",
        "source_page": action.source_page if action else "",
        "reason_followed": reason,
        "extracted_action": extracted,
        "next_step": next_step,
    }
    log.append(row)
    return row


def _final_file(response: Any, source_url: str) -> bool:
    return bool(response.status and 200 <= response.status < 300 and (response.content_type.lower().startswith(FINAL_TYPES) or FILE_RE.search(source_url)))


def _blocked_html(body: str) -> tuple[str, str] | None:
    lowered = body.lower()
    if "turnstile" in lowered or "cf-turnstile" in lowered:
        return "cloudflare_turnstile", "Workflow stopped at Cloudflare Turnstile verification."
    if BLOCKER_RE.search(body):
        return "captcha_required", "Workflow stopped at CAPTCHA verification."
    if LOGIN_RE.search(body):
        return "login_required", "Workflow stopped at login verification."
    return None


def _workflow_steps(movie_ok: bool, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps = [{"label": "Movie page", "state": "passed" if movie_ok else "failed"}]
    if not movie_ok:
        return steps
    steps.append({"label": "Download / quality page", "state": "passed" if results else "failed"})
    if any(item["status"] == "success" for item in results):
        return steps + [{"label": "Final file", "state": "passed"}]
    if any(item["status"] == "blocked" for item in results):
        return steps + [{"label": "Cloudflare blocked", "state": "failed"}]
    return steps + [{"label": "Final file", "state": "failed"}]


def analyze_movie_workflow(site_url: str, movie_url: str, selected_quality: str | None = None) -> dict[str, Any]:
    """Follow every verified quality branch to a file or an interactive block.

    GET navigation, HTTP redirects, static meta refreshes, and static
    JavaScript/onclick redirects are followed within strict depth/size limits.
    POST forms are reported but never submitted.
    """
    site_url, movie_url = validate_public_url(site_url), validate_public_url(movie_url)
    session, log, results = SafeSession(timeout=15), [], []
    _, site, _ = session.fetch_html_once(site_url)
    _log(log, site, "configured source website", None, "Movie page requested")
    movie_html, movie, _ = session.fetch_html_once(movie_url, referer=site_url)
    _log(log, movie, "verified selected search result", None, "Download sections extracted")
    if not movie_html or not movie.status or movie.status >= 400:
        return {"status": "failed", "site": site_url, "movie_url": movie_url, "results": [], "execution_log": log, "workflow_steps": _workflow_steps(False, []), "message": "Movie page could not be fetched as HTML."}
    blocked = _blocked_html(movie_html)
    if blocked:
        return {"status": "blocked", "site": site_url, "movie_url": movie_url, "results": [{"quality": "", "source": "Movie page", "page_url": movie_url, "final_url": None, "is_final_file": False, "status": "blocked", "blocked_by": blocked[0], "message": blocked[1]}], "execution_log": log, "workflow_steps": _workflow_steps(True, [{"status": "blocked"}]), "message": blocked[1]}

    movie_actions = _parse(movie_url, movie_html)
    quality_actions = _pick(movie_actions, lambda action: bool(action.quality) and (not selected_quality or selected_quality.lower() in {"all", "*"} or action.quality.lower() == selected_quality.lower()), MAX_QUALITY_LINKS)
    if not quality_actions:
        quality_actions = _pick(movie_actions, _is_download, MAX_QUALITY_LINKS)

    for quality_action in quality_actions:
        quality = quality_action.quality or "Unknown quality"
        queue: list[tuple[Action, int]] = [(quality_action, 0)]
        visited: set[tuple[str, str]] = set()
        terminal = False
        while queue and len(visited) < MAX_ACTIONS_PER_PAGE and not terminal:
            action, depth = queue.pop(0)
            key = (action.url, action.method)
            if key in visited:
                continue
            visited.add(key)
            if action.method != "GET":
                fake = type("FormResponse", (), {"url": redact_url(action.url), "status": None, "location": None, "content_type": "", "headers": {}})()
                _log(log, fake, action.reason, action, "Stopped at form", "POST form detected; not submitted")
                results.append({"quality": quality, "source": action.label, "page_url": action.url, "final_url": None, "is_final_file": False, "status": "partial", "blocked_by": "", "message": "POST form detected; it was not submitted automatically."})
                continue
            html, response, next_url = session.fetch_html_once(action.url, referer=action.source_page)
            row = _log(log, response, action.reason, action, "Page inspected")
            base = {"quality": quality, "source": action.label, "page_url": action.url, "final_url": None, "is_final_file": False, "status": "partial", "blocked_by": "", "message": "No verified final file response."}
            if _final_file(response, action.url):
                row["extracted_action"], row["next_step"] = "Actual downloadable response reached", "Final file URL reached"
                base.update({"final_url": action.url, "is_final_file": True, "status": "success", "message": "Verified final file response."})
                results.append(base); terminal = True; continue
            if response.status in REDIRECT_CODES and next_url:
                row["extracted_action"], row["next_step"] = f"HTTP redirect to {redact_url(next_url)}", "Redirect destination requested"
                queue.append((Action(next_url, "HTTP redirect", action.url, "HTTP Location header", quality), depth + 1))
                continue
            if getattr(response, "error", "") or not html:
                row["extracted_action"], row["next_step"] = "No readable HTML response", "Stopped at response"
                base["message"] = "Workflow stopped before a final file response."
                results.append(base); continue
            blocker = _blocked_html(html)
            if blocker:
                row["extracted_action"], row["next_step"] = "Interactive verification detected", "BLOCKED — Final file URL not reached"
                base.update({"status": "blocked", "blocked_by": blocker[0], "message": blocker[1]})
                results.append(base); continue
            if depth >= MAX_DEPTH:
                row["extracted_action"], row["next_step"] = "Traversal limit reached", "Stopped at bounded depth"
                base["message"] = "Workflow depth limit reached before a final file response."
                results.append(base); continue
            next_actions = _pick(_parse(action.url, html), lambda item: item.method != "GET" or _is_download(item) or bool(item.quality) or "static" in item.reason, MAX_ACTIONS_PER_PAGE)
            if not next_actions:
                row["extracted_action"], row["next_step"] = "No verified next download action", "Stopped at HTML landing page"
                results.append(base); continue
            row["extracted_action"], row["next_step"] = f"{len(next_actions)} verified navigation action(s) extracted", "Next action requested"
            queue.extend((next_action, depth + 1) for next_action in next_actions)
        if not terminal and not any(item["quality"] == quality for item in results):
            results.append({"quality": quality, "source": quality_action.label, "page_url": quality_action.url, "final_url": None, "is_final_file": False, "status": "partial", "blocked_by": "", "message": "No verified final file response."})

    if not results:
        message, status = "No quality-specific or download action was verified.", "partial"
    elif any(item["status"] == "success" for item in results):
        message, status = "Verified final file response found.", "success"
    elif any(item["status"] == "blocked" for item in results):
        blocked = next(item for item in results if item["status"] == "blocked")
        message, status = blocked["message"], "blocked"
    else:
        message, status = "Final file URL not reached.", "partial"
    return {"status": status, "site": site_url, "movie_url": movie_url, "results": results, "execution_log": log, "workflow_steps": _workflow_steps(True, results), "message": message}
