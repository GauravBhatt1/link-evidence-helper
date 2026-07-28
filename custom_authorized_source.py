"""Generic, admin-configured parser for authorized public HTML/JSON catalogs.

There are deliberately no provider-specific domains, selectors, browser hooks, or
access-control workarounds here.  The adapter only fetches an admin-configured
public HTTP(S) search URL and parses normal response bodies.
"""
from __future__ import annotations

import json
import base64
import ipaddress
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


REQUIRED = ("resultContainers", "title", "link")
OPTIONAL = ("thumbnail", "quality", "fileSize", "provider", "pagination", "waitFor")
MAX_RESPONSE_BYTES = 2_000_000
MAX_REDIRECTS = 5
SAFE_HEADER_BLOCKLIST = {"host", "content-length", "connection", "proxy-authorization", "proxy-connection"}


class LiveFetchError(ValueError):
    """A user-facing failure which never embeds credentials in its message."""

    def __init__(self, status: str, message: str) -> None:
        self.status = status
        super().__init__(message)


class NoRedirect(HTTPRedirectHandler):
    """Do not follow redirects to an unverified host."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    parent: "Node | None" = None
    children: list["Node"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(part for part in self.text_parts + [child.text for child in self.children] if part).strip()


class TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document", {})
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag.lower(), {str(k).lower(): str(v or "") for k, v in attrs}, self.current)
        self.current.children.append(node)
        if tag.lower() not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}:
            self.current = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.current.tag == tag.lower():
            self.current = self.current.parent or self.root

    def handle_endtag(self, tag: str) -> None:
        node = self.current
        while node.parent and node.tag != tag.lower():
            node = node.parent
        if node.parent:
            self.current = node.parent

    def handle_data(self, data: str) -> None:
        value = re.sub(r"\s+", " ", data).strip()
        if value:
            self.current.text_parts.append(value)


def _descendants(node: Node) -> list[Node]:
    found: list[Node] = []
    for child in node.children:
        found.append(child)
        found.extend(_descendants(child))
    return found


def _matches(node: Node, selector: str) -> bool:
    """Small safe CSS subset: tag, .class, #id, [attr], [attr=value], and combinations."""
    selector = selector.strip()
    if not selector or any(token in selector for token in (">", "+", "~", ":", "*")):
        return False
    attributes = re.findall(r"\[([\w:-]+)(?:\s*=\s*['\"]?([^\]'\"]+)['\"]?)?\]", selector)
    selector = re.sub(r"\[[^\]]+\]", "", selector)
    id_match = re.search(r"#([\w-]+)", selector)
    classes = re.findall(r"\.([\w-]+)", selector)
    tag = re.split(r"[.#]", selector, maxsplit=1)[0].strip().lower()
    if tag and node.tag != tag:
        return False
    if id_match and node.attrs.get("id") != id_match.group(1):
        return False
    node_classes = set(node.attrs.get("class", "").split())
    if any(value not in node_classes for value in classes):
        return False
    for key, expected in attributes:
        expected = expected or None
        actual = node.attrs.get(key.lower())
        if actual is None or (expected is not None and actual != expected.strip()):
            return False
    return bool(tag or id_match or classes or attributes)


def select_all(scope: Node, selectors: list[str]) -> list[Node]:
    result: list[Node] = []
    seen: set[int] = set()
    for raw in selectors:
        for selector in str(raw or "").split(","):
            parts = selector.strip().split()
            if not parts:
                continue
            current = [scope]
            for part in parts:
                current = [child for parent in current for child in _descendants(parent) if _matches(child, part)]
            for node in current:
                if id(node) not in seen:
                    seen.add(id(node)); result.append(node)
    return result


def _as_selectors(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in values if str(item or "").strip()]


def _selector_for(node: Node) -> str:
    """Return a portable selector from a node without using page-specific rules."""
    if node.attrs.get("id"):
        return f"{node.tag}#{node.attrs['id']}"
    classes = [part for part in node.attrs.get("class", "").split() if re.fullmatch(r"[\w-]+", part)]
    if classes:
        return f"{node.tag}." + ".".join(classes[:2])
    for attr in ("data-result", "data-title", "data-url", "data-quality", "data-size", "data-provider"):
        if attr in node.attrs:
            return f"{node.tag}[{attr}]"
    return node.tag


def _ranked(items: list[tuple[str, float]], limit: int = 5) -> list[dict[str, Any]]:
    best: dict[str, float] = {}
    for selector, score in items:
        if selector:
            best[selector] = max(best.get(selector, 0), score)
    return [{"selector": selector, "confidence": round(score)} for selector, score in
            sorted(best.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]]


def _with_fallback(items: list[dict[str, Any]], fallbacks: tuple[str, ...]) -> list[dict[str, Any]]:
    """Keep the one-click form editable even when a field is weakly inferred.

    These are deliberately labelled low-confidence alternatives, not claimed
    matches.  The UI shows their score and an administrator can replace them.
    """
    seen = {str(item.get("selector") or "") for item in items}
    output = list(items)
    for selector in fallbacks:
        if selector not in seen:
            output.append({"selector": selector, "confidence": 15})
        if len(output) >= 5:
            break
    return output[:5]


def _all_nodes(tree: TreeBuilder | Node) -> list[Node]:
    return _descendants(tree.root if isinstance(tree, TreeBuilder) else tree)


def _field_suggestions(containers: list[Node], field: str) -> list[dict[str, Any]]:
    """Rank generic selectors by coverage and harmless semantic HTML hints."""
    candidates: list[tuple[str, float]] = []
    hints = {
        "thumbnail": ("thumb", "poster", "image", "cover"),
        "title": ("title", "name", "heading"), "quality": ("quality", "resolution", "format"),
        "fileSize": ("size", "filesize", "file-size"), "provider": ("provider", "source", "host"),
    }[field]
    for container in containers:
        for node in _descendants(container):
            selector = _selector_for(node)
            blob = " ".join([node.tag, node.attrs.get("class", ""), node.attrs.get("id", ""), " ".join(node.attrs)]).lower()
            semantic = any(hint in blob for hint in hints)
            if field == "thumbnail":
                eligible = node.tag == "img" and bool(node.attrs.get("src") or node.attrs.get("data-src"))
            elif field == "title":
                eligible = bool(node.text) and (semantic or node.tag in {"h1", "h2", "h3", "h4", "a"})
            elif field == "quality":
                eligible = bool(node.text) and (semantic or bool(re.search(r"\b(?:480|576|720|1080|1440|2160)p\b|\b(?:4k|uhd)\b", node.text, re.I)))
            elif field == "fileSize":
                eligible = bool(node.text) and (semantic or bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:kb|mb|gb|tb)\b", node.text, re.I)))
            else:
                eligible = bool(node.text) and semantic
            if eligible:
                coverage = sum(1 for item in containers if select_all(item, [selector])) / max(1, len(containers))
                specificity = 10 if selector != node.tag else 0
                candidates.append((selector, min(99, 35 + coverage * 50 + (14 if semantic else 0) + specificity)))
    return _ranked(candidates)


