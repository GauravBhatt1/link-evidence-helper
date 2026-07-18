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

from movie_report_finder import build_evidence, print_rows, search_movie


DIRECT_HOST_MARKERS = (
    "video-downloads.googleusercontent.com",
    "instant.busycdn.xyz",
)


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def main() -> int:
    query = " ".join(sys.argv[1:]).strip()
    if not query:
        query = ask("Movie name")
    if not query:
        print("Movie name required.")
        return 1

    quality = "1080p"
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

    quality = ask("Quality", quality)
    print("\nFinding report links. No file body will be downloaded...\n")
    rows = build_evidence(
        query=query,
        candidate=candidates[pick - 1],
        quality=quality,
        timeout=20,
        max_hops=10,
        max_html_bytes=2_000_000,
        first_only=True,
    )
    best = ""
    for row in rows:
        candidates = [row.final_inner_url, row.final_wrapper, row.instant_link]
        best = next((url for url in candidates if any(marker in url for marker in DIRECT_HOST_MARKERS)), "")
        if best:
            break
    if best:
        print("FINAL LINK:")
        print(best)
        print()
        if shutil.which("termux-clipboard-set"):
            subprocess.run(["termux-clipboard-set"], input=best.encode("utf-8"), check=False)
            print("Copied to Termux clipboard.")
        else:
            print("Tip: install clipboard support with: pkg install termux-api")
    else:
        print("Direct link not found automatically. Debug details:\n")
        print_rows(rows, "pretty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
