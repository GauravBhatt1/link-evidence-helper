#!/usr/bin/env python3
"""
Simple movie-name to report-link finder.

Given a movie name, this searches BollyFlix, finds matching result pages, extracts
quality-specific download listing links, then follows only redirects/HTML landing
pages to produce report-ready URLs. It does not download movie files.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus, urlparse, parse_qs

from evidence_link_finder import (
    Link,
    LinkParser,
    fetch_html,
    final_or_next_url,
    follow_redirects,
)


DEFAULT_SITE = "https://bollyflix.at"


@dataclass
class Candidate:
    title: str
    url: str


@dataclass
class EvidenceRow:
    query: str
    title: str
    source_page: str
    section: str
    listing_link: str
    landing_page: str
    instant_link: str
    final_wrapper: str
    final_inner_url: str
    status: str


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def unique_candidates(links: list[Link], query: str) -> list[Candidate]:
    terms = [term for term in norm(query).split() if term]
    seen: set[str] = set()
    candidates: list[Candidate] = []
    for link in links:
        if not link.href.startswith(DEFAULT_SITE):
            continue
        if "/search/" in link.href or "/movies" in link.href:
            continue
        title = link.text or link.section
        title_norm = norm(title)
        if not title or not title_norm.startswith("download "):
            continue
        if terms and not all(term in title_norm for term in terms):
            continue
        if link.href in seen:
            continue
        seen.add(link.href)
        candidates.append(Candidate(title=title, url=link.href))
    return candidates


def search_movie(query: str, limit: int, timeout: int, max_html_bytes: int) -> list[Candidate]:
    search_url = f"{DEFAULT_SITE}/search/{quote_plus(query).replace('+', '%20')}/"
    page = fetch_html(search_url, max_html_bytes, timeout)
    terms = [term for term in norm(query).split() if term]
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for match in re.finditer(
        r'<h2[^>]*class="[^"]*\btitle\b[^"]*"[^>]*>\s*'
        r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>',
        page,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        url = match.group(1)
        title = re.sub(r"<[^>]+>", " ", match.group(2))
        title = re.sub(r"\s+", " ", title).strip()
        if not title or url in seen:
            continue
        if terms and not all(term in norm(title) for term in terms):
            continue
        seen.add(url)
        candidates.append(Candidate(title=title, url=url))
        if len(candidates) >= limit:
            break
    if candidates:
        return candidates
    parser = LinkParser(search_url)
    parser.feed(page)
    return unique_candidates(parser.links, query)[:limit]


def extract_inner_url(wrapper: str) -> str:
    parsed = urlparse(wrapper)
    values = parse_qs(parsed.query).get("url", [])
    return values[0] if values else ""


def find_listing_links(page_url: str, quality: str, timeout: int, max_html_bytes: int) -> list[Link]:
    page = fetch_html(page_url, max_html_bytes, timeout)
    parser = LinkParser(page_url)
    parser.feed(page)
    links: list[Link] = []
    for link in parser.links:
        haystack = norm(f"{link.section} {link.text} {link.href}")
        if quality.lower() in haystack and ("google" in haystack or "drive" in haystack or "dl.fastdlserver" in haystack):
            if link.href.startswith("http"):
                links.append(link)
    return links


def find_deep_links(landing_url: str, original_url: str, timeout: int, max_html_bytes: int) -> list[Link]:
    html = ""
    for candidate in [landing_url, original_url]:
        if not candidate:
            continue
        try:
            html = fetch_html(candidate, max_html_bytes, timeout)
            landing_url = candidate
            break
        except Exception:
            continue
    if not html:
        return []
    parser = LinkParser(landing_url)
    parser.feed(html)
    deep_links: list[Link] = []
    for link in parser.links:
        haystack = norm(f"{link.section} {link.text} {link.href}")
        if any(term in haystack for term in ["instant", "direct download", "10gbps", "busycdn"]):
            deep_links.append(link)
    return deep_links


def build_evidence(query: str, candidate: Candidate, quality: str, timeout: int, max_hops: int, max_html_bytes: int) -> list[EvidenceRow]:
    rows: list[EvidenceRow] = []
    try:
        listing_links = find_listing_links(candidate.url, quality, timeout, max_html_bytes)
    except Exception as exc:
        return [
            EvidenceRow(query, candidate.title, candidate.url, "", "", "", "", "", "", f"page fetch failed: {exc}")
        ]
    if not listing_links:
        return [
            EvidenceRow(query, candidate.title, candidate.url, "", "", "", "", "", "", f"no {quality} listing link found")
        ]

    for listing in listing_links:
        hops = follow_redirects(listing.href, max_hops, timeout)
        landing = final_or_next_url(hops) or ""
        deep_links = find_deep_links(landing, listing.href, timeout, max_html_bytes)
        if not deep_links:
            rows.append(
                EvidenceRow(
                    query,
                    candidate.title,
                    candidate.url,
                    listing.section,
                    listing.href,
                    landing,
                    "",
                    "",
                    "",
                    "listing found; no instant/direct link extracted",
                )
            )
            continue
        for deep in deep_links:
            deep_hops = follow_redirects(deep.href, max_hops, timeout)
            final = final_or_next_url(deep_hops) or ""
            rows.append(
                EvidenceRow(
                    query,
                    candidate.title,
                    candidate.url,
                    listing.section,
                    listing.href,
                    landing,
                    deep.href,
                    final,
                    extract_inner_url(final),
                    "ok",
                )
            )
    return rows


def print_rows(rows: list[EvidenceRow], output: str) -> None:
    fields = [
        "query",
        "title",
        "source_page",
        "section",
        "listing_link",
        "landing_page",
        "instant_link",
        "final_wrapper",
        "final_inner_url",
        "status",
    ]
    if output == "tsv":
        writer = csv.DictWriter(sys.stdout, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)
        return
    if output == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)
        return
    for row in rows:
        print()
        print(f"Query: {row.query}")
        print(f"Title: {row.title}")
        print(f"Source page: {row.source_page}")
        print(f"Section: {row.section}")
        print(f"Listing link: {row.listing_link}")
        print(f"Landing page: {row.landing_page}")
        print(f"Instant link: {row.instant_link}")
        print(f"Final wrapper: {row.final_wrapper}")
        print(f"Final inner URL: {row.final_inner_url}")
        print(f"Status: {row.status}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Search movie name and extract report-ready links.")
    parser.add_argument("query", nargs="?", help="Movie name to search")
    parser.add_argument("--batch", help="Text file with one movie name per line")
    parser.add_argument("--quality", default="1080p")
    parser.add_argument("--pick", type=int, default=1, help="Search result number to use, 1-based")
    parser.add_argument("--show-results", action="store_true", help="Only show matching search results")
    parser.add_argument("--search-limit", type=int, default=10)
    parser.add_argument("--output", choices=["pretty", "csv", "tsv"], default="pretty")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--max-hops", type=int, default=10)
    parser.add_argument("--max-html-bytes", type=int, default=2_000_000)
    args = parser.parse_args()

    queries: list[str] = []
    if args.batch:
        queries.extend(
            line.strip()
            for line in Path(args.batch).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    if args.query:
        queries.append(args.query)
    if not queries:
        parser.error("provide a movie name or --batch file")

    if args.output == "pretty":
        print(f"Timestamp UTC: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
        print("Mode: evidence only; no file body is downloaded")

    all_rows: list[EvidenceRow] = []
    for query in queries:
        try:
            candidates = search_movie(query, args.search_limit, args.timeout, args.max_html_bytes)
        except Exception as exc:
            all_rows.append(EvidenceRow(query, "", "", "", "", "", "", "", "", f"search failed: {exc}"))
            continue
        if args.show_results:
            if args.output == "pretty":
                print(f"\nSearch: {query}")
                for index, candidate in enumerate(candidates, 1):
                    print(f"{index}. {candidate.title}")
                    print(f"   {candidate.url}")
            continue
        if not candidates:
            all_rows.append(EvidenceRow(query, "", "", "", "", "", "", "", "", "no search result found"))
            continue
        pick_index = max(args.pick - 1, 0)
        if pick_index >= len(candidates):
            all_rows.append(EvidenceRow(query, "", "", "", "", "", "", "", "", f"pick {args.pick} unavailable"))
            continue
        all_rows.extend(
            build_evidence(query, candidates[pick_index], args.quality, args.timeout, args.max_hops, args.max_html_bytes)
        )

    if not args.show_results:
        print_rows(all_rows, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
