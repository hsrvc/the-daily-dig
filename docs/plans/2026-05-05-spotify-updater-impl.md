# Spotify Playlist Updater — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add the most popular track from each daily recommended album to a dedicated Spotify playlist, with SQLite dedup to skip already-added albums.

**Architecture:** `spotify_auth.py` handles one-time OAuth setup (Authorization Code flow → refresh token stored in `~/.daily-dig.env`). `update_spotify.py` is the daily pipeline script: reads today's parsed JSON, searches Spotify for each album, picks the most popular track (artist top-track fallback), adds to playlist, records in SQLite. Wired into `pipeline.sh --spotify` as a soft-fail step 7.

**Tech Stack:** Python 3, `urllib.request` (stdlib, consistent with `send_email.py`), SQLite via existing `db.py`, Spotify Web API v1.

---

## Task 1: Add `spotify_added` table to `db.py`

**Files:**
- Modify: `scripts/db.py` (SCHEMA string + 2 helper functions)
- Test: `tests/test_db_spotify.py` (create)

**Step 1: Write the failing test**

```python
# tests/test_db_spotify.py
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
    # artist-fallback path: spotify_album_id may be None
    conn = make_conn()
    db.spotify_mark_added(conn, "unknown artist||unknown album", None, "spotify:track:xyz", "2026-05-05")
    assert db.spotify_already_added(conn, "unknown artist||unknown album") is True
```

**Step 2: Run test to verify it fails**

```bash
cd $HOME/daily-dig
python3 -m pytest tests/test_db_spotify.py -v
```
Expected: `AttributeError: module 'db' has no attribute 'spotify_already_added'`

**Step 3: Add to `db.py`**

Add the table to the `SCHEMA` string (after the `covers` table block):

```python
CREATE TABLE IF NOT EXISTS spotify_added (
    album_key        TEXT PRIMARY KEY,
    spotify_album_id TEXT,
    track_uri        TEXT NOT NULL,
    added_at         TEXT NOT NULL
);
```

Add two functions at the end of `db.py` (before `if __name__ == "__main__":`):

```python
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
```

**Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_db_spotify.py -v
```
Expected: 4 passed

**Step 5: Commit**

```bash
git add scripts/db.py tests/test_db_spotify.py
git commit -m "feat: add spotify_added table and helpers to db.py"
```

---

## Task 2: Write `spotify_auth.py` (one-time OAuth setup)

**Files:**
- Create: `scripts/spotify_auth.py`

No automated tests — this is an interactive local-only script. Manual test at the end.

**Step 1: Create `scripts/spotify_auth.py`**

```python
#!/usr/bin/env python3
"""
One-time Spotify OAuth setup.

Run this locally once to get a refresh token and store it in ~/.daily-dig.env.
Never run this in CI.

Usage:
    python3 scripts/spotify_auth.py

Requires in ~/.daily-dig.env (or env vars):
    SPOTIFY_CLIENT_ID
    SPOTIFY_CLIENT_SECRET

Creates/updates in ~/.daily-dig.env:
    SPOTIFY_REFRESH_TOKEN
    SPOTIFY_PLAYLIST_ID
"""
from __future__ import annotations

import http.server
import json
import os
import secrets
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

REDIRECT_URI = "http://localhost:8888/callback"
SCOPES = "playlist-modify-public playlist-read-private"
ENV_FILE = Path.home() / ".daily-dig.env"
PLAYLIST_NAME = "The Daily Dig"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    # env vars override file
    for key in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def write_env(updates: dict[str, str]) -> None:
    lines: list[str] = []
    existing: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith("#"):
                k, _, v = stripped.partition("=")
                existing[k.strip()] = v.strip()
            else:
                lines.append(line)
    existing.update(updates)
    for k, v in existing.items():
        lines.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    print(f"Written to {ENV_FILE}")


def get_auth_code(client_id: str) -> str:
    state = secrets.token_urlsafe(16)
    code_holder: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                code_holder.append(params["code"][0])
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Auth complete. You can close this tab.</h2>")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("localhost", 8888), Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()

    params = urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
    })
    url = f"https://accounts.spotify.com/authorize?{params}"
    print(f"\nOpening browser for Spotify login...\n{url}\n")
    webbrowser.open(url)
    thread.join(timeout=120)

    if not code_holder:
        raise RuntimeError("No auth code received within 120s")
    return code_holder[0]


