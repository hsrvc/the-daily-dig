#!/usr/bin/env python3
"""
Scrape Facebook pages for vinyl record shop posts using Apify.
Returns structured data with full text, image URLs, post links.

Usage:
    python scripts/scrape_apify.py                          # Scrape all pages, 10 posts each
    python scripts/scrape_apify.py --limit 5                # 5 posts per page
    python scripts/scrape_apify.py --pages uourecords       # Single page
    python scripts/scrape_apify.py --dataset-id <id>        # Reuse existing dataset (no new scrape)
    python scripts/scrape_apify.py --reuse-latest           # Auto-find today's most recent run
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "data" / "raw"
SOURCES_FILE = PROJECT_DIR / "sources.yaml"
LOCAL_ENV = Path.home() / ".daily-dig.env"


def load_sources() -> dict[str, str]:
    import yaml
    with open(SOURCES_FILE) as f:
        config = yaml.safe_load(f)
    return {
        p["slug"]: p["url"]
        for p in config.get("facebook_pages", [])
    }


def get_apify_token() -> str:
    """Read token from env, ~/.daily-dig.env, or ~/.apify/auth.json."""
    token = os.environ.get("APIFY_TOKEN")
    if token:
        return token
    # Load from local secrets file (used when running locally without export)
    if LOCAL_ENV.exists():
        for line in LOCAL_ENV.read_text().splitlines():
            line = line.strip().lstrip("export").strip()
            if line.startswith("APIFY_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
                if token:
                    return token
    auth_file = Path.home() / ".apify" / "auth.json"
    if auth_file.exists():
        with open(auth_file) as f:
            return json.load(f).get("token", "")
    print("ERROR: No Apify token found. Add APIFY_TOKEN to ~/.daily-dig.env or run 'apify login'.")
    sys.exit(1)


def find_latest_dataset_today() -> str:
    """Query Apify API for the most recent successful facebook-posts-scraper run today."""
    import urllib.request
    token = get_apify_token()
    url = f"https://api.apify.com/v2/acts/apify~facebook-posts-scraper/runs?token={token}&limit=10&desc=1"
    with urllib.request.urlopen(url) as resp:
        runs = json.loads(resp.read())["data"]["items"]
    today_prefix = date.today().isoformat()  # e.g. "2026-05-07"
    for run in runs:
        started = run.get("startedAt", "")
        if run.get("status") == "SUCCEEDED" and started.startswith(today_prefix):
            dataset_id = run["defaultDatasetId"]
            print(f"Found today's run: {run['id']} started {started}")
            print(f"  Dataset: {dataset_id}")
            return dataset_id
    print("ERROR: No successful facebook-posts-scraper run found for today.")
    sys.exit(1)


def is_error_stub(post: dict) -> bool:
    """An Apify per-page error placeholder, not a real post.

    When the actor's CheerioCrawler fails every retry for a startUrl (proxy
    ECONNRESET, Node HTTP parse error, etc.), it emits one of these into the
    dataset instead of failing the run. Shape:
      {"url": "<page url>", "error": "no_items",
       "errorDescription": "Empty or private data for provided input"}
    """
    return bool(post.get("error")) and not post.get("postId")


def assert_actor_succeeded(run_data: dict) -> None:
    """Detect the SUCCEEDED-but-crawler-failed-everything case.

    Apify marks the run SUCCEEDED whenever the actor exits cleanly, even when
    its internal crawler failed every single request. The signal sits in
    statusMessage as e.g. "Finished! Total 4 requests: 0 succeeded, 4 failed."
    Exits non-zero so the wrapper's hourly retry can attempt again.

    Parse the succeeded/failed counts numerically — a naive `"0 succeeded" in
    msg` substring check false-positives on any nonzero count ending in 0 (e.g.
    "20 succeeded" contains "0 succeeded").
    """
    import re
    msg = run_data.get("statusMessage") or ""
    m = re.search(r"(\d+)\s+succeeded,\s*(\d+)\s+failed", msg)
    if m and int(m.group(1)) == 0 and int(m.group(2)) > 0:
        print("ERROR: Apify actor finished but the crawler failed every request.")
        print(f"  Run ID:        {run_data.get('id', '<unknown>')}")
        print(f"  statusMessage: {msg}")
        print(f"  Dataset:       {run_data.get('defaultDatasetId', '<unknown>')}")
        print("  Typically a transient Apify proxy-pool issue. Hourly cron will retry.")
        sys.exit(1)


def partition_posts(posts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split a fetched dataset into (real_posts, error_stubs)."""
    real = [p for p in posts if not is_error_stub(p)]
    stubs = [p for p in posts if is_error_stub(p)]
    return real, stubs


