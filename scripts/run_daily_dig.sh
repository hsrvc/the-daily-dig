#!/bin/zsh
# Wrapper invoked by launchd (or manually) to run the Daily Dig pipeline locally.
# Pulls secrets from ~/.zshenv (CLAUDE_CODE_OAUTH_TOKEN, CLOUDFLARE_*),
# ~/.apify/auth.json (APIFY_TOKEN), and ~/.daily-dig.env (RESEND_API_KEY,
# NOTIFY_EMAIL). Logs to ~/Library/Logs/daily-dig/{date}.log.
#
# Run manually:   bash scripts/run_daily_dig.sh
# Run via launchd: see scripts/com.erdscribe.daily-dig.plist

set -uo pipefail

# Copy claude to a stable path so macOS TCC grants it once and never re-prompts
# on version updates (TCC tracks the real binary path, not the symlink).
CLAUDE_STABLE="$HOME/.local/bin/claude_runner"
/bin/cp -f "$(readlink -f $HOME/.local/bin/claude)" "$CLAUDE_STABLE" 2>/dev/null || true

REPO_DIR="$HOME/daily-dig"
LOG_DIR="$HOME/Library/Logs/daily-dig"
mkdir -p "$LOG_DIR"

# Use TPE for the date so the log file matches the digest date.
DATE_TAG=$(TZ=Asia/Taipei date '+%Y-%m-%d')
LOG_FILE="$LOG_DIR/$DATE_TAG.log"

# Catch-up guard — prevents double-runs and pre-08:30 fires (from RunAtLoad).
SEEN_FILE="$REPO_DIR/data/seen/$DATE_TAG.json"
if [[ -f "$SEEN_FILE" ]]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S %Z') catch-up guard: $SEEN_FILE exists — already ran today, skipping" >> "$LOG_FILE"
  exit 0
fi
CURRENT_MINS=$(( $(TZ=Asia/Taipei date '+%-H') * 60 + $(TZ=Asia/Taipei date '+%-M') ))
if (( CURRENT_MINS < 510 )); then  # 510 = 08:30
  echo "$(date '+%Y-%m-%d %H:%M:%S %Z') catch-up guard: $(TZ=Asia/Taipei date '+%H:%M') TPE is before 08:30 — skipping" >> "$LOG_FILE"
  exit 0
fi

# Pull in user shell env (claude OAuth token, Cloudflare creds, etc.)
[[ -f "$HOME/.zshenv" ]] && source "$HOME/.zshenv"

# Apify token lives in ~/.apify/auth.json — extract if not already exported.
if [[ -z "${APIFY_TOKEN:-}" ]] && [[ -f "$HOME/.apify/auth.json" ]]; then
  export APIFY_TOKEN=$(/usr/bin/python3 -c "import json,sys; print(json.load(open('$HOME/.apify/auth.json'))['token'])")
fi

# Resend creds + notify recipient live in ~/.daily-dig.env.
[[ -f "$HOME/.daily-dig.env" ]] && source "$HOME/.daily-dig.env"

# Make sure the binaries we need are on PATH (claude, jq, npm, wrangler, python3).
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

cd "$REPO_DIR" || { echo "FATAL: cannot cd to $REPO_DIR"; exit 1; }

{
  echo "=========================================================="
  echo "Daily Dig run"
  echo "  started:    $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "  date tag:   $DATE_TAG"
  echo "  repo:       $REPO_DIR"
  echo "  log:        $LOG_FILE"
  echo "=========================================================="
  echo

  PIPELINE_OK=0
  if "$CLAUDE_STABLE" --dangerously-skip-permissions -p \
    "Use the daily-dig skill to run today's pipeline end-to-end. Today is $DATE_TAG. Report a one-paragraph summary at the end."; then
    PIPELINE_OK=1
  fi

  echo
  echo "--- pipeline exit status: $PIPELINE_OK"

  # Safety net: commit digest if Claude didn't (e.g. crashed before step 6).
  # Idempotent — if Claude already committed, nothing to stage and this is a no-op.
  if [[ "$PIPELINE_OK" == "1" ]]; then
    git -C "$REPO_DIR" add data/seen data/parsed site/src/content/digests site/public/covers 2>/dev/null || true
    if ! git -C "$REPO_DIR" diff --cached --quiet; then
      git -C "$REPO_DIR" commit -m "digest: $DATE_TAG [wrapper safety net]"
      git -C "$REPO_DIR" push origin main || echo "[warn] digest safety-net push failed"
    fi
  fi

  if [[ "$PIPELINE_OK" == "1" ]]; then
    # Steps 8+9: Spotify and email in parallel — both fire after deploy, independent of each other.
    python3 "$REPO_DIR/scripts/update_spotify.py" "$DATE_TAG" &
    SPOTIFY_PID=$!
    python3 "$REPO_DIR/scripts/send_email.py" --date "$DATE_TAG" &
    EMAIL_PID=$!

    wait "$SPOTIFY_PID" || echo "[warn] Spotify update failed (non-blocking)"
    wait "$EMAIL_PID"   || echo "[warn] Email send failed (non-blocking)"

    # Commit any new notified markers and dig.db (spotify_added rows written after digest commit).
    git -C "$REPO_DIR" add data/notified/ data/dig.db
    if ! git -C "$REPO_DIR" diff --cached --quiet; then
      git -C "$REPO_DIR" commit -m "notified: $DATE_TAG [local]"
    fi

    # Always push — skill commits but never pushes; wrapper owns the push unconditionally.
    git -C "$REPO_DIR" pull --rebase origin main || echo "[warn] rebase failed"
    git -C "$REPO_DIR" push origin main || echo "[warn] push failed — GH Actions may double-send"
  fi

  echo
  echo "  finished:   $(date '+%Y-%m-%d %H:%M:%S %Z') (exit=$PIPELINE_OK)"
  echo "=========================================================="
} >> "$LOG_FILE" 2>&1

exit $((1 - PIPELINE_OK))
