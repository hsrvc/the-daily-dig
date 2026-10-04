# SQLite Phase 2 — Point Pipeline at DB

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace all JSON-file reads in `dedup_raw.py` and `dedup_albums.py` with SQLite queries, and write each run's parsed records + evaluated URLs into the DB so it stays current.

**Architecture:** The two dedup scripts gain a DB-first read path (fall back to JSON only if `data/dig.db` is missing, e.g. on a fresh checkout without the committed DB). `migrate_to_sqlite.py` gains a `--date` flag so the pipeline can write a single day's records after each run. `SKILL.md` calls the DB write step after parsing and after album dedup. JSON files continue to be written exactly as before — the Astro build and `generate_digest.py` are untouched.

**Tech Stack:** Python 3, `sqlite3` stdlib, `scripts/db.py` helpers (`connect`, `init_schema`, `normalize`), pytest

---

## Task 1: Create tests/ directory and test harness

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

**Step 1: Create tests/ directory with conftest containing a shared temp-DB fixture**

```python
# tests/conftest.py
import sys
from pathlib import Path
import pytest

# make scripts/ importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

@pytest.fixture
def tmp_db(tmp_path):
    """Return a connected, schema-initialized SQLite DB in a temp directory."""
    from db import connect, init_schema
    db_file = tmp_path / "test.db"
    conn = connect(db_file)
    init_schema(conn)
    return conn, db_file
```

**Step 2: Run pytest to confirm the fixture loads without errors**

```bash
cd $HOME/daily-dig
python -m pytest tests/ -v
```

Expected: `no tests ran` (0 items collected), no import errors.

**Step 3: Commit**

```bash
git add tests/
git commit -m "chore: add tests/ directory with pytest conftest and tmp_db fixture"
```

---

## Task 2: Tests for DB-backed `dedup_raw.py`

**Files:**
- Create: `tests/test_dedup_raw.py`

**Step 1: Write failing tests**

```python
# tests/test_dedup_raw.py
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def test_collect_seen_urls_from_db(tmp_db):
    conn, db_file = tmp_db
    conn.execute(
        "INSERT INTO evaluations (post_url, source_page, evaluated_date, kept) VALUES (?,?,?,?)",
        ("https://facebook.com/post/111", "THTRECORDs", "2026-05-04", 1),
    )
    conn.execute(
        "INSERT INTO evaluations (post_url, source_page, evaluated_date, kept) VALUES (?,?,?,?)",
        ("https://facebook.com/post/222", "THTRECORDs", "2026-05-03", 0),
    )
    # today's URL should NOT be excluded by exclude_date
    conn.execute(
        "INSERT INTO evaluations (post_url, source_page, evaluated_date, kept) VALUES (?,?,?,?)",
        ("https://facebook.com/post/333", "THTRECORDs", "2026-05-05", 1),
    )
    conn.commit()

    from dedup_raw import collect_seen_urls_db
    seen = collect_seen_urls_db(db_file, exclude_date="2026-05-05")

    assert "https://facebook.com/post/111" in seen
    assert "https://facebook.com/post/222" in seen
    # today's is excluded
    assert "https://facebook.com/post/333" not in seen


def test_collect_seen_urls_db_returns_empty_for_missing_db(tmp_path):
    from dedup_raw import collect_seen_urls_db
    seen = collect_seen_urls_db(tmp_path / "nonexistent.db", exclude_date="2026-05-05")
    assert seen == set()
```

**Step 2: Run to confirm they fail**

```bash
cd $HOME/daily-dig
python -m pytest tests/test_dedup_raw.py -v
```

Expected: `ImportError: cannot import name 'collect_seen_urls_db' from 'dedup_raw'`

**Step 3: Commit the failing tests**

```bash
git add tests/test_dedup_raw.py
git commit -m "test: failing tests for dedup_raw DB read path"
```

---

## Task 3: Implement DB read path in `dedup_raw.py`

**Files:**
- Modify: `scripts/dedup_raw.py`

**Step 1: Add `collect_seen_urls_db` function and update `main` to use it**

Add after the existing imports and `collect_seen_urls` function:

```python
from pathlib import Path as _Path

def collect_seen_urls_db(db_path, exclude_date: str) -> set[str]:
    """Query evaluations table for all post_urls except those from exclude_date.
    Returns empty set (and logs a warning) if the DB file doesn't exist."""
    if not _Path(db_path).exists():
        print(f"  note: DB not found at {db_path}, falling back to JSON seen files")
        return set()
    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT post_url FROM evaluations WHERE evaluated_date != ?",
        (exclude_date,),
    ).fetchall()
    conn.close()
    return {row[0] for row in rows}
```

