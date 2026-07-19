#!/usr/bin/env python3
"""
Easy interactive wrapper.

Run:
  ./find.py

Then type the movie name. The script shows search results, asks which one to
use, and prints the 1080p report links.
"""

from __future__ import annotations

import sys
import shutil
import subprocess
import argparse

from movie_report_finder import build_evidence, print_rows, search_movie


DIRECT_HOST_MARKERS = (
    "video-downloads.googleusercontent.com",
    "instant.busycdn.xyz",
)

DEFAULT_QUALITIES = ("480p", "720p", "1080p", "2160p", "4k")


def human_size(content_length: str) -> str:
    try:
        size = int(content_length)
    except (TypeError, ValueError):
        return "size unknown"
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.2f} {units[unit_index]}"


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def main() -> int:
    parser = argparse.ArgumentParser(description="Easy interactive report-link finder.")
    parser.add_argument("query", nargs="*", help="Movie/file name to search")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Stop after the first usable candidate. Faster, but may miss a later direct link.",
    )
    parser.add_argument(
        "-q",
        "--quality",
        default="",
        help="Quality to scan, for example 480p, 720p, 1080p, or all.",
    )
    args = parser.parse_args()

    query = " ".join(args.query).strip()
    if not query:
        query = ask("Movie name")
    if not query:
        print("Movie name required.")
        return 1

    print(f"\nSearching: {query}\n")
    candidates = search_movie(query, limit=10, timeout=20, max_html_bytes=2_000_000)
    if not candidates:
        print("No search results found.")
        return 1

    for index, candidate in enumerate(candidates, 1):
        print(f"{index}. {candidate.title}")
        print(f"   {candidate.url}")

    pick_raw = ask("\nWhich result number", "1")
    try:
        pick = int(pick_raw)
    except ValueError:
        print("Invalid number.")
        return 1
    if pick < 1 or pick > len(candidates):
        print("Result number out of range.")
        return 1

    quality = args.quality.strip() or ask("Quality (480p/720p/1080p/all)", "1080p")
    qualities = DEFAULT_QUALITIES if quality.lower() in {"all", "*"} else (quality,)

    print("\nFinding report links. No file body will be downloaded...\n")
    final_links: list[tuple[str, str, str]] = []
    debug_rows = []
    for item_quality in qualities:
        rows = build_evidence(
            query=query,
            candidate=candidates[pick - 1],
            quality=item_quality,
            timeout=20,
            max_hops=10,
            max_html_bytes=2_000_000,
            first_only=args.fast,
        )
        debug_rows.extend(rows)
        best = ""
        for row in rows:
            row_candidates = [row.final_inner_url, row.final_wrapper, row.instant_link]
            best = next((url for url in row_candidates if any(marker in url for marker in DIRECT_HOST_MARKERS)), "")
            if best:
                size_label = human_size(row.content_length)
                break
        if best:
            final_links.append((item_quality, size_label, best))

    if final_links:
        print("FINAL LINKS:")
        for item_quality, size_label, link in final_links:
            print(f"\n[{item_quality}] {size_label}")
            print(link)
        print()
        clipboard_text = "\n\n".join(
            f"[{item_quality}] {size_label}\n{link}" for item_quality, size_label, link in final_links
        )
        if shutil.which("termux-clipboard-set"):
            subprocess.run(["termux-clipboard-set"], input=clipboard_text.encode("utf-8"), check=False)
            print("Copied final links to Termux clipboard.")
        else:
            print("Tip: install clipboard support with: pkg install termux-api")
    else:
        print("Direct link not found automatically. Debug details:\n")
        print_rows(debug_rows, "pretty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
