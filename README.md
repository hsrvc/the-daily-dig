# The Daily Dig

> **This is a public snapshot of the code.** The live pipeline runs from a private repo.
> Not included: the scraped data (`data/`), the generated digests (`site/src/content/digests/`)
> and the cover images (`site/public/covers/`). These come from third-party shop posts and album
> artwork. The GitHub Actions workflows are in `examples/github-workflows/`, so they do not run
> here. To run your own copy, copy them to `.github/workflows/` and set the secrets listed below.

A personal music discovery pipeline that scrapes vinyl record shop posts from Facebook, presents them as a newspaper-style daily digest, and runs unattended every morning.

**Live site:** https://dailydig.erdscribe.com/

**Schedule:** cron primary at 08:30 Asia/Taipei; GH Actions backup at 12:00 TPE

---

## How It Works

```
sources.yaml          Facebook pages to monitor + posting_style metadata
      │
      ▼
scrape_apify.py       Latest N posts per page (default --limit 8)
      │
      ▼
data/raw/{date}-apify.json     Full Apify output (gitignored)
      │
      ▼
dedup_raw.py          Filter against SQLite evaluations table — every post URL
      │               ever evaluated, kept or skipped (falls back to data/seen/ JSON)
      ▼
data/raw/{date}-apify-new.json   Only new-since-history posts
      │
      ▼
[Claude — daily-dig skill]    Parse posts → records; self-audit; write
      │                       data/parsed/{date}.json + data/seen/{date}.json
      │                       + write evaluations to data/dig.db (step 4b)
      ▼
dedup_albums.py       Cross-day dedup via SQLite albums table (14-day window)
      │               + write deduped albums to data/dig.db (step 4.6)
      ▼
data/parsed/{date}.json       Structured album records (committed)
data/seen/{date}.json         All evaluated URLs (committed)
data/dig.db                   SQLite history — albums + evaluations (committed)
      │
      ▼
fetch_covers.py       FB CDN → iTunes → MusicBrainz fallback chain
      │               saves as {slug}-{hash8}.jpg (content-hash for CDN cache-busting)
      ▼
site/public/covers/{slug}-{hash8}.jpg     Local cover images (committed)
      │
      ▼
generate_digest.py    Astro markdown for the day
      │
      ▼
site/src/content/digests/{date}.md     (committed)
      │
      ▼
npm run build → site/dist/ → wrangler pages deploy → Cloudflare Pages
                              ↑
                    ⏰ cron @ 08:30 TPE (primary, 3 hourly retries through 11:30)
                    ⏰ GH Actions @ 12:00 TPE (backup — skips if already done)
```

The pipeline runs unattended. `cron` is the primary scheduler; GH Actions fires
later as a cloud backup if the Mac was offline or closed all morning.

## Project Structure

