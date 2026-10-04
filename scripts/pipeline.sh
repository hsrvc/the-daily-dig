#!/usr/bin/env bash
#
# Daily Music Digging Pipeline
#
# Steps (in order):
#   1. Scrape      — Apify → data/raw/
#   2. Dedup       — filter already-seen posts
#   3. Parse       — Claude Code writes data/parsed/ (blocking; exits if not done)
#   4. Covers      — FB CDN → iTunes → MusicBrainz
#   5. Digest      — generate site/src/content/digests/
#   6. Build       — Astro → site/dist/
#   7. Deploy      — wrangler → Cloudflare Pages      (opt-in: --deploy)
#   8. Spotify     — update playlist                  (opt-in: --spotify)
#   9. Email       — send HTML digest via Resend       (opt-in: --email, requires --deploy)
#
# Usage:
#   ./scripts/pipeline.sh                              # scrape + build only
#   ./scripts/pipeline.sh --skip-scrape                # skip scraping, regenerate site
#   ./scripts/pipeline.sh --deploy --spotify --email   # full end-to-end run
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DATE=$(date +%Y-%m-%d)

SKIP_SCRAPE=false
RUN_DEPLOY=false
RUN_SPOTIFY=false
RUN_EMAIL=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --skip-scrape) SKIP_SCRAPE=true;  shift ;;
    --deploy)      RUN_DEPLOY=true;   shift ;;
    --spotify)     RUN_SPOTIFY=true;  shift ;;
    --email)       RUN_EMAIL=true;    shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [[ "$RUN_EMAIL" == "true" && "$RUN_DEPLOY" != "true" ]]; then
  echo "ERROR: --email requires --deploy (email must go out after the site is live)"
  exit 1
fi

RAW_FILE="$PROJECT_DIR/data/raw/$DATE-apify.json"
RAW_NEW="$PROJECT_DIR/data/raw/$DATE-apify-new.json"
PARSED_FILE="$PROJECT_DIR/data/parsed/$DATE.json"

echo "==========================================="
echo "  The Daily Dig — Pipeline"
echo "  Date: $DATE"
echo "==========================================="
echo ""

# Step 1: Scrape Facebook pages via Apify
if [[ "$SKIP_SCRAPE" = false ]]; then
  echo ">>> Step 1: Scraping Facebook pages (Apify)..."
  python3 "$SCRIPT_DIR/scrape_apify.py"
  echo ""

  if [[ ! -f "$RAW_FILE" ]]; then
    echo "ERROR: Scraping produced no output at $RAW_FILE"
    exit 1
  fi
  echo "Raw data saved to $RAW_FILE"
else
  echo ">>> Step 1: Skipped (--skip-scrape)"
fi

echo ""

# Step 2: Dedup raw posts against digest history
echo ">>> Step 2: Deduping raw posts vs digest history..."
if [[ -f "$RAW_FILE" ]]; then
  python3 "$SCRIPT_DIR/dedup_raw.py" --date "$DATE"
  NEW_COUNT=$(python3 -c "import json,sys; print(len(json.load(open('$RAW_NEW'))))")
  echo ""
  if [[ "$NEW_COUNT" == "0" && ! -f "$PARSED_FILE" ]]; then
    echo "Zero new posts since last digest, and no existing parsed file."
    echo "Nothing to add to the site. Exiting cleanly."
    exit 0
  fi
elif [[ ! -f "$PARSED_FILE" ]]; then
  echo "ERROR: no raw file at $RAW_FILE and no existing parsed file. Nothing to do."
  exit 1
else
  echo "Skipped (no raw file, but parsed file exists at $PARSED_FILE)"
fi

echo ""

# Step 3: Parse raw posts into structured records (Claude Code does this inline)
if [[ -f "$PARSED_FILE" ]]; then
  echo ">>> Step 3: Parsed data already exists at $PARSED_FILE"
else
  echo ">>> Step 3: WAITING — parsed data not found at $PARSED_FILE"
  echo "    Run Claude Code to parse raw posts into structured records."
  echo "    Input:  $RAW_NEW  ($NEW_COUNT new posts)"
  echo "    Output: $PARSED_FILE"
  exit 0
fi

echo ""

# Step 4: Fetch cover art (FB CDN → iTunes → MusicBrainz fallback)
echo ">>> Step 4: Fetching cover art..."
python3 "$SCRIPT_DIR/fetch_covers.py" --date "$DATE" || {
  echo "[warn] Some covers failed to fetch (non-blocking)"
}
echo ""

# Step 5: Generate digest Markdown
echo ">>> Step 5: Generating digest Markdown..."
python3 "$SCRIPT_DIR/generate_digest.py" --date "$DATE"
echo ""

# Step 6: Build Astro site
echo ">>> Step 6: Building site..."
cd "$PROJECT_DIR/site"
npm run build
echo ""

echo "==========================================="
echo "  Build complete."
echo "==========================================="
echo ""

# Step 7 (optional): Deploy to Cloudflare Pages
if [[ "$RUN_DEPLOY" == "true" ]]; then
  echo ">>> Step 7: Deploying to Cloudflare Pages..."
  cd "$PROJECT_DIR/site"
  npx wrangler pages deploy dist --project-name=daily-dig
  echo ""
fi

# Steps 8 + 9 (optional): Spotify and email run in parallel after deploy —
# they're independent of each other (Spotify needs only parsed data, email
# needs the deployed site which is now live).
if [[ "$RUN_SPOTIFY" == "true" || "$RUN_EMAIL" == "true" ]]; then
  echo ">>> Steps 8–9: Spotify + email (parallel)..."

  SPOTIFY_PID=""
  EMAIL_PID=""

  if [[ "$RUN_SPOTIFY" == "true" ]]; then
    python3 "$SCRIPT_DIR/update_spotify.py" "$DATE" &
    SPOTIFY_PID=$!
  fi

  if [[ "$RUN_EMAIL" == "true" ]]; then
    python3 "$SCRIPT_DIR/send_email.py" --date "$DATE" &
    EMAIL_PID=$!
  fi

  SPOTIFY_OK=0
  EMAIL_OK=0
  [[ -n "$SPOTIFY_PID" ]] && wait "$SPOTIFY_PID" || SPOTIFY_OK=$?
  [[ -n "$EMAIL_PID"   ]] && wait "$EMAIL_PID"   || EMAIL_OK=$?

  [[ $SPOTIFY_OK -ne 0 ]] && echo "[warn] Spotify update failed (exit $SPOTIFY_OK)"
  [[ $EMAIL_OK   -ne 0 ]] && echo "[warn] Email send failed (exit $EMAIL_OK)"
  echo ""
fi

echo "==========================================="
echo "  Done!"
if [[ "$RUN_DEPLOY" != "true" ]]; then
  echo "  Preview: cd site && npx astro dev"
fi
echo "==========================================="