def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }).encode()
    import base64
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=data,
        headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def find_or_create_playlist(access_token: str) -> str:
    """Return playlist ID for 'The Daily Dig', creating it if needed."""
    headers = {"Authorization": f"Bearer {access_token}"}

    # Get current user ID
    req = urllib.request.Request("https://api.spotify.com/v1/me", headers=headers)
    with urllib.request.urlopen(req) as resp:
        user_id = json.loads(resp.read())["id"]

    # List playlists (first 50)
    req = urllib.request.Request(
        f"https://api.spotify.com/v1/users/{user_id}/playlists?limit=50",
        headers=headers,
    )
    with urllib.request.urlopen(req) as resp:
        items = json.loads(resp.read()).get("items", [])

    for pl in items:
        if pl["name"] == PLAYLIST_NAME:
            print(f"Found existing playlist: {pl['id']}")
            return pl["id"]

    # Create new playlist
    body = json.dumps({"name": PLAYLIST_NAME, "public": True, "description": "Daily vinyl recommendations from The Daily Dig"}).encode()
    req = urllib.request.Request(
        f"https://api.spotify.com/v1/users/{user_id}/playlists",
        data=body,
        headers={**headers, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        pl = json.loads(resp.read())
        print(f"Created new playlist: {pl['id']}")
        return pl["id"]


def main():
    env = load_env()
    client_id = env.get("SPOTIFY_CLIENT_ID") or input("SPOTIFY_CLIENT_ID: ").strip()
    client_secret = env.get("SPOTIFY_CLIENT_SECRET") or input("SPOTIFY_CLIENT_SECRET: ").strip()

    code = get_auth_code(client_id)
    print("Got auth code. Exchanging for tokens...")
    tokens = exchange_code(client_id, client_secret, code)

    access_token = tokens["access_token"]
    refresh_token = tokens["refresh_token"]
    print(f"Got refresh token: {refresh_token[:10]}...")

    playlist_id = find_or_create_playlist(access_token)

    write_env({
        "SPOTIFY_CLIENT_ID": client_id,
        "SPOTIFY_CLIENT_SECRET": client_secret,
        "SPOTIFY_REFRESH_TOKEN": refresh_token,
        "SPOTIFY_PLAYLIST_ID": playlist_id,
    })
    print("\nDone! Now add these as GitHub Actions secrets:")
    print("  SPOTIFY_CLIENT_ID")
    print("  SPOTIFY_CLIENT_SECRET")
    print("  SPOTIFY_REFRESH_TOKEN")
    print("  SPOTIFY_PLAYLIST_ID")


if __name__ == "__main__":
    main()
```

**Step 2: Make executable**

```bash
chmod +x scripts/spotify_auth.py
```

**Step 3: Commit**

```bash
git add scripts/spotify_auth.py
git commit -m "feat: add spotify_auth.py for one-time OAuth setup"
```

---

## Task 3: Write `update_spotify.py` (core pipeline script)

**Files:**
- Create: `scripts/update_spotify.py`
- Test: `tests/test_update_spotify.py` (create)

**Step 1: Write failing tests for the helper functions**

```python
# tests/test_update_spotify.py
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
    assert sp.album_key("Ryuichi Sakamoto (坂本龍一)", "B-2 Unit") == "ryuichi sakamoto (坂本龍一)||b-2 unit"
```

**Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_update_spotify.py -v
```
Expected: `ModuleNotFoundError: No module named 'update_spotify'`

**Step 3: Create `scripts/update_spotify.py`**

```python
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
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_DIR = Path(__file__).resolve().parent.parent
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
        "Authorization":  f"Basic {creds}",
        "Content-Type":   "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req) as resp:
        tokens = json.loads(resp.read())

    # Persist rotated refresh token if Spotify issued a new one
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
    return f"{artist.lower()}||{album.lower()}"


def pick_best_match(results: list[dict], artist: str, album: str) -> dict | None:
    if not results:
        return None

    def score(r: dict) -> int:
        s = 0
        if r.get("name", "").lower() == album.lower():
            s += 10
        elif album.lower() in r.get("name", "").lower():
            s += 5
        if r.get("album_type") == "album":
            s += 3
        artists = [a["name"].lower() for a in r.get("artists", [])]
        if artist.lower() in artists:
            s += 5
        return s

    ranked = sorted(results, key=score, reverse=True)
    return ranked[0] if score(ranked[0]) > 0 else None


# ---------------------------------------------------------------------------
# Spotify API calls
# ---------------------------------------------------------------------------

def search_album(token: str, artist: str, album: str) -> dict | None:
    q = urllib.parse.quote(f"album:{album} artist:{artist}")
    url = f"{API_BASE}/search?q={q}&type=album&limit=5"
    data = api_get(url, token)
    items = data.get("albums", {}).get("items", [])
    return pick_best_match(items, artist, album)


def get_most_popular_track(token: str, album_id: str) -> dict | None:
    data = api_get(f"{API_BASE}/albums/{album_id}/tracks?limit=50", token)
    tracks = data.get("items", [])
    if not tracks:
        return None
    # Tracks from album endpoint don't include popularity — fetch full objects
    track_ids = ",".join(t["id"] for t in tracks[:20])
    full = api_get(f"{API_BASE}/tracks?ids={track_ids}", token)
    full_tracks = full.get("tracks", [])
    if not full_tracks:
        return tracks[0]  # fallback to first track
    return max(full_tracks, key=lambda t: t.get("popularity", 0))


def get_artist_top_track(token: str, artist: str) -> dict | None:
    q = urllib.parse.quote(f"artist:{artist}")
    data = api_get(f"{API_BASE}/search?q={q}&type=artist&limit=1", token)
    items = data.get("artists", {}).get("items", [])
    if not items:
        return None
    artist_id = items[0]["id"]
    top = api_get(f"{API_BASE}/artists/{artist_id}/top-tracks?market=US", token)
    tracks = top.get("tracks", [])
    return tracks[0] if tracks else None


def add_to_playlist(token: str, playlist_id: str, track_uri: str) -> None:
    api_post(f"{API_BASE}/playlists/{playlist_id}/items", token, {"uris": [track_uri]})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("date", nargs="?", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    date_str = args.date or datetime.now(TPE).strftime("%Y-%m-%d")
    parsed_file = PARSED_DIR / f"{date_str}.json"

    if not parsed_file.exists():
        print(f"ERROR: no parsed data for {date_str} at {parsed_file}")
        return 1

    env = load_env()
    for key in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "SPOTIFY_PLAYLIST_ID"):
        if key not in env:
            print(f"[warn] {key} not set — skipping Spotify update")
            return 0

    albums = json.loads(parsed_file.read_text())
    print(f"=== Spotify updater | {date_str} | {len(albums)} albums ===")

    token       = refresh_access_token(env)
    playlist_id = env["SPOTIFY_PLAYLIST_ID"]

    # Import here so tests can import pure helpers without db
    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    import db as dbmod
    conn = dbmod.connect()
    dbmod.init_schema(conn)

    found = missed = skipped = 0

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

        print(f"  searching: {artist} — {album}")
        best_album = search_album(token, artist, album)

        if best_album:
            track = get_most_popular_track(token, best_album["id"])
        else:
            print(f"    album not found, trying artist top track")
            track = get_artist_top_track(token, artist)

        if not track:
            print(f"    [miss] no track found for {artist}")
            missed += 1
            continue

        track_uri  = track["uri"]
        track_name = track.get("name", "?")
        print(f"    -> {track_name} ({track_uri})")

        if not args.dry_run:
            add_to_playlist(token, playlist_id, track_uri)
            dbmod.spotify_mark_added(conn, key, best_album["id"] if best_album else None, track_uri, date_str)

        found += 1

    conn.close()
    print(f"\nDone: {found} added, {skipped} skipped, {missed} missed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_update_spotify.py -v
```
Expected: 4 passed

**Step 5: Commit**

```bash
git add scripts/update_spotify.py tests/test_update_spotify.py
git commit -m "feat: add update_spotify.py with album search and playlist update"
```

---

## Task 4: Wire into `pipeline.sh` and remove `spotify.sh`

**Files:**
- Modify: `scripts/pipeline.sh` (Step 7 block only)
- Delete: `scripts/spotify.sh`

**Step 1: Update Step 7 in `pipeline.sh`**

Find the existing Step 7 block (around line 116–122) and replace:

```bash
  bash "$SCRIPT_DIR/spotify.sh" "$DATE" || {
    echo "[warn] Spotify update failed (non-blocking)"
  }
```

With:

```bash
  python3 "$SCRIPT_DIR/update_spotify.py" "$DATE" || {
    echo "[warn] Spotify update failed (non-blocking)"
  }
```

**Step 2: Remove old shell script**

```bash
/bin/rm scripts/spotify.sh
```

**Step 3: Commit**

```bash
git add scripts/pipeline.sh
git rm scripts/spotify.sh
git commit -m "chore: wire update_spotify.py into pipeline, remove spotify.sh"
```

---

## Task 5: Add GitHub Actions secrets to workflow

**Files:**
- Modify: `.github/workflows/*.yml` (add env block to the workflow job)

**Step 1: Find the workflow file**

```bash
ls .github/workflows/
```

**Step 2: Add env vars to the job that runs the pipeline**

Find the step that runs `pipeline.sh` and add (or extend the `env:` block of that step):

```yaml
env:
  SPOTIFY_CLIENT_ID: ${{ secrets.SPOTIFY_CLIENT_ID }}
  SPOTIFY_CLIENT_SECRET: ${{ secrets.SPOTIFY_CLIENT_SECRET }}
  SPOTIFY_REFRESH_TOKEN: ${{ secrets.SPOTIFY_REFRESH_TOKEN }}
  SPOTIFY_PLAYLIST_ID: ${{ secrets.SPOTIFY_PLAYLIST_ID }}
```

Also ensure the pipeline step passes `--spotify`:

```yaml
run: bash scripts/pipeline.sh --spotify
```

**Step 3: Commit**

```bash
git add .github/workflows/
git commit -m "chore: add Spotify env vars to GitHub Actions workflow"
```

---

## Task 6: One-Time Auth + End-to-End Test

This task is manual — not automated.

**Step 1: Create a Spotify app**

1. Go to https://developer.spotify.com/dashboard
2. Create an app — any name
3. Add redirect URI: `http://localhost:8888/callback`
4. Copy Client ID and Client Secret

**Step 2: Set credentials in `~/.daily-dig.env`**

```
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
```

**Step 3: Run the one-time auth**

```bash
python3 scripts/spotify_auth.py
```

Expected: browser opens, you log in, `~/.daily-dig.env` is updated with `SPOTIFY_REFRESH_TOKEN` and `SPOTIFY_PLAYLIST_ID`.

**Step 4: Dry-run test**

```bash
python3 scripts/update_spotify.py --dry-run
```

Expected: shows albums found, tracks selected, no writes to DB or playlist.

**Step 5: Live run**

```bash
python3 scripts/update_spotify.py
```

Expected: tracks added to playlist, DB updated.

**Step 6: Add GH Actions secrets**

```bash
gh secret set SPOTIFY_CLIENT_ID --body "$(grep SPOTIFY_CLIENT_ID ~/.daily-dig.env | cut -d= -f2)"
gh secret set SPOTIFY_CLIENT_SECRET --body "$(grep SPOTIFY_CLIENT_SECRET ~/.daily-dig.env | cut -d= -f2)"
gh secret set SPOTIFY_REFRESH_TOKEN --body "$(grep SPOTIFY_REFRESH_TOKEN ~/.daily-dig.env | cut -d= -f2)"
gh secret set SPOTIFY_PLAYLIST_ID --body "$(grep SPOTIFY_PLAYLIST_ID ~/.daily-dig.env | cut -d= -f2)"
```

**Step 7: Update BACKLOG.md — mark #9 done**

Move item #9 from Open to Done in `docs/BACKLOG.md`.

```bash
git add docs/BACKLOG.md
git commit -m "docs: mark backlog #9 Spotify playlist updater as done"
```
