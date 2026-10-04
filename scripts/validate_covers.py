#!/usr/bin/env python3
"""
Validate that every local_image path in data/parsed/*.json
exists as a real file in site/public/covers/.

Exits 1 if any references are broken.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
PARSED_DIR = PROJECT_DIR / "data" / "parsed"
COVERS_DIR = PROJECT_DIR / "site" / "public" / "covers"

missing = []

for json_path in sorted(PARSED_DIR.glob("*.json")):
    records = json.loads(json_path.read_text())
    for r in records:
        img = r.get("local_image", "")
        if not img:
            continue
        file_path = COVERS_DIR / img.removeprefix("/covers/")
        if not file_path.exists():
            missing.append((json_path.name, img, r.get("artist", ""), r.get("album", "")))

if missing:
    print(f"❌ {len(missing)} broken cover reference(s):")
    for date, img, artist, album in missing:
        print(f"  [{date}] {artist} — {album}")
        print(f"    {img}")
    sys.exit(1)

total = sum(
    1 for jf in PARSED_DIR.glob("*.json")
    for r in json.loads(jf.read_text())
    if r.get("local_image")
)
print(f"✓ All {total} cover references are valid.")