```
music/
├── examples/github-workflows/   (moved out of .github/ in this snapshot)
│   └── daily-dig.yml         # GH Actions: cron backup @ 14:00 TPE + manual dispatch
├── .claude/skills/daily-dig/
│   ├── SKILL.md              # End-to-end pipeline orchestration for the agent
│   └── references/parsing.md # Detailed parsing spec (LLM-judgment step)
├── sources.yaml              # FB pages to monitor + per-shop posting_style
├── requirements.txt          # Python deps (pyyaml)
├── scripts/
│   ├── scrape_apify.py       # Apify Facebook Posts Scraper (primary)
│   ├── dedup_raw.py          # Filter raw posts vs SQLite evaluations table
│   ├── dedup_albums.py       # Cross-day album dedup via SQLite albums table (artist+album + shop+album keys)
│   ├── dedup_covers.py       # Post-fetch cover hash dedup — drops same-image albums across days
│   ├── fetch_covers.py       # Cover-art fallback: FB → iTunes → MusicBrainz; saves as {slug}-{hash8}.jpg
│   ├── generate_digest.py    # Parsed JSON → Astro markdown
│   ├── send_email.py         # HTML digest email via Resend (ZH-only, cover-card layout)
│   ├── update_spotify.py     # Adds tracks to Spotify playlist; writes spotify_added rows to dig.db
│   ├── db.py                 # SQLite schema + connect/init_schema/normalize/hash helpers
│   ├── migrate_to_sqlite.py  # Backfill DB from JSON; --date flag for per-run writes
│   ├── backfill_covers.py    # One-shot backfill of covers table from existing disk files
│   ├── migrate_covers_to_hash.py  # One-shot migration: rename covers to {slug}-{hash8}.jpg
│   ├── run_daily_dig.sh      # cron wrapper (source-of-truth; deploy to ~/.local/bin/)
│   ├── com.erdscribe.daily-dig.plist  # legacy launchd plist (superseded by cron)
│   ├── pipeline.sh           # Manual end-to-end orchestrator
│   ├── spotify.sh            # legacy, superseded by update_spotify.py
│   └── debug_fb.py           # Debug utility
├── data/
│   ├── raw/                  # Daily scraping output (gitignored)
│   ├── parsed/               # Structured album records (committed)
│   ├── seen/                 # All evaluated post URLs per day (committed)
│   └── dig.db                # SQLite history — albums + evaluations + covers (committed)
├── tests/
│   ├── conftest.py           # Shared tmp_db pytest fixture
│   ├── test_dedup_raw.py     # Tests for DB read path in dedup_raw
│   ├── test_dedup_albums.py  # Tests for DB read path in dedup_albums (incl. shop+album key)
│   ├── test_dedup_covers.py  # Tests for cover hash dedup
│   ├── test_db_write.py      # Tests for migrate_to_sqlite per-date helpers
│   └── test_covers_db.py     # Tests for db.py hash helpers + write_cover_row
└── site/                     # Astro static site (Cloudflare Pages)
    ├── src/
    │   ├── pages/
    │   │   ├── index.astro          # Homepage (latest digest)
    │   │   ├── archive.astro        # All digests by date
    │   │   └── digest/[...date].astro
    │   ├── layouts/Newspaper.astro
    │   ├── components/{AlbumHero,AlbumStory}.astro
    │   ├── lib/
    │   │   ├── parse-albums.ts      # Markdown → Album[]
    │   │   └── score-hero.ts        # Hero-pick scoring (shared by index+digest)
    │   └── content/digests/         # Generated markdown per day (committed)
    └── public/
        ├── favicon.svg
        └── covers/                  # Local cover images (committed)
```

## Setup (one-time)

- **GitHub repo**: your own copy of this repo
- **Cloudflare Pages**: project `daily-dig`, deployed via `wrangler pages deploy`
- **GitHub Actions secrets** (set on repo, not in code):
  - `APIFY_TOKEN` — Apify API token
  - `CLOUDFLARE_API_TOKEN` — Cloudflare API token
  - `CLOUDFLARE_ACCOUNT_ID` — (your Cloudflare account ID)
  - `CLAUDE_CODE_OAUTH_TOKEN` — Claude OAuth token
  - `RESEND_API_KEY` — Resend API key for on-success email
  - `NOTIFY_EMAIL` — recipient email for notifications
- **Local secrets** (sourced by launchd wrapper, never committed):
  - `~/.zshenv` — `CLOUDFLARE_API_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`
  - `~/.apify/auth.json` — Apify token (written by `apify login`)
  - `~/.daily-dig.env` — `RESEND_API_KEY`, `NOTIFY_EMAIL`

To add or rotate a secret:
```bash
printf '%s' "$NEW_VALUE" | gh secret set NAME --repo <owner>/<repo>
```

## Scheduler

The pipeline uses a three-layer scheduler stack:

| Time (TPE) | Trigger | Behaviour |
|---|---|---|
| 08:30 | cron primary | Runs pipeline |
| 09:30–11:30 | cron hourly retries ×3 | Skips if seen file exists (already ran) |
| 12:00 | GH Actions cron backup | Skips in ~5s if seen file in repo; runs full pipeline if Mac was off all day |

**Catch-up guard:** `run_daily_dig.sh` checks `data/seen/{today}.json` before doing
anything. If it exists → already ran, exits immediately. If current TPE time < 08:30
→ too early, exits immediately.

**Deploying wrapper changes:**
```bash
cp scripts/run_daily_dig.sh ~/.local/bin/run_daily_dig.sh
```

