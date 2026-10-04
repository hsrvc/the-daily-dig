# Music Digging Pipeline — Design Doc

**Date:** 2026-05-04
**Status:** Approved

## Goal

Build a personal music discovery pipeline that:
1. Scrapes daily posts from vinyl record shops on Facebook
2. Presents them as a newspaper-style daily digest website
3. Maintains a Spotify playlist of recommended music (async, non-blocking)

## Sources

| Facebook Page | Type |
|---|---|
| facebook.com/uourecords | Vinyl shop |
| facebook.com/Tokyobuybuydiary | Vinyl shop |
| facebook.com/beethobearrecords | Vinyl shop |
| facebook.com/THTRECORDs | Vinyl shop |

## Architecture

Two independent pipelines sharing a data store (daily JSON/Markdown files):

### Pipeline A: Daily Digest (critical path)

```
Playwright (Chrome profile) → Raw posts JSON
    → Claude Code /loop parses inline → Structured records
    → Markdown file written to Astro content/
    → Astro static build → Deploy
```

- **Scraper:** Playwright with `launch_persistent_context` using the user's Chrome profile (FB already logged in)
- **Parser:** Claude Code processes raw post text inline during /loop — no external API needed
- **Output:** Daily Markdown file with frontmatter in `src/content/digests/YYYY-MM-DD.md`
- **Build:** Astro static site generation
- **Deploy:** Cloudflare Pages (or local preview initially)

### Pipeline B: Spotify Playlist (async, best-effort)

```
Structured records → spogo search → spogo playlist add
```

- **Tool:** `spogo` CLI (already installed, authenticated via browser cookies)
- **Flow:** Read daily records, search Spotify, add found tracks to dedicated playlist
- **Failures don't block Pipeline A**

## Data Schema

Each parsed record:

```json
{
  "artist": "Tatsuro Yamashita",
  "album": "For You",
  "genre": "City Pop",
  "year": "1982",
  "price": "$45",
  "condition": "NM/NM",
  "image_url": "https://...",
  "description": "Original Japanese pressing...",
  "source_page": "uourecords",
  "post_url": "https://facebook.com/...",
  "post_date": "2026-05-04",
  "spotify_id": null
}
```

## Website Design

**Stack:** Astro + Tailwind CSS + CSS Grid

**Newspaper aesthetic:**
- Fonts: Playfair Display (headlines), Libre Baskerville (body)
- Masthead with site name + date, thin rule borders
- Multi-column CSS Grid layout with `column-rule` dividers
- Dense spacing, monochrome palette (#f5f0e8 background, black text)
- Each album = a "story" block with image, artist, album, genre, price, source

**Pages:**
- `/` — Today's front page (latest digest)
- `/archive/` — List of past digests by date
- `/digest/YYYY-MM-DD/` — Individual day's digest

## Orchestration

- **Trigger:** Claude Code `/loop` on a schedule (e.g., every 12h)
- **Steps per loop iteration:**
  1. Run Playwright scraper script → `data/raw/YYYY-MM-DD.json`
  2. Claude parses raw posts → `data/parsed/YYYY-MM-DD.json`
  3. Generate Astro content Markdown → `src/content/digests/YYYY-MM-DD.md`
  4. Astro build
  5. (Async) Run spogo playlist updater

## File Structure

```
music/
├── docs/plans/          # Design docs
├── scripts/
│   ├── scrape.py        # Playwright FB scraper
│   ├── spotify.sh       # spogo playlist updater
│   └── build.sh         # Astro build + deploy
├── data/
│   ├── raw/             # Raw scraped JSON per day
│   └── parsed/          # Structured records per day
└── site/                # Astro project
    ├── src/
    │   ├── content/
    │   │   └── digests/ # Daily Markdown files
    │   ├── layouts/
    │   │   └── Newspaper.astro
    │   ├── pages/
    │   │   ├── index.astro
    │   │   ├── archive.astro
    │   │   └── digest/[date].astro
    │   └── styles/
    │       └── newspaper.css
    ├── astro.config.mjs
    ├── tailwind.config.mjs
    └── package.json
```
