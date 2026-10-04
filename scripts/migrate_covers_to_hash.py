#!/usr/bin/env python3
"""
One-time migration: rename cover images to content-hashed filenames.

Usage:
    python3.12 scripts/migrate_covers_to_hash.py --dry-run   # preview
    python3.12 scripts/migrate_covers_to_hash.py             # run

For each .jpg in site/public/covers/ that doesn't already have a -{8hex}
suffix, compute SHA-256, append the first 8 chars to the stem, then:
  1. Rename the file
  2. Update data/parsed/*.json  (local_image field)
  3. Update site/src/content/digests/*.md  (Image: /covers/... lines)
  4. Update the SQLite covers table  (file_path column)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
COVERS_DIR = PROJECT_DIR / "site" / "public" / "covers"
PARSED_DIR = PROJECT_DIR / "data" / "parsed"
DIGESTS_DIR = PROJECT_DIR / "site" / "src" / "content" / "digests"

# Already-migrated pattern: ends with -{8 hex chars}.jpg
HASHED_RE = re.compile(r"^.+-[0-9a-f]{8}\.jpg$", re.IGNORECASE)

sys.path.insert(0, str(PROJECT_DIR / "scripts"))
from db import DB_PATH, connect, hash_file  # noqa: E402


def build_rename_map(dry_run: bool) -> dict[str, str]:
    """Return {old_filename: new_filename} for files that need renaming."""
    mapping: dict[str, str] = {}
    for jpg in sorted(COVERS_DIR.glob("*.jpg")):
        if HASHED_RE.match(jpg.name):
            continue  # already hashed — skip
        h = hash_file(jpg)
        if h is None:
            print(f"  WARN: could not hash {jpg.name}, skipping")
            continue
        h8 = h[:8]
        new_name = f"{jpg.stem}-{h8}.jpg"
        mapping[jpg.name] = new_name
        action = "dry-run" if dry_run else "rename"
        print(f"  [{action}] {jpg.name} → {new_name}")
    return mapping


def rename_files(mapping: dict[str, str]) -> int:
    count = 0
    for old_name, new_name in mapping.items():
        src = COVERS_DIR / old_name
        dst = COVERS_DIR / new_name
        if src.exists():
            src.rename(dst)
            count += 1
        else:
            print(f"  WARN: source not found: {src}")
    return count


def update_parsed_jsons(mapping: dict[str, str], dry_run: bool) -> int:
    """Update local_image in all parsed JSON files. Returns number of files changed."""
    files_changed = 0
    for json_path in sorted(PARSED_DIR.glob("*.json")):
        records = json.loads(json_path.read_text(encoding="utf-8"))
        changed = False
        for rec in records:
            li = rec.get("local_image", "")
            if li.startswith("/covers/"):
                old_fname = li[len("/covers/"):]
                if old_fname in mapping:
                    new_li = f"/covers/{mapping[old_fname]}"
                    print(f"  [json] {json_path.name}: {li} → {new_li}")
                    if not dry_run:
                        rec["local_image"] = new_li
                    changed = True
        if changed:
            files_changed += 1
            if not dry_run:
                json_path.write_text(
                    json.dumps(records, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
    return files_changed


def update_digest_mds(mapping: dict[str, str], dry_run: bool) -> int:
    """Update Image: lines in digest markdown files. Returns number of files changed."""
    files_changed = 0
    # Match lines like: - **Image:** /covers/some-file.jpg
    img_re = re.compile(r"^(- \*\*Image:\*\* /covers/)(.+\.jpg)$")
    for md_path in sorted(DIGESTS_DIR.glob("*.md")):
        lines = md_path.read_text(encoding="utf-8").splitlines(keepends=True)
        new_lines = []
        changed = False
        for line in lines:
            m = img_re.match(line.rstrip("\n\r"))
            if m:
                old_fname = m.group(2)
                if old_fname in mapping:
                    new_line = f"{m.group(1)}{mapping[old_fname]}\n"
                    print(f"  [md]  {md_path.name}: {old_fname} → {mapping[old_fname]}")
                    changed = True
                    new_lines.append(new_line)
                    continue
            new_lines.append(line)
        if changed:
            files_changed += 1
            if not dry_run:
                md_path.write_text("".join(new_lines), encoding="utf-8")
    return files_changed


def update_db(mapping: dict[str, str], dry_run: bool) -> int:
    """Update covers.file_path in SQLite. Returns number of rows updated."""
    if not DB_PATH.exists():
        print("  note: DB not found, skipping DB update")
        return 0
    conn = connect(DB_PATH)
    rows_updated = 0
    try:
        all_rows = conn.execute("SELECT id, file_path FROM covers WHERE file_path IS NOT NULL").fetchall()
        for row in all_rows:
            fp = row["file_path"]
            # file_path looks like site/public/covers/{filename}
            for old_name, new_name in mapping.items():
                if fp.endswith(old_name):
                    new_fp = fp[: len(fp) - len(old_name)] + new_name
                    print(f"  [db]  covers.id={row['id']}: {fp} → {new_fp}")
                    if not dry_run:
                        conn.execute(
                            "UPDATE covers SET file_path = ? WHERE id = ?",
                            (new_fp, row["id"]),
                        )
                    rows_updated += 1
                    break
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return rows_updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate covers to content-hashed filenames")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()

    dry_run = args.dry_run
    if dry_run:
        print("=== DRY RUN — no files will be modified ===\n")

    print("--- Building rename map ---")
    mapping = build_rename_map(dry_run)
    print(f"  {len(mapping)} file(s) to rename\n")

    if not mapping:
        print("Nothing to migrate.")
        return

    renamed = 0
    if not dry_run:
        print("--- Renaming files ---")
        renamed = rename_files(mapping)
        print(f"  renamed {renamed} file(s)\n")

    print("--- Updating parsed JSONs ---")
    jsons_updated = update_parsed_jsons(mapping, dry_run)
    print(f"  updated {jsons_updated} JSON file(s)\n")

    print("--- Updating digest markdown files ---")
    mds_updated = update_digest_mds(mapping, dry_run)
    print(f"  updated {mds_updated} markdown file(s)\n")

    print("--- Updating DB covers table ---")
    db_rows = update_db(mapping, dry_run)
    print(f"  updated {db_rows} DB row(s)\n")

    print("=== Summary ===")
    if dry_run:
        print(f"  Would rename:          {len(mapping)} file(s)")
    else:
        print(f"  Renamed:               {renamed} file(s)")
    print(f"  Parsed JSONs updated:  {jsons_updated}")
    print(f"  Digest MDs updated:    {mds_updated}")
    print(f"  DB rows updated:       {db_rows}")
    if dry_run:
        print("\nRe-run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