def detect_selectors(sample_html: str) -> dict[str, Any]:
    """Suggest selectors using only the administrator's pasted HTML; no requests."""
    tree = TreeBuilder(); tree.feed(sample_html)
    nodes = _all_nodes(tree)
    container_candidates: list[tuple[str, float]] = []
    for node in nodes:
        descendants = _descendants(node)
        links = [item for item in descendants if item.tag == "a" and (item.attrs.get("href") or item.attrs.get("data-url"))]
        text_nodes = [item for item in descendants if item.text and item.tag in {"h1", "h2", "h3", "h4", "a", "span", "p"}]
        if links and text_nodes:
            selector = _selector_for(node)
            same = sum(1 for other in nodes if _matches(other, selector))
            if 2 <= same <= 100:
                score = min(99, 35 + min(same, 10) * 4 + (20 if node.tag in {"article", "li", "tr"} else 0) + (16 if any(x in selector.lower() for x in ("result", "item", "card", "movie", "entry")) else 0))
                container_candidates.append((selector, score))
    containers_ranked = _ranked(container_candidates)
    if not containers_ranked:
        return {"reliable": False, "reason": "No repeating content blocks containing both text and a public link were found. Choose one of the low-confidence alternatives or use a complete search-results page.", "suggestions": {
            "resultContainers": _with_fallback([], (".result", ".item", "[data-result]", "article", "li")),
            "title": _with_fallback([], (".title", "h2", "h3", "[data-title]", "a")),
            "link": _with_fallback([], ("a[href]", "a.download", "[data-url]")),
            "quality": _with_fallback([], (".quality", "[data-quality]", ".resolution")),
            "fileSize": _with_fallback([], (".size", "[data-size]", ".file-size")),
            "provider": _with_fallback([], (".provider", "[data-provider]", ".source")),
            "pagination": _with_fallback([], (".next", "a[rel=next]", ".pagination")),
            "waitFor": _with_fallback([], (".results", ".result", "[data-result]")),
        }}
    best_containers = select_all(tree.root, [containers_ranked[0]["selector"]])
    links: list[tuple[str, float]] = []
    for container in best_containers:
        for node in _descendants(container):
            if node.tag == "a" and (node.attrs.get("href") or node.attrs.get("data-url")):
                selector = _selector_for(node)
                coverage = sum(1 for item in best_containers if select_all(item, [selector])) / len(best_containers)
                links.append((selector, min(99, 55 + coverage * 40 + (4 if selector != "a" else 0))))
    pagination: list[tuple[str, float]] = []
    for node in nodes:
        if node.tag == "a" and (node.attrs.get("href") or node.attrs.get("data-url")):
            blob = " ".join((node.text, node.attrs.get("class", ""), node.attrs.get("id", ""), node.attrs.get("rel", ""))).lower()
            if any(token in blob for token in ("next", "pagination", "page-next", "older")):
                pagination.append((_selector_for(node), 88))
    return {"reliable": True, "reason": "", "suggestions": {
        "resultContainers": _with_fallback(containers_ranked, (".result", ".item", "[data-result]")),
        "title": _with_fallback(_field_suggestions(best_containers, "title"), (".title", "h2", "h3", "[data-title]")),
        "link": _with_fallback(_ranked(links), ("a[href]", "a.download", "[data-url]")),
        "quality": _with_fallback(_field_suggestions(best_containers, "quality"), (".quality", "[data-quality]", ".resolution")),
        "fileSize": _with_fallback(_field_suggestions(best_containers, "fileSize"), (".size", "[data-size]", ".file-size")),
        "provider": _with_fallback(_field_suggestions(best_containers, "provider"), (".provider", "[data-provider]", ".source")),
        "pagination": _with_fallback(_ranked(pagination), (".next", "a[rel=next]", ".pagination")),
        "waitFor": _with_fallback(containers_ranked, (".results", ".result", "[data-result]")),
    }}


def preview_selectors(sample_html: str, config: dict[str, Any]) -> dict[str, Any]:
    """Return local, UI-safe container-by-container selector coverage."""
    cfg = validate_config(config)
    tree = TreeBuilder(); tree.feed(sample_html)
    containers = select_all(tree.root, cfg["resultContainers"])
    rows = []
    for index, container in enumerate(containers):
        found = {key: bool(select_all(container, cfg[key])) for key in ("title", "link", "quality", "fileSize", "provider")}
        rows.append({"index": index + 1, "snippet": re.sub(r"\s+", " ", container.text)[:220], "found": found,
                     "missing": [label for key, label in (("title", "Missing Title"), ("link", "Missing Link"), ("quality", "Missing Quality"), ("fileSize", "Missing Size"), ("provider", "Missing Provider")) if not found[key]]})
    return {"containers": rows, "containersMatched": len(rows)}


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    allowed_domain = str(config.get("allowedDomain") or "").strip().lower()
    search_template = str(config.get("searchUrlTemplate") or "").strip()
    if not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,63}", allowed_domain):
        raise ValueError("Allowed domain must be a hostname, without a path")
    parsed = urlparse(search_template)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != allowed_domain:
        raise ValueError("Search URL template must be HTTP(S) and use the allowed domain exactly")
    if "{query}" not in search_template:
        raise ValueError("Search URL template must include {query}")
    normalized: dict[str, Any] = {"allowedDomain": allowed_domain, "searchUrlTemplate": search_template}
    for key in REQUIRED + OPTIONAL:
        normalized[key] = _as_selectors(config.get(key))
    missing = [key for key in REQUIRED if not normalized[key]]
    if missing:
        raise ValueError("Missing selector configuration: " + ", ".join(missing))
    timeout = int(config.get("requestTimeout") or 12)
    if not 1 <= timeout <= 60:
        raise ValueError("Request timeout must be between 1 and 60 seconds")
    normalized["requestTimeout"] = timeout
    # Learned during guided setup.  It describes a final delivery URL, while
    # the normal result link may be a detail/download page several hops away.
    # This is data, not provider-specific resolver code.
    delivery = config.get("deliveryPattern") or {}
    if isinstance(delivery, dict):
        normalized["deliveryPattern"] = {
            "hostname": str(delivery.get("hostname") or "").lower(),
            "pathPrefix": str(delivery.get("pathPrefix") or "/"),
            "fileExtension": str(delivery.get("fileExtension") or "").lower(),
            "maxDepth": max(0, min(3, int(delivery.get("maxDepth") or 2))),
        }
    return normalized


def validate_live_page_url(value: Any, allow_private_network: bool = False) -> str:
    """Validate an HTTP(S) target and reject SSRF targets before connecting.

    DNS is resolved for every hop.  A hostname is rejected if *any* answer is
    non-global, which also protects against mixed public/private DNS answers.
    The explicit private-network flag is intended only for an administrator's
    own intranet catalog.
    """
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise LiveFetchError("invalid-url", "Live Page URL must be a plain HTTP or HTTPS URL.")
    hostname = parsed.hostname.rstrip(".").lower()
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror:
        raise LiveFetchError("invalid-url", "Live Page URL host could not be resolved.") from None
    if not addresses:
        raise LiveFetchError("invalid-url", "Live Page URL host could not be resolved.")
    if not allow_private_network:
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise LiveFetchError("access-restricted", "Localhost and internal network addresses are blocked. Enable private-network access only for your own authorized catalog.")
    return url


