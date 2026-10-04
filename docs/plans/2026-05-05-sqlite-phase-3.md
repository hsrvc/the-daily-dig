# SQLite Phase 3 — Cover-Art Provenance

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Record every cover-art fetch in the SQLite `covers` table with source, URL, file path, and SHA-256 hash so a single query can surface image-collision bugs (two albums sharing the same cover bytes).

**Architecture:** Three additions: (1) hash helpers + `write_cover_row` in `db.py`; (2) `fetch_covers.py` writes a row on every successful download; (3) `backfill_covers.py` retroactively populates the table from existing disk files. A `--check-collisions` flag on `migrate_to_sqlite.py` surfaces hash duplicates. The `covers` table schema already exists in `db.py` — no migration needed.

**Tech Stack:** Python 3, `hashlib` (stdlib), `sqlite3` (stdlib), `scripts/db.py`, pytest

---

### Task 1: Add hash helpers and `write_cover_row` to `db.py`

**Files:**
- Modify: `scripts/db.py`
- Create: `tests/test_covers_db.py`

**Step 1: Write failing tests**

```python
# tests/test_covers_db.py
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def test_hash_bytes():
    from db import hash_bytes
    assert hash_bytes(b"hello") == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_hash_file(tmp_path):
    from db import hash_file
    f = tmp_path / "test.jpg"
    f.write_bytes(b"hello")
    assert hash_file(f) == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_hash_file_missing(tmp_path):
    from db import hash_file
    assert hash_file(tmp_path / "nonexistent.jpg") is None


def test_write_cover_row(tmp_db):
    conn, _ = tmp_db
    # Insert a parent album row first (covers has FK to albums)
    conn.execute(
        """INSERT INTO albums
           (artist, album, artist_norm, album_norm, source_page, post_url, digest_date)
           VALUES (?,?,?,?,?,?,?)""",
        ("Test Artist", "Test Album", "test artist", "test album",
         "THTRECORDs", "https://fb.com/post/1", "2026-05-05"),
    )
    conn.commit()
    album_id = conn.execute("SELECT id FROM albums WHERE post_url='https://fb.com/post/1'").fetchone()[0]

    from db import write_cover_row
    write_cover_row(
        conn,
        album_id=album_id,
        source="fb_cdn",
        fetched_url="https://fb-cdn.example.com/image.jpg",
        file_path="site/public/covers/test-artist_test-album.jpg",
        file_hash="abc123",
    )

    row = conn.execute("SELECT * FROM covers WHERE album_id=?", (album_id,)).fetchone()
    assert row["source"] == "fb_cdn"
    assert row["fetched_url"] == "https://fb-cdn.example.com/image.jpg"
    assert row["file_hash"] == "abc123"
```

**Step 2: Run to confirm they fail**

```bash
cd $HOME/daily-dig
pytest tests/test_covers_db.py -v
```

Expected: `ImportError: cannot import name 'hash_bytes' from 'db'`

**Step 3: Commit failing tests**

```bash
git add tests/test_covers_db.py
git commit -m "test: failing tests for db hash helpers and write_cover_row"
```

**Step 4: Implement in `db.py`**

Add after the existing imports (add `import hashlib` at the top):

```python
import hashlib
```

Add these three functions after the `normalize` function:

```python
def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def write_cover_row(
    conn: sqlite3.Connection,
    album_id: int,
    source: str,
    fetched_url: str | None,
    file_path: str | None,
    file_hash: str | None,
) -> None:
    conn.execute(
        """INSERT INTO covers (album_id, source, fetched_url, file_path, file_hash)
           VALUES (?, ?, ?, ?, ?)""",
        (album_id, source, fetched_url, file_path, file_hash),
    )
    conn.commit()
```

**Step 5: Run tests to confirm they pass**

```bash
pytest tests/test_covers_db.py -v
```

Expected: 4 PASS.

**Step 6: Commit**

```bash
git add scripts/db.py
git commit -m "feat: add hash_bytes, hash_file, write_cover_row to db.py"
```

---

### Task 2: Wire DB write into `fetch_covers.py`

**Files:**
- Modify: `scripts/fetch_covers.py`
- Modify: `tests/test_covers_db.py`

**Step 1: Write failing test**

Add to `tests/test_covers_db.py`:

```python
def test_get_album_id_found(tmp_db):
    conn, _ = tmp_db
    conn.execute(
        """INSERT INTO albums
           (artist, album, artist_norm, album_norm, source_page, post_url, digest_date)
           VALUES (?,?,?,?,?,?,?)""",
        ("Ryuichi Sakamoto", "Async", "ryuichi sakamoto", "async",
         "THTRECORDs", "https://fb.com/post/sakamoto", "2026-05-05"),
    )
    conn.commit()

    from fetch_covers import get_album_id
    record = {
        "artist": "Ryuichi Sakamoto",
        "album": "Async",
        "post_url": "https://fb.com/post/sakamoto",
    }
    album_id = get_album_id(conn, record)
    assert album_id is not None
    assert isinstance(album_id, int)


def test_get_album_id_missing(tmp_db):
    conn, _ = tmp_db
    from fetch_covers import get_album_id
    record = {"artist": "Nobody", "album": "Nothing", "post_url": "https://fb.com/post/none"}
    assert get_album_id(conn, record) is None
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_covers_db.py::test_get_album_id_found tests/test_covers_db.py::test_get_album_id_missing -v
```