Then in `main()`, replace the line:
```python
seen = collect_seen_urls(exclude_date=digest_date)
```
with:
```python
from db import DB_PATH
seen = collect_seen_urls_db(DB_PATH, exclude_date=digest_date)
if not seen:
    # fall back to JSON if DB returned nothing (fresh checkout, first-ever run)
    seen = collect_seen_urls(exclude_date=digest_date)
print(f"Loaded {len(seen)} previously-evaluated URLs (DB-backed, excluding {digest_date})")
```

**Step 2: Run tests to confirm they pass**

```bash
cd $HOME/daily-dig
python -m pytest tests/test_dedup_raw.py -v
```

Expected: both tests PASS.

**Step 3: Smoke-test the script against real data**

```bash
cd $HOME/daily-dig
python3 scripts/dedup_raw.py --date 2026-05-05 --input data/raw/2026-05-05-apify.json 2>/dev/null || echo "no raw file — OK for smoke test"
# If raw file doesn't exist, confirm the DB import path fires without error:
python3 -c "
import sys; sys.path.insert(0,'scripts')
from dedup_raw import collect_seen_urls_db
from db import DB_PATH
seen = collect_seen_urls_db(DB_PATH, '2026-05-05')
print(f'DB returned {len(seen)} URLs')
"
```

Expected: prints a non-zero count from the DB (should match `sqlite3 data/dig.db 'SELECT COUNT(*) FROM evaluations WHERE evaluated_date != "2026-05-05"'`).

**Step 4: Commit**

```bash
git add scripts/dedup_raw.py
git commit -m "feat: dedup_raw reads seen URLs from SQLite evaluations table"
```

---

## Task 4: Tests for DB-backed `dedup_albums.py`

**Files:**
- Create: `tests/test_dedup_albums.py`

**Step 1: Write failing tests**

```python
# tests/test_dedup_albums.py
import sys
from pathlib import Path
from datetime import date, timedelta

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def test_collect_history_from_db(tmp_db):
    conn, db_file = tmp_db
    # Insert two albums from different prior days
    conn.executemany(
        """INSERT INTO albums
           (artist, album, artist_norm, album_norm, source_page, post_url, digest_date)
           VALUES (?,?,?,?,?,?,?)""",
        [
            ("Ryuichi Sakamoto", "Async", "ryuichi sakamoto", "async",
             "THTRECORDs", "https://fb.com/post/1", "2026-05-04"),
            ("Mobb Deep", "Murda Muzik", "mobb deep", "murda muzik",
             "uourecords", "https://fb.com/post/2", "2026-05-03"),
        ],
    )
    conn.commit()

    from dedup_albums import collect_history_db
    target = date(2026, 5, 5)
    history = collect_history_db(db_file, target_date=target, window_days=14)

    assert ("ryuichi sakamoto", "async") in history
    assert ("mobb deep", "murda muzik") in history
    # today should NOT appear (not inserted, but belt-and-suspenders)
    assert ("something", "today") not in history


def test_collect_history_db_respects_window(tmp_db):
    conn, db_file = tmp_db
    conn.execute(
        """INSERT INTO albums
           (artist, album, artist_norm, album_norm, source_page, post_url, digest_date)
           VALUES (?,?,?,?,?,?,?)""",
        ("Old Album", "From 2020", "old album", "from 2020",
         "THTRECORDs", "https://fb.com/post/old", "2020-01-01"),
    )
    conn.commit()

    from dedup_albums import collect_history_db
    target = date(2026, 5, 5)
    history = collect_history_db(db_file, target_date=target, window_days=14)

    # 2020-01-01 is outside the 14-day window
    assert ("old album", "from 2020") not in history


def test_collect_history_db_returns_empty_for_missing_db(tmp_path):
    from dedup_albums import collect_history_db
    history = collect_history_db(
        tmp_path / "nonexistent.db",
        target_date=date(2026, 5, 5),
        window_days=14,
    )
    assert history == {}
```

**Step 2: Run to confirm they fail**

```bash
cd $HOME/daily-dig
python -m pytest tests/test_dedup_albums.py -v
```

Expected: `ImportError: cannot import name 'collect_history_db' from 'dedup_albums'`

