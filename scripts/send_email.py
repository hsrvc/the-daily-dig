#!/usr/bin/env python3
"""
Send today's Daily Dig as an HTML newsletter email via Resend.

Reads data/parsed/{date}.json and generates a bilingual HTML digest with
cover-art cards (image left, text right), then POSTs to the Resend API.

Usage:
    python3 scripts/send_email.py                    # today (TPE timezone)
    python3 scripts/send_email.py --date 2026-05-05
    python3 scripts/send_email.py --dry-run          # print HTML, skip send
    python3 scripts/send_email.py --preview          # write /tmp/digest_preview.html

Env vars required (soft-fail if missing):
    RESEND_API_KEY
    NOTIFY_EMAIL
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

PROJECT_DIR   = Path(__file__).resolve().parent.parent
PARSED_DIR    = PROJECT_DIR / "data" / "parsed"
NOTIFIED_DIR  = PROJECT_DIR / "data" / "notified"
SOURCES_FILE = PROJECT_DIR / "sources.yaml"
SITE_BASE   = "https://dailydig.erdscribe.com"
RESEND_URL  = "https://api.resend.com/emails"

TPE = ZoneInfo("Asia/Taipei")


def load_sources() -> dict[str, str]:
    with open(SOURCES_FILE) as f:
        cfg = yaml.safe_load(f)
    return {p["slug"]: p["name"] for p in cfg.get("facebook_pages", [])}


def cover_url(record: dict) -> str:
    local = record.get("local_image", "")
    if local:
        return f"{SITE_BASE}{urllib.parse.quote(local, safe='/')}"
    return record.get("image_url", "")


def fmt_date_long(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return d.strftime("%A, %B %-d, %Y")


def album_card_html(record: dict, source_names: dict[str, str]) -> str:
    artist    = record.get("artist", "")
    album     = record.get("album", "")
    genre     = record.get("genre", "")
    year      = record.get("year", "")
    desc_zh   = record.get("description_zh", "")
    post_url  = record.get("post_url", "#")
    source    = source_names.get(record.get("source_page", ""), record.get("source_page", ""))
    img_url   = cover_url(record)

    meta_parts = []
    if genre:
        meta_parts.append(genre)
    if year:
        meta_parts.append(year)
    if source:
        meta_parts.append(source)
    meta_line = " &nbsp;·&nbsp; ".join(meta_parts)

    if img_url:
        img_tag = (
            f'<a href="{post_url}" style="text-decoration:none;">'
            f'<img src="{img_url}" width="120" alt="{artist} — {album}" '
            f'style="display:block;width:120px;height:auto;border:0;'
            f'background-color:#e8e5df;" border="0">'
            f'</a>'
        )
    else:
        # Gray placeholder when no image available
        img_tag = (
            f'<div style="width:120px;height:120px;background-color:#dddbd5;'
            f'display:flex;align-items:center;justify-content:center;">'
            f'<span style="font-family:Arial,sans-serif;font-size:10px;'
            f'color:#999999;text-align:center;">no cover</span></div>'
        )

    return f"""
    <tr>
      <td bgcolor="#ffffff" style="background-color:#ffffff;padding:0;border-bottom:1px solid #eae8e2;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <!-- Cover image -->
            <td class="img-cell" width="120" valign="top"
                style="width:120px;padding:20px 0 20px 24px;vertical-align:top;">
              {img_tag}
            </td>
            <!-- Spacer -->
            <td width="16" style="width:16px;">&nbsp;</td>
            <!-- Album details -->
            <td class="text-cell" valign="top"
                style="padding:20px 24px 20px 0;vertical-align:top;">
              <a href="{post_url}" style="text-decoration:none;">
                <p style="margin:0 0 2px 0;font-family:Georgia,'Times New Roman',serif;
                           font-size:16px;font-weight:bold;color:#1a1a1a;line-height:1.3;">
                  {artist}
                </p>
                <p style="margin:0 0 10px 0;font-family:Georgia,'Times New Roman',serif;
                           font-size:14px;font-style:italic;color:#555555;line-height:1.3;">
                  {album}
                </p>
              </a>
              <p style="margin:0 0 12px 0;font-family:Arial,Helvetica,sans-serif;
                         font-size:10px;color:#999999;text-transform:uppercase;
                         letter-spacing:1.5px;line-height:1.4;">
                {meta_line}
              </p>
              {'<p style="margin:0;font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#2d2d2d;line-height:1.65;">' + desc_zh + '</p>' if desc_zh else ''}
            </td>
          </tr>
        </table>
      </td>
    </tr>"""


def build_html(records: list[dict], date_str: str, source_names: dict[str, str]) -> str:
    date_long = fmt_date_long(date_str)
    count = len(records)
    count_label = f"{count} pick{'s' if count != 1 else ''}"
    site_url = f"{SITE_BASE}/"

    album_cards = "\n".join(album_card_html(r, source_names) for r in records)

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <title>The Daily Dig — {date_long}</title>
  <style type="text/css">
    /* Prevent iOS from auto-linking phone numbers / dates */
    a[x-apple-data-detectors] {{
      color: inherit !important;
      text-decoration: none !important;
    }}
    /* Mobile: stack image above text */
    @media only screen and (max-width: 480px) {{
      .outer-td {{ padding: 12px 8px !important; }}
      .container {{ width: 100% !important; }}
      .img-cell {{
        display: block !important;
        width: 100% !important;
        padding: 16px 16px 0 16px !important;
      }}
      .img-cell img {{
        width: 100% !important;
        height: auto !important;
        max-height: 220px;
        object-fit: cover;
      }}
      .text-cell {{
        display: block !important;
        width: 100% !important;
        padding: 12px 16px 16px 16px !important;
      }}
    }}
  </style>
</head>
<body bgcolor="#f0ede6" style="margin:0;padding:0;background-color:#f0ede6;
      -webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">

<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f0ede6"
       style="background-color:#f0ede6;">
  <tr>
    <td class="outer-td" align="center" style="padding:28px 16px;">

      <!-- Main container: 600px max -->
      <table class="container" width="600" cellpadding="0" cellspacing="0" border="0"
             style="max-width:600px;width:100%;">

        <!-- ── MASTHEAD ── -->
        <tr>
          <td bgcolor="#1a1a1a" style="background-color:#1a1a1a;padding:28px 28px 22px 28px;">
            <p style="margin:0 0 6px 0;font-family:Arial,Helvetica,sans-serif;font-size:10px;
                       color:#888888;letter-spacing:3px;text-transform:uppercase;">
              Daily Music Discovery
            </p>
            <h1 style="margin:0 0 6px 0;font-family:Georgia,'Times New Roman',serif;
                        font-size:38px;font-weight:bold;color:#ffffff;letter-spacing:-1px;
                        line-height:1.1;">
              The Daily Dig
            </h1>
            <p style="margin:0;font-family:Georgia,'Times New Roman',serif;font-size:13px;
                       color:#aaaaaa;line-height:1.4;">
              {date_long} &nbsp;·&nbsp; {count_label}
            </p>
          </td>
        </tr>

        <!-- Red rule -->
        <tr>
          <td bgcolor="#8b1a1a" height="3"
              style="background-color:#8b1a1a;font-size:3px;line-height:3px;">&nbsp;</td>
        </tr>

        <!-- ── ALBUM CARDS ── -->
        {album_cards}

        <!-- ── FOOTER ── -->
        <tr>
          <td bgcolor="#1a1a1a" style="background-color:#1a1a1a;padding:18px 28px;">
            <p style="margin:0;font-family:Arial,Helvetica,sans-serif;font-size:11px;
                       color:#888888;text-align:center;line-height:1.6;">
              <a href="{site_url}" style="color:#cccccc;text-decoration:none;">
                dailydig.erdscribe.com
              </a>
              &nbsp;·&nbsp; The Daily Dig
            </p>
          </td>
        </tr>

      </table><!-- /container -->
    </td>
  </tr>
</table><!-- /outer -->

</body>
</html>"""


