#!/usr/bin/env python3
"""
Fetch cover art for parsed album records.

Priority chain per album:
  1. local_image already on disk → skip
  2. image_url (Facebook CDN) → download
  3. iTunes Search API → 600x600 artwork
  4. MusicBrainz + Cover Art Archive → front art

On success, writes local_image back into the parsed JSON in place.

Usage:
    python scripts/fetch_covers.py                    # Today's date
    python scripts/fetch_covers.py --date 2026-05-04
    python scripts/fetch_covers.py --input path.json  # Explicit file
    python scripts/fetch_covers.py --force            # Ignore existing files
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
PARSED_DIR = PROJECT_DIR / "data" / "parsed"
COVERS_DIR = PROJECT_DIR / "site" / "public" / "covers"

try:
    from db import DB_PATH, connect, hash_bytes, write_cover_row, normalize
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False

UA_GENERIC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
UA_MUSICBRAINZ = "TheDailyDig/0.1 (https://dailydig.erdscribe.com)"

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30


def slugify(s: str, max_len: int | None = None) -> str:
    s = s.lower()
    # Preserve a-z, 0-9, and CJK Unified Ideographs (incl. extension A).
    # Parentheses and other punctuation become hyphens so filenames are URL-safe.
    s = re.sub(r"[^a-z0-9㐀-鿿]+", "-", s)
    s = s.strip("-")
    if max_len is not None and len(s) > max_len:
        s = s[:max_len]
    return s


def _fb_photo_id(url: str) -> str | None:
    """Extract the FB photo ID from a CDN URL, e.g. '686874520_1823193482398423'."""
    m = re.search(r"/(\d+_\d+)_\d+_[a-z]\.jpg", url)
    return m.group(1) if m else None


def cover_path_for(record: dict) -> Path:
    """Return the absolute filesystem path where this album's cover should live."""
    existing = record.get("local_image", "")
    if existing.startswith("/covers/"):
        return COVERS_DIR / existing[len("/covers/"):]
    artist = slugify(record.get("artist", "unknown"))
    album = slugify(record.get("album", "unknown"), max_len=30)
    return COVERS_DIR / f"{artist}_{album}.jpg"


def get_album_id(conn, record: dict) -> int | None:
    """Look up album.id by (post_url, artist_norm, album_norm). Returns None if not found."""
    row = conn.execute(
        "SELECT id FROM albums WHERE post_url = ? AND artist_norm = ? AND album_norm = ?",
        (
            record.get("post_url", ""),
            normalize(record.get("artist", "")),
            normalize(record.get("album", "")),
        ),
    ).fetchone()
    return row["id"] if row else None


def http_get(url: str, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA_GENERIC})
    with urllib.request.urlopen(req, timeout=READ_TIMEOUT) as resp:
        return resp.read()


def http_get_json(url: str, headers: dict | None = None) -> dict | list:
    return json.loads(http_get(url, headers=headers).decode("utf-8"))


def try_download(url: str) -> bytes | None:
    try:
        data = http_get(url)
        if len(data) < 1024:
            return None
        return data
    except Exception as e:
        print(f"      download failed: {e}")
        return None


def try_fb_cache(url: str, date_str: str) -> bytes | None:
    """Check local image cache written by scrape_apify.py at scrape time."""
    # Key by photo ID (stable across CDN edge variants and URL query params)
    m = re.search(r"/(\d+_\d+)_\d+_[a-z]\.", url)
    key = m.group(1) if m else hashlib.md5(url.encode()).hexdigest()[:16]
    cache_file = PROJECT_DIR / "data" / "images" / date_str / f"{key}.jpg"
    if cache_file.exists() and cache_file.stat().st_size > 1024:
        return cache_file.read_bytes()
    return None


def try_itunes(artist: str, album: str) -> bytes | None:
    term = urllib.parse.quote(f"{artist} {album}")
    url = f"https://itunes.apple.com/search?term={term}&entity=album&limit=1"
    try:
        body = http_get_json(url)
    except Exception as e:
        print(f"      iTunes search failed: {e}")
        return None
    results = body.get("results") if isinstance(body, dict) else None
    if not results:
        return None
    art_url = results[0].get("artworkUrl100", "")
    if not art_url:
        return None
    hi_res = art_url.replace("100x100bb.jpg", "600x600bb.jpg")
    return try_download(hi_res)


def try_musicbrainz(artist: str, album: str) -> bytes | None:
    query = f'artist:"{artist}" AND release:"{album}"'
    url = (
        "https://musicbrainz.org/ws/2/release/"
        f"?query={urllib.parse.quote(query)}&fmt=json&limit=5"
    )
    try:
        body = http_get_json(url, headers={"User-Agent": UA_MUSICBRAINZ})
    except Exception as e:
        print(f"      MusicBrainz search failed: {e}")
        return None
    releases = body.get("releases", []) if isinstance(body, dict) else []
    for rel in releases:
        mbid = rel.get("id")
        if not mbid:
            continue
        art_url = f"https://coverartarchive.org/release/{mbid}/front-500"
        try:
            data = http_get(art_url, headers={"User-Agent": UA_MUSICBRAINZ})
            if data and len(data) > 1024:
                return data
        except Exception:
            continue
        time.sleep(1)  # MusicBrainz rate limit politeness
    return None


def _hashed_target(record: dict, data: bytes) -> Path:
    """Return the hashed cover path for freshly-downloaded bytes.

    Derives the slug base from artist/album (same logic as cover_path_for when
    no local_image is set), appends the 8-char SHA-256 prefix, and returns the
    absolute path inside COVERS_DIR.
    """
    artist = slugify(record.get("artist", "unknown"))
    album = slugify(record.get("album", "unknown"), max_len=30)
    slug_base = f"{artist}_{album}"
    h8 = hash_bytes(data)[:8]
    return COVERS_DIR / f"{slug_base}-{h8}.jpg"


