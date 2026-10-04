# 2026-05-22 — Apify Proxy-Pool Failure Silently Produced Empty Digest

**Status:** resolved (digest recovered + deployed; detection patched in `scripts/scrape_apify.py`)
**Impact:** May 22 digest published as "no new albums, 4 noise posts" instead of 11 albums. No email, no Spotify update, no site update for that day. Recovered retroactively the next morning. Future incidents of the same shape will exit non-zero and trigger the hourly cron retry instead of silently finalizing.

## What happened

The launchd cron fired at 08:30 TPE on 2026-05-22 (00:30 UTC). Apify's `facebook-posts-scraper` actor was called as normal. The run returned `status: SUCCEEDED` and the script proceeded happily down the pipeline.

But the dataset Apify wrote contained four entries of this shape, one per source page:

```json
{
  "url": "https://www.facebook.com/uourecords",
  "error": "no_items",
  "errorDescription": "Empty or private data for provided input"
}
```

`dedup_raw.py` treated these as posts (they have a `url` field — the page URL, not a post URL — and the dedup only checks `url` membership). The parser correctly identified them as not-albums and recorded all four URLs in `data/seen/2026-05-22.json` as "evaluated noise." `generate_digest.py` saw an empty parsed array and exited cleanly without writing `2026-05-22.md`. The wrapper's `notified: 2026-05-22 [local]` commit went up. From the outside it looked exactly like a real quiet day.

It wasn't. A manual rescrape ~17 hours later got 72 real posts from the same four pages — including the Moses Yoofee Trio, Hozan Yamamoto *尺八 & Bossa Nova Vol.2*, Hadley Caliman *Iapetus*, and Masahiko Togashi *We Now Create* picks that ultimately made the recovered digest.

## Root cause

The Apify run log for `<run-id>` (the failed run) shows the actor's internal CheerioCrawler retried every URL 10 times across multiple proxy sessions and got the same error every time:

```
WARN  CheerioCrawler: Reclaiming failed request back to the list or queue.
      Parse Error: Expected HTTP/, RTSP/ or ICE/
WARN  CheerioCrawler: Detected a session error, rotating session...
      The proxy responded with 595 ECONNRESET
INFO  CheerioCrawler: Final request statistics:
      {"requestsFinished":0,"requestsFailed":4,...}
INFO  CheerioCrawler: Finished! Total 4 requests: 0 succeeded, 4 failed.
```

`Parse Error: Expected HTTP/, RTSP/ or ICE/` is Node's `http_parser` rejecting bytes that don't start with a valid HTTP response line — TLS handshake aborted, mid-stream reset, or non-HTTP framing from the upstream socket. `595 ECONNRESET` is **Apify's proxy-pool error code** (Apify reserves 590-599 for proxy-layer failures), not a Facebook response. The proxy itself bailed before FB even saw the request.

So the failure was network-layer inside Apify's infrastructure — not auth (would have been 401), not rate limit (would have been 429), not FB blocking the actor (would have been 403), and not "the pages had nothing to post" (the rescrape proved otherwise). Same `APIFY_TOKEN` worked 17 hours later, ruling out credentials.

The actor's design choice is what made this invisible: when a startUrl fails all retries, the actor emits a `{url, error: "no_items"}` placeholder into the dataset rather than failing the run. The orchestration layer marks the run `SUCCEEDED` because no Node exception escaped. Our `scrape_apify.py` only checked `status != "SUCCEEDED"`, so the bad output flowed silently downstream.

The real signal sat in `run.statusMessage`: `"Finished! Total 4 requests: 0 succeeded, 4 failed."` — null on healthy runs.

## Resolution

**Recovery (live as of 2026-05-23 02:17 TPE):** rebuilt May 22 from the rescrape data. 11 albums in `data/parsed/2026-05-22.json`, real post URLs in `data/seen/2026-05-22.json` (replacing the four page-URL stubs), digest at `site/src/content/digests/2026-05-22.md`, Cloudflare deploy live at `https://music.erdscribe.com/digest/2026-05-22/`. Spotify added 10 tracks (1 skip — Yamamoto Vol.2 collided with Vol.1 via top-track fallback); email sent via Resend. Markers committed in `50455e2` so no duplicate send is possible.