def send(html: str, date_str: str, count: int) -> int:
    api_key = os.environ.get("RESEND_API_KEY", "")
    to_addr = os.environ.get("NOTIFY_EMAIL", "")
    if not api_key or not to_addr:
        print("  send_email: RESEND_API_KEY or NOTIFY_EMAIL not set — skipping send.")
        return None

    date_long = fmt_date_long(date_str)
    payload = json.dumps({
        "from": "The Daily Dig <news@dig.erdscribe.com>",
        "to": [to_addr],
        "subject": f"The Daily Dig — {date_long} ({count} picks)",
        "html": html,
    }).encode()

    req = urllib.request.Request(
        RESEND_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "daily-dig/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            code = resp.getcode()
            print(f"  send_email: Resend HTTP {code} — email queued.")
            return code
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"  send_email: Resend HTTP {e.code} — {body}")
        return e.code


def _load_env_file() -> None:
    env_file = Path.home() / ".daily-dig.env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            k = k.strip()
            if k and k not in os.environ:
                os.environ[k] = v.strip()


def main() -> None:
    _load_env_file()

    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Digest date (YYYY-MM-DD). Defaults to today TPE.")
    parser.add_argument("--dry-run", action="store_true", help="Print HTML to stdout, skip send.")
    parser.add_argument("--preview", action="store_true", help="Write HTML to /tmp/digest_preview.html, skip send.")
    parser.add_argument("--force", action="store_true", help="Send even if already-sent marker exists.")
    args = parser.parse_args()

    date_str = args.date or datetime.now(tz=TPE).strftime("%Y-%m-%d")

    marker = NOTIFIED_DIR / f"email-{date_str}"
    if marker.exists() and not args.force and not args.dry_run and not args.preview:
        print(f"  send_email: already sent for {date_str} — use --force to resend.")
        sys.exit(0)

    parsed_path = PARSED_DIR / f"{date_str}.json"

    if not parsed_path.exists():
        print(f"  send_email: no parsed data at {parsed_path} — skipping.")
        sys.exit(0)

    records = json.loads(parsed_path.read_text(encoding="utf-8"))
    if not records:
        print("  send_email: 0 albums today — skipping email.")
        sys.exit(0)

    source_names = load_sources()
    html = build_html(records, date_str, source_names)

    if args.dry_run:
        print(html)
        return

    if args.preview:
        out = Path("/tmp/digest_preview.html")
        out.write_text(html, encoding="utf-8")
        print(f"  send_email: preview written to {out}")
        return

    code = send(html, date_str, len(records))
    if code is None:
        return  # env vars not set locally — don't write marker, let CI send it
    if code not in (0, 200):
        print(f"  send_email: non-200 response ({code}) — failing.")
        sys.exit(1)

    NOTIFIED_DIR.mkdir(parents=True, exist_ok=True)
    (NOTIFIED_DIR / f"email-{date_str}").touch()
    print(f"  send_email: marker written → data/notified/email-{date_str}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"  send_email: unhandled error — {e}")
        sys.exit(1)