**macOS TCC note:** The wrapper copies the claude binary to `~/.local/bin/claude_runner`
before each run. This stable path holds the Documents folder FDA grant permanently —
Claude auto-updates don't trigger new permission dialogs. If you set this up fresh,
run `~/.local/bin/claude_runner --version` once in Terminal and click Allow, then
add it to System Settings → Privacy & Security → Full Disk Access.

## Running Locally

### Trigger the pipeline manually

```bash
~/.local/bin/run_daily_dig.sh
# or (from repo root):
bash scripts/run_daily_dig.sh
```

Logs go to `~/Library/Logs/daily-dig/{date}.log`.

### Trigger GH Actions manually

```bash
gh workflow run daily-dig.yml --repo <owner>/<repo>
gh workflow run daily-dig.yml --repo <owner>/<repo> -f limit=15  # backfill
```

### Watch a GH Actions run

```bash
gh run list --repo <owner>/<repo> --workflow daily-dig.yml --limit 5
gh run watch <run-id> --repo <owner>/<repo>
```

### Run tests

```bash
pytest tests/ -v
```

## SQLite Database

`data/dig.db` is committed to the repo and serves as the source of truth for
dedup history. Three tables:

- **`albums`** — one row per published (post_url, artist, album) tuple. The
  `UNIQUE(post_url, artist_norm, album_norm)` constraint loudly rejects parser
  image-collision bugs (same post_url assigned to two different albums) at insert time.
- **`evaluations`** — one row per Facebook post URL ever evaluated, whether kept or
  skipped. `dedup_raw.py` queries this to filter out already-seen posts.
- **`covers`** — cover fetch provenance: source (fb_cdn / itunes / musicbrainz),
  fetched URL, local file path, and SHA-256 hash. `dedup_covers.py` queries this
  after each cover fetch pass to drop any album whose image hash matches a prior-day
  album — catching Facebook pfbid URL rotation and artist-name spelling variants that
  slip past URL-based dedup.
- **`spotify_added`** — one row per album key added to the Spotify playlist. Cross-day
  dedup guard: prevents the same album being added again on future runs. Written by
  `update_spotify.py` **after** the digest commit, so the wrapper commits `dig.db`
  alongside the notified markers to ensure GH Actions (fresh checkout) sees these rows.

The dedup scripts are DB-first and fall back to JSON if `dig.db` is missing.

**Re-sync the DB from JSON** (idempotent — safe to re-run):
```bash
python3 scripts/migrate_to_sqlite.py           # all dates
python3 scripts/migrate_to_sqlite.py --date 2026-05-05  # single date
python3 scripts/migrate_to_sqlite.py --reset   # wipe and rebuild
```

## Adding Sources

Edit `sources.yaml`:

```yaml
- name: New Record Shop
  slug: newrecordshop
  url: https://www.facebook.com/newrecordshop
  type: vinyl_shop
  location: City, Country
  notes: What they specialize in.
  posting_style: single  # or regular_batch / occasional_batch
```

`posting_style` tells the parser how aggressively to look for multi-album content:
- **`single`** — 1 album per post (default if omitted)
- **`regular_batch`** — often 2 albums per post
- **`occasional_batch`** — sometimes 3-4 mixed items; may include zines/cassettes/merch to skip

## Album Cover Art

`scripts/fetch_covers.py` runs the fallback chain in priority order:

1. **Existing local file** — skip if already on disk
2. **Facebook CDN** — `image_url` from the parsed record (expires ~4 days)
3. **iTunes Search API** — no auth; swap `100x100bb` → `600x600bb` for hi-res
4. **MusicBrainz + Cover Art Archive** — no auth; best for obscure world music

Filename format: `{artist_slug}_{album_slug}-{hash8}.jpg` — first 8 hex chars of SHA-256
appended so filenames change whenever content changes. CJK preserved, lowercase, dashes
for separators, album truncated to 30 chars. Stored in `site/public/covers/`.

Cloudflare caches covers with `Cache-Control: public, max-age=31536000, immutable`
(via `site/public/_headers`). The hash suffix guarantees a new URL whenever a cover
is replaced — no manual cache purge needed.

## Hero Pick Scoring

`site/src/lib/score-hero.ts` decides which album leads each digest. Continuous score:

