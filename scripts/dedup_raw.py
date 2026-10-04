#!/usr/bin/env python3
"""
Filter scraped Apify posts against history of already-evaluated post URLs.

Reads data/seen/*.json (excluding today's file), each file containing a flat
JSON array of post_url strings — every post the parser has ever evaluated,
whether kept as an album or skipped as noise. Filters data/raw/{date}-apify.json
keeping only posts whose `url` is not in the seen set. Also dedups within the
input file itself.

Output: data/raw/{date}-apify-new.json — input for Claude's parse step.

Usage:
    python scripts/dedup_raw.py                    # Today's date
    python scripts/dedup_raw.py --date 2026-05-04
    python scripts/dedup_raw.py --input path.json  # Explicit input
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_DIR / "data" / "raw"
SEEN_DIR = PROJECT_DIR / "data" / "seen"


def collect_seen_urls(exclude_date: str) -> set[str]:
    """Read every seen JSON file except today's, return union of all URLs."""
    seen: set[str] = set()
    if not SEEN_DIR.exists():
        return seen
    for seen_file in sorted(SEEN_DIR.glob("*.json")):
        if seen_file.stem == exclude_date:
            continue
        try:
            urls = json.loads(seen_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"  warning: skipping malformed {seen_file.name}: {e}")
            continue
        for url in urls:
            if isinstance(url, str) and url.strip():
                seen.add(url.strip())
    return seen


def collect_seen_db(db_path, exclude_date: str) -> tuple[set[str], set[str]]:
    """Query evaluations for (post_urls, post_ids) except those from exclude_date.
    Returns empty sets (and logs a warning) if the DB file doesn't exist."""
    from pathlib import Path as _Path
    if not _Path(db_path).exists():
        print(f"  note: DB not found at {db_path}, falling back to JSON seen files")
        return set(), set()
    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT post_url, post_id FROM evaluations WHERE evaluated_date != ?",
        (exclude_date,),
    ).fetchall()
    conn.close()
    urls = {r[0] for r in rows if r[0]}
    ids = {str(r[1]) for r in rows if r[1]}
    return urls, ids


def dedup(
    posts: list[dict], seen_urls: set[str], seen_ids: set[str]
) -> tuple[list[dict], dict[str, int]]:
    """Return (kept_posts, counts).

    Dedups on Apify's stable postId first, falling back to post_url for posts
    without an id and for history rows predating the post_id column. Facebook
    rotates the pfbid token inside post URLs, so URL-only matching lets old
    posts re-enter the pipeline as "new".
    """
    kept: list[dict] = []
    batch_urls: set[str] = set()
    batch_ids: set[str] = set()
    counts = {"total": len(posts), "already_digested": 0, "batch_dupe": 0, "kept": 0}
    for p in posts:
        url = (p.get("url") or "").strip()
        pid = str(p.get("postId") or "").strip()
        if not url and not pid:
            kept.append(p)
            counts["kept"] += 1
            continue
        if (pid and pid in seen_ids) or (url and url in seen_urls):
            counts["already_digested"] += 1
            continue
        if (pid and pid in batch_ids) or (url and url in batch_urls):
            counts["batch_dupe"] += 1
            continue
        if pid:
            batch_ids.add(pid)
        if url:
            batch_urls.add(url)
        kept.append(p)
        counts["kept"] += 1
    return kept, counts


def main():
    parser = argparse.ArgumentParser(description="Dedup raw Apify posts vs digest history")
    parser.add_argument("--date", default=str(date.today()))
    parser.add_argument("--input", help="Explicit raw JSON path (overrides --date)")
    args = parser.parse_args()

    digest_date = args.date
    in_path = Path(args.input) if args.input else RAW_DIR / f"{digest_date}-apify.json"

    if not in_path.exists():
        print(f"ERROR: raw file not found: {in_path}")
        sys.exit(1)

    out_path = RAW_DIR / f"{digest_date}-apify-new.json"

    posts = json.loads(in_path.read_text(encoding="utf-8"))
    print(f"Read {len(posts)} posts from {in_path.name}")

    from db import DB_PATH
    seen_urls, seen_ids = collect_seen_db(DB_PATH, exclude_date=digest_date)
    if not seen_urls:
        # fall back to JSON if DB returned nothing (fresh checkout, first-ever run)
        seen_urls = collect_seen_urls(exclude_date=digest_date)
    print(
        f"Loaded {len(seen_urls)} previously-evaluated URLs / {len(seen_ids)} post ids "
        f"(DB-backed, excluding {digest_date})"
    )

    kept, counts = dedup(posts, seen_urls, seen_ids)

    out_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nSummary:")
    print(f"  total scraped:       {counts['total']}")
    print(f"  already evaluated:   {counts['already_digested']}")
    print(f"  batch duplicates:    {counts['batch_dupe']}")
    print(f"  kept (new):          {counts['kept']}")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
