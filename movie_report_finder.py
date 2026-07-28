#!/usr/bin/env python3
"""
Simple movie-name to report-link finder.

Given a movie name, this searches BollyFlix, finds matching result pages, extracts
quality-specific download listing links, then follows only redirects/HTML landing
pages to produce report-ready URLs. It does not download movie files.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import html
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus, urlparse, parse_qs

from evidence_link_finder import (
    Link,
    LinkParser,
    content_length_from_range,
    fetch_html,
    final_or_next_url,
    follow_redirects,
)


DEFAULT_SITE = "https://bollyflix.at"
DIRECT_HOST_MARKERS = (
    "video-downloads.googleusercontent.com",
    "instant.busycdn.xyz",
    "cloud-dl.",
    "quick.cloudpaglu",
)
LISTING_HOST_MARKERS = (
    "google",
    "drive",
    "dl.fastdlserver",
    "fxlinks",
)


@dataclass
class Candidate:
    title: str
    url: str
    source: str = ""


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
    content_length: str
    status: str


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def configured_sites() -> list[str]:
    raw_sites = os.environ.get("BOLLYFLIX_SITES", DEFAULT_SITE)
    sites: list[str] = []
    for raw_site in raw_sites.split(","):
        site = raw_site.strip().rstrip("/")
        if not site:
            continue
        if not site.startswith(("http://", "https://")):
            site = f"https://{site}"
        if site not in sites:
            sites.append(site)
    return sites or [DEFAULT_SITE]


def unique_candidates(links: list[Link], query: str, site: str) -> list[Candidate]:
    terms = [term for term in norm(query).split() if term]
    seen: set[str] = set()
    candidates: list[Candidate] = []
    for link in links:
        if not link.href.startswith(site):
            continue
        if "/search/" in link.href or "/movies" in link.href:
            continue
        title = html.unescape(link.text)
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


def clean_result_title(title: str) -> str:
    cleaned = html.unescape(title or "")
    cleaned = re.sub(r"\bdownload\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned.lower()).strip()
    return cleaned


def rank_candidates(candidates: list[Candidate], query: str) -> list[Candidate]:
    query_norm = clean_result_title(query)
    terms = [term for term in query_norm.split() if term]

    def score(indexed: tuple[int, Candidate]) -> tuple[int, int]:
        index, candidate = indexed
        title = html.unescape(candidate.title)
        title_norm = clean_result_title(title)
        value = 0
        if query_norm and query_norm in title_norm:
            value += 60
        if terms and all(term in title_norm for term in terms):
            value += 40
        if title_norm.startswith(query_norm):
            value += 30
        if re.search(r"\bseason\s*0*1\s*(?:-|–|to)\s*\d+\b", title, flags=re.IGNORECASE):
            value += 25
        if re.search(r"\bseason\s*0*1\b", title, flags=re.IGNORECASE):
            value += 10
        if re.search(rf"\b{re.escape(query)}\s*:", title, flags=re.IGNORECASE):
            value -= 20
        value -= min(20, max(0, len(title_norm.split()) - len(terms) - 10))
        return (value, -index)

    return [candidate for _, candidate in sorted(enumerate(candidates), key=score, reverse=True)]


def search_movie_on_site(query: str, limit: int, timeout: int, max_html_bytes: int, site: str) -> list[Candidate]:
    search_url = f"{site.rstrip('/')}/search/{quote_plus(query).replace('+', '%20')}/"
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
        title = html.unescape(re.sub(r"<[^>]+>", " ", match.group(2)))
        title = re.sub(r"\s+", " ", title).strip()
        if not title or url in seen:
            continue
        if terms and not all(term in norm(title) for term in terms):
            continue
        seen.add(url)
        candidates.append(Candidate(title=title, url=url))
        if len(candidates) >= limit * 2:
            break
    parser = LinkParser(search_url)
    parser.feed(page)
    for candidate in unique_candidates(parser.links, query, site.rstrip("/")):
        if candidate.url not in seen:
            seen.add(candidate.url)
            candidates.append(candidate)
    if candidates:
        return rank_candidates(candidates, query)[:limit]
    return rank_candidates(unique_candidates(parser.links, query, site.rstrip("/")), query)[:limit]


def search_movie(query: str, limit: int, timeout: int, max_html_bytes: int) -> list[Candidate]:
    errors: list[str] = []
    collected: list[Candidate] = []
    seen: set[tuple[str, str]] = set()
    for site in configured_sites():
        try:
            candidates = search_movie_on_site(query, limit, timeout, max_html_bytes, site)
        except Exception as exc:
            errors.append(f"{site}: {exc}")
            continue
        for candidate in candidates:
            candidate.source = site.rstrip("/")
            key = (candidate.url, candidate.source)
            if key not in seen:
                seen.add(key)
                collected.append(candidate)
    if collected:
        # Keep results grouped by configured source order so the UI can make
        # it clear which provider returned each title.
        return collected
    if errors:
        raise RuntimeError("; ".join(errors))
    return []


def extract_inner_url(wrapper: str) -> str:
    parsed = urlparse(wrapper)
    values = parse_qs(parsed.query).get("url", [])
    return values[0] if values else ""


def has_direct_marker(*urls: str) -> bool:
    return any(marker in url for url in urls for marker in DIRECT_HOST_MARKERS)


def find_listing_links(page_url: str, quality: str, timeout: int, max_html_bytes: int) -> list[Link]:
    page = fetch_html(page_url, max_html_bytes, timeout)
    parser = LinkParser(page_url)
    parser.feed(page)
    links: list[Link] = []
    for link in parser.links:
        haystack = norm(f"{link.section} {link.text} {link.href}")
        if quality.lower() in haystack and any(marker in haystack for marker in LISTING_HOST_MARKERS):
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
        raw_haystack = f"{link.section} {link.text} {link.href}".lower()
        haystack = norm(raw_haystack)
        href_norm = norm(link.href)
        if "login" in href_norm:
            continue
        if any(term in haystack for term in ["instant", "10gbps", "busycdn"]) or any(
            term in raw_haystack for term in ["dl.fastdlserver", "gdflix"]
        ):
            deep_links.append(link)
    return deep_links


def evidence_for_listing(
    query: str,
    candidate: Candidate,
    listing: Link,
    timeout: int,
    max_hops: int,
    max_html_bytes: int,
) -> list[EvidenceRow]:
    rows: list[EvidenceRow] = []
    hops = follow_redirects(listing.href, max_hops, timeout)
    landing = final_or_next_url(hops) or ""
    deep_links = find_deep_links(landing, listing.href, timeout, max_html_bytes)
    if not deep_links:
        time.sleep(1.2)
        deep_links = find_deep_links(landing, listing.href, timeout, max_html_bytes)
    if not deep_links:
        return [
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
                "",
                "listing found; no instant/direct link extracted",
            )
        ]
    for deep in deep_links:
        deep_hops = follow_redirects(deep.href, max_hops, timeout)
        final = final_or_next_url(deep_hops) or ""
        final_inner_url = extract_inner_url(final)
        content_length = next((hop.content_length for hop in reversed(deep_hops) if hop.content_length), "")
        if not content_length:
            content_length = content_length_from_range(final_inner_url or final, timeout)
        section_text = " ".join(part for part in (listing.section, listing.text, deep.section, deep.text) if part)
        rows.append(
            EvidenceRow(
                query,
                candidate.title,
                candidate.url,
                section_text,
                listing.href,
                landing,
                deep.href,
                final,
                final_inner_url,
                content_length,
                "ok",
            )
        )
    return rows


def build_evidence(
    query: str,
    candidate: Candidate,
    quality: str,
    timeout: int,
    max_hops: int,
    max_html_bytes: int,
    first_only: bool = False,
    stop_after_direct: bool = False,
    max_direct_links: int = 0,
    max_listing_workers: int = 0,
    listing_delay: float = 0,
) -> list[EvidenceRow]:
    rows: list[EvidenceRow] = []
    try:
        listing_links = find_listing_links(candidate.url, quality, timeout, max_html_bytes)
    except Exception as exc:
        return [
            EvidenceRow(query, candidate.title, candidate.url, "", "", "", "", "", "", "", f"page fetch failed: {exc}")
        ]
    if not listing_links:
        return [
            EvidenceRow(query, candidate.title, candidate.url, "", "", "", "", "", "", "", f"no {quality} listing link found")
        ]

    direct_count = 0
    if len(listing_links) > 1 and (max_direct_links > 1 or max_listing_workers > 1):
        worker_count = max_listing_workers or max_direct_links
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=min(worker_count, len(listing_links), 8))
        try:
            futures = [
                executor.submit(evidence_for_listing, query, candidate, listing, timeout, max_hops, max_html_bytes)
                for listing in listing_links
            ]
            for future in concurrent.futures.as_completed(futures):
                try:
                    listing_rows = future.result()
                except Exception:
                    continue
                rows.extend(listing_rows)
                direct_count += sum(
                    1
                    for row in listing_rows
                    if has_direct_marker(row.final_inner_url, row.final_wrapper, row.instant_link)
                )
                if max_direct_links and direct_count >= max_direct_links:
                    return rows
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        return rows

    for index, listing in enumerate(listing_links):
        if index and listing_delay > 0:
            time.sleep(listing_delay)
        listing_rows = evidence_for_listing(query, candidate, listing, timeout, max_hops, max_html_bytes)
        rows.extend(listing_rows)
        for row in listing_rows:
            if stop_after_direct and has_direct_marker(row.final_inner_url, row.final_wrapper, row.instant_link):
                return rows
            if max_direct_links and has_direct_marker(row.final_inner_url, row.final_wrapper, row.instant_link):
                direct_count += 1
                if direct_count >= max_direct_links:
                    return rows
            if first_only and (row.final_inner_url or row.final_wrapper):
                return rows
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
        "content_length",
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
        print(f"Content-Length: {row.content_length}")
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
            all_rows.append(EvidenceRow(query, "", "", "", "", "", "", "", "", "", f"search failed: {exc}"))
            continue
        if args.show_results:
            if args.output == "pretty":
                print(f"\nSearch: {query}")
                for index, candidate in enumerate(candidates, 1):
                    print(f"{index}. {candidate.title}")
                    print(f"   {candidate.url}")
            continue
        if not candidates:
            all_rows.append(EvidenceRow(query, "", "", "", "", "", "", "", "", "", "no search result found"))
            continue
        pick_index = max(args.pick - 1, 0)
        if pick_index >= len(candidates):
            all_rows.append(EvidenceRow(query, "", "", "", "", "", "", "", "", "", f"pick {args.pick} unavailable"))
            continue
        all_rows.extend(
            build_evidence(query, candidates[pick_index], args.quality, args.timeout, args.max_hops, args.max_html_bytes)
        )

    if not args.show_results:
        print_rows(all_rows, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