- `descriptionEn.length` — primary signal, 1 point per character
- `descriptionZh.length × 0.3` — bilingual completeness bonus
- `(slash count in genre) × 15` — multi-genre nuance per `/` separator
- `year set: +30` — documented release metadata

Image presence required (filtered before scoring). Tie-breaks go to parse order.
Both `index.astro` and `digest/[...date].astro` use the same helper.

## Data Schema

Each record in `data/parsed/{date}.json`:

```json
{
  "artist": "Yuhan Su (蘇郁涵)",
  "album": "OVER the MOONs",
  "genre": "Contemporary Jazz / Free Jazz",
  "year": "2025",
  "description_en": "English description...",
  "description_zh": "中文描述...",
  "source_page": "uourecords",
  "post_url": "https://www.facebook.com/...",
  "image_url": "https://scontent-...",
  "local_image": "/covers/yuhan-su-(蘇郁涵)_over-the-moons.jpg"
}
```

## Cost & Quotas

| Item | Per run | Monthly |
|---|---|---|
| Apify Facebook Posts Scraper | ~$0.20 (40 posts × $0.0045 + $0.017) | ~$6 |
| Claude API (~5K tokens parse + audit) | ~$0.50–2 | ~$15–60 |
| GitHub Actions (Ubuntu, ~5 min/run when backup fires) | 5 min | ~150 min free |
| Cloudflare Pages | free | free |

Monthly cost depends on your Apify plan: the free tier ($5/month) covers about
`SCRAPE_LIMIT=5`. Default `SCRAPE_LIMIT` is 10 (set in the skill + workflow); bump to
20+ via the workflow_dispatch `limit` input for backfill after gaps.

## Roadmap

**Done:**
- ✅ Cover-art fallback automation (FB → iTunes → MusicBrainz)
- ✅ Dedup against full evaluation history (SQLite `evaluations` table)
- ✅ Cross-day album dedup (SQLite `albums` table, 14-day window)
- ✅ SQLite DB committed to repo — albums + evaluations + covers history with integrity constraints
- ✅ Local launchd scheduler (primary) with catch-up guard + hourly retry slots
- ✅ GH Actions backup cron at 14:00 TPE — skips instantly if launchd already ran
- ✅ daily-dig skill with self-audit + per-shop posting styles
- ✅ Hero-pick scoring helper (shared homepage + digest pages)
- ✅ Observability — auto-issue on workflow failure + post-deploy smoke test
- ✅ HTML digest email via Resend — bilingual cover-card layout, ZH description, sent after every successful run
- ✅ Pytest test suite (18 tests covering SQLite read/write paths)
- ✅ SQLite Phase 3: cover-art provenance — `covers` table with SHA-256 hash per download
- ✅ Two-layer pfbid dedup — shop+album key in `dedup_albums.py` + cover hash pass in `dedup_covers.py`

- ✅ Cache-bust cover filenames — `{slug}-{hash8}.jpg` with `Cache-Control: immutable` headers

- ✅ Spotify playlist updater — `update_spotify.py`, search-based track selection, SQLite dedup, cross-day dedup via `spotify_added` table
- ✅ TCC permission fix — `claude_runner` stable binary copy; one-time FDA grant survives all Claude auto-updates

**Next:**
- 📧 **Public newsletter (公開電子報)** — subscriber list with HTML digest rendering

## Key Technical Decisions

| Decision | Why |
|---|---|
| Apify as primary scraper | Facebook scrambles DOM text via CSS; Apify returns clean structured data |
| Claude inline for parsing | No API key needed beyond OAuth; LLM judgment for genre/description quality |
| launchd primary + GH Actions backup | launchd has direct filesystem/credential access; GH Actions provides cloud fallback when Mac is offline |
| SQLite committed to repo | Full provenance after fresh checkout; integrity constraints catch parser bugs loudly at insert time |
| `data/seen/` + `evaluations` table | Records every evaluated post, not just kept albums — prevents re-evaluating noise |
| Static site (Astro) | No server; fast deploys; content as committed Markdown |
| Per-shop `posting_style` | Some shops (uourecords) batch 3-4 items per post — agent needs structural hint |
