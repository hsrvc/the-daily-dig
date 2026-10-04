# The Daily Dig — Backlog

Single source of truth for open work. Update this file each session.

---

## Open

### #10 — Pipeline orchestration refactor
Two overlapping orchestrators exist: the daily-dig Claude skill (does scrape→deploy) and `pipeline.sh` (does scrape→build, with unused `--deploy --email --spotify` flags). Production works but the architecture is tangled. Full analysis in `docs/pipeline-orchestration-analysis.md`. Email + Spotify reliability now confirmed — this is the next structural item.

### #11 — Cover collision auto-fallback
`db.py` already has `cover_collisions()` which detects albums sharing the same file hash. Wire it up after `fetch_covers.py`: if any collision is found within a day's digest (same photo assigned to two albums), auto-fall back to iTunes/MusicBrainz for the conflicting records. Triggered by 2026-05-06 incident where a shop posted two angles of the same album cover and none for the second album (Lower/Middle Caste Religious Music From India).

### #12 — Auto-purge CDN cache after re-deploys
Mid-day re-deploys (fixing covers after the morning run) can leave Cloudflare's edge cache serving 404 HTML for new cover URLs that got hit before the deploy completed — the bad response gets cached for a year (`Cache-Control: immutable`). Wire a cache-purge step into the deploy flow that fires `POST /zones/{zone_id}/purge_cache` for any newly-added cover URLs and the affected digest page. Use `CLOUDFLARE_PURGE_TOKEN` (separate from the Pages token; both in `~/.zshenv`). Triggered by 2026-05-13 Sakanaction fix.

### #8 — Public newsletter (公開電子報)
Subscriber list, double opt-in, unsubscribe flow, and HTML digest sent to subscribers (not just the owner). Resend review team requires opt-in/unsubscribe before enabling broadcast sends.

---

## Done

