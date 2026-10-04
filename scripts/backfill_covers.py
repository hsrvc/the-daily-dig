#!/usr/bin/env python3
"""
Backfill the covers table from existing data/parsed/*.json + site/public/covers/.

For each album record that has a local_image pointing to an existing file,
inserts a covers row with source='backfill', the file path, and the file's SHA-256.
Skips albums that already have a covers row (idempotent).

Usage:
    python3 scripts/backfill_covers.py
    python3 scripts/backfill_covers.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import DB_PATH, connect, init_schema, hash_file, normalize, write_cover_row

PARSED_DIR = PROJECT_DIR / "data" / "parsed"
COVERS_DIR = PROJECT_DIR / "site" / "public" / "covers"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    conn = connect(DB_PATH)
    init_schema(conn)

    inserted = skipped_no_file = skipped_no_album = skipped_already = 0

    for parsed_path in sorted(PARSED_DIR.glob("*.json")):
        try:
            records = json.loads(parsed_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue

        for r in records:
            local_image = r.get("local_image", "")
            if not local_image or not local_image.startswith("/covers/"):
                skipped_no_file += 1
                continue

            file_path = COVERS_DIR / local_image[len("/covers/"):]
            if not file_path.exists():
                skipped_no_file += 1
                continue

            # Look up album_id
            row = conn.execute(
                "SELECT id FROM albums WHERE post_url = ? AND artist_norm = ? AND album_norm = ?",
                (r.get("post_url", ""), normalize(r.get("artist", "")), normalize(r.get("album", ""))),
            ).fetchone()
            if row is None:
                skipped_no_album += 1
                continue
            album_id = row["id"]

            # Skip if already has a covers row
            existing = conn.execute(
                "SELECT id FROM covers WHERE album_id = ?", (album_id,)
            ).fetchone()
            if existing:
                skipped_already += 1
                continue

            fhash = hash_file(file_path)
            rel_path = str(file_path.relative_to(PROJECT_DIR))

            if not args.dry_run:
                write_cover_row(conn, album_id, "backfill", None, rel_path, fhash)
            inserted += 1
            print(f"  {'[dry-run] ' if args.dry_run else ''}backfilled: {r.get('artist')} — {r.get('album')} ({(fhash or 'no-hash')[:8]}...)")

    conn.close()
    print(f"\ninserted: {inserted}  skipped-no-file: {skipped_no_file}  "
          f"skipped-no-album: {skipped_no_album}  skipped-already: {skipped_already}")


if __name__ == "__main__":
    main()