Expected: `ImportError: cannot import name 'get_album_id' from 'fetch_covers'`

**Step 3: Commit failing tests**

```bash
git add tests/test_covers_db.py
git commit -m "test: failing tests for fetch_covers get_album_id helper"
```

**Step 4: Add `get_album_id` to `fetch_covers.py`**

Add after the existing imports:

```python
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
```

Wait — `scripts/` is already the directory, so `db` can be imported directly. Add this import near the top (after the existing imports):

```python
try:
    from db import DB_PATH, connect, hash_bytes, write_cover_row, normalize
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False
```

Add the `get_album_id` function after `cover_path_for`:

```python
def get_album_id(conn, record: dict) -> int | None:
    """Look up album.id by (post_url, artist_norm, album_norm). Returns None if not found."""
    import sqlite3
    row = conn.execute(
        "SELECT id FROM albums WHERE post_url = ? AND artist_norm = ? AND album_norm = ?",
        (
            record.get("post_url", ""),
            normalize(record.get("artist", "")),
            normalize(record.get("album", "")),
        ),
    ).fetchone()
    return row["id"] if row else None
```

**Step 5: Wire DB write into `fetch_one`**

Modify `fetch_one` signature:

```python
def fetch_one(record: dict, force: bool = False, conn=None) -> str:
```

After each successful download (the three `target.write_bytes(data)` lines for fb, itunes, musicbrainz), add the DB write:

```python
# After target.write_bytes(data) for fb:
if conn is not None:
    album_id = get_album_id(conn, record)
    if album_id is not None:
        write_cover_row(conn, album_id, "fb_cdn", fb_url,
                        str(target.relative_to(PROJECT_DIR)), hash_bytes(data))
```

```python
# After target.write_bytes(data) for itunes:
if conn is not None:
    album_id = get_album_id(conn, record)
    if album_id is not None:
        write_cover_row(conn, album_id, "itunes", None,
                        str(target.relative_to(PROJECT_DIR)), hash_bytes(data))
```

```python
# After target.write_bytes(data) for musicbrainz:
if conn is not None:
    album_id = get_album_id(conn, record)
    if album_id is not None:
        write_cover_row(conn, album_id, "musicbrainz", None,
                        str(target.relative_to(PROJECT_DIR)), hash_bytes(data))
```

Modify `main()` to open a DB connection and pass it:

```python
def main():
    ...
    # After parsing args, before the records loop:
    conn = None
    if _DB_AVAILABLE:
        try:
            conn = connect(DB_PATH)
        except Exception as e:
            print(f"  note: could not connect to DB ({e}) — covers table will not be updated")

    counts: dict[str, int] = {}
    for r in records:
        status = fetch_one(r, force=args.force, conn=conn)
        counts[status] = counts.get(status, 0) + 1

    if conn is not None:
        conn.close()
    ...
```

**Step 6: Run tests**

```bash
pytest tests/test_covers_db.py -v
```

Expected: all 6 tests PASS.

**Step 7: Smoke-test against real data**

```bash
cd $HOME/daily-dig
python3 scripts/fetch_covers.py --date 2026-05-05 2>&1 | head -20
sqlite3 data/dig.db 'SELECT COUNT(*) FROM covers;'
```

Expected: covers count is now > 0 (the `ok-cached` path doesn't write, but if any covers were re-fetched they'd show). If count is still 0 (all cached), that's expected and correct — `--force` would populate it.

**Step 8: Commit**

```bash
git add scripts/fetch_covers.py
git commit -m "feat: fetch_covers writes provenance row to covers table on each download"
```

---

### Task 3: Backfill `covers` table from existing disk files

This is a one-time operation to populate `covers` for files already on disk from previous runs.

**Files:**
- Create: `scripts/backfill_covers.py`

**Step 1: Write the script**

```python
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
            print(f"  {'[dry-run] ' if args.dry_run else ''}backfilled: {r.get('artist')} — {r.get('album')} ({fhash[:8]}...)")

    conn.close()
    print(f"\ninserted: {inserted}  skipped-no-file: {skipped_no_file}  "
          f"skipped-no-album: {skipped_no_album}  skipped-already: {inserted and skipped_already}")


if __name__ == "__main__":
    main()
```

**Step 2: Dry-run to preview**

```bash
cd $HOME/daily-dig
python3 scripts/backfill_covers.py --dry-run
```

Expected: lists albums it would backfill, prints counts at end. No DB changes.

**Step 3: Run for real**

