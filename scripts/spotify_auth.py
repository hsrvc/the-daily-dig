#!/usr/bin/env python3
"""
One-time Spotify OAuth setup. Run locally once to get a refresh token.
Never run in CI.

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

REDIRECT_URI  = "http://127.0.0.1:8888/callback"
SCOPES        = "playlist-modify-public playlist-read-private"
ENV_FILE      = Path.home() / ".daily-dig.env"
PLAYLIST_NAME = "The Daily Dig"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
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
            returned_state = params.get("state", [""])[0]
            if returned_state != state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"<h2>State mismatch. Auth rejected.</h2>")
                return
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
        "client_id":     client_id,
        "response_type": "code",
        "redirect_uri":  REDIRECT_URI,
        "scope":         SCOPES,
        "state":         state,
    })
    url = f"https://accounts.spotify.com/authorize?{params}"
    print(f"\nOpening browser for Spotify login...\n{url}\n")
    webbrowser.open(url)
    thread.join(timeout=120)

    if not code_holder:
        raise RuntimeError("No auth code received within 120s")
    return code_holder[0]


def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    import base64
    data  = urllib.parse.urlencode({
        "grant_type":   "authorization_code",
        "code":         code,
        "redirect_uri": REDIRECT_URI,
    }).encode()
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=data,
        headers={
            "Authorization":  f"Basic {creds}",
            "Content-Type":   "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def find_or_create_playlist(access_token: str) -> str:
    headers = {"Authorization": f"Bearer {access_token}"}

    req = urllib.request.Request("https://api.spotify.com/v1/me", headers=headers)
    with urllib.request.urlopen(req) as resp:
        user_id = json.loads(resp.read())["id"]

    # Paginate through all playlists
    url: str | None = "https://api.spotify.com/v1/me/playlists?limit=50"
    while url:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        for pl in data.get("items", []):
            if pl and pl.get("name") == PLAYLIST_NAME:
                print(f"Found existing playlist: {pl['id']}")
                return pl["id"]
        url = data.get("next")

    body = json.dumps({
        "name":        PLAYLIST_NAME,
        "public":      True,
        "description": "Daily vinyl recommendations from The Daily Dig",
    }).encode()
    req = urllib.request.Request(
        "https://api.spotify.com/v1/me/playlists",
        data=body,
        headers={**headers, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        pl = json.loads(resp.read())
        print(f"Created new playlist: {pl['id']}")
        return pl["id"]


def main():
    env = load_env()
    client_id     = env.get("SPOTIFY_CLIENT_ID")     or input("SPOTIFY_CLIENT_ID: ").strip()
    client_secret = env.get("SPOTIFY_CLIENT_SECRET") or input("SPOTIFY_CLIENT_SECRET: ").strip()

    code = get_auth_code(client_id)
    print("Got auth code. Exchanging for tokens...")
    tokens        = exchange_code(client_id, client_secret, code)
    access_token  = tokens["access_token"]
    refresh_token = tokens["refresh_token"]
    print(f"Got refresh token: {refresh_token[:10]}...")

    # Save tokens immediately before any further API calls
    write_env({
        "SPOTIFY_CLIENT_ID":     client_id,
        "SPOTIFY_CLIENT_SECRET": client_secret,
        "SPOTIFY_REFRESH_TOKEN": refresh_token,
    })

    playlist_id = find_or_create_playlist(access_token)

    write_env({"SPOTIFY_PLAYLIST_ID": playlist_id})
    print("\nDone! Now add these as GitHub Actions secrets:")
    print("  SPOTIFY_CLIENT_ID")
    print("  SPOTIFY_CLIENT_SECRET")
    print("  SPOTIFY_REFRESH_TOKEN")
    print("  SPOTIFY_PLAYLIST_ID")


if __name__ == "__main__":
    main()
