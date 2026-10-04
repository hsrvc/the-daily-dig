"""
Backfill the SQLite DB from existing data/parsed/*.json and data/seen/*.json.

Idempotent — safe to re-run. Uses INSERT OR REPLACE on albums (keyed by
post_url) and INSERT OR REPLACE on evaluations (keyed by post_url). The
covers table is left empty here; Phase 3 will populate it as fetch_covers
runs.

After running:
  - albums has one row per (post_url, album) ever published
  - evaluations has one row per post_url ever evaluated, with kept=1 if
    that URL turned into an album and kept=0 if it was filtered as filler

Usage:
    python3 scripts/migrate_to_sqlite.py
    python3 scripts/migrate_to_sqlite.py --reset          # drop + recreate first
    python3 scripts/migrate_to_sqlite.py --date 2026-05-05  # single date only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from db import DB_PATH, connect, init_schema, normalize, PROJECT_DIR

PARSED_DIR = PROJECT_DIR / "data" / "parsed"
SEEN_DIR = PROJECT_DIR / "data" / "seen"


def reset_db(db_path: Path) -> None:
    if db_path.exists():
        db_path.unlink()
    # Also remove WAL / SHM sidecars if present.
    for suffix in ("-wal", "-shm"):
        side = db_path.with_name(db_path.name + suffix)
        if side.exists():
            side.unlink()


def upsert_albums_for_date(conn, path: Path, digest_date: str) -> int:
    """Upsert albums from a single parsed JSON file. Returns the number of rows written."""
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"  ! skipping malformed {path.name}: {e}", file=sys.stderr)
        return 0

    rows = []
    for r in records:
        post_url = (r.get("post_url") or "").strip()
        if not post_url:
            continue
        rows.append((
            r.get("artist", ""),
            r.get("album", ""),
            normalize(r.get("artist", "")),
            normalize(r.get("album", "")),
            r.get("genre", "") or None,
            r.get("year", "") or None,
            r.get("description_en", "") or None,
            r.get("description_zh", "") or None,
            r.get("source_page", "") or "",
            post_url,
            r.get("image_url", "") or None,
            r.get("local_image", "") or None,
            digest_date,
        ))

    conn.executemany(
        """
        INSERT INTO albums (
            artist, album, artist_norm, album_norm,
            genre, year, description_en, description_zh,
            source_page, post_url, image_url, local_image, digest_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(post_url, artist_norm, album_norm) DO UPDATE SET
            artist=excluded.artist,
            album=excluded.album,
            genre=excluded.genre,
            year=excluded.year,
            description_en=excluded.description_en,
            description_zh=excluded.description_zh,
            source_page=excluded.source_page,
            image_url=excluded.image_url,
            local_image=excluded.local_image,
            digest_date=excluded.digest_date
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_albums(conn) -> dict[str, int]:
    """Read every data/parsed/*.json into albums. Returns counts per file."""
    counts: dict[str, int] = {}
    for path in sorted(PARSED_DIR.glob("*.json")):
        digest_date = path.stem  # filename is YYYY-MM-DD
        count = upsert_albums_for_date(conn, path, digest_date)
        if count > 0 or path.exists():
            counts[digest_date] = count
    return counts


def post_id_map(evaluated_date: str) -> dict[str, str]:
    """url -> Apify postId, read from that date's raw scrape (gitignored, present
    at pipeline runtime both locally and in CI). Empty dict when unavailable."""
    raw = PROJECT_DIR / "data" / "raw" / f"{evaluated_date}-apify.json"
    if not raw.exists():
        return {}
    try:
        posts = json.loads(raw.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    out = {}
    for p in posts:
        url = (p.get("url") or "").strip()
        pid = str(p.get("postId") or "").strip()
        if url and pid:
            out[url] = pid
    return out


def upsert_evaluations_for_date(conn, path: Path, evaluated_date: str) -> int:
    """Upsert evaluations from a single seen JSON file. Returns the number of rows written.

    kept is set by joining against albums on post_url after the fact:
      kept=1 if the post_url turned into an album row,
      kept=0 if the parser saw the post but skipped it as filler.
    """
    try:
        urls = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"  ! skipping malformed {path.name}: {e}", file=sys.stderr)
        return 0

    pid_by_url = post_id_map(evaluated_date)
    rows = []
    for url in urls:
        if not isinstance(url, str) or not url.strip():
            continue
        url = url.strip()
        # Best-effort source_page from the FB URL: /<page_slug>/posts/...
        source_page = ""
        parts = url.split("/")
        if "facebook.com" in url and len(parts) > 3:
            # https://www.facebook.com/<slug>/posts/...
            source_page = parts[3] if len(parts) > 3 else ""
        rows.append((url, source_page, evaluated_date, 0, None, pid_by_url.get(url)))

    # kept defaults to 0; we'll flip it to 1 below for URLs that match albums.
    conn.executemany(
        """
        INSERT INTO evaluations (post_url, source_page, evaluated_date, kept, skip_reason, post_id)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(post_url) DO UPDATE SET
            source_page=excluded.source_page,
            evaluated_date=excluded.evaluated_date,
            post_id=COALESCE(excluded.post_id, evaluations.post_id)
        """,
        rows,
    )
    # Backfill kept=1 for any evaluation whose post_url has a matching album.
    conn.execute(
        """
        UPDATE evaluations
        SET kept = 1
        WHERE post_url IN (SELECT post_url FROM albums)
        """
    )
    conn.commit()
    return len(rows)


def upsert_evaluations(conn) -> dict[str, int]:
    """Read every data/seen/*.json into evaluations. Each entry → one row.

    kept is set by joining against albums on post_url after the fact:
      kept=1 if the post_url turned into an album row (matching digest_date),
      kept=0 if the parser saw the post but skipped it as filler.
    """
    counts: dict[str, int] = {}
    for path in sorted(SEEN_DIR.glob("*.json")):
        evaluated_date = path.stem
        count = upsert_evaluations_for_date(conn, path, evaluated_date)
        if count > 0 or path.exists():
            counts[evaluated_date] = count
    return counts


def report(conn, parsed_counts: dict, seen_counts: dict) -> None:
    print("\n=== migration report ===")
    print(f"DB: {DB_PATH}")
    print()

    print("albums by digest_date (DB ← JSON):")
    for date, json_count in sorted(parsed_counts.items()):
        db_count = conn.execute(
            "SELECT COUNT(*) AS n FROM albums WHERE digest_date = ?", (date,)
        ).fetchone()["n"]
        match = "✓" if db_count == json_count else "✗"
        print(f"  {match} {date}: db={db_count}, json={json_count}")

    print()
    print("evaluations by evaluated_date (DB ← JSON):")
    for date, json_count in sorted(seen_counts.items()):
        db_count = conn.execute(
            "SELECT COUNT(*) AS n FROM evaluations WHERE evaluated_date = ?", (date,)
        ).fetchone()["n"]
        match = "✓" if db_count == json_count else "✗"
        print(f"  {match} {date}: db={db_count}, json={json_count}")

    print()
    print("integrity checks:")
    img_collisions = conn.execute(
        """
        SELECT image_url, COUNT(*) AS n FROM albums
        WHERE image_url IS NOT NULL AND image_url <> ''
        GROUP BY image_url HAVING n > 1
        """
    ).fetchall()
    print(f"  image_url collisions: {len(img_collisions)}")
    for row in img_collisions:
        offenders = conn.execute(
            "SELECT artist, album, digest_date FROM albums WHERE image_url = ?",
            (row["image_url"],),
        ).fetchall()
        for o in offenders:
            print(f"    [{o['digest_date']}] {o['artist']} — {o['album']}")

    norm_collisions = conn.execute(
        """
        SELECT artist_norm, album_norm, COUNT(DISTINCT digest_date) AS days
        FROM albums
        GROUP BY artist_norm, album_norm
        HAVING days > 1
        """
    ).fetchall()
    print(f"  cross-day (artist_norm, album_norm) duplicates: {len(norm_collisions)}")
    for row in norm_collisions[:5]:
        print(f"    {row['artist_norm']} — {row['album_norm']} (across {row['days']} days)")
    if len(norm_collisions) > 5:
        print(f"    ... and {len(norm_collisions) - 5} more")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--reset", action="store_true", help="Drop and recreate the DB first")
    p.add_argument("--date", help="Process only this date (YYYY-MM-DD). Default: all dates.")
    p.add_argument("--check-collisions", action="store_true",
                   help="Report cover hash collisions (same image on multiple albums).")
    args = p.parse_args()

    if args.check_collisions:
        from db import cover_collisions
        conn = connect()
        init_schema(conn)
        rows = cover_collisions(conn)
        conn.close()
        if not rows:
            print("no cover hash collisions found ✓")
        else:
            print(f"{len(rows)} collision(s) found:")
            for r in rows:
                print(f"  hash {r['file_hash'][:12]}... shared by {r['n']} albums:")
                for album_str in r['albums'].split(','):
                    print(f"    {album_str.strip()}")
        return

    if args.reset:
        print(f"resetting {DB_PATH}")
        reset_db(DB_PATH)

    conn = connect()
    init_schema(conn)

    if args.date:
        n_albums = upsert_albums_for_date(conn, PARSED_DIR / f"{args.date}.json", args.date)
        n_evals = upsert_evaluations_for_date(conn, SEEN_DIR / f"{args.date}.json", args.date)
        print(f"wrote {n_albums} albums and {n_evals} evaluations for {args.date}")
    else:
        print("loading albums from data/parsed/...")
        parsed_counts = upsert_albums(conn)

        print("loading evaluations from data/seen/...")
        seen_counts = upsert_evaluations(conn)

        report(conn, parsed_counts, seen_counts)

    conn.close()


if __name__ == "__main__":
    main()
