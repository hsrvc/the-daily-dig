import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def _insert_album(conn, *, artist, album, source_page, post_url, digest_date):
    cur = conn.execute(
        """INSERT INTO albums
           (artist, album, artist_norm, album_norm, source_page, post_url, digest_date)
           VALUES (?,?,?,?,?,?,?)""",
        (
            artist, album,
            artist.lower(), album.lower(),
            source_page, post_url, digest_date,
        ),
    )
    conn.commit()
    return cur.lastrowid


def _insert_cover(conn, *, album_id, file_hash, source="backfill"):
    conn.execute(
        "INSERT INTO covers (album_id, source, file_hash) VALUES (?,?,?)",
        (album_id, source, file_hash),
    )
    conn.commit()


def test_find_cover_duplicates_detects_cross_day_collision(tmp_db):
    conn, db_file = tmp_db

    prior_id = _insert_album(
        conn,
        artist="The Heliocentrics / Marshall Allen", album="Nuclear War",
        source_page="beethobearrecords", post_url="https://fb.com/p1",
        digest_date="2026-05-04",
    )
    _insert_cover(conn, album_id=prior_id, file_hash="aabbcc1122")

    today_id = _insert_album(
        conn,
        artist="The Heliocentrics, Marshall Allen, Knoel Scott", album="Nuclear War",
        source_page="beethobearrecords", post_url="https://fb.com/p2",
        digest_date="2026-05-05",
    )
    _insert_cover(conn, album_id=today_id, file_hash="aabbcc1122")

    from dedup_covers import find_cover_duplicates
    dups = find_cover_duplicates(conn, "2026-05-05", window_days=14)

    assert len(dups) == 1
    assert dups[0]["album_norm"] == "nuclear war"
    assert dups[0]["prior_date"] == "2026-05-04"
    assert dups[0]["hash"] == "aabbcc1122"


def test_find_cover_duplicates_no_false_positive_for_unique_hash(tmp_db):
    conn, db_file = tmp_db

    prior_id = _insert_album(
        conn,
        artist="Mobb Deep", album="Murda Muzik",
        source_page="uourecords", post_url="https://fb.com/p1",
        digest_date="2026-05-04",
    )
    _insert_cover(conn, album_id=prior_id, file_hash="hash_prior")

    today_id = _insert_album(
        conn,
        artist="Ryuichi Sakamoto", album="Async",
        source_page="THTRECORDs", post_url="https://fb.com/p2",
        digest_date="2026-05-05",
    )
    _insert_cover(conn, album_id=today_id, file_hash="hash_today_different")

    from dedup_covers import find_cover_duplicates
    dups = find_cover_duplicates(conn, "2026-05-05", window_days=14)

    assert dups == []


def test_find_cover_duplicates_respects_window(tmp_db):
    conn, db_file = tmp_db

    old_id = _insert_album(
        conn,
        artist="Sun Ra", album="Nuclear War",
        source_page="beethobearrecords", post_url="https://fb.com/p_old",
        digest_date="2020-01-01",
    )
    _insert_cover(conn, album_id=old_id, file_hash="samehashabc")

    today_id = _insert_album(
        conn,
        artist="Sun Ra", album="Nuclear War",
        source_page="beethobearrecords", post_url="https://fb.com/p_today",
        digest_date="2026-05-05",
    )
    _insert_cover(conn, album_id=today_id, file_hash="samehashabc")

    from dedup_covers import find_cover_duplicates
    # 14-day window — 2020-01-01 is far outside
    dups = find_cover_duplicates(conn, "2026-05-05", window_days=14)

    assert dups == []
