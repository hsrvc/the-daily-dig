#!/usr/bin/env python3
"""
Update the Spotify 'The Daily Dig' playlist with one track per album
from today's parsed digest.

Usage:
    python3 scripts/update_spotify.py              # today
    python3 scripts/update_spotify.py 2026-05-04   # specific date
    python3 scripts/update_spotify.py --dry-run    # no writes

Env vars (from ~/.daily-dig.env or GH Actions secrets):
    SPOTIFY_CLIENT_ID
    SPOTIFY_CLIENT_SECRET
    SPOTIFY_REFRESH_TOKEN
    SPOTIFY_PLAYLIST_ID
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_DIR  = Path(__file__).resolve().parent.parent
NOTIFIED_DIR = PROJECT_DIR / "data" / "notified"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod
PARSED_DIR  = PROJECT_DIR / "data" / "parsed"
ENV_FILE    = Path.home() / ".daily-dig.env"
TPE         = ZoneInfo("Asia/Taipei")

API_BASE    = "https://api.spotify.com/v1"
TOKEN_URL   = "https://accounts.spotify.com/api/token"


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------

def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    for key in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET",
                "SPOTIFY_REFRESH_TOKEN", "SPOTIFY_PLAYLIST_ID"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def update_env_file(key: str, value: str) -> None:
    if not ENV_FILE.exists():
        return
    lines = []
    found = False
    for line in ENV_FILE.read_text().splitlines():
        if line.strip().startswith(f"{key}="):
            lines.append(f"{key}={value}")
            found = True
        else:
            lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n")


def update_gh_secret(key: str, value: str) -> None:
    import subprocess
    try:
        subprocess.run(
            ["gh", "secret", "set", key, "--body", value],
            capture_output=True, check=True,
            cwd=PROJECT_DIR,
        )
    except Exception as e:
        print(f"[warn] Could not update GH secret {key}: {e}")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _request(method: str, url: str, headers: dict, body: bytes | None = None, retries: int = 3) -> dict:
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", 2 ** attempt))
                print(f"[rate-limit] sleeping {wait}s (attempt {attempt+1}/{retries})")
                time.sleep(wait)
            elif e.code == 404:
                return {}
            else:
                raise
    raise RuntimeError(f"Max retries exceeded for {url}")


def api_get(url: str, token: str) -> dict:
    return _request("GET", url, {"Authorization": f"Bearer {token}"})


def api_post(url: str, token: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    return _request("POST", url, {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }, body=data)


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

def refresh_access_token(env: dict[str, str]) -> str:
    client_id     = env["SPOTIFY_CLIENT_ID"]
    client_secret = env["SPOTIFY_CLIENT_SECRET"]
    refresh_token = env["SPOTIFY_REFRESH_TOKEN"]

    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data  = urllib.parse.urlencode({
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, headers={
        "Authorization": f"Basic {creds}",
        "Content-Type":  "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req) as resp:
        tokens = json.loads(resp.read())

    if "refresh_token" in tokens and tokens["refresh_token"] != refresh_token:
        new_rt = tokens["refresh_token"]
        print("[oauth] refresh token rotated — updating")
        update_env_file("SPOTIFY_REFRESH_TOKEN", new_rt)
        if os.environ.get("GITHUB_ACTIONS"):
            update_gh_secret("SPOTIFY_REFRESH_TOKEN", new_rt)

    return tokens["access_token"]


# ---------------------------------------------------------------------------
# Pure helpers (testable without network)
# ---------------------------------------------------------------------------

def album_key(artist: str, album: str) -> str:
    # Normalize strips parentheticals so "monolog + Ai Ichikawa (市川愛)" and
    # "monolog + Ai Ichikawa" produce the same key across days
    return f"{dbmod.normalize(artist)}||{dbmod.normalize(album)}"


def is_various_artists(artist_norm: str) -> bool:
    return artist_norm in ("various artists", "v.a.", "va")


def pick_best_match(results: list[dict], artist: str, album: str) -> dict | None:
    if not results:
        return None

    def score(r: dict) -> tuple[int, int]:
        name_score = 0
        if r.get("name", "").lower() == album.lower():
            name_score += 10
        elif album.lower() in r.get("name", "").lower():
            name_score += 5
        artists = [a["name"].lower() for a in r.get("artists", [])]
        artist_l = artist.lower()
        if artist_l in artists:
            name_score += 5
        else:
            # Handle reversed word order (e.g. "Takada Wataru" vs "Wataru Takada")
            artist_words = set(artist_l.split())
            if artist_words and any(artist_words == set(a.split()) for a in artists):
                name_score += 5
        type_bonus = 3 if r.get("album_type") == "album" else 0
        return (name_score, name_score + type_bonus)

    ranked = sorted(results, key=lambda r: score(r)[1], reverse=True)
    best = ranked[0]
    # Require at least one name/artist match — type bonus alone is not enough
    return best if score(best)[0] > 0 else None


# ---------------------------------------------------------------------------
# Spotify API calls
# ---------------------------------------------------------------------------

def _extract_romanization(artist: str) -> str | None:
    """Extract Latin-script name from parenthetical, e.g. '高田渡 (Takada Wataru)' → 'Takada Wataru'."""
    import re
    m = re.search(r'[\(（]([A-Za-z][^\)）]+)[\)）]', artist)
    return m.group(1).strip() if m else None


def search_album(token: str, artist: str, album: str) -> dict | None:
    album_clean = dbmod.normalize(album).title()
    artist_norm = dbmod.normalize(artist)

    # Various Artists: artist constraint kills Spotify API results — search by title only
    if artist_norm == "various artists":
        q = urllib.parse.quote(f"album:{album_clean}")
        items = api_get(f"{API_BASE}/search?q={q}&type=album&limit=5", token).get("albums", {}).get("items", [])
        return items[0] if items else None

    q = urllib.parse.quote(f"album:{album_clean} artist:{artist_norm}")
    items = api_get(f"{API_BASE}/search?q={q}&type=album&limit=5", token).get("albums", {}).get("items", [])
    result = pick_best_match(items, artist, album)

    # CJK artist name didn't match — retry with romanized name from parenthetical
    if not result:
        romanized = _extract_romanization(artist)
        if romanized:
            print(f"    [retry] searching with romanized name: {romanized}")
            q = urllib.parse.quote(f"album:{album_clean} artist:{romanized}")
            items = api_get(f"{API_BASE}/search?q={q}&type=album&limit=5", token).get("albums", {}).get("items", [])
            result = pick_best_match(items, romanized, album)

    return result


def get_most_popular_track(token: str, artist: str, album: str) -> dict | None:
    album_clean = dbmod.normalize(album).title()
    artist_norm = dbmod.normalize(artist)

    # Various Artists: search by album title only
    if artist_norm == "various artists":
        q = urllib.parse.quote(f"album:{album_clean}")
        tracks = api_get(f"{API_BASE}/search?q={q}&type=track&limit=1", token).get("tracks", {}).get("items", [])
        return tracks[0] if tracks else None

    q = urllib.parse.quote(f"album:{album_clean} artist:{artist_norm}")
    tracks = api_get(f"{API_BASE}/search?q={q}&type=track&limit=1", token).get("tracks", {}).get("items", [])
    if tracks:
        return tracks[0]

    # CJK artist name didn't match — retry with romanized name from parenthetical
    romanized = _extract_romanization(artist)
    if romanized:
        print(f"    [retry] searching track with romanized name: {romanized}")
        q = urllib.parse.quote(f"album:{album_clean} artist:{romanized}")
        tracks = api_get(f"{API_BASE}/search?q={q}&type=track&limit=1", token).get("tracks", {}).get("items", [])
        return tracks[0] if tracks else None

    return None


def _artist_names_match(search_artist: str, track: dict) -> bool:
    """Return True if any credited artist loosely matches the search artist."""
    import re
    # Build a set of name variants to check against: normalized full name + parenthetical contents
    variants = {dbmod.normalize(search_artist)}
    for m in re.finditer(r"[\(（]([^\)）]+)[\)）]", search_artist):
        variants.add(dbmod.normalize(m.group(1)))
    for a in track.get("artists", []):
        a_norm = dbmod.normalize(a.get("name", ""))
        for v in variants:
            if v and (v in a_norm or a_norm in v):
                return True
            # Handle reversed word order (e.g. "Takada Wataru" vs "Wataru Takada")
            if v and set(v.split()) == set(a_norm.split()):
                return True
    return False



def llm_verify_artist_match(search_artist: str, track: dict) -> bool:
    """Ask Claude whether the track's credited artists include the artist we're looking for."""
    track_name = track.get("name", "?")
    credited = ", ".join(a["name"] for a in track.get("artists", []))
    prompt = (
        f"We searched Spotify for the artist '{search_artist}' and found the track '{track_name}' "
        f"credited to: {credited}. "
        f"Is '{search_artist}' the same artist as any of: {credited}? "
        f"Note: names that differ only by an article like 'The' (e.g. 'Dowser' vs 'The Dowser') "
        f"may be completely different artists — do not assume they are the same. "
        f"An exact or near-exact name match means YES. A genuinely different artist means NO. "
        f"Reply with only YES or NO."
    )
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=15,
        )
        answer = result.stdout.strip().upper()
        if answer.startswith("NO"):
            print(f"    [llm-verify] '{credited}' is not '{search_artist}' — skipping")
            return False
        if answer.startswith("YES"):
            print(f"    [llm-verify] confirmed '{credited}' is '{search_artist}'")
            return True
        print(f"    [llm-verify] unclear response '{answer}' — allowing track")
        return True
    except Exception as e:
        print(f"    [llm-verify] skipped ({e}) — allowing track")
        return True


def get_artist_top_track(token: str, artist: str) -> dict | None:
    q = urllib.parse.quote(f"artist:{artist}")
    data = api_get(f"{API_BASE}/search?q={q}&type=track&limit=10", token)
    tracks = data.get("tracks", {}).get("items", [])
    for track in tracks:
        primary = track.get("artists", [{}])[0]
        if _artist_names_match(artist, {"artists": [primary]}):
            return track
    if tracks:
        got = [a["name"] for a in tracks[0].get("artists", [])]
        print(f"    [skip fallback] no track found where '{artist}' is primary artist, got {got}")
    return None


def add_to_playlist(token: str, playlist_id: str, track_uri: str) -> None:
    api_post(f"{API_BASE}/playlists/{playlist_id}/items", token, {"uris": [track_uri]})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="?", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Run even if already-done marker exists.")
    args = parser.parse_args(argv)

    date_str = args.date or datetime.now(TPE).strftime("%Y-%m-%d")

    marker = NOTIFIED_DIR / f"spotify-{date_str}"
    if marker.exists() and not args.force and not args.dry_run:
        print(f"  update_spotify: already ran for {date_str} — use --force to re-run.")
        return 0

    parsed_file = PARSED_DIR / f"{date_str}.json"

    if not parsed_file.exists():
        print(f"ERROR: no parsed data for {date_str} at {parsed_file}")
        return 1

    env = load_env()
    for key in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "SPOTIFY_PLAYLIST_ID"):
        if key not in env:
            print(f"ERROR: {key} not set — cannot update Spotify")
            return 1

    albums = json.loads(parsed_file.read_text())
    print(f"=== Spotify updater | {date_str} | {len(albums)} albums ===")

    token       = refresh_access_token(env)
    playlist_id = env["SPOTIFY_PLAYLIST_ID"]

    conn = dbmod.connect()
    dbmod.init_schema(conn)

    found = missed = skipped = 0
    added_uris: set[str] = set()
    added_artists: set[str] = set()

    for rec in albums:
        artist = rec.get("artist", "").strip()
        album  = rec.get("album",  "").strip()
        if not artist or not album:
            continue

        key = album_key(artist, album)

        if dbmod.spotify_already_added(conn, key):
            print(f"  skip (dup): {artist} — {album}")
            skipped += 1
            continue

        artist_key = dbmod.normalize(artist)
        if not is_various_artists(artist_key) and artist_key in added_artists:
            print(f"  skip (artist already added today): {artist} — {album}")
            skipped += 1
            continue

        # spotify_artist is set by the parser when the credited artist is a band/project
        # unlikely to be on Spotify by that name, but a key individual behind it is findable
        # (e.g. The Milky Way → Makoto Matsushita). Used as fallback when primary search fails.
        spotify_artist = rec.get("spotify_artist", "").strip()

        print(f"  searching: {artist} — {album}")
        best_album = search_album(token, artist, album)

        if best_album:
            track = get_most_popular_track(token, artist, album)
        else:
            # Try spotify_artist (named key person) before falling back to generic top track
            if spotify_artist:
                print(f"    album not found, trying key artist: {spotify_artist}")
                best_album = search_album(token, spotify_artist, album)
                if best_album:
                    track = get_most_popular_track(token, spotify_artist, album)
                else:
                    print(f"    album still not found, trying {spotify_artist} top track")
                    track = get_artist_top_track(token, spotify_artist)
                    if track and not llm_verify_artist_match(spotify_artist, track):
                        track = None
            else:
                print(f"    album not found, trying artist top track")
                track = get_artist_top_track(token, artist)
                if track and not llm_verify_artist_match(artist, track):
                    track = None

        if not track:
            print(f"    [miss] no track found for {artist}")
            missed += 1
            continue

        track_uri  = track["uri"]
        track_name = track.get("name", "?")

        if track_uri in added_uris:
            print(f"    [skip] {track_name} already added this run — skipping duplicate")
            skipped += 1
            continue

        print(f"    -> {track_name} ({track_uri})")

        if not args.dry_run:
            add_to_playlist(token, playlist_id, track_uri)
            dbmod.spotify_mark_added(conn, key, best_album["id"] if best_album else None, track_uri, date_str)
            added_uris.add(track_uri)

        if not is_various_artists(artist_key):
            added_artists.add(artist_key)

        found += 1

    conn.close()
    print(f"\nDone: {found} added, {skipped} skipped, {missed} missed")

    if not args.dry_run:
        NOTIFIED_DIR.mkdir(parents=True, exist_ok=True)
        (NOTIFIED_DIR / f"spotify-{date_str}").touch()
        print(f"Marker written → data/notified/spotify-{date_str}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