def _request_headers(auth: dict[str, Any] | None) -> dict[str, str]:
    """Build request headers without logging or returning secret values."""
    auth = auth if isinstance(auth, dict) else {}
    headers = {"user-agent": "AuthorizedCatalogParser/1.0", "accept": "text/html,application/xhtml+xml,application/json"}
    supplied = auth.get("headers") or {}
    if isinstance(supplied, str):
        pairs = [line.split(":", 1) for line in supplied.splitlines() if line.strip()]
        supplied = {key.strip(): value.strip() for key, value in pairs if len(key.strip()) and len(value.strip())}
    if not isinstance(supplied, dict):
        raise LiveFetchError("invalid-url", "Custom headers must be a JSON object or Header: value lines.")
    for key, value in supplied.items():
        name, text = str(key).strip(), str(value).strip()
        if not name or not text or "\r" in name + text or "\n" in name + text or name.lower() in SAFE_HEADER_BLOCKLIST:
            raise LiveFetchError("invalid-url", "One custom request header is invalid or unsafe.")
        headers[name] = text
    cookie = str(auth.get("cookie") or "").strip()
    bearer = str(auth.get("bearerToken") or "").strip()
    username, password = str(auth.get("basicUsername") or ""), str(auth.get("basicPassword") or "")
    if cookie:
        if "\r" in cookie or "\n" in cookie: raise LiveFetchError("invalid-url", "Cookie input is invalid.")
        headers["Cookie"] = cookie
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    elif username or password:
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    return headers


def _read_limited(response: Any, maximum: int = MAX_RESPONSE_BYTES) -> bytes:
    declared = response.headers.get("content-length")
    if declared and int(declared) > maximum:
        raise LiveFetchError("access-restricted", f"Response is larger than the {maximum // 1_000_000} MB safety limit.")
    body = response.read(maximum + 1)
    if len(body) > maximum:
        raise LiveFetchError("access-restricted", f"Response is larger than the {maximum // 1_000_000} MB safety limit.")
    return body


