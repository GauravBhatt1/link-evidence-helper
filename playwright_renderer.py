"""Private, bounded JavaScript renderer used only after a normal fetch is insufficient.

This module never interacts with challenges, login controls, or generic forms.
For a public page that exposes one clearly labelled direct-download action, the
workflow analyzer may ask it to perform that same normal browser action.  Browser
state is ephemeral and discarded at the end, so cookies and local storage cannot
leak across users.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from network_safety import MAX_HTML_BYTES, redact_url, validate_public_url
from challenge_detection import visible_document_text


def interactive_verification_present(html: str) -> bool:
    """Detect an interactive challenge/login page; detection only, never interaction.

    Cloudflare's JavaScript Detection beacon is often injected into otherwise
    public pages.  It runs invisibly in an ordinary browser and is not a
    Turnstile widget or a human challenge.  Treating that beacon as a block
    prevented normal browser rendering of those pages before it could begin.
    """
    lowered = visible_document_text(html).lower()
    markers = ("cf-turnstile", "turnstile", "g-recaptcha", "hcaptcha", "captcha", "login required", "authentication required")
    return any(marker in lowered for marker in markers)


class RendererUnavailable(RuntimeError):
    """The optional renderer is not installed or cannot start."""


@dataclass
class RenderedPage:
    url: str
    html: str
    status: int | None
    content_type: str
    error: str = ""
    # Internal only: never include this in UI/API responses because it may
    # contain a signed query string.
    navigation_url: str = ""


class PlaywrightRenderer:
    """One ephemeral Chromium context, with public-network routing only."""

    def __init__(self, timeout_ms: int = 5_000) -> None:
        self.timeout_ms = min(max(timeout_ms, 1_000), 30_000)
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    def __enter__(self) -> "PlaywrightRenderer":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # keeps non-Docker unit tests dependency-free
            raise RendererUnavailable("Playwright is unavailable") from exc
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._context = self._browser.new_context(
                user_agent="EvidenceLinkRenderer/1.0",
                java_script_enabled=True,
                accept_downloads=False,
            )

            def public_requests_only(route: Any) -> None:
                request = route.request
                if request.resource_type in {"image", "media", "font"}:
                    route.abort()
                    return
                try:
                    validate_public_url(request.url)
                except ValueError:
                    route.abort()
                    return
                route.continue_()

            self._context.route("**/*", public_requests_only)
            self._page = self._context.new_page()
            self._page.set_default_navigation_timeout(self.timeout_ms)
            self._page.set_default_timeout(self.timeout_ms)
            return self
        except Exception as exc:
            self.close()
            raise RendererUnavailable(f"Playwright renderer could not start: {exc}") from exc

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        for item in (self._context, self._browser, self._playwright):
            try:
                if item:
                    item.close() if item is self._context or item is self._browser else item.stop()
            except Exception:
                pass
        self._page = self._context = self._browser = self._playwright = None

    def render(self, url: str) -> RenderedPage:
        safe = validate_public_url(url)
        if not self._page:
            raise RendererUnavailable("Playwright renderer is not active")
        try:
            response = self._page.goto(safe, wait_until="domcontentloaded")
            # A very short idle wait lets ordinary client-side result lists
            # render without multiplying ad/analytics waits across a graph.
            try:
                self._page.wait_for_load_state("networkidle", timeout=min(self.timeout_ms, 1_000))
            except Exception:
                pass
            rendered_url = validate_public_url(self._page.url)
            html = self._page.content()
            if len(html.encode("utf-8", errors="ignore")) > MAX_HTML_BYTES:
                return RenderedPage(redact_url(rendered_url), "", getattr(response, "status", None), "", "Rendered HTML byte limit exceeded", rendered_url)
            headers = response.headers if response else {}
            return RenderedPage(
                redact_url(rendered_url), html, getattr(response, "status", None),
                str(headers.get("content-type", "")), "", rendered_url,
            )
        except Exception as exc:
            return RenderedPage(redact_url(safe), "", None, "", str(exc))

    def direct_download(self, url: str) -> str:
        """Follow one explicit public ``Direct/Instant Download`` UI action.

        This is intentionally narrow: it does not fill forms, click generic
        controls, or attempt any CAPTCHA/login interaction.  It only follows a
        user-visible button whose label promises a direct/instant download, then
        returns a public link shown by the resulting page.  The caller must still
        verify the returned response by headers before exposing it.
        """
        safe = validate_public_url(url)
        if not self._page:
            raise RendererUnavailable("Playwright renderer is not active")
        try:
            self._page.goto(safe, wait_until="domcontentloaded")
            if interactive_verification_present(self._page.content()):
                return ""
            buttons = self._page.get_by_role(
                "button", name=re.compile(r"\b(?:direct|instant)\b.*\bdownload\b", re.I)
            )
            if not buttons.count():
                return ""
            button = buttons.first
            # A form action changes server-side state. This limited path accepts
            # only script-backed direct-download buttons, never submit controls.
            if (button.get_attribute("type") or "button").lower() == "submit" or button.get_attribute("formaction"):
                return ""
            button.click()
            # This public host generates its visible final-link element after
            # a delayed client-side action. Keep the bounded proven wait so a
            # fast-path attempt never turns a valid workflow into a false
            # negative merely because the DOM is not ready yet.
            self._page.wait_for_timeout(min(max(self.timeout_ms + 1_500, 6_500), 12_000))
            if interactive_verification_present(self._page.content()):
                return ""
            links = self._page.get_by_role("link")
            for index in range(links.count()):
                link = links.nth(index)
                label = (link.inner_text() or "").strip()
                href = (link.get_attribute("href") or "").strip()
                if re.search(r"\b(?:download here|direct download|instant download)\b", label, re.I):
                    return validate_public_url(href)
        except Exception:
            return ""
        return ""
