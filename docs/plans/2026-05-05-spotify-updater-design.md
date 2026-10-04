# Spotify Playlist Updater — Design

Date: 2026-05-05

## Goal

Each day after the Daily Dig pipeline runs, add one song per recommended album to a dedicated Spotify playlist ("The Daily Dig"). Pick the most popular track from each album. If the album can't be found on Spotify, fall back to the artist's most popular track. Skip albums already added on previous days.

---

## Architecture

```
pipeline.sh
  └── update_spotify.py
        ├── OAuth: exchange refresh_token → access_token
        ├── Find or create "The Daily Dig" playlist
        └── For each album in today's parsed JSON:
              ├── Check SQLite: already added? → skip
              ├── Search Spotify: album:{name} artist:{artist}
              │     ├── Found → GET /albums/{id}/tracks → sort by popularity → pick #1
              │     └── Not found → GET artist top tracks → pick #1
              ├── POST /playlists/{id}/items  (1 track URI)
              └── Write album_key + track_uri to SQLite
```

---

## Section 1: Files

| File | Purpose |
|---|---|
| `scripts/spotify_auth.py` | One-time local OAuth setup — run once by hand |
| `scripts/update_spotify.py` | Daily pipeline script — runs unattended |
| `db.py` | Add `spotify_added` table + 2 helper functions |

The existing `scripts/spotify.sh` is superseded and can be removed.

---

## Section 2: OAuth Setup (One-Time)

`spotify_auth.py` is run once locally:

1. Opens browser → Spotify login + consent screen
2. Captures the callback via local HTTP server on port 8888
3. Exchanges code for `access_token` + `refresh_token`
4. Writes to `~/.daily-dig.env`:
   ```
   SPOTIFY_CLIENT_ID=...
   SPOTIFY_CLIENT_SECRET=...
   SPOTIFY_REFRESH_TOKEN=...
   SPOTIFY_PLAYLIST_ID=...
   ```
   Creates the playlist on first run and stores its ID.

At runtime, `update_spotify.py` exchanges the refresh token for a fresh access token:

```
POST https://accounts.spotify.com/api/token
  grant_type=refresh_token
  refresh_token=...
  client_id / client_secret
```

If the response includes a new `refresh_token`, overwrite it in `~/.daily-dig.env` (local) or via `gh secret set SPOTIFY_REFRESH_TOKEN` (CI).

**Scopes required:** `playlist-modify-public playlist-read-private`

---

## Section 3: Core Script Logic

```python
for album in today_parsed_json:
    key = f"{artist}||{album}".lower()

    # 1. Dedup check
    if db.spotify_already_added(key):
        log(f"skip: {artist} - {album}")
        continue

    # 2. Search album
    results = spotify.search(f"album:{album} artist:{artist}", type="album", limit=5)
    best = pick_best_match(results, artist, album)  # prefer album_type=album, closest name

    if best:
        # 3a. Get tracks, sort by popularity, pick #1
        tracks = spotify.get_album_tracks(best["id"])
        track = max(tracks, key=lambda t: t["popularity"])
    else:
        # 3b. Artist fallback
        track = spotify.get_artist_top_track(artist)

    if not track:
        log(f"miss: {artist} - {album}")
        continue

    # 4. Add to playlist
    spotify.add_to_playlist(PLAYLIST_ID, track["uri"])

    # 5. Record in SQLite
    db.spotify_mark_added(key, best["id"] if best else None, track["uri"])
```

**`pick_best_match`:** exact album name match scores highest; `album_type == "album"` preferred over single/compilation.

**Rate limit handling:** thin request wrapper — on 429, sleep `Retry-After` seconds, retry up to 3×, then raise.

**API notes (Feb 2026):**
- Playlist endpoint: `/playlists/{id}/items` (not `/tracks`)
- Search `limit` max is 10
- Batch album fetch endpoint removed — fetch one at a time

---

## Section 4: SQLite

New table in existing DB:

```sql
CREATE TABLE IF NOT EXISTS spotify_added (
    album_key        TEXT PRIMARY KEY,  -- "artist||album" lowercased
    spotify_album_id TEXT,              -- NULL if artist-fallback was used
    track_uri        TEXT NOT NULL,
    added_at         TEXT NOT NULL      -- ISO date YYYY-MM-DD
);
```

Two new functions in `db.py`:
- `spotify_already_added(album_key) -> bool`
- `spotify_mark_added(album_key, spotify_album_id, track_uri, added_at)`

---

## Section 5: Pipeline Wiring

**`pipeline.sh`** — already has a `--spotify` flag and Step 7 hook. Replace the `spotify.sh` call with:

```bash
if [[ "$RUN_SPOTIFY" = true ]]; then
  python3 "$SCRIPT_DIR/update_spotify.py" "$DATE" || {
    echo "[warn] Spotify update failed (non-blocking)"
  }
fi
```

**GitHub Actions** — add secrets to workflow env:

```yaml
env:
  SPOTIFY_CLIENT_ID: ${{ secrets.SPOTIFY_CLIENT_ID }}
  SPOTIFY_CLIENT_SECRET: ${{ secrets.SPOTIFY_CLIENT_SECRET }}
  SPOTIFY_REFRESH_TOKEN: ${{ secrets.SPOTIFY_REFRESH_TOKEN }}
  SPOTIFY_PLAYLIST_ID: ${{ secrets.SPOTIFY_PLAYLIST_ID }}
```

Script reads from env vars at runtime — same code path works locally (via `~/.daily-dig.env`) and in CI.

---

## Implementation Order

1. Add `spotify_added` table to `db.py`
2. Write `scripts/spotify_auth.py` (one-time OAuth setup)
3. Write `scripts/update_spotify.py` (core pipeline script)
4. Update `pipeline.sh` to call `update_spotify.py` instead of `spotify.sh`
5. Run `spotify_auth.py` locally, copy secrets to GH Actions
6. Test end-to-end with `pipeline.sh --spotify`
