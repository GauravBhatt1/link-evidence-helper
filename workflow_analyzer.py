"""Internal, bounded workflow engine used by the production Find Links path.

There is deliberately no HTTP handler or UI in this module.  It is callable
only from the main application and uses the shared SSRF-safe HTTP session.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from network_safety import REDIRECT_CODES, SafeSession, redact_url, validate_public_url

QUALITY_RE = re.compile(r"\b(?:2160|1440|1080|720|480)p\b|\b4k\b", re.I)
DOWNLOAD_RE = re.compile(r"\b(?:download|direct|instant|mirror|server|drive|get\s*(?:link|file))\b", re.I)
BLOCKER_RE = re.compile(r"cloudflare\s*turnstile|cf-turnstile|g-recaptcha|hcaptcha|captcha|verify\s+(?:that\s+)?you(?:'re| are)\s+human", re.I)
LOGIN_RE = re.compile(r"\b(?:sign in|log in|login required|authentication required)\b", re.I)
FILE_RE = re.compile(r"\.(?:mkv|mp4|webm|avi|zip)(?:$|[?#])", re.I)
FINAL_TYPES = ("video/", "audio/", "application/octet-stream", "application/zip")
MAX_QUALITY_LINKS, MAX_BRANCHES_PER_QUALITY = 6, 4


@dataclass(frozen=True)
class Action:
    url: str
    label: str
    source_page: str
    reason: str
    quality: str = ""


class WorkflowParser(HTMLParser):
    """Extract labeled public actions while preserving nearby heading quality."""
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url, self.actions, self._heading = base_url, [], ""
        self._open_actions: list[tuple[str, dict[str, str], list[str], str]] = []
        self._open_headings: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}: self._open_headings.append([])
        if tag in {"a", "button"}: self._open_actions.append((tag, attr, [], self._heading))

    def handle_data(self, data: str) -> None:
        for _, _, chunks, _ in self._open_actions: chunks.append(data)
        for chunks in self._open_headings: chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self._open_headings:
            self._heading = " ".join("".join(self._open_headings.pop()).split())[:220]
        if self._open_actions and self._open_actions[-1][0] == tag:
            _, attrs, chunks, heading = self._open_actions.pop()
            target = attrs.get("href") or attrs.get("data-url") or attrs.get("data-href") or ""
            if not target or target.lower().startswith(("javascript:", "#")): return
            try: url = validate_public_url(urljoin(self.base_url, target))
            except ValueError: return
            label = " ".join("".join(chunks).split()) or attrs.get("aria-label") or attrs.get("title") or "Unnamed action"
            combined = " ".join((heading, label, url, attrs.get("class", ""), attrs.get("id", "")))
            quality = QUALITY_RE.search(combined)
            reason = f"quality marker {quality.group(0).upper()} found in heading, label, or URL" if quality else ("download-related visible action" if DOWNLOAD_RE.search(label) else "visible public action")
            self.actions.append(Action(url, label[:180], self.base_url, reason, quality.group(0).upper() if quality else ""))


def _parse(url: str, body: str) -> WorkflowParser:
    parser = WorkflowParser(url); parser.feed(body); return parser


def _is_download(action: Action) -> bool:
    """Do not mistake category paths containing 'download' for a control."""
    return bool(DOWNLOAD_RE.search(action.label))


def _pick(actions: list[Action], predicate: Any, maximum: int) -> list[Action]:
    seen: set[str] = set(); chosen: list[Action] = []
    for action in actions:
        if action.url not in seen and predicate(action):
            seen.add(action.url); chosen.append(action)
            if len(chosen) >= maximum: break
    return chosen


def _log(log: list[dict[str, Any]], response: Any, reason: str, action: Action | None, next_step: str) -> dict[str, Any]:
    row = {"request_url": response.url, "http_status": response.status, "redirect_location": response.location or "", "content_type": response.content_type, "link_button_label": action.label if action else "", "source_page": action.source_page if action else "", "reason_followed": reason, "extracted_action": "", "next_step": next_step}
    log.append(row); return row


def _final_file(response: Any, source_url: str) -> bool:
    return bool(response.status and 200 <= response.status < 300 and (response.content_type.lower().startswith(FINAL_TYPES) or FILE_RE.search(source_url)))


def analyze_movie_workflow(site_url: str, movie_url: str, selected_quality: str | None = None) -> dict[str, Any]:
    """Inspect configured public workflow branches and return normalized results.

    CAPTCHA/login pages are detection-only terminal states.  No forms are
    submitted and no final URL is invented from an HTML landing page.
    """
    site_url, movie_url = validate_public_url(site_url), validate_public_url(movie_url)
    session, log, results = SafeSession(timeout=15), [], []
    _, site, _ = session.fetch_html_once(site_url)
    _log(log, site, "configured source website", None, "Movie page requested")
    movie_html, movie, _ = session.fetch_html_once(movie_url, referer=site_url)
    _log(log, movie, "selected result page", None, "Quality actions extracted")
    if not movie_html or not movie.status or movie.status >= 400:
        return {"status": "failed", "site": site_url, "movie_url": movie_url, "results": [], "execution_log": log, "message": "Movie page could not be fetched as HTML."}
    movie_actions = _pick(_parse(movie_url, movie_html).actions, lambda action: bool(action.quality) and _is_download(action) and (not selected_quality or selected_quality.lower() in {"all", "*"} or action.quality.lower() == selected_quality.lower()), MAX_QUALITY_LINKS)
    for quality_action in movie_actions:
        landing_html, landing, next_url = session.fetch_html_once(quality_action.url, referer=movie_url)
        quality_log = _log(log, landing, quality_action.reason, quality_action, "Landing page inspected")
        if landing.status in REDIRECT_CODES and next_url:
            quality_log["extracted_action"], quality_log["next_step"] = f"HTTP redirect to {redact_url(next_url)}", "Redirect depth limit reached"
            results.append({"quality": quality_action.quality, "source": quality_action.label, "page_url": quality_action.url, "final_url": None, "is_final_file": False, "status": "partial", "blocked_by": "", "message": "Verified redirect; destination was not treated as a final file."})
            continue
        for branch in _pick(_parse(quality_action.url, landing_html).actions, _is_download, MAX_BRANCHES_PER_QUALITY):
            branch_html, branch_response, branch_next = session.fetch_html_once(branch.url, referer=quality_action.url)
            branch_log = _log(log, branch_response, branch.reason, branch, "Landing page inspected")
            base = {"quality": quality_action.quality, "source": branch.label, "page_url": branch.url, "final_url": None, "is_final_file": False, "status": "partial", "blocked_by": "", "message": "No verified final file response."}
            if branch_response.status in REDIRECT_CODES and branch_next:
                branch_log["extracted_action"], branch_log["next_step"] = f"HTTP redirect to {redact_url(branch_next)}", "Redirect depth limit reached"; base["message"] = "Verified redirect; final file not reached."
            elif BLOCKER_RE.search(branch_html):
                branch_log["extracted_action"], branch_log["next_step"] = "Cloudflare Turnstile/CAPTCHA detected", "BLOCKED — Final file URL not reached"; base.update({"status": "blocked", "blocked_by": "cloudflare_turnstile", "message": "Workflow stopped at Cloudflare Turnstile verification."})
            elif LOGIN_RE.search(branch_html):
                branch_log["extracted_action"], branch_log["next_step"] = "Login requirement detected", "BLOCKED — Final file URL not reached"; base.update({"status": "blocked", "blocked_by": "login_required", "message": "Workflow stopped at login verification."})
            elif _final_file(branch_response, branch.url):
                branch_log["extracted_action"], branch_log["next_step"] = "Actual downloadable response reached", "Final file URL reached"; base.update({"final_url": branch.url, "is_final_file": True, "status": "success", "message": "Verified final file response."})
            else:
                branch_log["extracted_action"], branch_log["next_step"] = "No verified next download action", "Stopped at HTML landing page"
            results.append(base)
    if not results:
        message, status = "No quality-specific visible download action was verified.", "partial"
    elif any(item["status"] == "success" for item in results): message, status = "Verified final file response found.", "success"
    elif any(item["status"] == "blocked" for item in results): message, status = "Workflow stopped at Cloudflare Turnstile verification.", "blocked"
    else: message, status = "Final file URL not reached.", "partial"
    return {"status": status, "site": site_url, "movie_url": movie_url, "results": results, "execution_log": log, "message": message}
