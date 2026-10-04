import sys
from pathlib import Path
from datetime import date, timedelta

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def test_collect_history_from_db(tmp_db):
    conn, db_file = tmp_db
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
    history, _ = collect_history_db(db_file, target_date=target, window_days=14)

    assert ("ryuichi sakamoto", "async") in history
    assert ("mobb deep", "murda muzik") in history
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
    history, _ = collect_history_db(db_file, target_date=target, window_days=14)

    assert ("old album", "from 2020") not in history


def test_collect_history_db_returns_empty_for_missing_db(tmp_path):
    from dedup_albums import collect_history_db
    history, shop_album = collect_history_db(
        tmp_path / "nonexistent.db",
        target_date=date(2026, 5, 5),
        window_days=14,
    )
    assert history == {}
    assert shop_album == {}


def test_shop_album_dedup_catches_artist_variant(tmp_db):
    """Same shop + same album title = duplicate even if artist spelling differs."""
    conn, db_file = tmp_db
    conn.execute(
        """INSERT INTO albums
           (artist, album, artist_norm, album_norm, source_page, post_url, digest_date)
           VALUES (?,?,?,?,?,?,?)""",
        (
            "The Heliocentrics / Marshall Allen", "Nuclear War",
            "the heliocentrics / marshall allen", "nuclear war",
            "beethobearrecords", "https://fb.com/bbb/pfbid0old", "2026-05-04",
        ),
    )
    conn.commit()

    from dedup_albums import collect_history_db
    target = date(2026, 5, 5)
    history, shop_album = collect_history_db(db_file, target_date=target, window_days=14)

    # Full (artist_norm, album_norm) won't match due to different artist spelling
    assert ("the heliocentrics, marshall allen, knoel scott", "nuclear war") not in history
    # But shop+album will match
    assert ("beethobearrecords", "nuclear war") in shop_album
    assert shop_album[("beethobearrecords", "nuclear war")] == "2026-05-04"
