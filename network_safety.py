"""Bounded, SSRF-safe HTTP inspection helpers (never download media bodies)."""
from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Mapping
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener

MAX_HTML_BYTES = 1_500_000
DEFAULT_TIMEOUT = 15
REDIRECT_CODES = {301, 302, 303, 307, 308}

class UnsafeUrl(ValueError): pass

def _public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)

def validate_public_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise UnsafeUrl("Only public http(s) URLs are allowed")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "metadata.google.internal"} or host.endswith(".localhost"):
        raise UnsafeUrl("Local or metadata hosts are blocked")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise UnsafeUrl(f"DNS lookup failed: {exc}") from exc
    if not addresses or any(not _public_ip(address) for address in addresses):
        raise UnsafeUrl("Private, loopback, link-local, or reserved network target blocked")
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.params, parsed.query, ""))

def redact_url(url: str) -> str:
    """Keep useful path/host diagnostics without exposing signed query values."""
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "[redacted]" if parsed.query else "", ""))

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None

@dataclass
class InspectionResponse:
    url: str; status: int | None; location: str | None; content_type: str; content_length: str; headers: Mapping[str, str]; error: str = ""

class SafeSession:
    """One cookie-preserving session. Every hop is DNS revalidated before request."""
    def __init__(self, timeout: int = DEFAULT_TIMEOUT, max_html_bytes: int = MAX_HTML_BYTES):
        self.timeout, self.max_html_bytes = min(max(timeout, 1), 30), min(max_html_bytes, MAX_HTML_BYTES)
        self.cookies = CookieJar()
        # Some public file-share pages reject unknown automation identifiers
        # before serving their ordinary HTML.  Use a conventional browser
        # identification for the same GET/HEAD-only navigation a user makes;
        # this does not alter the explicit CAPTCHA/login blocking policy.
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.2",
        }
        self._opener = build_opener(_NoRedirect(), HTTPCookieProcessor(self.cookies))

    def inspect(self, url: str, method: str = "HEAD", referer: str = "") -> InspectionResponse:
        safe = validate_public_url(url)
        headers = dict(self.headers)
        if referer and urlparse(referer).hostname == urlparse(safe).hostname: headers["Referer"] = redact_url(referer)
        try:
            handle = self._opener.open(Request(safe, method=method, headers=headers), timeout=self.timeout)
            status, response_headers = getattr(handle, "status", 200), handle.headers
            handle.close()
        except HTTPError as exc:
            status, response_headers = exc.code, exc.headers
        except Exception as exc:
            return InspectionResponse(redact_url(safe), None, None, "", "", {}, str(exc))
        location = response_headers.get("location")
        absolute = urljoin(safe, location) if location else None
        if absolute:
            try: validate_public_url(absolute)
            except UnsafeUrl as exc: return InspectionResponse(redact_url(safe), status, None, response_headers.get("content-type", ""), response_headers.get("content-length", ""), dict(response_headers.items()), f"Blocked redirect: {exc}")
        return InspectionResponse(redact_url(safe), status, redact_url(absolute) if absolute else None, response_headers.get("content-type", ""), response_headers.get("content-length", ""), dict(response_headers.items()))

    def fetch_html(self, url: str, referer: str = "") -> tuple[str, InspectionResponse]:
        current = url
        for _ in range(6):
            safe = validate_public_url(current); headers = dict(self.headers)
            if referer and urlparse(referer).hostname == urlparse(safe).hostname: headers["Referer"] = redact_url(referer)
            try:
                handle = self._opener.open(Request(safe, headers=headers), timeout=self.timeout)
                content_type = handle.headers.get("content-type", "")
                if "html" not in content_type.lower() and content_type: handle.close(); raise ValueError("Response is not HTML")
                raw = handle.read(self.max_html_bytes + 1); status = getattr(handle, "status", 200); response_headers = handle.headers; handle.close()
                if len(raw) > self.max_html_bytes: raise ValueError("HTML byte limit exceeded")
                charset = response_headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace"), InspectionResponse(redact_url(safe), status, None, content_type, response_headers.get("content-length", ""), dict(response_headers.items()))
            except HTTPError as exc:
                location = exc.headers.get("location")
                if exc.code in REDIRECT_CODES and location:
                    current = validate_public_url(urljoin(safe, location)); continue
                return "", InspectionResponse(redact_url(safe), exc.code, None, exc.headers.get("content-type", ""), exc.headers.get("content-length", ""), dict(exc.headers.items()), str(exc))
            except Exception as exc:
                return "", InspectionResponse(redact_url(safe), None, None, "", "", {}, str(exc))
        return "", InspectionResponse(redact_url(current), None, None, "", "", {}, "Too many HTML redirects")

    def fetch_html_once(self, url: str, referer: str = "") -> tuple[str, InspectionResponse, str | None]:
        """Fetch one bounded HTML response without following redirects.

        The returned third value is an internally usable, DNS-validated next
        URL.  Diagnostics continue to use the redacted ``InspectionResponse``
        value so signed query strings never reach callers or logs.
        """
        safe = validate_public_url(url)
        headers = dict(self.headers)
        if referer and urlparse(referer).hostname == urlparse(safe).hostname:
            headers["Referer"] = redact_url(referer)
        try:
            handle = self._opener.open(Request(safe, headers=headers), timeout=self.timeout)
            content_type = handle.headers.get("content-type", "")
            status, response_headers = getattr(handle, "status", 200), handle.headers
            if "html" not in content_type.lower() and content_type:
                handle.close()
                return "", InspectionResponse(redact_url(safe), status, None, content_type, response_headers.get("content-length", ""), dict(response_headers.items()), "Response is not HTML"), None
            raw = handle.read(self.max_html_bytes + 1)
            handle.close()
            if len(raw) > self.max_html_bytes:
                return "", InspectionResponse(redact_url(safe), status, None, content_type, response_headers.get("content-length", ""), dict(response_headers.items()), "HTML byte limit exceeded"), None
            return raw.decode(response_headers.get_content_charset() or "utf-8", errors="replace"), InspectionResponse(redact_url(safe), status, None, content_type, response_headers.get("content-length", ""), dict(response_headers.items())), None
        except HTTPError as exc:
            headers_map = dict(exc.headers.items())
            raw_location = exc.headers.get("location")
            next_url: str | None = None
            error = str(exc)
            if exc.code in REDIRECT_CODES and raw_location:
                try:
                    next_url = validate_public_url(urljoin(safe, raw_location))
                    error = ""
                except UnsafeUrl as blocked:
                    error = f"Blocked redirect: {blocked}"
            return "", InspectionResponse(redact_url(safe), exc.code, redact_url(next_url) if next_url else None, exc.headers.get("content-type", ""), exc.headers.get("content-length", ""), headers_map, error), next_url
        except Exception as exc:
            return "", InspectionResponse(redact_url(safe), None, None, "", "", {}, str(exc)), None

    def fetch_json(self, url: str, referer: str = "") -> tuple[object | None, InspectionResponse]:
        """Fetch a bounded public JSON document; never follows media links or bodies.

        A configured public referer is allowed for ordinary cross-origin search
        APIs.  Cookies remain governed by the cookie jar's normal domain rules.
        """
        try:
            safe = validate_public_url(url)
            headers = dict(self.headers)
            if referer:
                headers["Referer"] = redact_url(validate_public_url(referer))
            handle = self._opener.open(Request(safe, headers=headers), timeout=self.timeout)
            content_type = handle.headers.get("content-type", "")
            if "json" not in content_type.lower():
                handle.close()
                raise ValueError("Response is not JSON")
            raw = handle.read(self.max_html_bytes + 1)
            status, response_headers = getattr(handle, "status", 200), handle.headers
            handle.close()
            if len(raw) > self.max_html_bytes:
                raise ValueError("JSON byte limit exceeded")
            charset = response_headers.get_content_charset() or "utf-8"
            return json.loads(raw.decode(charset, errors="replace")), InspectionResponse(
                redact_url(safe), status, None, content_type,
                response_headers.get("content-length", ""), dict(response_headers.items())
            )
        except HTTPError as exc:
            return None, InspectionResponse(redact_url(url), exc.code, None, exc.headers.get("content-type", ""), exc.headers.get("content-length", ""), dict(exc.headers.items()), str(exc))
        except Exception as exc:
            return None, InspectionResponse(redact_url(url), None, None, "", "", {}, str(exc))

    def redirect_chain(self, url: str, max_hops: int = 8) -> list[InspectionResponse]:
        chain, current, seen = [], url, set()
        for _ in range(min(max(max_hops, 1), 10)):
            if current in seen: break
            seen.add(current); hop = self.inspect(current); chain.append(hop)
            if hop.status not in REDIRECT_CODES or not hop.location or hop.error: break
            # location is redacted: reconstruct only when no query was present; signed redirects terminate safely.
            if "[redacted]" in hop.location: break
            current = hop.location
        return chain
