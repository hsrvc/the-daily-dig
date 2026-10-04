"""Tests for pure helper functions in update_spotify.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import update_spotify as sp


def test_pick_best_match_exact():
    results = [
        {"name": "Hejira", "album_type": "album", "artists": [{"name": "Joni Mitchell"}], "id": "aaa"},
        {"name": "Hejira (Deluxe)", "album_type": "album", "artists": [{"name": "Joni Mitchell"}], "id": "bbb"},
    ]
    best = sp.pick_best_match(results, "Joni Mitchell", "Hejira")
    assert best["id"] == "aaa"


def test_pick_best_match_prefers_album_type():
    results = [
        {"name": "Hejira", "album_type": "single", "artists": [{"name": "Joni Mitchell"}], "id": "single"},
        {"name": "Hejira", "album_type": "album",  "artists": [{"name": "Joni Mitchell"}], "id": "album"},
    ]
    best = sp.pick_best_match(results, "Joni Mitchell", "Hejira")
    assert best["id"] == "album"


def test_pick_best_match_empty():
    assert sp.pick_best_match([], "artist", "album") is None


def test_album_key():
    assert sp.album_key("Joni Mitchell", "Hejira") == "joni mitchell||hejira"
    assert sp.album_key("Ryuichi Sakamoto", "B-2 Unit") == "ryuichi sakamoto||b-2 unit"


def test_pick_best_match_no_good_results():
    # Results that don't match artist or album at all should return None
    results = [
        {"name": "Something Else", "album_type": "album", "artists": [{"name": "Other Artist"}], "id": "zzz"},
    ]
    assert sp.pick_best_match(results, "Joni Mitchell", "Hejira") is None


def test_pick_best_match_artist_match_scores():
    results = [
        {"name": "Hejira", "album_type": "album", "artists": [{"name": "Other Artist"}], "id": "no_artist"},
        {"name": "Different Name", "album_type": "album", "artists": [{"name": "Joni Mitchell"}], "id": "with_artist"},
    ]
    # Both score > 0; first has album name match (10+3=13), second has artist match (5+3=8)
    best = sp.pick_best_match(results, "Joni Mitchell", "Hejira")
    assert best["id"] == "no_artist"


def test_is_various_artists():
    assert sp.is_various_artists("various artists")
    assert sp.is_various_artists("v.a.")
    assert sp.is_various_artists("va")
    assert not sp.is_various_artists("coldplay")
    assert not sp.is_various_artists("the jesus and mary chain")