```bash
python3 scripts/backfill_covers.py
sqlite3 data/dig.db 'SELECT COUNT(*) FROM covers;'
```

Expected: covers count matches number of albums that have a local_image file on disk.

**Step 4: Commit**

```bash
git add scripts/backfill_covers.py data/dig.db
git commit -m "feat: backfill_covers.py populates covers table from existing disk files"
```

---

### Task 4: Collision query — surface duplicate cover hashes

**Files:**
- Modify: `scripts/db.py`
- Modify: `scripts/migrate_to_sqlite.py`
- Modify: `tests/test_covers_db.py`

**Step 1: Write failing test**

Add to `tests/test_covers_db.py`:

```python
def test_cover_collisions_detects_duplicate_hash(tmp_db):
    conn, _ = tmp_db
    # Two albums with different post_urls but same file_hash
    conn.executemany(
        """INSERT INTO albums
           (artist, album, artist_norm, album_norm, source_page, post_url, digest_date)
           VALUES (?,?,?,?,?,?,?)""",
        [
            ("Artist A", "Album A", "artist a", "album a", "shop", "https://fb.com/1", "2026-05-04"),
            ("Artist B", "Album B", "artist b", "album b", "shop", "https://fb.com/2", "2026-05-04"),
        ],
    )
    conn.commit()
    id_a = conn.execute("SELECT id FROM albums WHERE post_url='https://fb.com/1'").fetchone()[0]
    id_b = conn.execute("SELECT id FROM albums WHERE post_url='https://fb.com/2'").fetchone()[0]

    from db import write_cover_row, cover_collisions
    write_cover_row(conn, id_a, "fb_cdn", None, "covers/a.jpg", "deadbeef")
    write_cover_row(conn, id_b, "fb_cdn", None, "covers/b.jpg", "deadbeef")  # same hash!

    collisions = cover_collisions(conn)
    assert len(collisions) == 1
    assert collisions[0]["file_hash"] == "deadbeef"
    assert collisions[0]["n"] == 2


def test_cover_collisions_empty_when_no_duplicates(tmp_db):
    conn, _ = tmp_db
    conn.execute(
        """INSERT INTO albums
           (artist, album, artist_norm, album_norm, source_page, post_url, digest_date)
           VALUES (?,?,?,?,?,?,?)""",
        ("Artist A", "Album A", "artist a", "album a", "shop", "https://fb.com/1", "2026-05-04"),
    )
    conn.commit()
    album_id = conn.execute("SELECT id FROM albums WHERE post_url='https://fb.com/1'").fetchone()[0]

    from db import write_cover_row, cover_collisions
    write_cover_row(conn, album_id, "fb_cdn", None, "covers/a.jpg", "unique_hash")

    assert cover_collisions(conn) == []
```

**Step 2: Run to confirm they fail**

```bash
pytest tests/test_covers_db.py::test_cover_collisions_detects_duplicate_hash tests/test_covers_db.py::test_cover_collisions_empty_when_no_duplicates -v
```

Expected: `ImportError: cannot import name 'cover_collisions' from 'db'`

**Step 3: Commit failing tests**

```bash
git add tests/test_covers_db.py
git commit -m "test: failing tests for cover_collisions query"
```

**Step 4: Add `cover_collisions` to `db.py`**

```python
def cover_collisions(conn: sqlite3.Connection) -> list:
    """Return rows where the same file_hash appears on more than one album.
    Each row has: file_hash, n (count), albums (comma-joined artist — album (date))."""
    return conn.execute(
        """
        SELECT c.file_hash,
               COUNT(*) AS n,
               GROUP_CONCAT(a.artist || ' — ' || a.album || ' (' || a.digest_date || ')') AS albums
        FROM covers c
        JOIN albums a ON c.album_id = a.id
        WHERE c.file_hash IS NOT NULL
        GROUP BY c.file_hash
        HAVING n > 1
        ORDER BY n DESC
        """
    ).fetchall()
```

**Step 5: Add `--check-collisions` flag to `migrate_to_sqlite.py`**

In `main()`, add argument:

```python
p.add_argument("--check-collisions", action="store_true",
               help="Report cover hash collisions (same image on multiple albums).")
```

Add handling at the end of `main()`, after the existing `--date` / full-run branches:

```python
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
```

**Step 6: Run tests**

```bash
pytest tests/test_covers_db.py -v
```

Expected: all 8 tests PASS.

**Step 7: Run collision check on real DB**

```bash
python3 scripts/migrate_to_sqlite.py --check-collisions
```

Expected: either "no cover hash collisions found ✓" or a list of albums sharing a hash.
If collisions exist, they're the exact parser image-collision bugs we've been hunting.

**Step 8: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass (6 existing + 8 new = 14 total).

**Step 9: Commit everything and push**

```bash
git add scripts/db.py scripts/migrate_to_sqlite.py tests/test_covers_db.py data/dig.db
git commit -m "feat: cover_collisions query + --check-collisions CLI flag"
git push origin main
```
