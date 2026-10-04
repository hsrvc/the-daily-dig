#!/usr/bin/env python3
"""Debug FB page structure: take screenshot + dump accessible text."""
import json
import os
import time
from pathlib import Path

PROFILE_DIR = os.path.expanduser("~/.music-digger-profile")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1280, "height": 900},
        locale="en-US",
        ignore_default_args=["--enable-automation"],
    )

    page = context.new_page()
    page.goto("https://www.facebook.com/uourecords", wait_until="domcontentloaded")
    time.sleep(5)

    # Scroll a bit
    for _ in range(3):
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        time.sleep(2)

    # Scroll back to top for screenshot
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(1)

    # Screenshot
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(DATA_DIR / "debug_uou_screenshot.png"), full_page=False)
    print(f"Screenshot saved to {DATA_DIR / 'debug_uou_screenshot.png'}")

    # Dump page text content (much more useful than raw HTML)
    text = page.evaluate("""() => {
        // Get all text from the page feed area
        const feed = document.querySelector('[role="main"]') || document.body;
        return feed.innerText;
    }""")

    (DATA_DIR / "debug_uou_text.txt").write_text(text[:20000], encoding="utf-8")
    print(f"Text dump saved to {DATA_DIR / 'debug_uou_text.txt'}")

    # Also try getting the accessibility tree which is more structured
    snapshot = page.accessibility.snapshot()
    (DATA_DIR / "debug_uou_a11y.json").write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False)[:30000], encoding="utf-8"
    )
    print(f"Accessibility tree saved to {DATA_DIR / 'debug_uou_a11y.json'}")

    page.close()
    context.close()
