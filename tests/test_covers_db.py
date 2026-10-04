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


def test_cover_collisions_detects_duplicate_hash(tmp_db):
    conn, _ = tmp_db
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
