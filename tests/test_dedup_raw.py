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
