# Handoff: Double Email / Spotify Send Bug

**Period:** 2026-05-08 to 2026-05-12  
**Status:** Closed — verified working 2026-05-12. Two additional fixes applied this session (see below).

---

## What the bug is

Every day the pipeline runs, it should send exactly one digest email and add tracks to Spotify once. Instead, it was sending duplicate emails and double-adding Spotify tracks — sometimes by the local run, sometimes by the GH Actions backup, sometimes both.

---

## How the pipeline works (relevant parts)

Two execution paths run the pipeline:

**Local (cron at 08:30, 09:30, 10:30, 11:30 TPE):**
```
cron → run_daily_dig.sh → claude (daily-dig skill) → wrapper post-steps
```

**GH Actions backup (04:00 UTC = 12:00 TPE):**
```
schedule trigger → daily-dig.yml → claude (daily-dig skill) → workflow post-steps
```

GH Actions does a **fresh `git checkout`** — it only sees files committed and pushed to the repo. This is critical.

Email and Spotify are guarded by marker files:
- `data/notified/email-{date}` — created after successful email send
- `data/notified/spotify-{date}` — created after successful Spotify update

Both scripts check for their marker before running and skip if it exists. GH Actions checks for these markers at the top of the workflow and skips steps if already done.

**The invariant that must hold:** markers must be committed and pushed to the repo before GH Actions runs. If they're only local, GH Actions doesn't see them.

---

## Root cause chain (five separate failures)

### Failure 1 — Markers not committed at all (2026-05-08)
Email and Spotify ran in `run_daily_dig.sh` after claude exited, creating markers locally. But neither the wrapper nor the skill ever committed them. GH Actions did a fresh checkout, saw no markers, ran both again.

**Fix:** Added `git add data/notified/ && git commit` to both the skill (step 8.5) and the wrapper.

### Failure 2 — Skill told to push markers, causing non-fast-forward conflicts (2026-05-09)
The skill was instructed to commit AND push markers. When the wrapper also tried to push, it got rejected with non-fast-forward. The `|| echo "[warn]"` swallowed the error silently. Either skill or wrapper would succeed — but not reliably.

**Fix:** Skill commits only. Wrapper owns the push.

### Failure 3 — Wrapper push was inside a conditional block (2026-05-11 — today's bug)
The wrapper's push only fired if the wrapper itself had something new to commit:
```bash
git add data/notified/
if ! git diff --cached --quiet; then
  git commit -m "notified: [local]"
  git pull --rebase && git push   # ← only runs when wrapper commits something
fi
```
If the skill already committed markers, the wrapper staged nothing, the `if` was false, and the entire block — including the push — was skipped. Skill's commit sat unpushed for hours. GH Actions ran, saw no markers, double-sent.

**Fix:** Moved `pull --rebase + push` outside and after the conditional block — unconditional, always runs when `PIPELINE_OK=1`.

---

## What was changed

### `scripts/run_daily_dig.sh` (and `$HOME/.local/bin/run_daily_dig.sh`)
Key section after email+Spotify run:
```bash
# Commit any new notified markers (skill may have committed them already).
git -C "$REPO_DIR" add data/notified/
if ! git -C "$REPO_DIR" diff --cached --quiet; then
  git -C "$REPO_DIR" commit -m "notified: $DATE_TAG [local]"
fi

# Always push — skill commits but never pushes; wrapper owns the push unconditionally.
git -C "$REPO_DIR" pull --rebase origin main || echo "[warn] rebase failed"
git -C "$REPO_DIR" push origin main || echo "[warn] push failed — GH Actions may double-send"
```

Also added a digest commit safety net before the email/Spotify block — if claude crashes before committing the digest, wrapper commits whatever is staged. Note: this only helps if claude completed all file writes before crashing. It cannot rescue a partial/corrupt digest.

### `.claude/skills/daily-dig/SKILL.md`
- Step 8.5 now says: skill commits markers, does NOT push. Wrapper owns the push.
- Clarified `/tmp` files are local-only and not visible to GH Actions.

### `.github/workflows/daily-dig.yml`
- Added "Commit digest safety net" step after the skill runs — same logic as wrapper safety net, for GH Actions path.

### `scripts/send_email.py`
- Added auto-loading from `~/.daily-dig.env` so it works without the wrapper setting env vars.

### `scripts/update_spotify.py`
- Fixed `--dry-run` writing the marker even when no real work was done.

---

## What still could go wrong

| Scenario | Likelihood | Impact |
|---|---|---|
| `git push` fails (network) — push failure now surfaces as step failure in GH Actions (fixed 2026-05-12) | Low | Double send |
| Two concurrent local runs both pass catch-up guard before either writes seen file | Very low | Double send |
| Manual `python3 scripts/send_email.py` run without `git pull` first — local has no markers, sends again | User error | Double send |
| Resend returns non-200 success code (e.g. 201) — script exits 1, no marker written, GH Actions retries | Very low | Double send |

---

## Additional fixes applied 2026-05-12

### Fix 4 — GH Actions push failures were silently swallowed
Both CI steps (`Commit digest safety net`, `Commit notified markers`) used bash `||`/`&&` operator chaining that accidentally made `git push` unconditional but swallowed push failures with `|| true`. Rewrote as explicit `if/commit/push` blocks — push failures now fail the step and surface in the run log.

### Fix 5 — dig.db dirty after every run, blocking git pull --rebase
`update_spotify.py` writes to the `spotify_added` table in `data/dig.db` after the digest commit (step 6). This left dig.db modified but uncommitted, causing `git pull --rebase` to fail on every run. More critically, the `spotify_added` dedup rows were never pushed, so GH Actions (fresh checkout) could re-add the same tracks to Spotify on future runs.

**Fix:** Added `data/dig.db` to the notified markers commit in both the wrapper and GH Actions workflow — dig.db is now committed alongside the notification markers after Spotify runs.