def run_scraper(urls: list[dict], results_limit: int) -> dict:
    input_data = json.dumps({
        "startUrls": urls,
        "resultsLimit": results_limit,
    })
    print(f"Running Apify scraper for {len(urls)} pages, {results_limit} posts each...")
    result = subprocess.run(
        [
            "apify", "actors", "call", "apify/facebook-posts-scraper",
            "--json",
            "--user-agent", "apify-agent-skills/apify-ultimate-scraper",
            "--input", input_data,
        ],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        print(f"ERROR: Apify call failed:\n{result.stderr}")
        sys.exit(1)
    cli_output = json.loads(result.stdout)
    # The apify-cli --json shape has drifted between versions (flat run object,
    # nested under "run", or nested under "data"). Probe the known shapes for a
    # run id; if none match, fall back to the REST API's "latest run today"
    # lookup — the actor call just finished, so it's the most recent run.
    run_id = (
        (cli_output.get("run") or {}).get("id")
        or (cli_output.get("data") or {}).get("id")
        or cli_output.get("id")
    )
    import urllib.request
    token = get_apify_token()
    if run_id:
        with urllib.request.urlopen(f"https://api.apify.com/v2/actor-runs/{run_id}?token={token}") as resp:
            run_data = json.loads(resp.read())["data"]
    else:
        print("  WARN: could not read run id from apify-cli output; "
              "falling back to REST API latest-run lookup.")
        dataset_id = find_latest_dataset_today()
        with urllib.request.urlopen(
            f"https://api.apify.com/v2/datasets/{dataset_id}?token={token}"
        ) as resp:
            ds = json.loads(resp.read())["data"]
        run_id = ds.get("actRunId")
        with urllib.request.urlopen(f"https://api.apify.com/v2/actor-runs/{run_id}?token={token}") as resp:
            run_data = json.loads(resp.read())["data"]
    status = run_data.get("status")
    if status != "SUCCEEDED":
        print(f"ERROR: Run status: {status}")
        print(f"Message: {run_data.get('statusMessage')}")
        sys.exit(1)
    assert_actor_succeeded(run_data)
    cost = run_data.get("usageTotalUsd", 0)
    events = run_data.get("chargedEventCounts", {})
    print(f"  Status: {status}")
    print(f"  Cost: ${cost:.4f}")
    print(f"  Posts scraped: {events.get('post', 0)}")
    print(f"  Dataset: {run_data['defaultDatasetId']}")
    return run_data


def cache_images(posts: list[dict], today: str) -> None:
    """Download all post images immediately while FB CDN is fresh.

    Saves to data/raw/images/{today}/{md5(url)[:16]}.jpg so fetch_covers.py
    can use them as a local fallback after CDN expiry.
    """
    import urllib.request
    cache_dir = OUTPUT_DIR.parent / "images" / today
    cache_dir.mkdir(parents=True, exist_ok=True)

    urls: list[str] = []
    for post in posts:
        for m in post.get("media", []):
            uri = (m.get("image") or {}).get("uri") or (m.get("photo_image") or {}).get("uri") or ""
            if uri and "fbcdn" in uri and uri not in urls:
                urls.append(uri)

    ok = skip = fail = 0
    for url in urls:
        # Key by photo ID (stable across CDN edge variants and truncated URLs)
        m = __import__('re').search(r'/(\d+_\d+)_\d+_[a-z]\.', url)
        key = m.group(1) if m else hashlib.md5(url.encode()).hexdigest()[:16]
        dest = cache_dir / f"{key}.jpg"
        if dest.exists():
            skip += 1
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "daily-dig/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            if len(data) > 1024:
                dest.write_bytes(data)
                ok += 1
            else:
                fail += 1
        except Exception:
            fail += 1

    total = ok + skip + fail
    print(f"  Image cache: {ok} saved, {skip} already cached, {fail} failed ({total} total)")


def fetch_dataset(dataset_id: str) -> list[dict]:
    import urllib.request
    token = get_apify_token()
    url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={token}"
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Scrape FB pages via Apify")
    parser.add_argument("--limit", type=int, default=10, help="Posts per page")
    parser.add_argument("--pages", nargs="*", help="Specific page names to scrape")
    parser.add_argument("--dataset-id", help="Reuse an existing Apify dataset (skip scrape)")
    parser.add_argument("--reuse-latest", action="store_true", help="Auto-find today's most recent successful run")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    out_file = OUTPUT_DIR / f"{today}-apify.json"

    # --- Reuse path: no new scrape ---
    if args.dataset_id or args.reuse_latest:
        dataset_id = args.dataset_id or find_latest_dataset_today()
        print(f"\nFetching dataset {dataset_id} (no new scrape)...")
        raw_posts = fetch_dataset(dataset_id)
        posts, stubs = partition_posts(raw_posts)
        print(f"  Retrieved {len(raw_posts)} entries ({len(posts)} real, {len(stubs)} error stubs)")
        if stubs:
            for s in stubs:
                print(f"    stub: {s.get('url')} — {s.get('error')}: {s.get('errorDescription')}")
        if not posts:
            print("ERROR: Dataset contains only Apify error stubs — no real posts to write.")
            sys.exit(1)
        from collections import Counter
        for page, count in Counter(p.get("pageName", "?") for p in posts).most_common():
            print(f"    {page}: {count}")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
        print(f"\nSaved to {out_file}")
        print("\nCaching post images...")
        cache_images(posts, today)
        return

    # --- Normal scrape path ---
    PAGES = load_sources()
    if args.pages:
        selected = {k: v for k, v in PAGES.items() if k in args.pages}
        if not selected:
            print(f"ERROR: Unknown pages. Available: {list(PAGES.keys())}")
            sys.exit(1)
    else:
        selected = PAGES

    urls = [{"url": url} for url in selected.values()]
    run_data = run_scraper(urls, args.limit)
    dataset_id = run_data["defaultDatasetId"]

    print("\nFetching results...")
    raw_posts = fetch_dataset(dataset_id)
    posts, stubs = partition_posts(raw_posts)
    print(f"  Retrieved {len(raw_posts)} entries ({len(posts)} real, {len(stubs)} error stubs)")
    if stubs:
        for s in stubs:
            print(f"    stub: {s.get('url')} — {s.get('error')}: {s.get('errorDescription')}")
    if not posts:
        print("ERROR: Dataset contains only Apify error stubs — no real posts to write.")
        sys.exit(1)

    from collections import Counter
    for page, count in Counter(p.get("pageName", "?") for p in posts).most_common():
        print(f"    {page}: {count}")

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)

    print(f"\nSaved to {out_file}")
    print(f"Total cost: ${run_data.get('usageTotalUsd', 0):.4f}")
    print("\nCaching post images...")
    cache_images(posts, today)


if __name__ == "__main__":
    main()
