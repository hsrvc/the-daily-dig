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