def fetch_one(record: dict, force: bool = False, conn=None, date_str: str = "") -> str:
    """Returns a status string: ok-cached | ok-fb | ok-itunes | ok-mb | failed."""
    artist = record.get("artist", "").strip()
    album = record.get("album", "").strip()
    print(f"  {artist} — {album}")

    target = cover_path_for(record)
    target.parent.mkdir(parents=True, exist_ok=True)

    if not force and target.exists() and target.stat().st_size > 1024:
        record["local_image"] = f"/covers/{target.name}"
        print(f"      cached: {target.name}")
        return "ok-cached"

    fb_url = record.get("image_url", "")
    if fb_url:
        # Reuse existing cover if we've already downloaded from the same FB photo ID.
        # FB CDN serves the same photo_id from different edge servers which can return
        # different content — reusing avoids stale/wrong CDN responses.
        if conn is not None:
            photo_id = _fb_photo_id(fb_url)
            if photo_id:
                existing = conn.execute(
                    "SELECT file_path FROM covers WHERE fetched_url LIKE ? LIMIT 1",
                    (f"%{photo_id}%",)
                ).fetchone()
                if existing and existing["file_path"]:
                    existing_path = PROJECT_DIR / existing["file_path"]
                    if existing_path.exists():
                        target = _hashed_target(record, existing_path.read_bytes())
                        if not target.exists():
                            import shutil
                            shutil.copy2(existing_path, target)
                        record["local_image"] = f"/covers/{target.name}"
                        print(f"      fb: reused existing cover for photo_id {photo_id}")
                        album_id = get_album_id(conn, record)
                        if album_id is not None:
                            write_cover_row(conn, album_id, "fb_cdn_reuse", fb_url,
                                            str(target.relative_to(PROJECT_DIR)), hash_bytes(target.read_bytes()))
                        return "ok-fb"
        # Try local image cache (downloaded at scrape time before CDN expiry)
        if date_str:
            data = try_fb_cache(fb_url, date_str)
            if data:
                target = _hashed_target(record, data)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                record["local_image"] = f"/covers/{target.name}"
                print(f"      fb-cache: {target.name} ({len(data)} bytes)")
                if conn is not None:
                    album_id = get_album_id(conn, record)
                    if album_id is not None:
                        write_cover_row(conn, album_id, "fb_cache", fb_url,
                                        str(target.relative_to(PROJECT_DIR)), hash_bytes(data))
                return "ok-fb"

        data = try_download(fb_url)
        if data:
            target = _hashed_target(record, data)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            record["local_image"] = f"/covers/{target.name}"
            print(f"      fb: {target.name} ({len(data)} bytes)")
            if conn is not None:
                album_id = get_album_id(conn, record)
                if album_id is not None:
                    write_cover_row(conn, album_id, "fb_cdn", fb_url,
                                    str(target.relative_to(PROJECT_DIR)), hash_bytes(data))
            return "ok-fb"
        print("      fb: 4xx/expired, falling back")

    if artist and album:
        data = try_itunes(artist, album)
        if data:
            target = _hashed_target(record, data)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            record["local_image"] = f"/covers/{target.name}"
            print(f"      itunes: {target.name} ({len(data)} bytes)")
            if conn is not None:
                album_id = get_album_id(conn, record)
                if album_id is not None:
                    write_cover_row(conn, album_id, "itunes", None,
                                    str(target.relative_to(PROJECT_DIR)), hash_bytes(data))
            return "ok-itunes"

        data = try_musicbrainz(artist, album)
        if data:
            target = _hashed_target(record, data)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            record["local_image"] = f"/covers/{target.name}"
            print(f"      musicbrainz: {target.name} ({len(data)} bytes)")
            if conn is not None:
                album_id = get_album_id(conn, record)
                if album_id is not None:
                    write_cover_row(conn, album_id, "musicbrainz", None,
                                    str(target.relative_to(PROJECT_DIR)), hash_bytes(data))
            return "ok-mb"

    print(f"      FAILED: no cover found for {artist} — {album}")
    return "failed"


def main():
    parser = argparse.ArgumentParser(description="Fetch cover art for parsed records")
    parser.add_argument("--date", default=str(date.today()))
    parser.add_argument("--input", help="Explicit parsed JSON path")
    parser.add_argument("--force", action="store_true", help="Re-download even if cached")
    args = parser.parse_args()

    in_path = Path(args.input) if args.input else PARSED_DIR / f"{args.date}.json"
    if not in_path.exists():
        print(f"ERROR: parsed file not found: {in_path}")
        sys.exit(1)

    records = json.loads(in_path.read_text(encoding="utf-8"))
    print(f"Fetching covers for {len(records)} albums from {in_path.name}")

    conn = None
    if _DB_AVAILABLE:
        try:
            conn = connect(DB_PATH)
        except Exception as e:
            print(f"  note: could not connect to DB ({e}) — covers table will not be updated")

    counts: dict[str, int] = {}
    for r in records:
        status = fetch_one(r, force=args.force, conn=conn, date_str=args.date)
        counts[status] = counts.get(status, 0) + 1

    if conn is not None:
        conn.close()

    in_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nSummary:")
    for k in ("ok-cached", "ok-fb", "ok-itunes", "ok-mb", "failed"):
        if k in counts:
            print(f"  {k}: {counts[k]}")
    print(f"Updated {in_path}")

    if counts.get("failed", 0) > 0:
        sys.exit(2)


if __name__ == "__main__":
    main()
