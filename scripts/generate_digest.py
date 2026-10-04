#!/usr/bin/env python3
"""
Generate a bilingual daily digest from parsed album records.

Reads structured records (artist, album, genre, descriptions in EN/ZH)
and generates Astro-compatible Markdown in the newspaper album format.

Usage:
    python scripts/generate_digest.py                    # Today's date
    python scripts/generate_digest.py --date 2026-05-04  # Specific date
"""

import argparse
import json
import sys
import yaml
from datetime import date
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
PARSED_DIR = PROJECT_DIR / "data" / "parsed"
DIGESTS_DIR = PROJECT_DIR / "site" / "src" / "content" / "digests"
SOURCES_FILE = PROJECT_DIR / "sources.yaml"


def load_sources() -> dict[str, dict]:
    with open(SOURCES_FILE) as f:
        config = yaml.safe_load(f)
    return {p["slug"]: p for p in config.get("facebook_pages", [])}


def generate_markdown(records: list[dict], digest_date: str, sources: dict) -> str:
    source_slugs = sorted(set(r.get("source_page", "") for r in records))
    source_names = [sources.get(s, {}).get("name", s) for s in source_slugs]

    lines = [
        "---",
        f'title: "The Daily Dig"',
        f"date: {digest_date}",
        f"sources: {json.dumps(source_names)}",
        "---",
        "",
    ]

    for r in records:
        artist = r.get("artist", "Unknown")
        album = r.get("album", "Unknown")
        genre = r.get("genre", "")
        year = r.get("year", "")
        source = sources.get(r.get("source_page", ""), {}).get("name", r.get("source_page", ""))
        image = r.get("local_image", "") or r.get("image_url", "")
        post_url = r.get("post_url", "")
        desc_en = r.get("description_en", "")
        desc_zh = r.get("description_zh", "")

        lines.append(f"## {artist} — {album}")
        if genre:
            lines.append(f"- **Genre:** {genre}")
        if year:
            lines.append(f"- **Year:** {year}")
        lines.append(f"- **Source:** {source}")
        if image:
            lines.append(f"- **Image:** {image}")
        if post_url:
            lines.append(f"- **Post URL:** {post_url}")
        if desc_en:
            lines.append(f"- **Description EN:** {desc_en}")
        if desc_zh:
            lines.append(f"- **Description ZH:** {desc_zh}")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate daily digest Markdown")
    parser.add_argument("--date", default=str(date.today()), help="Digest date")
    parser.add_argument("--input", help="Path to parsed JSON file")
    args = parser.parse_args()

    digest_date = args.date
    sources = load_sources()

    input_path = Path(args.input) if args.input else PARSED_DIR / f"{digest_date}.json"

    if not input_path.exists():
        print(f"ERROR: No parsed data at {input_path}")
        sys.exit(1)

    with open(input_path) as f:
        records = json.load(f)

    if not records:
        print("WARNING: No records found.")
        sys.exit(0)

    print(f"Generating digest for {digest_date} with {len(records)} albums...")
    markdown = generate_markdown(records, digest_date, sources)

    DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DIGESTS_DIR / f"{digest_date}.md"
    out_path.write_text(markdown, encoding="utf-8")
    print(f"Wrote digest to {out_path}")


if __name__ == "__main__":
    main()
