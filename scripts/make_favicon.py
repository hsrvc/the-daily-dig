#!/usr/bin/env python3
"""
Generate favicon set from a source photo.

Crops a square from the source preserving a vertical color split (1/3 left :
2/3 right by default), applies rounded corners, and writes:

  site/public/favicon-32.png       — browser tab (rounded)
  site/public/favicon-512.png      — PWA / hi-dpi (rounded)
  site/public/apple-touch-icon.png — iOS home screen (square, iOS rounds it)

Usage:
  python3 scripts/make_favicon.py SOURCE_IMAGE [--seam-x PX] [--ratio 0.333]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "site" / "public"


def crop_square(src: Image.Image, seam_x: int, ratio: float) -> Image.Image:
    """Crop a square from src so the vertical seam lands at `ratio` from the left."""
    w, h = src.size
    side = h
    seam_in_crop = int(side * ratio)
    x0 = max(0, min(w - side, seam_x - seam_in_crop))
    return src.crop((x0, 0, x0 + side, side))


def round_corners(img: Image.Image, radius_pct: float = 0.22) -> Image.Image:
    """Return a copy of img with rounded corners (transparent outside)."""
    img = img.convert("RGBA")
    w, h = img.size
    radius = int(min(w, h) * radius_pct)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w, h), radius=radius, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("source", type=Path)
    p.add_argument("--seam-x", type=int, default=None,
                   help="Column (px) where cyan→green transition sits in source. "
                        "Default: 35%% of source width.")
    p.add_argument("--ratio", type=float, default=1 / 3,
                   help="Where the seam lands inside the square crop (0..1).")
    args = p.parse_args()

    src = Image.open(args.source).convert("RGB")
    seam = args.seam_x if args.seam_x is not None else int(src.width * 0.35)
    square = crop_square(src, seam, args.ratio)

    # Master at 512.
    master = square.resize((512, 512), Image.LANCZOS)

    # Rounded variants.
    PUBLIC.mkdir(parents=True, exist_ok=True)
    round_corners(master).save(PUBLIC / "favicon-512.png", optimize=True)
    round_corners(master.resize((32, 32), Image.LANCZOS)).save(
        PUBLIC / "favicon-32.png", optimize=True
    )

    # Apple touch icon: square, no pre-rounding (iOS applies its own mask).
    master.resize((180, 180), Image.LANCZOS).save(
        PUBLIC / "apple-touch-icon.png", optimize=True
    )

    print(f"crop seam at x={seam}, ratio={args.ratio:.3f}")
    print("wrote:")
    for name in ("favicon-32.png", "favicon-512.png", "apple-touch-icon.png"):
        print(f"  site/public/{name}")


if __name__ == "__main__":
    main()