**Step 3: Commit the failing tests**

```bash
git add tests/test_dedup_albums.py
git commit -m "test: failing tests for dedup_albums DB read path"
```

---

## Task 5: Implement DB read path in `dedup_albums.py`

**Files:**
- Modify: `scripts/dedup_albums.py`

**Step 1: Add `collect_history_db` function and update `main` to use it**

Add after the existing imports and `collect_history` function:

```python
def collect_history_db(db_path, target_date: date, window_days: int) -> dict[tuple[str, str], str]:
    """Query albums table for (artist_norm, album_norm) in the lookback window,
    excluding target_date. Returns empty dict if DB doesn't exist."""
    from pathlib import Path as _Path
    if not _Path(db_path).exists():
        return {}
    import sqlite3
    cutoff = (target_date - timedelta(days=window_days)).isoformat()
    target_str = target_date.isoformat()
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """SELECT artist_norm, album_norm, digest_date FROM albums
           WHERE digest_date >= ? AND digest_date < ?""",
        (cutoff, target_str),
    ).fetchall()
    conn.close()
    history: dict[tuple[str, str], str] = {}
    for artist_norm, album_norm, d in rows:
        k = (artist_norm, album_norm)
        if k not in history:
            history[k] = d
    return history
```

Then in `main()`, replace:
```python
history = collect_history(target_date, args.window)
```
with:
```python
from db import DB_PATH
history = collect_history_db(DB_PATH, target_date, args.window)
if not history and target_date > datetime.strptime("2026-05-01", "%Y-%m-%d").date():
    # fall back to JSON if DB is empty (shouldn't happen after Phase 2 is live)
    history = collect_history(target_date, args.window)
print(f"  loaded {len(history)} prior albums from DB (window {args.window}d)")
```

**Step 2: Run tests to confirm they pass**

```bash
cd $HOME/daily-dig
python -m pytest tests/test_dedup_albums.py -v
```

Expected: all 3 tests PASS.

**Step 3: Smoke-test against real data**

```bash
cd $HOME/daily-dig
python3 -c "
import sys; sys.path.insert(0,'scripts')
from dedup_albums import collect_history_db
from db import DB_PATH
from datetime import date
history = collect_history_db(DB_PATH, date(2026, 5, 5), 14)
print(f'DB returned {len(history)} prior albums')
for k,v in list(history.items())[:3]:
    print(f'  {k} → {v}')
"
```

Expected: prints several entries from 2026-05-04 (the only prior date in DB).

**Step 4: Commit**

```bash
git add scripts/dedup_albums.py
git commit -m "feat: dedup_albums reads cross-day history from SQLite albums table"
```

---

## Task 6: Add `--date` flag to `migrate_to_sqlite.py` for pipeline writes

The pipeline needs to write today's records into the DB after each run. Rather than a new script, we add a `--date` flag to `migrate_to_sqlite.py` so it only processes one date.

**Files:**
- Modify: `scripts/migrate_to_sqlite.py`

**Step 1: Write failing test**

```python
# Add to tests/test_dedup_raw.py or create tests/test_db_write.py
# tests/test_db_write.py
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def test_migrate_single_date_inserts_albums(tmp_path, tmp_db):
    conn, db_file = tmp_db
    # Write a fake parsed JSON for one date
    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    records = [
        {
            "artist": "Test Artist",
            "album": "Test Album",
            "genre": "Jazz",
            "year": "1970",
            "description_en": "Great record.",
            "description_zh": "好唱片。",
            "source_page": "THTRECORDs",
            "post_url": "https://fb.com/post/test1",
            "image_url": None,
            "local_image": None,
        }
    ]
    (parsed_dir / "2026-05-06.json").write_text(json.dumps(records))

    seen_dir = tmp_path / "seen"
    seen_dir.mkdir()
    (seen_dir / "2026-05-06.json").write_text(json.dumps(["https://fb.com/post/test1"]))

    from migrate_to_sqlite import upsert_albums_for_date, upsert_evaluations_for_date
    upsert_albums_for_date(conn, parsed_dir / "2026-05-06.json", "2026-05-06")
    upsert_evaluations_for_date(conn, seen_dir / "2026-05-06.json", "2026-05-06")

    album_count = conn.execute(
        "SELECT COUNT(*) FROM albums WHERE digest_date='2026-05-06'"
    ).fetchone()[0]
    eval_count = conn.execute(
        "SELECT COUNT(*) FROM evaluations WHERE evaluated_date='2026-05-06'"
    ).fetchone()[0]

    assert album_count == 1
    assert eval_count == 1
```

