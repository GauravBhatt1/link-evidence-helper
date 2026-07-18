#!/usr/bin/env python3
"""
Collect report-ready link evidence from a web page without downloading files.

This tool fetches ordinary HTML pages, extracts links, and follows redirect
chains with HEAD requests. It is intended for reporting/takedown evidence, not
for downloading or mirroring content.
"""

from __future__ import annotations

import argparse
import html
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, build_opener
from urllib.request import HTTPRedirectHandler


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 "
    "evidence-link-finder/1.0"
)
DEFAULT_MAX_HTML_BYTES = 2_000_000
DEFAULT_TIMEOUT = 20


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class Link:
    href: str
    text: str
    section: str


@dataclass
class Hop:
    url: str
    status: int | None
    location: str | None
    content_type: str | None
    content_length: str | None
    error: str | None = None


class LinkParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[Link] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []
        self._heading_tag: str | None = None
        self._heading_text: list[str] = []
        self._section = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_tag = tag
            self._heading_text = []
        if tag == "a" and attrs_dict.get("href"):
            self._current_href = urljoin(self.base_url, attrs_dict["href"] or "")
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._heading_tag:
            self._heading_text.append(data)
        if self._current_href:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._heading_tag == tag:
            text = clean_text(" ".join(self._heading_text))
            if text:
                self._section = text
            self._heading_tag = None
            self._heading_text = []
        if tag == "a" and self._current_href:
            text = clean_text(" ".join(self._current_text))
            self.links.append(Link(self._current_href, text, self._section))
            self._current_href = None
            self._current_text = []


def clean_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def request_headers(url: str, method: str = "HEAD", timeout: int = DEFAULT_TIMEOUT):
    opener = build_opener(NoRedirect)
    req = Request(url, method=method, headers={"User-Agent": USER_AGENT})
    return opener.open(req, timeout=timeout)


