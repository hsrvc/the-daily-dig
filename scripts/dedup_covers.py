#!/usr/bin/env python3
"""
Remove albums from today's digest whose cover hash matches an album already
published in the lookback window. Run after fetch_covers.py, before
generate_digest.py.

Uses the covers + albums tables written by fetch_covers.py (Phase 3). If the
DB or covers table is empty, exits cleanly with no changes.

Usage:
    python scripts/dedup_covers.py
    python scripts/dedup_covers.py --date 2026-05-05 --window 14
    python scripts/dedup_covers.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
PARSED_DIR = PROJECT_DIR / "data" / "parsed"


def find_cover_duplicates(conn, target_date: str, window_days: int) -> list[dict]:
    """Return today's albums whose cover hash matches a prior-day album.

    Self-joins covers on file_hash so each matching today-row gets paired with
    its prior duplicate. Deduplicates on (artist_norm, album_norm) so a single
    today album only appears once even if it collides with multiple prior rows.
    """
    cutoff = (
        datetime.strptime(target_date, "%Y-%m-%d").date() - timedelta(days=window_days)
    ).isoformat()

    rows = conn.execute(
        """
        SELECT today.artist_norm,
               today.album_norm,
               today.artist,
               today.album,
               prior.artist  AS prior_artist,
               prior.album   AS prior_album,
               prior.digest_date AS prior_date,
               tc.file_hash
        FROM   covers tc
        JOIN   albums today ON tc.album_id = today.id
        JOIN   covers pc    ON tc.file_hash = pc.file_hash
                           AND tc.album_id  != pc.album_id
        JOIN   albums prior ON pc.album_id = prior.id
        WHERE  tc.file_hash  IS NOT NULL
          AND  today.digest_date =  ?
          AND  prior.digest_date >= ?
          AND  prior.digest_date <  ?
        """,
        (target_date, cutoff, target_date),
    ).fetchall()

    seen: set[tuple[str, str]] = set()
    result: list[dict] = []
    for artist_norm, album_norm, artist, album, prior_artist, prior_album, prior_date, h in rows:
        k = (artist_norm, album_norm)
        if k not in seen:
            seen.add(k)
            result.append(
                {
                    "artist_norm": artist_norm,
                    "album_norm": album_norm,
                    "artist": artist,
                    "album": album,
                    "prior_artist": prior_artist,
                    "prior_album": prior_album,
                    "prior_date": prior_date,
                    "hash": h,
                }
            )
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=str(date.today()))
    p.add_argument("--window", type=int, default=14)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    target = PARSED_DIR / f"{args.date}.json"
    if not target.exists():
        print(f"ERROR: parsed file not found: {target}")
        sys.exit(1)

    try:
        import sqlite3
        from db import DB_PATH, connect
    except ImportError:
        print("  note: db module not available — skipping cover dedup")
        return

    if not DB_PATH.exists():
        print("  note: dig.db not found — skipping cover dedup")
        return

    conn = connect(DB_PATH)
    duplicates = find_cover_duplicates(conn, args.date, args.window)
    conn.close()

    if not duplicates:
        print(f"{args.date}: no cover-hash duplicates found")
        return

    dup_keys = {(d["artist_norm"], d["album_norm"]) for d in duplicates}

    records = json.loads(target.read_text(encoding="utf-8"))

    # normalize must match db.py / dedup_albums.py logic
    import re
    _PAREN_RE = re.compile(r"\s*[\(（][^\)）]*[\)）]\s*")

    def _norm(s: str) -> str:
        s = _PAREN_RE.sub(" ", s or "")
        return re.sub(r"\s+", " ", s).strip().lower()

    kept = [r for r in records if (_norm(r.get("artist", "")), _norm(r.get("album", ""))) not in dup_keys]
    dropped_records = [r for r in records if (_norm(r.get("artist", "")), _norm(r.get("album", ""))) in dup_keys]

    print(f"{args.date}: {len(records)} albums, dropping {len(dropped_records)} cover-hash duplicate(s):")
    for d in duplicates:
        print(f"  - {d['artist']} — {d['album']}")
        print(f"    same cover hash as: {d['prior_artist']} — {d['prior_album']} ({d['prior_date']})")
        print(f"    hash: {d['hash'][:16]}…")

    if args.dry_run:
        print("  [dry-run] file unchanged")
        return

    target.write_text(json.dumps(kept, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  wrote {len(kept)} kept records back to {target.name}")


if __name__ == "__main__":
    main()