**Step 2: Run to confirm it fails**

```bash
cd $HOME/daily-dig
python -m pytest tests/test_db_write.py -v
```

Expected: `ImportError: cannot import name 'upsert_albums_for_date'`

**Step 3: Refactor `migrate_to_sqlite.py` — extract per-date helpers**

Rename the inner loop bodies of `upsert_albums` and `upsert_evaluations` into standalone functions `upsert_albums_for_date(conn, path, digest_date)` and `upsert_evaluations_for_date(conn, path, evaluated_date)`. The existing `upsert_albums`/`upsert_evaluations` functions become thin wrappers that iterate over the directory and call the new helpers.

Also add a `--date` argument to `main()`:

```python
p.add_argument("--date", help="Process only this date (YYYY-MM-DD). Default: all dates.")
```

When `--date` is given:
- Call `upsert_albums_for_date(conn, PARSED_DIR / f"{args.date}.json", args.date)` directly
- Call `upsert_evaluations_for_date(conn, SEEN_DIR / f"{args.date}.json", args.date)` directly
- Print a brief confirmation: `wrote {N} albums and {M} evaluations for {args.date}`

**Step 4: Run tests**

```bash
cd $HOME/daily-dig
python -m pytest tests/ -v
```

Expected: all tests PASS.

**Step 5: Smoke-test `--date` flag manually**

```bash
cd $HOME/daily-dig
python3 scripts/migrate_to_sqlite.py --date 2026-05-05
```

Expected: prints confirmation for 2026-05-05 without touching other dates.

**Step 6: Commit**

```bash
git add scripts/migrate_to_sqlite.py tests/test_db_write.py
git commit -m "feat: migrate_to_sqlite --date flag for single-day pipeline writes"
```

---

## Task 7: Update SKILL.md to write to DB after each run

**Files:**
- Modify: `.claude/skills/daily-dig/SKILL.md`

**Step 1: Add DB write calls in SKILL.md**

After **step 4** (Parse new posts — after writing `data/seen/{today}.json`):

```markdown
#### 4b. Write evaluations to DB

```bash
python3 scripts/migrate_to_sqlite.py --date {today}
```

This writes today's `data/seen/{today}.json` to the `evaluations` table so tomorrow's `dedup_raw.py` reads from DB. (The `albums` write is deferred until after album dedup in step 4.5 so only kept records enter the DB.)
```

After **step 4.5** (Cross-day album dedup — after `dedup_albums.py` rewrites the file):

```markdown
#### 4.6. Write deduped albums to DB

```bash
python3 scripts/migrate_to_sqlite.py --date {today}
```

This writes today's final (deduped) `data/parsed/{today}.json` into the `albums` table. The `UNIQUE(post_url, artist_norm, album_norm)` constraint will raise loudly on any parser image-collision bugs (same post_url on two different album rows from the same scrape).
```

Also update the **Quick reference** table to add a row:

```
| `data/dig.db` | SQLite DB — albums + evaluations history (committed) |
```

**Step 2: Re-read the updated SKILL.md section to verify the wording is correct**

**Step 3: Commit**

```bash
git add .claude/skills/daily-dig/SKILL.md
git commit -m "feat: SKILL.md writes evaluations + albums to SQLite after each pipeline run"
```

---

## Task 8: Final integration check

**Step 1: Run full test suite**

```bash
cd $HOME/daily-dig
python -m pytest tests/ -v
```

Expected: all tests PASS.

**Step 2: Verify DB integrity after any re-runs today**

```bash
sqlite3 data/dig.db 'SELECT digest_date, COUNT(*) FROM albums GROUP BY digest_date ORDER BY digest_date;'
sqlite3 data/dig.db 'SELECT COUNT(*) FROM evaluations;'
```

Expected: same counts as before (14 albums for 2026-05-05, 23 for 2026-05-04).

**Step 3: Dry-run dedup scripts against today to confirm they hit DB, not JSON**

```bash
cd $HOME/daily-dig
python3 scripts/dedup_albums.py --date 2026-05-05 --dry-run
```

Expected: output should include "loaded N prior albums from DB".

**Step 4: Commit DB if modified, then push**

```bash
git status
# if data/dig.db changed:
git add data/dig.db
git commit -m "chore: re-sync dig.db after Phase 2 integration check"
git push origin main
```