def fetch_html(url: str, max_bytes: int, timeout: int) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with build_opener().open(req, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise RuntimeError(f"HTML exceeded max byte limit ({max_bytes})")
            charset = response.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
    except Exception as exc:
        curl = subprocess.run(
            [
                "curl",
                "-Ls",
                "--max-time",
                str(timeout),
                "--max-filesize",
                str(max_bytes),
                "-A",
                USER_AGENT,
                url,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if curl.returncode != 0:
            detail = curl.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"{exc}; curl fallback failed: {detail}") from exc
        if len(curl.stdout) > max_bytes:
            raise RuntimeError(f"HTML exceeded max byte limit ({max_bytes})") from exc
        return curl.stdout.decode("utf-8", errors="replace")


def follow_redirects(url: str, max_hops: int, timeout: int) -> list[Hop]:
    hops: list[Hop] = []
    current = url
    for _ in range(max_hops):
        try:
            response = request_headers(current, "HEAD", timeout)
            status = getattr(response, "status", None)
            headers = response.headers
            hops.append(
                Hop(
                    url=current,
                    status=status,
                    location=None,
                    content_type=headers.get("content-type"),
                    content_length=headers.get("content-length"),
                )
            )
            break
        except HTTPError as exc:
            headers = exc.headers
            location = headers.get("location")
            hops.append(
                Hop(
                    url=current,
                    status=exc.code,
                    location=urljoin(current, location) if location else None,
                    content_type=headers.get("content-type"),
                    content_length=headers.get("content-length"),
                )
            )
            if exc.code in {301, 302, 303, 307, 308} and location:
                current = urljoin(current, location)
                continue
            break
        except URLError as exc:
            hops.append(Hop(current, None, None, None, None, str(exc.reason)))
            break
    curl_hops = follow_redirects_with_curl(url, max_hops, timeout)
    if curl_hops and len(curl_hops) > len(hops):
        return curl_hops
    return hops


def follow_redirects_with_curl(url: str, max_hops: int, timeout: int) -> list[Hop]:
    curl = subprocess.run(
        [
            "curl",
            "-I",
            "-L",
            "--max-redirs",
            str(max_hops),
            "--max-time",
            str(timeout),
            "-A",
            USER_AGENT,
            url,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if curl.returncode != 0:
        return []
    blocks = re.split(r"\r?\n\r?\n", curl.stdout.decode("utf-8", errors="replace").strip())
    hops: list[Hop] = []
    current = url
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines or not lines[0].startswith("HTTP/"):
            continue
        status_match = re.search(r"\s(\d{3})\s", lines[0])
        status = int(status_match.group(1)) if status_match else None
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.lower()] = value.strip()
        location = headers.get("location")
        absolute_location = urljoin(current, location) if location else None
        hops.append(
            Hop(
                url=current,
                status=status,
                location=absolute_location,
                content_type=headers.get("content-type"),
                content_length=headers.get("content-length"),
            )
        )
        if absolute_location:
            current = absolute_location
    return hops


def score_link(link: Link, terms: Iterable[str]) -> bool:
    haystack = f"{link.section} {link.text} {link.href}".lower()
    return all(term.lower() in haystack for term in terms)


def print_hops(hops: list[Hop]) -> None:
    for idx, hop in enumerate(hops, 1):
        print(f"  Hop {idx}: {hop.status or 'ERR'} {hop.url}")
        if hop.location:
            print(f"    Location: {hop.location}")
        if hop.content_type:
            print(f"    Content-Type: {hop.content_type}")
        if hop.content_length:
            print(f"    Content-Length: {hop.content_length}")
        if hop.error:
            print(f"    Error: {hop.error}")
    if hops:
        final = hops[-1].location or hops[-1].url
        print(f"  Final/next URL: {final}")


def final_or_next_url(hops: list[Hop]) -> str | None:
    if not hops:
        return None
    return hops[-1].location or hops[-1].url


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract report-ready page links and redirect chains without downloading files."
    )
    parser.add_argument("url", help="Page or link URL to inspect")
    parser.add_argument(
        "--term",
        action="append",
        default=[],
        help="Filter extracted links by term. Can be repeated, e.g. --term 1080p --term Google",
    )
    parser.add_argument("--all", action="store_true", help="Show all extracted links")
    parser.add_argument("--follow", action="store_true", help="Follow each matched link with HEAD requests")
    parser.add_argument(
        "--deep",
        action="store_true",
        help="After following a matched link, fetch the landing HTML and extract Instant/Direct links",
    )
    parser.add_argument(
        "--deep-term",
        action="append",
        default=[],
        help="Term for --deep extracted links. Defaults to instant/direct/download/drive.",
    )
    parser.add_argument("--redirect-only", action="store_true", help="Treat URL as a direct link and print only its redirect chain")
    parser.add_argument("--max-links", type=int, default=20)
    parser.add_argument("--max-hops", type=int, default=10)
    parser.add_argument("--max-html-bytes", type=int, default=DEFAULT_MAX_HTML_BYTES)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()

    print(f"Timestamp UTC: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    print("Mode: evidence only; no file body is downloaded")
    print(f"Input: {args.url}")

    if args.redirect_only:
        print_hops(follow_redirects(args.url, args.max_hops, args.timeout))
        return 0

    try:
        page = fetch_html(args.url, args.max_html_bytes, args.timeout)
    except Exception as exc:
        print(f"Fetch failed: {exc}", file=sys.stderr)
        return 2

    parser_obj = LinkParser(args.url)
    parser_obj.feed(page)
    terms = args.term if args.term else []
    links = parser_obj.links if args.all or not terms else [
        link for link in parser_obj.links if score_link(link, terms)
    ]

    if not links:
        print("No matching links found.")
        return 1

    for index, link in enumerate(links[: args.max_links], 1):
        print()
        print(f"[{index}] Section: {link.section or '(none)'}")
        print(f"    Text: {link.text or '(no text)'}")
        print(f"    URL: {link.href}")
        hops: list[Hop] = []
        if args.follow:
            hops = follow_redirects(link.href, args.max_hops, args.timeout)
            print_hops(hops)
        if args.deep:
            landing = final_or_next_url(hops) if hops else link.href
            if not landing:
                continue
            try:
                landing_html = fetch_html(landing, args.max_html_bytes, args.timeout)
            except Exception as exc:
                print(f"  Deep fetch failed for {landing}: {exc}")
                if landing != link.href:
                    try:
                        print(f"  Deep fetch retry from original matched URL: {link.href}")
                        landing_html = fetch_html(link.href, args.max_html_bytes, args.timeout)
                    except Exception as retry_exc:
                        print(f"  Deep fetch retry failed: {retry_exc}")
                        continue
                else:
                    continue
            deep_parser = LinkParser(landing)
            deep_parser.feed(landing_html)
            deep_terms = args.deep_term or ["instant", "direct", "download", "drive"]
            deep_links = [
                item
                for item in deep_parser.links
                if any(score_link(item, [term]) for term in deep_terms)
            ]
            if deep_links:
                print("  Deep links:")
            else:
                print("  Deep links: none matched")
            for deep_index, deep_link in enumerate(deep_links[: args.max_links], 1):
                print(f"    [{deep_index}] Section: {deep_link.section or '(none)'}")
                print(f"        Text: {deep_link.text or '(no text)'}")
                print(f"        URL: {deep_link.href}")
                deep_hops = follow_redirects(deep_link.href, args.max_hops, args.timeout)
                print_hops(deep_hops)

    if len(links) > args.max_links:
        print(f"\n{len(links) - args.max_links} more links hidden by --max-links.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
