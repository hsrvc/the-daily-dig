#!/usr/bin/env python3
"""
Scrape Facebook pages for vinyl record shop posts using Playwright.
Uses screenshot-based extraction — Facebook scrambles DOM text, but
screenshots capture the actual rendered content perfectly.

Claude Code /loop reads these screenshots and parses album info visually.

First run: Opens a browser for you to log into Facebook manually.
Subsequent runs: Reuses the saved session automatically.

Usage:
    python scripts/scrape.py            # Full scrape (screenshots)
    python scripts/scrape.py --dry-run  # Check session + page loads only
    python scripts/scrape.py --login    # Open browser for manual FB login
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROFILE_DIR = os.path.expanduser("~/.music-digger-profile")

PAGES = {
    "uourecords": "https://www.facebook.com/uourecords",
    "Tokyobuybuydiary": "https://www.facebook.com/Tokyobuybuydiary",
    "beethobearrecords": "https://www.facebook.com/beethobearrecords",
    "THTRECORDs": "https://www.facebook.com/THTRECORDs",
}

SCROLL_COUNT = 5          # How many viewport-heights to capture
SCROLL_PAUSE_MIN = 2.0    # Min seconds between scrolls
SCROLL_PAUSE_MAX = 3.5    # Max seconds between scrolls
PAGE_NAV_PAUSE_MIN = 2.0  # Min seconds between page navigations
PAGE_NAV_PAUSE_MAX = 4.0  # Max seconds between page navigations

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _human_delay(lo: float = 1.0, hi: float = 3.0) -> None:
    time.sleep(random.uniform(lo, hi))


def _is_logged_in(page) -> bool:
    if page.query_selector('input[name="email"]'):
        return False
    return True


def _dismiss_popups(page) -> None:
    """Try to close common Facebook popups/overlays."""
    selectors = [
        '[aria-label="Close"]',
        '[aria-label="關閉"]',
        '[data-testid="cookie-policy-manage-dialog-accept-button"]',
        'div[role="dialog"] [aria-label="Close"]',
        'div[role="dialog"] [aria-label="關閉"]',
    ]
    for sel in selectors:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                time.sleep(0.5)
        except Exception:
            pass


def _scroll_and_screenshot(page, page_name: str, output_dir: Path) -> list[str]:
    """
    Scroll through the page and take screenshots at each viewport position.
    Returns list of screenshot file paths.
    """
    screenshots = []

    # First, scroll to the posts section (past the page header/cover photo)
    page.evaluate("window.scrollBy(0, 600)")
    _human_delay(1.5, 2.5)

    for i in range(SCROLL_COUNT):
        # Take screenshot of current viewport
        filename = f"{page_name}_scroll_{i}.png"
        filepath = output_dir / filename
        page.screenshot(path=str(filepath), full_page=False)
        screenshots.append(str(filepath))
        print(f"    screenshot {i + 1}/{SCROLL_COUNT}: {filename}")

        # Scroll down
        page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
        _human_delay(SCROLL_PAUSE_MIN, SCROLL_PAUSE_MAX)

    return screenshots


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape Facebook vinyl record shop pages via screenshots"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Only check session and that FB pages load",
    )
    parser.add_argument(
        "--login", action="store_true",
        help="Open browser for manual Facebook login (one-time setup)",
    )
    args = parser.parse_args()

    os.makedirs(PROFILE_DIR, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "ERROR: playwright is not installed.\n"
            "Run:  pip install playwright && playwright install chromium"
        )
        sys.exit(1)

    with sync_playwright() as p:
        print(f"Using dedicated profile: {PROFILE_DIR}")
        print("Launching Playwright Chromium...")

        context = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            ignore_default_args=["--enable-automation"],
        )

        try:
            # --- Login mode ---
            if args.login:
                page = context.new_page()
                page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
                print("\n" + "=" * 60)
                print("  MANUAL LOGIN REQUIRED")
                print("  Log into Facebook in the browser window.")
                print("  Waiting up to 120 seconds for login...")
                print("=" * 60)

                logged_in = False
                for i in range(120):
                    time.sleep(1)
                    try:
                        url = page.url
                        if "login" not in url and "checkpoint" not in url:
                            if _is_logged_in(page):
                                logged_in = True
                                break
                    except Exception:
                        pass
                    if i % 10 == 0 and i > 0:
                        print(f"  Still waiting... ({i}s)")

                if logged_in:
                    time.sleep(3)
                    print("Login successful! Session saved.")
                else:
                    print("WARNING: Timed out. Session may still be saved if you logged in.")
                page.close()
                context.close()
                return

            # --- Check if logged in ---
            page = context.new_page()
            page.goto("https://www.facebook.com", wait_until="domcontentloaded")
            _human_delay(2.0, 3.0)

            if not _is_logged_in(page):
                print(
                    "ERROR: Not logged into Facebook.\n"
                    "Run:  python scripts/scrape.py --login"
                )
                page.close()
                context.close()
                sys.exit(1)

            print("Facebook session active.")
            page.close()

            # --- Prepare output directory for today ---
            today = date.today().isoformat()
            day_dir = OUTPUT_DIR / today
            day_dir.mkdir(parents=True, exist_ok=True)

            manifest = {
                "scrape_date": today,
                "pages": {},
            }

            # --- Scrape each page ---
            for page_name, page_url in PAGES.items():
                print(f"\n{'=' * 60}")
                print(f"Scraping {page_name}: {page_url}")
                print(f"{'=' * 60}")

                page = context.new_page()
                try:
                    page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                except Exception as exc:
                    print(f"  [error] Failed to load {page_url}: {exc}")
                    page.close()
                    continue

                _human_delay(3.0, 5.0)
                _dismiss_popups(page)

                if args.dry_run:
                    title = page.title()
                    print(f"  [dry-run] Page loaded OK. Title: {title}")
                    page.close()
                    _human_delay(PAGE_NAV_PAUSE_MIN, PAGE_NAV_PAUSE_MAX)
                    continue

                # Take scrolling screenshots
                print(f"  Taking {SCROLL_COUNT} screenshots...")
                screenshots = _scroll_and_screenshot(page, page_name, day_dir)

                manifest["pages"][page_name] = {
                    "url": page_url,
                    "screenshots": screenshots,
                    "screenshot_count": len(screenshots),
                }

                page.close()
                _human_delay(PAGE_NAV_PAUSE_MIN, PAGE_NAV_PAUSE_MAX)

        finally:
            context.close()

        # --- Save manifest ---
        if not args.dry_run:
            manifest_path = day_dir / "manifest.json"
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            print(f"\nManifest saved to {manifest_path}")

            total = sum(
                p["screenshot_count"] for p in manifest["pages"].values()
            )
            print(f"Total screenshots: {total}")
            print(f"Output dir: {day_dir}")
        else:
            print("\n[dry-run] All checks passed.")


if __name__ == "__main__":
    main()