| # | Item | Session |
|---|---|---|
| — | `SCRAPE_LIMIT` restored 5 → 10 + May 23–25 backfill — one `--limit 30` rescrape bucketed by post `time` field into three per-day digests (8 + 11 + 6 albums), three emails, Spotify updates | 2026-05-25 |
| — | `SCRAPE_LIMIT` default dropped 8 → 5 to stay inside the Apify free tier after a quota exhaustion. Accepts missing the oldest 2 posts on rare THTRECORDs burst days as the tradeoff | 2026-05-23 |
| — | Apify SUCCEEDED-but-all-requests-failed detection — `scrape_apify.py` now inspects `run.statusMessage` for the `"0 succeeded, N failed"` pattern and filters per-page error stubs from the dataset; exits non-zero so the wrapper's hourly retries can re-fire. Triggered by 2026-05-22 incident where Apify's proxy pool ECONNRESET'd every request, the actor still reported SUCCEEDED, and the pipeline silently published a "no albums" day. Recovery + full write-up in `docs/incidents/2026-05-22-apify-proxy-failure.md` | 2026-05-23 |
| — | Sakanaction 4-album cover mismatch (TBBD) — re-assigned `image_url` for 3 of 4 Sakanaction entries in 2026-05-13 digest after parser slapped the first photo on all 4 albums; manual fix + cache purge | 2026-05-13 |
| — | Vision-based multi-album cover matching — parsing skill now uses `Read` on cached `data/images/{date}/*.jpg` to visually match albums to covers for any post yielding 2+ records; positional matching is the documented fallback when cache files are missing. FB OCR (`media[].ocrText`) is too noisy to rely on. Replaces the prior heuristic chain (OCR → positional → primary-photo-for-all) that failed silently | 2026-05-13 |
| — | Cloudflare cache-purge token saved — separate `CLOUDFLARE_PURGE_TOKEN` (Zone:Cache Purge scope on erdscribe.com) now in `~/.zshenv` alongside the Pages-only `CLOUDFLARE_API_TOKEN`. Required when mid-day re-deploys leave the CDN serving cached 404-HTML for new cover URLs | 2026-05-13 |
| — | TCC permission fix — `claude_runner` stable binary copy in wrapper; one-time FDA grant survives Claude auto-updates | 2026-05-12 |
| — | dig.db committed after Spotify runs — `spotify_added` rows now included in notified markers commit; fixes cross-day Spotify dedup for GH Actions | 2026-05-12 |
| — | GH Actions push failures surface — rewrote CI commit/push steps to fail loudly instead of swallowing with `\|\| true` | 2026-05-12 |
| — | Double-send guard — scripts refuse to run if marker exists; `--force` flag to override; never delete marker files to resend | 2026-05-06 |
| — | Archive pick count — `/archive/` shows number of picks per digest instead of source count | 2026-05-06 |
| — | Notified marker system — `data/notified/spotify-{date}` + `email-{date}` written on success; GH Actions reads them to skip completed steps independently | 2026-05-06 |
| — | GH Actions reliability — per-step `pipeline_done/spotify_done/email_done` gates; npm install only when pipeline needs it; failure issue names specific step | 2026-05-06 |
| — | LLM verify timeout 60s → 15s — prevents Spotify from taking 3 hours on slow `claude -p` calls | 2026-05-06 |
| — | `send_email.py` + `update_spotify.py` — write notified markers, exit 1 on failure | 2026-05-06 |
| — | Spotify LLM artist verification — `llm_verify_artist_match()` prevents wrong-artist fallback tracks (Dowser vs The Dowser) | 2026-05-06 |
| — | Cover media format fix — parser now checks both `image.uri` and `photo_image.uri`; 6 missing covers backfilled for 2026-05-06 | 2026-05-06 |
| — | Hero scoring / proportional descriptions — description length now scales with post richness (2-3 typical, up to 4-5 for rich posts) | 2026-05-06 |
| — | Spotify footer link — two-column footer on index + digest pages; Listen on Spotify link wired to playlist | 2026-05-06 |
| — | Spotify search fixes — CJK name reversal (Takada Wataru↔Wataru Takada), Various Artists title-only search, duplicate URI guard within a run | 2026-05-06 |
| — | `spotify_artist` field — parser sets this when band/project has a findable key individual (e.g. The Milky Way → Makoto Matsushita); Spotify script uses as named fallback | 2026-05-06 |
| — | Cover image commits — 6 fixed covers for 2026-05-06 digest committed and deployed | 2026-05-06 |
| — | Spotify + email auto-fire — wired into both launchd and GH Actions run paths; parallel post-deploy execution; fixed broken `send_email.py` path in launchd | 2026-05-05 session 6 |
| — | Cloudflare cache purge — Snow Patrol cover cached as `text/html`; purged via API | 2026-05-05 session 6 |
| 9 | Spotify playlist updater — `update_spotify.py`, search-based track selection, SQLite dedup, GH Actions secrets | 2026-05-05 session 5 |
| 17 | Cache-bust cover filenames — `{slug}-{hash8}.jpg` + `Cache-Control: immutable` headers | 2026-05-05 session 4 |
| — | HTML digest email (`send_email.py`) — ZH-only, cover-card layout, Resend | 2026-05-05 session 4 |
| — | Year fallback chain — post text → knowledge → WebSearch → gemini-search | 2026-05-05 session 4 |
| — | SQLite Phase 3 — cover provenance table with SHA-256 hashes | 2026-05-05 session 3 |
| — | Two-layer pfbid dedup — shop+album key + cover hash pass | 2026-05-05 session 3 |
| — | launchd primary scheduler + GH Actions backup (12:00 TPE) | 2026-05-05 sessions 1–2 |
| — | SQLite DB — albums + evaluations + covers, committed to repo | 2026-05-05 sessions 1–2 |
| — | Cover-art fallback chain — FB CDN → iTunes → MusicBrainz | earlier |
