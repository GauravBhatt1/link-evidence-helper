"""Configuration-driven adapter runtime; no site-specific Python class needed."""
from __future__ import annotations
import re
from typing import Any
from urllib.parse import quote_plus, urlparse
from adapter_analyzer import PageParser, _score
from network_safety import SafeSession
from playwright_renderer import PlaywrightRenderer, RendererUnavailable, interactive_verification_present


class AdapterSearchUnsupported(RuntimeError):
    """Raised when a saved adapter needs a browser to obtain search results."""


def _looks_like_javascript_search(html: str) -> bool:
    """Identify client-rendered result pages without attempting to execute them."""
    lowered = html.lower()
    return ("fetch(" in lowered or "xmlhttprequest" in lowered or "axios." in lowered) and any(
        marker in lowered for marker in ("results-grid", "search results", "documents/search", "graphql")
    )


class SiteAdapter:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        # Exposed to the API as a human-readable explanation when no verified
        # link is available.  It intentionally contains no URLs or tokens.
        self.last_find_reason = ""
    def matches_url(self,url:str)->bool: return any((host in url) for host in self.config["domains"])
    def search(self,query:str)->list[dict[str,str]]:
        search = self.config.get("search", {})
        if search.get("mode") == "json_api":
            return self._search_json_api(query, search)
        template=search.get("url_template","")
        if not template: return []
        search_url=template.replace("{query}",quote_plus(query))
        html,meta=SafeSession().fetch_html(search_url)
        if meta.error:
            if meta.status not in {403, 429, 503}:
                return []
            html = ""
        if _looks_like_javascript_search(html) or not html:
            if interactive_verification_present(html):
                raise AdapterSearchUnsupported("Manual verification required")
            try:
                with PlaywrightRenderer() as renderer:
                    rendered = renderer.render(search_url)
            except RendererUnavailable as exc:
                raise AdapterSearchUnsupported(f"JavaScript rendering unavailable: {exc}") from exc
            if rendered.error or not rendered.html:
                return []
            if interactive_verification_present(rendered.html):
                raise AdapterSearchUnsupported("Manual verification required")
            html = rendered.html
        terms=[part for part in re.sub(r"[^a-z0-9]+"," ",query.lower()).split() if part]
        rows=self.extract_candidates(html,search_url)
        matches=[row for row in rows if not terms or all(term in (row["title"]+" "+row["url"]).lower() for term in terms)]
        if not matches and _looks_like_javascript_search(html):
            raise AdapterSearchUnsupported("Search results are loaded by the website's JavaScript and are not available to the safe server-side search.")
        return matches[:12]

    @staticmethod
    def _path(value: Any, path: str, default: Any = "") -> Any:
        """Read a dotted JSON path, without evaluating arbitrary expressions."""
        current = value
        for part in path.split("."):
            if not part or not re.fullmatch(r"[A-Za-z0-9_-]+", part):
                return default
            if not isinstance(current, dict):
                return default
            current = current.get(part, default)
        return current

    def _search_json_api(self, query: str, search: dict[str, Any]) -> list[dict[str, str]]:
        """Use an adapter-declared public JSON search endpoint safely."""
        template = str(search.get("url_template") or "")
        if not template:
            return []
        payload, meta = SafeSession().fetch_json(
            template.replace("{query}", quote_plus(query)), str(search.get("referer") or "")
        )
        if meta.error:
            raise RuntimeError(f"Public search service unavailable: {meta.error}")
        items = self._path(payload, str(search.get("json_results_path") or "results"), [])
        if not isinstance(items, list):
            raise RuntimeError("Adapter JSON search response has no result list")
        title_path, link_path = str(search.get("title_path") or "title"), str(search.get("link_path") or "url")
        rows: list[dict[str, str]] = []
        for item in items[:30]:
            title, url = self._path(item, title_path), self._path(item, link_path)
            if isinstance(title, str) and isinstance(url, str) and title.strip() and url.startswith(("http://", "https://")):
                rows.append({"title": title.strip(), "url": url})
        return rows[:12]
    @staticmethod
    def _public_http_action(url: str) -> bool:
        """Reject share/application actions before any network inspection.

        The parser can legitimately see ``whatsapp:``, ``mailto:``,
        ``tel:``, and ``javascript:`` anchors on a movie page.  They are UI
        actions, never download candidates, and must not reach SafeSession.
        """
        parsed = urlparse(str(url or ""))
        return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)

    def extract_candidates(self,html:str,page_url:str)->list[dict[str,str]]:
        doc=PageParser(page_url)
        doc.feed(html)
        return [{"title": x.text, "url": x.href} for x in doc.elements if x.text and self._public_http_action(x.href)]
    def extract_quality_links(self,html:str,page_url:str,quality:str)->list[dict[str,str]]:
        doc=PageParser(page_url)
        doc.feed(html)
        return [
            {"text": x.text, "url": x.href, "score": str(_score(x, quality)[0])}
            for x in doc.elements
            if self._public_http_action(x.href) and _score(x, quality)[0] >= 3
        ]
    def inspect_redirect_chain(self,url:str): return SafeSession().redirect_chain(url,self.config.get("redirects",{}).get("max_hops",8))
    def identify_final_link(self,chain): return chain[-1].url if chain else ""

    def _is_verified_final(self, candidate_url: str, chain: list[Any]) -> bool:
        """Return True only for a file/CDN response, never an HTML landing page.

        A redirect ending on an instruction, category, ad, or download landing
        page must not be presented as a direct link.  This check only examines
        headers and paths; it never downloads a media body.
        """
        if not chain:
            return False
        final = chain[-1]
        content_type = str(getattr(final, "content_type", "")).lower()
        path = candidate_url.split("?", 1)[0].lower()
        detection = self.config.get("final_link_detection", {})
        extensions = tuple(str(item).lower() for item in detection.get("file_extensions", []))
        if not extensions:
            extensions = (".mp4", ".mkv", ".webm", ".avi", ".mov", ".zip", ".rar", ".7z")
        content_prefixes = tuple(str(item).lower() for item in detection.get("content_type_prefixes", []))
        if not content_prefixes:
            content_prefixes = ("video/", "application/octet-stream")
        return (
            any(path.endswith(extension) for extension in extensions)
            or any(content_type.startswith(prefix) for prefix in content_prefixes)
        )

    def find_links(self, page_url: str, quality: str) -> list[dict[str, str]]:
        """Generate links only at click time; no temporary final URL is persisted."""
        html, meta = SafeSession().fetch_html(page_url)
        if meta.error and meta.status not in {403, 429, 503}:
            raise RuntimeError(meta.error)
        # This is a renderer fallback for ordinary client-side pages, not a
        # challenge solver. Final verification remains header-based below.
        if _looks_like_javascript_search(html) or not html:
            if interactive_verification_present(html):
                self.last_find_reason = "Manual verification required"
                return []
            try:
                with PlaywrightRenderer() as renderer:
                    rendered = renderer.render(page_url)
            except RendererUnavailable:
                rendered = None
            if rendered and not rendered.error:
                if interactive_verification_present(rendered.html):
                    self.last_find_reason = "Manual verification required"
                    return []
                html = rendered.html
        rows=[]
        inspected = 0
        for row in self.extract_quality_links(html,page_url,quality):
            chain=self.inspect_redirect_chain(row["url"])
            inspected += 1
            # Do not leak a redirect destination (it may contain a signed,
            # temporary query); only return the original URL if it itself was
            # verified by its headers as a final file response.
            if not self._is_verified_final(row["url"], chain):
                continue
            rows.append({"url":row["url"],"quality":quality or "Unknown","quality_label":quality or "Unknown","variant":row["text"],"pending":False,"temporary":True})
        if not rows:
            self.last_find_reason = (
                "This source did not provide a verified final link. "
                "Instruction, category, and landing-page links were excluded."
                if inspected else
                "No matching download option was found on this page."
            )
        return rows

def normalized_title(value: str) -> str:
    """Merge cosmetic title variants while retaining quality/source choices."""
    text=re.sub(r"\b(?:480p|720p|1080p|2160p|4k|fhd|uhd|download)\b", " ", value.lower())
    return re.sub(r"[^a-z0-9]+", " ", text).strip()
