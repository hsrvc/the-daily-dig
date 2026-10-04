"""
SQLite schema + connection helpers for The Daily Dig.

Tables:
  albums       — one row per (post_url, album) recommendation that was kept and
                 published. Carries the data the digest renders.
  evaluations  — one row per Facebook post URL ever evaluated by the parser,
                 kept or skipped. Replaces data/seen/*.json. The dedup-against-
                 history step queries this table.
  covers       — one row per cover-image fetch attempt with provenance: which
                 source it came from (FB CDN / iTunes / MusicBrainz), the URL
                 hit, the local file path, and the file's SHA-256. Same hash
                 across albums = parser image-collision bug, surfaceable via
                 a single SQL query.

Normalized fields:
  artist_norm / album_norm — same string after stripping parenthetical
  suffixes (CJK readings, edition tags, vinyl colors) and lowercasing. Used
  for cross-day dedup queries that should treat
    "Ryuichi Sakamoto" and "Ryuichi Sakamoto (坂本龍一)" as the same artist
    "Now Playing"      and "Now Playing (Blue Vinyl)"      as the same album.

DB file: data/dig.db (committed to git — small at our scale, gives full
provenance after a fresh checkout).
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "dig.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS albums (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    artist          TEXT NOT NULL,
    album           TEXT NOT NULL,
    artist_norm     TEXT NOT NULL,
    album_norm      TEXT NOT NULL,
    genre           TEXT,
    year            TEXT,
    description_en  TEXT,
    description_zh  TEXT,
    source_page     TEXT NOT NULL,
    post_url        TEXT NOT NULL,
    image_url       TEXT,
    local_image     TEXT,
    digest_date     TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    -- A single FB post may list multiple albums (uourecords batches, Blue Note
    -- Essentials sets, etc.) so post_url alone is not unique. The natural key
    -- is (post_url, artist_norm, album_norm): the same album from the same post
    -- shouldn't appear twice.
    UNIQUE (post_url, artist_norm, album_norm)
);

CREATE INDEX IF NOT EXISTS idx_albums_post_url    ON albums(post_url);
CREATE INDEX IF NOT EXISTS idx_albums_norm        ON albums(artist_norm, album_norm);
CREATE INDEX IF NOT EXISTS idx_albums_image_url   ON albums(image_url) WHERE image_url <> '';
CREATE INDEX IF NOT EXISTS idx_albums_digest_date ON albums(digest_date);

CREATE TABLE IF NOT EXISTS evaluations (
    post_url        TEXT PRIMARY KEY,
    source_page     TEXT NOT NULL,
    evaluated_date  TEXT NOT NULL,
    kept            INTEGER NOT NULL CHECK (kept IN (0, 1)),
    skip_reason     TEXT,
    -- Apify's stable numeric post id. Facebook rotates the pfbid token inside
    -- post_url periodically, so URL-only dedup silently re-evaluates old posts
    -- (observed 2026-09-21: 29/40 posts re-presented as new). post_id is the
    -- real dedup key; post_url is kept for provenance and legacy rows.
    post_id         TEXT
);

CREATE INDEX IF NOT EXISTS idx_evaluations_date ON evaluations(evaluated_date);
CREATE INDEX IF NOT EXISTS idx_evaluations_post_id ON evaluations(post_id) WHERE post_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS covers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id     INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    source       TEXT NOT NULL,
    fetched_url  TEXT,
    file_path    TEXT,
    file_hash    TEXT,
    fetched_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_covers_hash     ON covers(file_hash) WHERE file_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_covers_album_id ON covers(album_id);

CREATE TABLE IF NOT EXISTS spotify_added (
    album_key        TEXT PRIMARY KEY,
    spotify_album_id TEXT,
    track_uri        TEXT NOT NULL,
    added_at         TEXT NOT NULL
);
"""

# Strip parenthetical suffixes (ASCII and full-width) when normalizing
# artist / album for cross-day dedup. See the docstring at top.
_PAREN_RE = re.compile(r"\s*[\(（][^\)）]*[\)）]\s*")


def normalize(s: str) -> str:
    s = _PAREN_RE.sub(" ", s or "")
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


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


def cover_collisions(conn: sqlite3.Connection) -> list:
    """Return rows where the same file_hash appears on more than one album.
    Each row has: file_hash, n (count), albums (comma-joined 'artist — album (date)')."""
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


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open a connection with sane defaults for our use case."""
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if they don't exist. Safe to call repeatedly."""
    # Migrate DBs created before evaluations.post_id existed. Must run before
    # the schema script, which creates an index on that column.
    has_eval = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evaluations'"
    ).fetchone()
    if has_eval:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(evaluations)")}
        if "post_id" not in cols:
            conn.execute("ALTER TABLE evaluations ADD COLUMN post_id TEXT")
    conn.executescript(SCHEMA)
    conn.commit()


def spotify_already_added(conn: sqlite3.Connection, album_key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM spotify_added WHERE album_key = ?", (album_key,)
    ).fetchone()
    return row is not None


def spotify_mark_added(
    conn: sqlite3.Connection,
    album_key: str,
    spotify_album_id: str | None,
    track_uri: str,
    added_at: str,
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO spotify_added (album_key, spotify_album_id, track_uri, added_at)
           VALUES (?, ?, ?, ?)""",
        (album_key, spotify_album_id, track_uri, added_at),
    )
    conn.commit()


if __name__ == "__main__":
    # Bare invocation creates an empty DB at the default path.
    conn = connect()
    init_schema(conn)
    print(f"initialized schema at {DB_PATH}")
    print("tables:")
    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        print(f"  - {row['name']}")
    conn.close()
