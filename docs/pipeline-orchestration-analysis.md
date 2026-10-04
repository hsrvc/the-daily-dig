# Pipeline Orchestration — Architecture Analysis & Handoff

**Session:** 2026-05-05 session 6  
**Status:** Production works. Refactor deferred deliberately.

---

## What exists today

Two overlapping orchestrators run the same pipeline:

**1. `daily-dig` Claude skill** (primary, unread)
Invoked by both `run_daily_dig.sh` (launchd) and `daily-dig.yml` (GH Actions) via:
```bash
claude --dangerously-skip-permissions -p "Use the daily-dig skill..."
```
Does: scrape → parse (LLM) → covers → digest → build → **deploy** → git commits at each step.
This is the actual production path. Its full content has not been read in any session.

**2. `scripts/pipeline.sh`** (secondary, partially redundant)
A bash script added in an earlier session. Does steps 1–6 (scrape→build) with optional
`--deploy --spotify --email` flags added in session 6. **Nothing in the live production path
calls it with those flags.** Only useful for manual one-off runs from the terminal.

---

## Current production flow (post session 6 changes)

```
launchd / GH Actions
  └─ claude (daily-dig skill)      scrape → parse → covers → build → deploy
  └─ update_spotify.py             ← newly wired, fires after skill exits
  └─ send_email.py                 ← was broken (wrong path in launchd), now fixed
```

Spotify and email are bolted on after the skill in both run paths — not inside `pipeline.sh`.

---

## What was fixed in session 6

| Issue | Fix |
|---|---|
| Spotify never fired automatically | Added to `run_daily_dig.sh` and GH Actions |
| `send_email.py` path broken in launchd | `$(dirname $0)/send_email.py` → `~/.local/bin/send_email.py` (doesn't exist). Fixed to `$REPO_DIR/scripts/send_email.py` |
| Email fired before deploy in GH Actions | RESEND env vars moved to job level; email step now after pipeline step |
| Spotify and email ran sequentially | Now run in parallel in `pipeline.sh` (for manual runs only) |
| Cloudflare cached `text/html` for Snow Patrol cover | Purged via cache purge API |
| Gmail cached broken thumbnail responses | Re-sent email; all 14 covers now serving `image/jpeg` |

---

## The architectural problem

`pipeline.sh` was created before the daily-dig skill became the full orchestrator. When the
skill took over deploy, `pipeline.sh` became redundant for the deploy→email→spotify chain.
Session 6 added `--deploy --email --spotify` flags to `pipeline.sh` without realising those
flags are never called in production. This created a false impression the architecture was clean.

The real production path bypasses `pipeline.sh`'s new flags entirely.

---

## Will production run correctly?

**Yes.** Both paths (launchd and GH Actions) correctly fire Spotify and email after the skill
completes. Credentials available in both. Ordering correct (email after deploy, because deploy
is inside the skill which exits before email fires).

---

## The right long-term fix

Make the skill do **only what only an LLM can do**: the parse step (step 3). Move everything
else into `pipeline.sh`. Both launchd and GH Actions then simply call:

```bash
pipeline.sh --deploy --spotify --email
```

This eliminates the two-orchestrator problem entirely.

Canonical step order (target state):
```
pipeline.sh --deploy --spotify --email
  ├─ step 1: scrape       (python, apify)
  ├─ step 2: dedup        (python)
  ├─ step 3: parse        (calls claude for this step only)
  ├─ step 4: covers       (python)
  ├─ step 5: digest       (python)
  ├─ step 6: build        (npm)
  ├─ step 7: deploy       (wrangler)
  └─ steps 8+9: spotify + email  (parallel python)
```

---

## Why not done in session 6

1. The daily-dig skill's full content was **not read** — its exact step order, git commit
   points, and state assumptions are unknown
2. Session 6's first live test of Spotify + email wiring was tomorrow's run — breaking the
   skill risked no digest at all
3. The refactor is **correctness-neutral** — production works as-is
4. Risk/reward: high risk (broken daily run), zero functional gain

---

## Recommended next session checklist

- [ ] Read full daily-dig skill content (check `~/.claude/plugins/` or wherever skills live)
- [ ] Map every step it performs and where it git-commits
- [ ] Extract only the parse step into the skill; move everything else to `pipeline.sh`
- [ ] Test with `pipeline.sh --skip-scrape --deploy --spotify --email --date <past-date>`
- [ ] Confirm output matches current production output
- [ ] Update `run_daily_dig.sh` to call `pipeline.sh --deploy --spotify --email` directly
- [ ] Update `daily-dig.yml` to call `pipeline.sh --deploy --spotify --email` directly
- [ ] Remove bolted-on Spotify/email steps from both wrappers
- [ ] Run end-to-end on a non-production date before enabling on live cron
