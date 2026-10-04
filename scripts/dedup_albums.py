#!/usr/bin/env python3
"""
Filter today's parsed digest against the last N days of digest history,
removing albums that have already been published recently. Two records are
considered duplicates if their (artist, album) tuples match case-insensitively
after stripping whitespace.

This complements scripts/dedup_raw.py, which dedups by post URL only and
therefore lets the same release through when a different shop posts it.

Usage:
    python scripts/dedup_albums.py                       # today, 14-day window
    python scripts/dedup_albums.py --date 2026-05-05
    python scripts/dedup_albums.py --window 30 --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
PARSED_DIR = PROJECT_DIR / "data" / "parsed"

# Strip parenthetical suffixes — the parser sometimes adds CJK readings
# (e.g. "Ryuichi Sakamoto (坂本龍一)" vs plain "Ryuichi Sakamoto") or
# edition tags ("(Tone Poet Edition)", "(Blue Vinyl)") on different days.
# These are the same release for the purpose of cross-day dedup.
_PAREN_RE = re.compile(r"\s*[\(（][^\)）]*[\)）]\s*")


def normalize(s: str) -> str:
    s = _PAREN_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


def key(record: dict) -> tuple[str, str]:
    return (normalize(record.get("artist", "")), normalize(record.get("album", "")))


def collect_history(
    target_date: date, window_days: int
) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], str]]:
    """JSON-based fallback for collect_history_db. Returns the same 2-tuple."""
    history: dict[tuple[str, str], str] = {}
    shop_album: dict[tuple[str, str], str] = {}
    if not PARSED_DIR.exists():
        return history, shop_album
    cutoff = target_date - timedelta(days=window_days)
    for path in sorted(PARSED_DIR.glob("*.json")):
        try:
            file_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date >= target_date or file_date < cutoff:
            continue
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for r in records:
            k = key(r)
            if k[0] and k[1] and k not in history:
                history[k] = path.stem
            source_page = r.get("source_page", "")
            album_n = k[1]
            if source_page and album_n:
                sk = (source_page, album_n)
                if sk not in shop_album:
                    shop_album[sk] = path.stem
    return history, shop_album


def collect_history_db(
    db_path, target_date: date, window_days: int
) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], str]]:
    """Query albums table for the lookback window excluding target_date.

    Returns a 2-tuple:
      history       — {(artist_norm, album_norm): date_str}
      shop_album    — {(source_page, album_norm): date_str}

    The second dict catches same-shop re-posts where the parser spells the
    artist differently on different scrape days (e.g. pfbid URL rotation
    causing a fresh parse with slightly different artist normalization).
    Returns ({}, {}) if DB doesn't exist.
    """
    from pathlib import Path as _Path
    if not _Path(db_path).exists():
        return {}, {}
    import sqlite3
    cutoff = (target_date - timedelta(days=window_days)).isoformat()
    target_str = target_date.isoformat()
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """SELECT artist_norm, album_norm, digest_date, source_page FROM albums
           WHERE digest_date >= ? AND digest_date < ?""",
        (cutoff, target_str),
    ).fetchall()
    conn.close()
    history: dict[tuple[str, str], str] = {}
    shop_album: dict[tuple[str, str], str] = {}
    for artist_norm, album_norm, d, source_page in rows:
        k = (artist_norm, album_norm)
        if k not in history:
            history[k] = d
        if source_page and album_norm:
            sk = (source_page, album_norm)
            if sk not in shop_album:
                shop_album[sk] = d
    return history, shop_album


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=str(date.today()))
    p.add_argument("--window", type=int, default=14, help="Lookback days (default 14)")
    p.add_argument("--dry-run", action="store_true", help="Print but do not modify the file")
    args = p.parse_args()

    target = PARSED_DIR / f"{args.date}.json"
    if not target.exists():
        print(f"ERROR: parsed file not found: {target}")
        raise SystemExit(1)

    target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    from db import DB_PATH
    history, shop_album_history = collect_history_db(DB_PATH, target_date, args.window)
    if not history and target_date > datetime.strptime("2026-05-01", "%Y-%m-%d").date():
        history, shop_album_history = collect_history(target_date, args.window)
    print(f"  loaded {len(history)} prior albums from DB (window {args.window}d, {len(shop_album_history)} shop+album pairs)")
    records = json.loads(target.read_text(encoding="utf-8"))

    kept: list[dict] = []
    dropped: list[tuple[dict, str]] = []
    for r in records:
        k = key(r)
        prev = history.get(k)
        if not prev:
            source = r.get("source_page", "")
            album_n = normalize(r.get("album", ""))
            if source and album_n:
                prev = shop_album_history.get((source, album_n))
        if prev:
            dropped.append((r, prev))
        else:
            kept.append(r)

    print(f"{args.date}: {len(records)} albums, history window {args.window}d ({len(history)} prior records)")
    if not dropped:
        print("  no cross-day duplicates found")
        return

    print(f"  dropping {len(dropped)} cross-day duplicate(s):")
    for r, prev in dropped:
        print(f"    - {r.get('artist','')} — {r.get('album','')} (already in {prev}.json)")

    if args.dry_run:
        print("  [dry-run] file unchanged")
        return

    target.write_text(json.dumps(kept, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  wrote {len(kept)} kept records back to {target.name}")


if __name__ == "__main__":
    main()