def fetch_live_page(url: Any, *, timeout: Any = 12, allow_private_network: bool = False, auth: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fetch normal authorized HTML/JSON, validating each redirect hop.

    Secrets are accepted only in memory for this request.  Results deliberately
    contain no request headers, cookie values, authorization details, or URLs
    with credentials.
    """
    try: timeout_value = max(1, min(60, int(timeout or 12)))
    except (TypeError, ValueError): raise LiveFetchError("invalid-url", "Request timeout must be between 1 and 60 seconds.")
    current = validate_live_page_url(url, allow_private_network)
    initial = urlparse(current)
    headers = _request_headers(auth)
    for _ in range(MAX_REDIRECTS + 1):
        try:
            request = Request(current, headers=headers)
            try:
                response = build_opener(NoRedirect()).open(request, timeout=timeout_value)
            except urllib.error.HTTPError as exc:
                response = exc
            code = int(getattr(response, "status", response.getcode()))
            location = response.headers.get("location")
            if 300 <= code < 400 and location:
                if _ >= MAX_REDIRECTS:
                    raise LiveFetchError("access-restricted", "Too many redirects.")
                next_url = validate_live_page_url(urljoin(current, location), allow_private_network)
                # Never leak supplied auth/headers to a different origin.
                if urlparse(next_url).netloc != initial.netloc:
                    headers = {"user-agent": "AuthorizedCatalogParser/1.0", "accept": "text/html,application/xhtml+xml,application/json"}
                current = next_url
                continue
            if code in {401, 407}:
                raise LiveFetchError("authentication-required", "Authentication is required by this page.")
            if code in {403, 451}:
                raise LiveFetchError("access-restricted", "Access to this page is restricted.")
            if code >= 400:
                raise LiveFetchError("access-restricted", f"The page returned HTTP {code}.")
            content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].lower()
            if content_type and not any(kind in content_type for kind in ("text/html", "application/xhtml", "application/json", "text/json")):
                raise LiveFetchError("access-restricted", "The URL returned a non-HTML/non-JSON response; downloads are not allowed.")
            raw = _read_limited(response)
            return {"url": current, "httpStatus": code, "contentType": content_type or "text/html", "body": raw.decode("utf-8", "replace"), "rendered": False}
        except LiveFetchError:
            raise
        except TimeoutError:
            raise LiveFetchError("timed-out", "Request timed out.") from None
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                raise LiveFetchError("timed-out", "Request timed out.") from None
            raise LiveFetchError("access-restricted", "The authorized page could not be reached.") from None
    raise LiveFetchError("access-restricted", "Redirect handling failed.")


def _filename_from_headers_or_url(headers: Any, url: str) -> str:
    disposition = str(headers.get("content-disposition") or "")
    match = re.search(r"filename\*?=(?:UTF-8''|[\"'])?([^;\"']+)", disposition, re.I)
    if match:
        return re.sub(r"%([0-9A-Fa-f]{2})", lambda item: chr(int(item.group(1), 16)), match.group(1)).strip()
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


def inspect_example_result(url: Any, *, timeout: Any = 12, allow_private_network: bool = False, auth: dict[str, Any] | None = None) -> dict[str, Any]:
    """Inspect an example result by headers/redirects only; never download it."""
    try: timeout_value = max(1, min(60, int(timeout or 12)))
    except (TypeError, ValueError): raise LiveFetchError("invalid-url", "Request timeout must be between 1 and 60 seconds.")
    original = validate_live_page_url(url, allow_private_network)
    current, redirect_chain, headers = original, [original], _request_headers(auth)
    initial_netloc = urlparse(original).netloc
    method = "HEAD"
    for redirect_count in range(MAX_REDIRECTS + 1):
        try:
            request = Request(current, headers=headers, method=method)
            try:
                response = build_opener(NoRedirect()).open(request, timeout=timeout_value)
            except urllib.error.HTTPError as exc:
                response = exc
            code = int(getattr(response, "status", response.getcode()))
            location = response.headers.get("location")
            if 300 <= code < 400 and location:
                if redirect_count >= MAX_REDIRECTS: raise LiveFetchError("access-restricted", "Too many redirects.")
                current = validate_live_page_url(urljoin(current, location), allow_private_network)
                redirect_chain.append(current)
                if urlparse(current).netloc != initial_netloc:
                    headers = {"user-agent": "AuthorizedCatalogParser/1.0", "accept": "*/*"}
                continue
            if code in {405, 501} and method == "HEAD":
                # A single byte range request obtains headers without ever
                # downloading the target file when HEAD is unsupported.
                method = "GET"; headers = {**headers, "Range": "bytes=0-0"}; continue
            if code in {401, 407}: raise LiveFetchError("authentication-required", "Authentication is required by this example URL.")
            if code in {403, 451}: raise LiveFetchError("access-restricted", "Access to this example URL is restricted.")
            if code >= 400: raise LiveFetchError("access-restricted", f"Example URL returned HTTP {code}.")
            if method == "GET": response.read(1)  # consume at most one byte
            content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].lower()
            length_raw = str(response.headers.get("content-length") or "")
            try: content_length: int | None = max(0, int(length_raw)) if length_raw else None
            except ValueError: content_length = None
            final = current
            final_path_name = urlparse(final).path.rsplit("/", 1)[-1]
            extension = ("." + final_path_name.rsplit(".", 1)[-1].lower()) if "." in final_path_name else ""
            file_like = bool(response.headers.get("content-disposition")) or bool(extension) or (content_type and "html" not in content_type and "json" not in content_type)
            kind = "direct-file" if file_like else ("structured-api-result" if "json" in content_type else ("redirect-endpoint" if len(redirect_chain) > 1 else "result-page"))
            parsed = urlparse(final); directory = parsed.path.rsplit("/", 1)[0] + "/" if "/" in parsed.path else "/"
            return {"success": True, "kind": kind, "originalUrl": original, "finalUrl": final, "hostname": parsed.hostname or "", "pathPattern": f"{parsed.scheme}://{parsed.netloc}{directory}*{extension}", "pathPrefix": directory, "fileExtension": extension, "contentType": content_type or "unknown", "contentLength": content_length, "fileName": _filename_from_headers_or_url(response.headers, final), "redirectChain": redirect_chain, "httpStatus": code, "metadataOnly": True}
        except LiveFetchError: raise
        except TimeoutError: raise LiveFetchError("timed-out", "Example URL inspection timed out.") from None
        except urllib.error.URLError: raise LiveFetchError("access-restricted", "Example result could not be inspected.") from None
    raise LiveFetchError("access-restricted", "Example URL redirect handling failed.")


def render_live_page(url: str, *, timeout: Any = 12, allow_private_network: bool = False) -> dict[str, Any]:
    """Optional normal headless-browser rendering for JavaScript pages.

    Chromium receives a resolver rule that blocks every hostname except the
    already validated public target.  It has no download path and cannot use
    privileged browser extensions.  Authentication remains handled by the
    normal server-side fetch; browsers without an approved auth integration
    correctly report authentication-required rather than attempting a bypass.
    """
    safe_url = validate_live_page_url(url, allow_private_network)
    browser = next((shutil.which(item) for item in ("chromium", "chromium-browser", "google-chrome") if shutil.which(item)), None)
    if not browser:
        raise LiveFetchError("javascript-rendering-required", "JavaScript rendering is required, but no headless browser is installed.")
    try: timeout_value = max(2, min(60, int(timeout or 12)))
    except (TypeError, ValueError): timeout_value = 12
    hostname = urlparse(safe_url).hostname or ""
    # Block all subresource hosts by default; only the validated page host can
    # resolve. This makes an HTML page unable to turn the browser into SSRF.
    resolver_rules = f"MAP * 0.0.0.0, EXCLUDE {hostname}"
    with tempfile.TemporaryDirectory(prefix="authorized-selector-browser-") as profile:
        command = [browser, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-extensions", "--disable-background-networking", "--disable-sync", "--no-first-run", "--disable-component-update", "--disable-features=DownloadBubble,DownloadService", f"--host-resolver-rules={resolver_rules}", f"--user-data-dir={profile}", "--virtual-time-budget=3000", "--dump-dom", safe_url]
        try:
            completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout_value + 5, check=False)
        except subprocess.TimeoutExpired:
            raise LiveFetchError("timed-out", "Headless browser rendering timed out.") from None
    if completed.returncode != 0 or not completed.stdout:
        raise LiveFetchError("javascript-rendering-required", "The page requires browser rendering or authentication that was not accepted.")
    if len(completed.stdout) > MAX_RESPONSE_BYTES:
        raise LiveFetchError("access-restricted", f"Rendered page is larger than the {MAX_RESPONSE_BYTES // 1_000_000} MB safety limit.")
    return {"url": safe_url, "httpStatus": 200, "contentType": "text/html", "body": completed.stdout.decode("utf-8", "replace"), "rendered": True}


def live_auto_detect(url: Any, *, timeout: Any = 12, allow_private_network: bool = False, auth: dict[str, Any] | None = None, render_mode: str = "auto") -> dict[str, Any]:
    """Fetch a live authorized page and return selector suggestions, not secrets."""
    live = fetch_live_page(url, timeout=timeout, allow_private_network=allow_private_network, auth=auth)
    detected = detect_selectors(live["body"]) if "html" in live["contentType"] else {"reliable": False, "reason": "The page returned structured data; configure an HTML catalog endpoint or use Paste Sample HTML.", "suggestions": {key: [] for key in ("resultContainers", "title", "link", "quality", "fileSize", "provider", "pagination", "waitFor")}}
    should_render = render_mode == "browser" or (render_mode == "auto" and not detected["reliable"] and "html" in live["contentType"])
    if should_render:
        try:
            rendered = render_live_page(live["url"], timeout=timeout, allow_private_network=allow_private_network)
            live = rendered
            detected = detect_selectors(live["body"])
            if not detected["reliable"]:
                detected["reason"] = "No reliable repeating result blocks were found after JavaScript rendering."
        except LiveFetchError as exc:
            # Static content is still valuable. Only surface a rendering error
            # as the primary status when static parsing found nothing.
            if not detected["reliable"]:
                # The network fetch itself succeeded. Browser rendering is a
                # parser capability outcome, not an API transport failure.
                detected["reliable"] = False
                detected["reason"] = str(exc)
                detected["renderingError"] = exc.status
    containers = 0
    suggestions = detected.get("suggestions") or {}
    first_container = (suggestions.get("resultContainers") or [{}])[0].get("selector")
    if first_container and "html" in live["contentType"]:
        tree = TreeBuilder(); tree.feed(live["body"])
        containers = len(select_all(tree.root, [str(first_container)]))
    detection_status = "working" if detected["reliable"] else "no-selectors-detected"
    response_size = len(live["body"].encode("utf-8", "replace"))
    return {
        "ok": True, "success": True,
        # `status` is the remote page HTTP status; detection is intentionally
        # separate so status=200 can never be treated as a request failure.
        "status": live["httpStatus"], "contentType": live["contentType"],
        "html": live["body"], "finalUrl": live["url"],
        "message": "Page fetched successfully" if detected["reliable"] else "Page fetched successfully, but no reliable selectors were detected.",
        **detected, "detectionStatus": detection_status,
        "fetch": {key: live[key] for key in ("url", "httpStatus", "contentType", "rendered")},
        "diagnostics": {
            "remotePageHttpStatus": live["httpStatus"], "contentType": live["contentType"],
            "finalUrl": live["url"], "responseSize": response_size,
            "fetchCompleted": True, "domParsingCompleted": "html" in live["contentType"],
            "containersDetected": containers, "errorStage": "no-selectors-detected" if not detected["reliable"] else None,
        },
    }


def live_preview(url: Any, config: dict[str, Any], source_name: str, *, timeout: Any = 12, allow_private_network: bool = False, auth: dict[str, Any] | None = None, render_mode: str = "auto") -> dict[str, Any]:
    """Fetch then parse a live page without returning its private HTML body."""
    cfg = validate_config(config)
    live = fetch_live_page(url, timeout=timeout, allow_private_network=allow_private_network, auth=auth)
    items, diagnostics = parse_authorized_response(live["body"], live["contentType"], cfg, "preview", source_name or "Custom Authorized Source", live["url"])
    if render_mode == "browser" or (render_mode == "auto" and not items and "html" in live["contentType"]):
        try:
            rendered = render_live_page(live["url"], timeout=timeout, allow_private_network=allow_private_network)
            live = rendered
            items, diagnostics = parse_authorized_response(live["body"], live["contentType"], cfg, "preview", source_name or "Custom Authorized Source", live["url"])
        except LiveFetchError as exc:
            if not items and diagnostics["containersMatched"] == 0:
                diagnostics["parserStatus"] = exc.status
                diagnostics["rejectedResults"].append({"reason": str(exc)})
    diagnostics.update({"sourceMatched": True, "httpStatus": live["httpStatus"], "contentType": live["contentType"], "parserExecuted": True, "rawCandidatesFound": diagnostics["containersMatched"], "normalizedResults": len(items), "finalRenderedResults": len(items), "errorStage": None if items else "parsing"})
    status = "working" if items else ("parser-mismatch" if not diagnostics["containersMatched"] else "invalid-normalized-output")
    return {"ok": True, "status": status, "items": items, "diagnostics": diagnostics, "preview": preview_selectors(live["body"], cfg), "fetch": {key: live[key] for key in ("url", "httpStatus", "contentType", "rendered")}}


def _result_url_pattern(example_url: str) -> str:
    parsed = urlparse(example_url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return f"{parsed.scheme}://{parsed.netloc}/*"
    parts[-1] = "*"
    return f"{parsed.scheme}://{parsed.netloc}/{'/'.join(parts)}"


def _is_similar_result_url(value: str, example_url: str) -> bool:
    candidate, example = urlparse(value), urlparse(example_url)
    if candidate.scheme not in {"http", "https"} or candidate.netloc != example.netloc:
        return False
    if candidate.path == example.path:
        return True
    left = [part for part in candidate.path.split("/") if part]
    right = [part for part in example.path.split("/") if part]
    if not left or len(left) != len(right):
        return False
    # A result URL normally shares its route and differs at the slug/id. This
    # rejects category/navigation links which diverge earlier in the path.
    return left[:-1] == right[:-1] and bool(left[-1])


def _matches_example_metadata(value: str, example: dict[str, Any]) -> bool:
    """Compare stable URL parts only; signed/token query strings are ignored."""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != example.get("hostname"):
        return False
    prefix, extension = str(example.get("pathPrefix") or "/"), str(example.get("fileExtension") or "")
    if not parsed.path.startswith(prefix): return False
    return not extension or parsed.path.lower().endswith(extension.lower())


def _node_urls(node: Node, base_url: str) -> list[str]:
    """Extract ordinary, visible URL-bearing attributes from a DOM node.

    This intentionally does not inspect scripts, hidden APIs, or browser
    network traffic.  It is only the same public links a user can see.
    """
    values = []
    for key in ("href", "data-url", "data-href", "data-download", "data-link", "src"):
        value = str(node.attrs.get(key) or "").strip()
        if value:
            values.append(urljoin(base_url, value))
    return values


def _delivery_pattern_matches(value: str, pattern: dict[str, Any]) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != pattern.get("hostname"):
        return False
    prefix, extension = str(pattern.get("pathPrefix") or "/"), str(pattern.get("fileExtension") or "")
    return parsed.path.startswith(prefix) and (not extension or parsed.path.lower().endswith(extension))


def discover_authorized_delivery(start_url: str, delivery_pattern: dict[str, Any], *, timeout: Any = 12,
                                 allow_private_network: bool = False, auth: dict[str, Any] | None = None) -> dict[str, Any]:
    """Follow a small public HTML link chain to a configured delivery shape.

    Only normal redirects and visible anchor/data URLs are followed; file
    bodies are never downloaded.  The bounded traversal keeps this safe for
    a user's own catalog/CDN without becoming a site crawler.
    """
    max_depth = max(0, min(3, int(delivery_pattern.get("maxDepth") or 2)))
    queue: list[tuple[str, int]] = [(start_url, 0)]
    visited: set[str] = set()
    trail: list[str] = []
    while queue:
        current, depth = queue.pop(0)
        if current in visited:
            continue
        visited.add(current); trail.append(current)
        try:
            metadata = inspect_example_result(current, timeout=timeout, allow_private_network=allow_private_network, auth=auth)
            final_url = str(metadata.get("finalUrl") or current)
            if _delivery_pattern_matches(final_url, delivery_pattern):
                return {"found": True, "url": final_url, "trail": trail + [url for url in metadata.get("redirectChain", []) if url not in trail], "kind": metadata.get("kind")}
            if depth >= max_depth or metadata.get("kind") == "direct-file":
                continue
            live = fetch_live_page(current, timeout=timeout, allow_private_network=allow_private_network, auth=auth)
            if "html" not in str(live.get("contentType") or ""):
                continue
            tree = TreeBuilder(); tree.feed(str(live.get("body") or ""))
            candidates: list[str] = []
            for node in _all_nodes(tree):
                if _inside_excluded_region(node):
                    continue
                for target in _node_urls(node, str(live.get("url") or current)):
                    if target not in visited and target not in candidates:
                        candidates.append(target)
            # Prefer direct pattern matches; otherwise examine at most 12
            # visible links at the next depth.
            candidates.sort(key=lambda item: 0 if _delivery_pattern_matches(item, delivery_pattern) else 1)
            queue.extend((item, depth + 1) for item in candidates[:12])
        except LiveFetchError:
            continue
    return {"found": False, "url": "", "trail": trail, "kind": ""}


def _inside_excluded_region(node: Node) -> bool:
    current: Node | None = node
    while current:
        if current.tag in {"nav", "header", "footer", "aside", "form"}:
            return True
        blob = " ".join((current.attrs.get("class", ""), current.attrs.get("id", ""))).lower()
        if any(word in blob for word in ("nav", "menu", "footer", "header", "social", "advert", "banner", "login", "sidebar")):
            return True
        current = current.parent
    return False


def _guided_container_suggestions(tree: TreeBuilder, links: list[Node]) -> tuple[list[dict[str, Any]], list[Node]]:
    """Choose the smallest repeated ancestor that contains matching links."""
    all_nodes = _all_nodes(tree)
    link_ids = {id(link) for link in links}
    candidates: list[tuple[str, float]] = []
    selected_nodes: list[Node] = []
    for link in links:
        parent = link.parent
        depth = 0
        while parent and parent.tag != "document" and depth < 8:
            selector = _selector_for(parent)
            matching = [node for node in all_nodes if _matches(node, selector)]
            coverage = sum(1 for node in matching if any(id(item) in link_ids for item in _descendants(node)))
            if len(matching) >= 2 and coverage >= 2:
                specificity = 18 if selector != parent.tag else 0
                score = min(99, 52 + min(coverage, 8) * 5 + specificity - depth * 2)
                candidates.append((selector, score))
            parent = parent.parent; depth += 1
    ranked = _ranked(candidates)
    if ranked:
        selected_nodes = select_all(tree.root, [ranked[0]["selector"]])
    return ranked, selected_nodes


def discover_public_search_template(site_url: Any, *, timeout: Any = 12, allow_private_network: bool = False,
                                    auth: dict[str, Any] | None = None) -> dict[str, str]:
    """Infer a public GET search route from the site's own visible form.

    No URL convention is guessed: an actual search input and form action must
    be present in the returned HTML.  That keeps a one-URL setup honest.
    """
    landing = fetch_live_page(site_url, timeout=timeout, allow_private_network=allow_private_network, auth=auth)
    if "html" not in str(landing.get("contentType") or ""):
        raise LiveFetchError("html-parsing-failed", "Site URL was fetched, but it does not return an HTML page with a search form.")
    tree = TreeBuilder(); tree.feed(str(landing.get("body") or ""))
    ranked: list[tuple[Node, Node, int]] = []
    for form in [node for node in _all_nodes(tree) if node.tag == "form"]:
        if str(form.attrs.get("method") or "get").lower() not in {"", "get"}:
            continue
        for input_node in _descendants(form):
            if input_node.tag not in {"input", "textarea"}:
                continue
            name = str(input_node.attrs.get("name") or "").strip()
            if not name:
                continue
            blob = " ".join((name, input_node.attrs.get("type", ""), input_node.attrs.get("placeholder", ""), input_node.attrs.get("aria-label", ""))).lower()
            score = 100 if input_node.attrs.get("type", "").lower() == "search" else 80 if any(word in blob for word in ("search", "query", "keyword", "term", " q")) else 0
            if score:
                ranked.append((form, input_node, score))
    if not ranked:
        raise LiveFetchError("no-search-form", "No public GET search form was found on this site. Open the site's search page once, then use that URL instead.")
    form, input_node, _ = sorted(ranked, key=lambda item: -item[2])[0]
    action = urljoin(str(landing["url"]), str(form.attrs.get("action") or landing["url"]))
    parsed = urlparse(action)
    pairs = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != input_node.attrs["name"]]
    pairs.append((input_node.attrs["name"], "{query}"))
    template = parsed._replace(query=urlencode(pairs)).geturl().replace("%7Bquery%7D", "{query}")
    words: list[str] = []
    for node in _all_nodes(tree):
        if node.tag not in {"a", "h1", "h2", "h3", "h4"} or _inside_excluded_region(node):
            continue
        for word in re.findall(r"[A-Za-z0-9]{3,}", node.text):
            lowered = word.lower()
            if lowered not in {"login", "search", "privacy", "terms", "contact", "home", "next", "previous"} and lowered not in words:
                words.append(lowered)
            if len(words) >= 5:
                break
        if len(words) >= 5:
            break
    return {"searchTemplate": template, "landingUrl": str(landing["url"]), "searchInput": input_node.attrs["name"], "sampleQueries": words}


def auto_setup_from_site_url(site_url: Any, *, timeout: Any = 12, allow_private_network: bool = False,
                             auth: dict[str, Any] | None = None, render_mode: str = "auto") -> dict[str, Any]:
    """One-URL setup: discover visible search form, then reuse guided logic."""
    discovered = discover_public_search_template(site_url, timeout=timeout, allow_private_network=allow_private_network, auth=auth)
    query_candidates = list(dict.fromkeys([*discovered.get("sampleQueries", []), "a", "the", "movie", "1"]))[:8]
    search = None; candidate = ""; chosen_query = ""
    for query in query_candidates:
        search_url = discovered["searchTemplate"].replace("{query}", quote_plus(query))
        possible = fetch_live_page(search_url, timeout=timeout, allow_private_network=allow_private_network, auth=auth)
        if "html" not in str(possible.get("contentType") or ""):
            continue
        pages = [possible]
        if render_mode in {"auto", "browser"}:
            try:
                pages.append(render_live_page(str(possible["url"]), timeout=timeout, allow_private_network=allow_private_network))
            except LiveFetchError:
                pass
        for page in pages:
            tree = TreeBuilder(); tree.feed(str(page.get("body") or ""))
            detected = detect_selectors(str(page.get("body") or ""))
            container_selector = ((detected.get("suggestions") or {}).get("resultContainers") or [{}])[0].get("selector")
            for container in select_all(tree.root, [str(container_selector or "")]):
                for node in _descendants(container):
                    if node.tag == "a" and not _inside_excluded_region(node):
                        urls = _node_urls(node, str(page["url"]))
                        if urls:
                            search, candidate, chosen_query = page, urls[0], query
                            break
                if candidate: break
            if candidate: break
        if candidate: break
    if not search or not candidate:
        raise LiveFetchError("no-matching-result-links", "Search route was found, but it returned no visible result links for automatic learning.")
    result = guided_source_setup(str(search["url"]), candidate, timeout=timeout, allow_private_network=allow_private_network, auth=auth, render_mode=render_mode)
    result["discoveredSearchTemplate"] = discovered["searchTemplate"]
    result["discoveredFromSiteUrl"] = True
    result["discoveryQuery"] = chosen_query
    return result


def guided_source_setup(search_page_url: Any, example_result_url: Any, *, timeout: Any = 12, allow_private_network: bool = False, auth: dict[str, Any] | None = None, render_mode: str = "auto") -> dict[str, Any]:
    """Learn search-result selectors from a search page plus one known result.

    No site-specific route, selector, or provider knowledge is used.
    """
    try:
        search = fetch_live_page(search_page_url, timeout=timeout, allow_private_network=allow_private_network, auth=auth)
    except LiveFetchError as exc:
        raise LiveFetchError(exc.status, f"Search page could not be fetched: {exc}") from None
    try:
        example = inspect_example_result(example_result_url, timeout=timeout, allow_private_network=allow_private_network, auth=auth)
    except LiveFetchError as exc:
        raise LiveFetchError(exc.status, f"Example result could not be fetched: {exc}") from None
    # The normal fetch remains authoritative. Browser rendering is a safe
    # fallback for JavaScript DOMs; an unavailable browser does not turn a
    # successfully fetched static page into a network failure.
    if render_mode in {"auto", "browser"} and "html" in search["contentType"]:
        try:
            search = render_live_page(search["url"], timeout=timeout, allow_private_network=allow_private_network)
        except LiveFetchError as exc:
            if render_mode == "browser":
                return {"ok": True, "success": True, "status": search["httpStatus"], "message": str(exc), "detectionStatus": exc.status, "reason": "JavaScript rendering required", "suggestions": {}, "diagnostics": {"fetchCompleted": True, "domParsingCompleted": False, "errorStage": exc.status}}
    if "html" not in search["contentType"]:
        return {"ok": True, "success": True, "status": search["httpStatus"], "message": "Search page was fetched, but it is not HTML.", "detectionStatus": "html-parsing-failed", "reason": "Search page returned structured/non-HTML content.", "suggestions": {}, "diagnostics": {"errorStage": "html-parsing-failed"}}
    tree = TreeBuilder(); tree.feed(search["body"])
    anchors = [node for node in _all_nodes(tree) if node.tag == "a" and (node.attrs.get("href") or node.attrs.get("data-url"))]
    matching = []
    for anchor in anchors:
        href = anchor.attrs.get("href") or anchor.attrs.get("data-url") or ""
        target = urljoin(search["url"], href)
        link_text = anchor.text.lower()
        if _inside_excluded_region(anchor) or any(word in link_text for word in ("login", "sign in", "next", "previous", "privacy")):
            continue
        if _is_similar_result_url(target, example["finalUrl"]) or _matches_example_metadata(target, example):
            matching.append(anchor)
    # A direct CDN example normally will not occur in search HTML: the search
    # card links to a detail page first.  In that case, infer repeated cards
    # from the search DOM and use their visible result links as the first hop.
    # Actual delivery is then verified through a bounded public-link walk.
    fallback_detection: dict[str, Any] | None = None
    if not matching and example.get("kind") == "direct-file":
        fallback_detection = detect_selectors(search["body"])
        container_selector = ((fallback_detection.get("suggestions") or {}).get("resultContainers") or [{}])[0].get("selector")
        if container_selector:
            inferred_containers = select_all(tree.root, [str(container_selector)])
            for container in inferred_containers:
                candidates = [node for node in _descendants(container) if node.tag in {"a", "button"} and _node_urls(node, search["url"]) and not _inside_excluded_region(node)]
                if candidates:
                    matching.append(candidates[0])
    # If the exact example is not present, use its route signature; the same
    # matcher deliberately permits a different final slug/id.
    if not matching:
        return {"ok": True, "success": True, "status": search["httpStatus"], "contentType": search["contentType"], "finalUrl": search["url"], "message": "Search page fetched successfully, but no matching result links were found.", "detectionStatus": "url-pattern-mismatch", "reason": "No matching result links found", "urlPattern": example["pathPattern"], "example": example, "suggestions": {}, "diagnostics": {"searchPageHttpStatus": search["httpStatus"], "exampleResultHttpStatus": example["httpStatus"], "responseSize": len(search["body"].encode("utf-8", "replace")), "fetchCompleted": True, "domParsingCompleted": True, "matchingLinks": 0, "containersDetected": 0, "errorStage": "no-matching-result-links"}}
    containers_ranked, containers = _guided_container_suggestions(tree, matching)
    if fallback_detection and not containers_ranked:
        containers_ranked = list((fallback_detection.get("suggestions") or {}).get("resultContainers") or [])
        if containers_ranked:
            containers = select_all(tree.root, [str(containers_ranked[0].get("selector") or "")])
    if not containers_ranked:
        return {"ok": True, "success": True, "status": search["httpStatus"], "contentType": search["contentType"], "finalUrl": search["url"], "message": "Matching links found, but no repeating result container was found.", "detectionStatus": "no-repeating-result-container", "reason": "No repeating result container found", "urlPattern": example["pathPattern"], "example": example, "suggestions": {}, "diagnostics": {"searchPageHttpStatus": search["httpStatus"], "exampleResultHttpStatus": example["httpStatus"], "fetchCompleted": True, "domParsingCompleted": True, "matchingLinks": len(matching), "containersDetected": 0, "errorStage": "no-repeating-result-container"}}
    matching_ids = {id(node) for node in matching}
    link_items: list[tuple[str, float]] = []
    for container in containers:
        for anchor in _descendants(container):
            if anchor.tag == "a" and id(anchor) in matching_ids:
                selector = _selector_for(anchor)
                coverage = sum(1 for item in containers if select_all(item, [selector])) / max(1, len(containers))
                link_items.append((selector, min(99, 70 + coverage * 25)))
    suggestions = {
        "resultContainers": _with_fallback(containers_ranked, (".result", ".item", "[data-result]")),
        "title": _with_fallback(_field_suggestions(containers, "title"), (".title", "h2", "h3", "[data-title]")),
        "link": _with_fallback(_ranked(link_items), ("a[href]", "a.download", "[data-url]")),
        "thumbnail": _with_fallback(_field_suggestions(containers, "thumbnail"), ("img", ".thumbnail img", "[data-thumbnail]")),
        "quality": _with_fallback(_field_suggestions(containers, "quality"), (".quality", "[data-quality]", ".resolution")),
        "fileSize": _with_fallback(_field_suggestions(containers, "fileSize"), (".size", "[data-size]", ".file-size")),
        "provider": _with_fallback(_field_suggestions(containers, "provider"), (".provider", "[data-provider]", ".source")),
        "pagination": _with_fallback([], (".next", "a[rel=next]", ".pagination")),
        "waitFor": _with_fallback(containers_ranked, (".results", ".result", "[data-result]")),
    }
    cards = []
    for index, container in enumerate(containers[:20]):
        def value(key: str) -> str:
            nodes = select_all(container, [suggestions[key][0]["selector"]])
            if key == "thumbnail": return (nodes[0].attrs.get("src") or nodes[0].attrs.get("data-src") or "") if nodes else ""
            return nodes[0].text if nodes else ""
        link_node = next((node for node in _descendants(container) if id(node) in matching_ids), None)
        cards.append({"index": index + 1, "title": value("title"), "url": urljoin(search["url"], (link_node.attrs.get("href") or link_node.attrs.get("data-url") or "")) if link_node else "", "thumbnail": value("thumbnail"), "quality": value("quality"), "fileSize": value("fileSize"), "provider": value("provider"), "confidence": suggestions["resultContainers"][0]["confidence"], "rejected": []})
    # For a direct-file example, prove at least one visible result-card route
    # reaches the learned CDN shape before activating the source.  We inspect
    # only a few candidates, headers, redirects, and normal public HTML links.
    delivery_pattern = {"hostname": example.get("hostname") or "", "pathPrefix": example.get("pathPrefix") or "/", "fileExtension": example.get("fileExtension") or "", "maxDepth": 2}
    if example.get("kind") == "direct-file":
        for card in cards[:5]:
            delivery = discover_authorized_delivery(card["url"], delivery_pattern, timeout=timeout, allow_private_network=allow_private_network, auth=auth)
            if delivery["found"]:
                card["deliveryUrl"] = delivery["url"]
                card["deliveryTrail"] = delivery["trail"]
    valid_cards = [card for card in cards if card["title"].strip() and card["url"] and (example.get("kind") != "direct-file" or card.get("deliveryUrl"))]
    if not valid_cards:
        delivery_failure = example.get("kind") == "direct-file" and bool(cards)
        reason = "No visible result-card link reached the learned direct-file pattern." if delivery_failure else "Title could not be inferred"
        status = "delivery-chain-not-found" if delivery_failure else "title-could-not-be-inferred"
        return {"ok": True, "success": True, "status": search["httpStatus"], "message": reason, "detectionStatus": status, "reason": reason, "urlPattern": example["pathPattern"], "example": example, "suggestions": suggestions, "cards": cards, "diagnostics": {"fetchCompleted": True, "domParsingCompleted": True, "matchingLinks": len(matching), "containersDetected": len(containers), "validResults": 0, "errorStage": status}}
    source_url = urlparse(search["url"])
    query_keys = ("q", "query", "search", "term", "keyword", "s")
    query = dict(parse_qsl(source_url.query, keep_blank_values=True))
    query_key = next((key for key in query_keys if key in query), "q")
    query[query_key] = "{query}"
    search_template = source_url._replace(query=urlencode(query)).geturl().replace("%7Bquery%7D", "{query}")
    config = {"allowedDomain": source_url.hostname or "", "searchUrlTemplate": search_template, "requestTimeout": max(1, min(60, int(timeout or 12)))}
    config["deliveryPattern"] = delivery_pattern
    for key in REQUIRED + OPTIONAL:
        config[key] = [item["selector"] for item in suggestions.get(key, [])[:5]]
    message = "Direct file endpoint detected. Metadata inspected without downloading the file." if example["kind"] == "direct-file" else "Guided source setup completed."
    return {"ok": True, "success": True, "status": search["httpStatus"], "contentType": search["contentType"], "finalUrl": search["url"], "message": message, "detectionStatus": "working", "urlPattern": example["pathPattern"], "example": example, "config": config, "suggestions": suggestions, "cards": cards, "diagnostics": {"searchPageHttpStatus": search["httpStatus"], "exampleResultHttpStatus": example["httpStatus"], "searchPageFinalUrl": search["url"], "exampleResultFinalUrl": example["finalUrl"], "responseSize": len(search["body"].encode("utf-8", "replace")), "fetchCompleted": True, "domParsingCompleted": True, "matchingLinks": len(matching), "containersDetected": len(containers), "validResults": len(valid_cards), "errorStage": None}}


def _valid_public_url(value: str, allowed_domain: str) -> tuple[bool, str]:
    parsed = urlparse(value)
    if not value: return False, "Missing URL"
    if parsed.scheme not in {"http", "https"}: return False, "Unsupported protocol"
    if not parsed.hostname: return False, "Invalid URL"
    # Public link values may point to a separate authorized delivery host; only
    # the catalog fetch itself is domain-restricted. Admin config owns that trust.
    return True, ""


def parse_html(sample_html: str, config: dict[str, Any], source_id: str, source_name: str, page_url: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    cfg = validate_config(config)
    diagnostics: dict[str, Any] = {"containersMatched": 0, "titlesMatched": 0, "linksMatched": 0, "validResults": 0, "rejectedResults": [], "parserStatus": "parser-mismatch"}
    tree = TreeBuilder(); tree.feed(sample_html)
    containers = select_all(tree.root, cfg["resultContainers"])
    diagnostics["containersMatched"] = len(containers)
    if not containers:
        diagnostics["rejectedResults"].append({"reason": "Parser selector mismatch", "field": "resultContainers"})
        return [], diagnostics
    results: list[dict[str, str]] = []; seen: set[str] = set()
    for index, container in enumerate(containers):
        title_nodes = select_all(container, cfg["title"]); link_nodes = select_all(container, cfg["link"])
        diagnostics["titlesMatched"] += len(title_nodes); diagnostics["linksMatched"] += len(link_nodes)
        title = (title_nodes[0].text if title_nodes else "").strip()
        raw_url = (link_nodes[0].attrs.get("href") or link_nodes[0].attrs.get("data-url") or "") if link_nodes else ""
        public_url = urljoin(page_url, raw_url) if raw_url else ""
        reason = ""
        if not title: reason = "Missing title"
        elif not raw_url: reason = "Missing URL"
        else: reason = _valid_public_url(public_url, cfg["allowedDomain"])[1]
        if reason:
            diagnostics["rejectedResults"].append({"index": index, "reason": reason})
            continue
        if public_url in seen:
            diagnostics["rejectedResults"].append({"index": index, "reason": "Duplicate result"})
            continue
        seen.add(public_url)
        def field(key: str) -> str:
            nodes = select_all(container, cfg[key]); return nodes[0].text.strip() if nodes else ""
        quality, file_size, provider = field("quality"), field("fileSize"), field("provider")
        filename = link_nodes[0].attrs.get("download") or title
        confidence = "high" if quality and file_size and provider else "medium" if quality or file_size else "low"
        results.append({"sourceId": source_id, "title": title, "quality": quality or "Unknown", "filename": filename, "fileSize": file_size or "Unknown", "providerName": provider or source_name, "pageUrl": page_url, "publicUrl": public_url, "sourceBadge": source_name, "confidence": confidence})
    diagnostics["validResults"] = len(results)
    diagnostics["parserStatus"] = "working" if results else "invalid-normalized-output"
    return results, diagnostics


def parse_authorized_response(body: str, content_type: str, config: dict[str, Any], source_id: str, source_name: str, page_url: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if "html" not in content_type.lower():
        return [], {"containersMatched": 0, "titlesMatched": 0, "linksMatched": 0, "validResults": 0, "rejectedResults": [{"reason": "Unsupported response content type"}], "parserStatus": "parser-mismatch"}
    return parse_html(body, config, source_id, source_name, page_url)


def search_authorized_source(source: dict[str, Any], query: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    config = validate_config(source.get("config") or {})
    page_url = config["searchUrlTemplate"].replace("{query}", quote_plus(query))
    diagnostics: dict[str, Any] = {"sourceMatched": True, "httpStatus": None, "contentType": "", "parserExecuted": False, "rawCandidatesFound": 0, "normalizedResults": 0, "rejectedResults": [], "finalRenderedResults": 0, "errorStage": None}
    try:
        request = Request(page_url, headers={"user-agent": "AuthorizedCatalogParser/1.0", "accept": "text/html,application/xhtml+xml"})
        # Redirects are intentionally not followed.  This prevents an allowed
        # catalog URL from becoming an indirect request to another host.
        with build_opener(NoRedirect()).open(request, timeout=config["requestTimeout"]) as response:
            diagnostics["httpStatus"] = response.status; content_type = str(response.headers.get("content-type") or ""); body = response.read(2_000_000).decode("utf-8", "replace")
        diagnostics["contentType"] = content_type; diagnostics["parserExecuted"] = True
        results, parser = parse_authorized_response(body, content_type, config, str(source["id"]), str(source["name"]), page_url)
        diagnostics.update(parser); diagnostics["rawCandidatesFound"] = parser["containersMatched"]; diagnostics["normalizedResults"] = len(results); diagnostics["finalRenderedResults"] = len(results)
        if not results: diagnostics["errorStage"] = "parsing" if not parser["containersMatched"] else "validation"
        return results, diagnostics
    except TimeoutError:
        diagnostics["errorStage"] = "fetching"; diagnostics["parserStatus"] = "timed-out"; return [], diagnostics
    except Exception as exc:
        diagnostics["errorStage"] = "fetching"; diagnostics["parserStatus"] = "access-restricted" if "403" in str(exc) or "401" in str(exc) else "needs-adapter"; diagnostics["rejectedResults"] = [{"reason": str(exc)[:300]}]; return [], diagnostics


def resolve_authorized_result(source: dict[str, Any], result_url: str) -> dict[str, Any]:
    """Resolve one configured catalog result through its learned public path."""
    config = validate_config(source.get("config") or {})
    pattern = config.get("deliveryPattern") or {}
    if not pattern.get("hostname"):
        return {"success": True, "url": result_url, "trail": [result_url], "message": "Result page URL selected."}
    delivery = discover_authorized_delivery(result_url, pattern, timeout=config["requestTimeout"])
    if not delivery["found"]:
        return {"success": False, "url": "", "trail": delivery["trail"], "message": "The configured delivery path was not found from this result page."}
    return {"success": True, "url": delivery["url"], "trail": delivery["trail"], "message": "Authorized delivery URL resolved."}