**Patch (`a586d3c`):** two new detection layers in `scripts/scrape_apify.py`:

- `assert_actor_succeeded(run_data)` — inspects `statusMessage` for the `"0 succeeded, N failed"` pattern Apify emits when the crawler bailed. Exits non-zero so the wrapper's hourly retries (09:30 → 13:30 TPE) re-fire the scrape against a fresh actor run.
- `partition_posts(posts)` — splits the fetched dataset into real posts vs error stubs (truthy `error` + missing `postId`). Exits non-zero if no real posts remain. Belt-and-suspenders for the case where `statusMessage` doesn't carry the signal (e.g. partial failure across pages).

Wired into both the normal scrape path and `--reuse-latest`/`--dataset-id`. Tests cover the exact `statusMessage` string from the failed run plus stub shape variations. Replaying the saved `data/raw/2026-05-22-apify.json.broken-backup` through the patched logic reports `0 real / 4 stubs` and exits non-zero — confirming the patched pipeline would have caught this on the original morning run and let the hourly retry pick up real data once Apify's proxy pool recovered.

## Why the catch-up guard didn't help on May 22

The wrapper's catch-up guard (`run_daily_dig.sh:27`) skips if `data/seen/$DATE_TAG.json` exists. The pre-patch flow wrote the seen file (4 page URLs) on the first failed run, so all subsequent hourly fires (09:30, 10:30, 11:30 …) just hit the guard and skipped — there was no path to retry.

The new patch fixes this asymmetrically: on Apify failure, `scrape_apify.py` exits non-zero **before** the seen file is written. The skill's "if a step fails, stop and report" branch (`SKILL.md` step 2) propagates the failure, the wrapper exits non-zero, and the next hourly fire actually attempts the pipeline again.

## Lessons / preventions

- **A SUCCEEDED Apify run is not proof of useful output.** Always inspect `statusMessage` and at least one post-level field before trusting the dataset.
- **The "quiet day" branch is dangerous when it shares a code path with "scraper broke."** Distinguishing them at the earliest possible layer (run metadata, not parsed array) is what unblocks automated retry.
- **Apify proxy failures self-heal on the order of minutes-to-hours.** Don't add complex retry logic inside a single actor call — the hourly cron is already a perfectly good retry loop once the pipeline can exit non-zero cleanly.
- **Don't poison `data/seen/` with non-post URLs.** The dedup contract assumes every URL in `seen` represents an actually-evaluated post. Page-URL stubs slipping in is a soft form of the same class of bug as committing 0-album days with garbage — the symptom was different (no future re-eval blocked, since no real post URL would ever collide with `facebook.com/<page>`), but the principle holds: validate before persisting.

## References

- Failed Apify run: `<run-id>` (started 2026-05-22T00:30:37Z, dataset `<dataset-id>`, 4 requests / 0 succeeded)
- Recovery rescrape: `<run-id>` (2026-05-22T17:32:30Z, 72 posts)
- Forensic artifacts (local-only, gitignored): `data/raw/2026-05-22-apify.json.broken-backup`, `data/raw/2026-05-22-rescrape.json`
- Patch commit: `a586d3c fix(scrape): detect Apify SUCCEEDED-but-all-requests-failed runs`
- Recovery commits: `f15bf6f digest: 2026-05-22 retroactive (11 albums)`, `50455e2 notified: 2026-05-22 (retroactive backfill)`
- Wrapper retry chain: `scripts/run_daily_dig.sh:27-35`, launchd plist at `scripts/com.erdscribe.daily-dig.plist`
- Skill failure-handling clause: `.claude/skills/daily-dig/SKILL.md` step 2 ("If Apify fails ... stop and report")
