"""Bounded, verification-only workflow analysis for configured movie sources.

The module follows public, server-visible navigation only.  It never executes
JavaScript, submits POST forms, solves challenges, or treats an HTML page as a
file.  Each request is logged so callers can show exactly why a branch moved
forward or stopped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from types import SimpleNamespace
import time
from typing import Any
from urllib.parse import urljoin, urlparse

from network_safety import REDIRECT_CODES, SafeSession, redact_url, validate_public_url
from playwright_renderer import PlaywrightRenderer, RendererUnavailable

QUALITY_RE = re.compile(r"\b(?:2160|1440|1080|720|480)p\b|\b4k\b", re.I)
DOWNLOAD_RE = re.compile(r"\b(?:download|direct|instant|mirror|server|drive|gdf[l]?ix|vcloud|fast\s*cloud|zipdisk|cloud\s*resume|quick\s*download|get\s*(?:link|file)|continue)\b", re.I)
NAVIGATION_RE = re.compile(r"\b(?:home|menu|privacy|terms|contact|telegram|trailer|login|sign\s*in|download\s*tips|tips)\b", re.I)
HOST_PRIORITY = ((re.compile(r"\bdirect\s*download\b", re.I), 0), (re.compile(r"\bvcloud\b", re.I), 1), (re.compile(r"\bgdf[l]?ix\b", re.I), 2))
BLOCKER_RE = re.compile(r"cloudflare\s*turnstile|cf-turnstile|g-recaptcha|hcaptcha|captcha|verify\s+(?:that\s+)?you(?:'re| are)\s+human", re.I)
# "Sign in" frequently appears in a dormant header/login modal on otherwise
# public pages.  Only explicit access-denied wording is a workflow blocker.
LOGIN_RE = re.compile(r"\b(?:login required|authentication required)\b", re.I)
FILE_RE = re.compile(r"\.(?:mkv|mp4|webm|avi|zip)(?:$|[?#])", re.I)
FINAL_TYPES = ("video/", "audio/", "application/octet-stream", "application/zip")
# Find Links is a synchronous user request. Keep the graph useful but bounded
# well below common proxy/browser request limits; unfinished queued mirrors are
# reported as partial rather than causing the client connection to time out.
MAX_QUALITY_LINKS, MAX_ACTIONS_PER_PAGE, MAX_DEPTH, MAX_WORKFLOW_NODES = 12, 18, 8, 24
MAX_WORKFLOW_SECONDS = 42
STATIC_LOCATION_RE = re.compile(r"(?:window\.|document\.)?(?:location(?:\.href)?\s*=|location\.(?:assign|replace)\s*\(|open\s*\()\s*['\"]([^'\"]+)['\"]", re.I)
META_REFRESH_RE = re.compile(r"\s*\d+(?:\.\d+)?\s*;\s*url\s*=\s*(.+)\s*", re.I)
SIZE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(kb|mb|gb|tb)\b", re.I)


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
    seen: set[tuple[str, str, str]] = set()
    chosen: list[Action] = []
    for action in actions:
        key = (action.url, action.method, action.quality)
        if key not in seen and predicate(action):
            seen.add(key)
            chosen.append(action)
            if len(chosen) >= maximum:
                break
    return chosen


def _merge_actions(*groups: list[Action]) -> list[Action]:
    """Combine server and rendered DOM actions without re-visiting a target."""
    merged: list[Action] = []
    seen: set[tuple[str, str, str]] = set()
    for group in groups:
        for action in group:
            key = (action.url, action.method, action.quality)
            if key not in seen:
                seen.add(key)
                merged.append(action)
    return merged


def _branch_actions(actions: list[Action]) -> list[Action]:
    """Prefer download actions, but do not discard an unlabeled real branch."""
    direct = _pick(actions, lambda item: item.method != "GET" or _is_download(item) or bool(item.quality) or "static" in item.reason, MAX_ACTIONS_PER_PAGE)
    candidates = direct or _pick(actions, lambda item: item.method != "GET" or not NAVIGATION_RE.search(item.label), MAX_ACTIONS_PER_PAGE)
    def rank(item: Action) -> int:
        text = f"{item.label} {item.url}"
        return next((priority for pattern, priority in HOST_PRIORITY if pattern.search(text)), 3)
    return sorted(candidates, key=rank)


def _same_resource(left: str, right: str) -> bool:
    """Avoid rendered self-redirects (often http/https spelling changes)."""
    a, b = urlparse(left), urlparse(right)
    return (a.hostname or "").lower() == (b.hostname or "").lower() and a.path == b.path and a.query == b.query


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
        return "cloudflare_turnstile", "Manual verification required"
    if BLOCKER_RE.search(body):
        return "captcha_required", "Manual verification required"
    if LOGIN_RE.search(body):
        return "login_required", "Manual verification required"
    return None


def _needs_javascript_rendering(html: str, actions: list[Action] | None = None) -> bool:
    """Only invoke Chromium for a likely client-rendered page with no usable path."""
    lowered = html.lower()
    js_signals = (
        "enable javascript", "javascript is required", "__next_data__", "id=\"root\"",
        "id='root'", "fetch(", "xmlhttprequest", "axios.", "graphql",
    )
    return bool(any(signal in lowered for signal in js_signals) and (actions is None or not actions))


def _quality_sizes_from_movie_page(html: str) -> dict[str, str]:
    """Read an explicitly published File Size list without inspecting media bodies.

    Some sources publish one ordered size per common quality in the movie-page
    metadata.  Keeping this page-declared value with the workflow branch
    lets the second source show the same useful size label as the first.
    """
    text = re.sub(r"<[^>]+>", " ", unescape(html or ""))
    match = re.search(r"\bfile\s*size\s*:\s*(.{0,220})", text, re.I | re.S)
    if not match:
        return {}
    sizes = [f"{number} {unit.upper()}" for number, unit in SIZE_RE.findall(match.group(1))]
    qualities = ("480P", "720P", "1080P", "2160P")
    if len(sizes) < len(qualities):
        return {}
    return dict(zip(qualities, sizes))


def _workflow_steps(movie_ok: bool, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps = [{"label": "Movie page", "state": "passed" if movie_ok else "failed"}]
    if not movie_ok:
        return steps
    steps.append({"label": "Download / quality page", "state": "passed" if results else "failed"})
    if any(item["status"] == "success" for item in results):
        return steps + [{"label": "Final file", "state": "passed"}]
    if any(item["status"] == "blocked" for item in results):
        return steps + [{"label": "Manual verification required", "state": "failed"}]
    return steps + [{"label": "Final file", "state": "failed"}]


def analyze_movie_workflow(site_url: str, movie_url: str, selected_quality: str | None = None) -> dict[str, Any]:
    """Follow every verified quality branch to a file or an interactive block.

    GET navigation, HTTP redirects, static meta refreshes, and static
    JavaScript/onclick redirects are followed within strict depth/size limits.
    POST forms are reported but never submitted.
    """
    renderer: PlaywrightRenderer | None = None
    def get_renderer() -> PlaywrightRenderer:
        nonlocal renderer
        if renderer is None:
            # Graph traversal may inspect many landing pages. Keep each
            # navigation bounded while retaining the same browser context.
            renderer = PlaywrightRenderer().__enter__()
        return renderer
    try:
        return _analyze_movie_workflow(site_url, movie_url, selected_quality, get_renderer)
    finally:
        if renderer is not None:
            renderer.close()


def _analyze_movie_workflow(site_url: str, movie_url: str, selected_quality: str | None, get_renderer: Any) -> dict[str, Any]:
    site_url, movie_url = validate_public_url(site_url), validate_public_url(movie_url)
    deadline = time.monotonic() + MAX_WORKFLOW_SECONDS
    session, log, results = SafeSession(timeout=8), [], []
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
    quality_sizes = _quality_sizes_from_movie_page(movie_html)
    # The normal request establishes first-party behaviour and catches an
    # immediate challenge.  Every ordinary HTML workflow page is then also
    # rendered once so client-created buttons are part of the graph.
    if movie_html:
        try:
            rendered = get_renderer().render(movie_url)
        except RendererUnavailable as exc:
            rendered = None
            _log(log, SimpleNamespace(url=redact_url(movie_url), status=None, location=None, content_type="", headers={}), "JavaScript rendering fallback", None, "HTTP-only analysis continued", str(exc))
        if rendered and rendered.html:
            rendered_response = SimpleNamespace(url=rendered.url, status=rendered.status, location=None, content_type=rendered.content_type, headers={}, error=rendered.error)
            _log(log, rendered_response, "JavaScript rendering fallback", None, "Rendered movie page inspected", "Rendered page content used after normal HTTP had no usable actions")
            rendered_actions = _parse(rendered.navigation_url or movie_url, rendered.html)
            movie_html = rendered.html
            movie_url = rendered.navigation_url or movie_url
            blocked = _blocked_html(movie_html)
            if blocked:
                return {"status": "blocked", "site": site_url, "movie_url": movie_url, "results": [{"quality": "", "source": "Movie page", "page_url": movie_url, "final_url": None, "is_final_file": False, "status": "blocked", "blocked_by": blocked[0], "message": blocked[1]}], "execution_log": log, "workflow_steps": _workflow_steps(True, [{"status": "blocked"}]), "message": blocked[1]}
            movie_actions = _merge_actions(movie_actions, rendered_actions)
    # Headings can make unrelated navigation links inherit a quality label.
    # A root quality branch must be both quality-tagged and download-like.
    quality_actions = _pick(movie_actions, lambda action: bool(action.quality) and not NAVIGATION_RE.search(action.label) and (not selected_quality or selected_quality.lower() in {"all", "*"} or action.quality.lower() == selected_quality.lower()), MAX_QUALITY_LINKS)
    if not quality_actions:
        quality_actions = _pick(movie_actions, _is_download, MAX_QUALITY_LINKS)

    # A global queue makes this a bounded graph traversal: each download
    # branch is inspected once, rather than stopping after the first landing
    # page or the first quality that happens to be encountered.
    queue: list[tuple[Action, int, str]] = [(action, 0, action.quality or "Unknown quality") for action in quality_actions]
    visited: set[tuple[str, str]] = set()
    timed_out = False
    while queue and len(visited) < MAX_WORKFLOW_NODES:
        if time.monotonic() >= deadline:
            timed_out = True
            break
        action, depth, quality = queue.pop(0)
        key = (action.url, action.method)
        if key in visited:
            continue
        visited.add(key)
        if action.method != "GET":
            fake = type("FormResponse", (), {"url": redact_url(action.url), "status": None, "location": None, "content_type": "", "headers": {}})()
            _log(log, fake, action.reason, action, "Stopped at form", "Form detected; it was not submitted")
            results.append({"quality": quality, "source": action.label, "page_url": action.url, "final_url": None, "is_final_file": False, "status": "partial", "blocked_by": "", "message": "Form detected; it was not submitted automatically."})
            continue
        html, response, next_url = session.fetch_html_once(action.url, referer=action.source_page)
        row = _log(log, response, action.reason, action, "Page inspected")
        base = {"quality": quality, "size": quality_sizes.get(quality.upper(), ""), "source": action.label, "page_url": action.url, "final_url": None, "is_final_file": False, "status": "partial", "blocked_by": "", "message": "No verified final file response."}
        if time.monotonic() >= deadline:
            timed_out = True
            row["extracted_action"], row["next_step"] = "Workflow time limit reached", "Remaining branches not requested"
            base["message"] = "Workflow time limit reached before this branch could be rendered."
            results.append(base)
            break
        if _final_file(response, action.url):
            row["extracted_action"], row["next_step"] = "Actual downloadable response reached", "Final file URL reached"
            base.update({"final_url": action.url, "is_final_file": True, "status": "success", "message": "Verified final file response."})
            results.append(base); continue
        if response.status in REDIRECT_CODES and next_url:
            row["extracted_action"], row["next_step"] = f"HTTP redirect to {redact_url(next_url)}", "Redirect destination requested"
            queue.append((Action(next_url, "HTTP redirect", action.url, "HTTP Location header", quality), depth + 1, quality))
            continue
        blocker = _blocked_html(html) if html else None
        if blocker:
            row["extracted_action"], row["next_step"] = "Interactive verification detected", "BLOCKED — Final file URL not reached"
            base.update({"status": "blocked", "blocked_by": blocker[0], "message": blocker[1]})
            results.append(base); continue
        server_actions = _parse(action.url, html) if html else []
        try:
            rendered = get_renderer().render(action.url)
        except RendererUnavailable as exc:
            rendered = None
            row["extracted_action"], row["next_step"] = "JavaScript renderer unavailable", "Server-rendered actions inspected"
            base["message"] = f"Browser rendering unavailable: {exc}"
        if rendered:
            rendered_response = SimpleNamespace(url=rendered.url, status=rendered.status, location=None, content_type=rendered.content_type, headers={}, error=rendered.error)
            rendered_row = _log(log, rendered_response, "JavaScript-rendered page inspection", action, "Rendered page inspected", "Rendered DOM actions extracted")
            if rendered.error or not rendered.html:
                rendered_row["next_step"] = "Server-rendered actions inspected"
            else:
                blocker = _blocked_html(rendered.html)
                if blocker:
                    rendered_row["extracted_action"], rendered_row["next_step"] = "Interactive verification detected", "BLOCKED — Final file URL not reached"
                    base.update({"status": "blocked", "blocked_by": blocker[0], "message": blocker[1]})
                    results.append(base); continue
                page_base_url = rendered.navigation_url or action.url
                server_actions = _merge_actions(server_actions, _parse(page_base_url, rendered.html))
        # A small number of public hosts expose their verified file only after
        # a clearly labelled client-side "Direct/Instant Download" action.
        # This is not a form submission or a challenge interaction; the
        # renderer refuses generic, login, and CAPTCHA controls.  The returned
        # target is still verified by response headers before being used.
        try:
            direct_download = getattr(get_renderer(), "direct_download", None)
            direct_url = direct_download(action.url) if callable(direct_download) else ""
        except RendererUnavailable:
            direct_url = ""
        if direct_url:
            direct_response = session.inspect(direct_url, "HEAD")
            direct_row = _log(log, direct_response, "Public direct-download action", action, "Direct download target inspected")
            if _final_file(direct_response, direct_url):
                direct_row["extracted_action"], direct_row["next_step"] = "Actual downloadable response reached", "Final file URL reached"
                base.update({"final_url": direct_url, "is_final_file": True, "status": "success", "message": "Verified final file response.", "source": "Browser direct download"})
                results.append(base)
                continue
            direct_row["next_step"] = "Generated target was not a downloadable response"
        if getattr(response, "error", "") and not server_actions:
            row["extracted_action"], row["next_step"] = "No readable page or rendered action", "Stopped at response"
            results.append(base); continue
        if depth >= MAX_DEPTH:
            row["extracted_action"], row["next_step"] = "Traversal limit reached", "Stopped at bounded depth"
            base["message"] = "Workflow depth limit reached before a final file response."
            results.append(base); continue
        next_actions = _branch_actions(server_actions)
        next_actions = [item for item in next_actions if not _same_resource(item.url, action.url)]
        if selected_quality and selected_quality.lower() not in {"all", "*"}:
            wanted = selected_quality.lower()
            next_actions = [item for item in next_actions if not item.quality or item.quality.lower() == wanted]
        if not next_actions:
            row["extracted_action"], row["next_step"] = "No actionable download elements", "Stopped at HTML landing page"
            results.append(base); continue
        row["extracted_action"], row["next_step"] = f"{len(next_actions)} actionable branch(es) extracted", "All branches queued"
        queue.extend((next_action, depth + 1, next_action.quality or quality) for next_action in next_actions)

    if queue:
        limit_message = (
            "Workflow time limit reached before all branches could be inspected."
            if timed_out else
            "Workflow node limit reached before all branches could be inspected."
        )
        results.append({"quality": "Multiple qualities", "source": "Workflow graph", "page_url": movie_url, "final_url": None, "is_final_file": False, "status": "partial", "blocked_by": "", "message": limit_message})

    if not results:
        message, status = "No quality-specific or download action was verified.", "partial"
    elif any(item["status"] == "success" for item in results):
        message, status = "Verified final file response found.", "success"
    elif any(item["status"] == "blocked" for item in results):
        blocked_items = [item for item in results if item["status"] == "blocked"]
        if len(blocked_items) == len(results):
            message, status = blocked_items[0]["message"], "blocked"
        else:
            message, status = "Some branches require manual verification; remaining branches were exhausted.", "partial"
    else:
        message, status = "Final file URL not reached.", "partial"
    return {"status": status, "site": site_url, "movie_url": movie_url, "results": results, "execution_log": log, "workflow_steps": _workflow_steps(True, results), "message": message}
