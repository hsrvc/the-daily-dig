import sqlite3
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import db

def make_conn():
    conn = db.connect(Path(":memory:"))
    db.init_schema(conn)
    return conn

def test_spotify_already_added_false_initially():
    conn = make_conn()
    assert db.spotify_already_added(conn, "joni mitchell||hejira") is False

def test_spotify_mark_and_check():
    conn = make_conn()
    db.spotify_mark_added(conn, "joni mitchell||hejira", "spotify_album_123", "spotify:track:abc", "2026-05-05")
    assert db.spotify_already_added(conn, "joni mitchell||hejira") is True

def test_spotify_already_added_different_key():
    conn = make_conn()
    db.spotify_mark_added(conn, "joni mitchell||hejira", None, "spotify:track:abc", "2026-05-05")
    assert db.spotify_already_added(conn, "joni mitchell||blue") is False

def test_spotify_mark_added_null_album_id():
    conn = make_conn()
    db.spotify_mark_added(conn, "unknown artist||unknown album", None, "spotify:track:xyz", "2026-05-05")
    assert db.spotify_already_added(conn, "unknown artist||unknown album") is True

def test_spotify_mark_added_idempotent():
    conn = make_conn()
    db.spotify_mark_added(conn, "joni mitchell||hejira", "album_id", "spotify:track:abc", "2026-05-05")
    db.spotify_mark_added(conn, "joni mitchell||hejira", "album_id", "spotify:track:abc", "2026-05-05")
    count = conn.execute("SELECT COUNT(*) FROM spotify_added WHERE album_key = 'joni mitchell||hejira'").fetchone()[0]
    assert count == 1
