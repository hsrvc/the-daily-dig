# Parsing Apify Posts → Album Records

This is the LLM-judgment step in the pipeline. The agent reads `data/raw/{date}-apify-new.json` (a list of Facebook post objects from Apify) and writes `data/parsed/{date}.json` (a list of structured album records).

## Output schema

Each record in the parsed JSON array:

```json
{
  "artist": "Ryuichi Sakamoto",
  "album": "Async",
  "genre": "Ambient / Electronic / Experimental",
  "year": "2017",
  "description_zh": "坂本龍一罹患咽喉癌之後的首張個人專輯...",
  "description_en": "Ryuichi Sakamoto's first solo album after being diagnosed with throat cancer...",
  "source_page": "THTRECORDs",
  "post_url": "https://www.facebook.com/THTRECORDs/posts/pfbid02Q...",
  "image_url": "https://scontent-bos5-1.xx.fbcdn.net/v/t51.82787-15/689052213_..."
}
```

Field-by-field:

- **`artist`** / **`album`** — required. Extract from the post text. Posts often format these like `◉ Artist Name - Album Name` near the bottom, or in the opening sentence.
  - **Format**: `Latin Name (CJK)` if the artist has both forms (e.g. `Yuhan Su (蘇郁涵)`, `Huang Shan (黃閃)`). Latin-only or CJK-only is also fine when only one is provided.
  - **Romanization**: trust the artist's actual credit (album cover, label, social media) — don't invent hyphenations. The OCR text Apify provides on photos (`media[].ocrText`) often shows the canonical Latin spelling. For the example above, the cover read `YUHAN SU` (one word), not `Yu-Han Su`.
  - **Em-dash separator only**: the parser produces `## {artist} — {album}` headlines and the site splits on em-dash (—) or en-dash (–). Do not put em-dashes inside artist or album names — they'd be misparsed.
- **`genre`** — best-effort. Multiple genres separated by ` / ` (e.g. `Spiritual Jazz / Ambient`). Extract from the post's descriptive language; if not stated, infer conservatively from artist context. Empty string is acceptable if truly unclear.
- **`year`** — release/reissue year as a 4-digit string. Four-tier fallback:
  1. **Post text** — use any 4-digit year explicitly stated in the post (e.g. "1976年發行", "released 2024").
  2. **General knowledge** — fill from training data for well-known albums (e.g. Thriller → "1982").
  3. **WebSearch** — for obscure or recent albums where you're not confident, use the built-in WebSearch tool with a query like `"Turbojazz Memorabilia album release year"`. This is available in all execution environments (local and GitHub Actions).
  4. **gemini-search skill** — last resort if WebSearch is unavailable (local-only; the `gemini` CLI is not installed on GitHub Actions).
  Never use the post's own posting date. Leave empty only when all four tiers fail.
- **`description_zh`** — Traditional Chinese description, concise and evocative. Length should match how much genuine album content the post contains — typically 2-3 sentences, up to 4-5 for posts with rich critical or historical context. Condense freely: drop price lines, store filler, hashtags. If the post is in English/other, translate.
- **`description_en`** — English description matching the same depth as `description_zh`, concise and evocative. Translate from `description_zh` if the post is Chinese-only. Don't pad — but don't truncate rich source material either.
- **`spotify_artist`** — optional. Set this when the credited `artist` is a band/project that is unlikely to be found on Spotify by that name, but a key individual behind the project *is* findable. The Spotify script uses this as a fallback search when the primary artist search fails.
  - **When to set**: the post explicitly names a person as the driving force behind the band (e.g. "松下誠主導", "led by X", "X's project"), AND that person is more famous than the group name.
  - **Format**: Latin name preferred (e.g. `"Makoto Matsushita"`), or CJK if no Latin form is available.
  - **Example**: `The Milky Way — Summer-Time Love Song` → post says 松下誠 is the key man → set `"spotify_artist": "Makoto Matsushita"`.
  - **Leave absent** when the credited artist is already a well-known name on Spotify, or when no individual is clearly identified as the driving force.
- **`source_page`** — copy from the post's `pageName` field (e.g. `Tokyobuybuydiary`, `THTRECORDs`).
- **`post_url`** — copy from the post's `url` field.
- **`image_url`** — the highest-quality image. Look in `media[]` for entries with `__typename: "Photo"`. The URI field name varies by source — check `image.uri` first, then `photo_image.uri`. Use whichever is present. Skip `mediaset_token`-only entries (those are video/album wrappers with no direct image URI). **For multi-album posts**, see the "Multi-album posts" section below — do not naively reuse the first photo for every album.

Do **not** set `local_image` — `fetch_covers.py` adds that downstream.

## Skip rules — non-album posts

Many posts in the raw file are not album recommendations. Skip:

- Pure greetings / thanks ("Luv U.", "感謝大家")
- Store hours, location updates, ordering reminders
- Photos of the shop interior with no album content
- Event announcements (in-store DJ nights, exhibitions) unless tied to a specific album
- Stock-update posts ("還有兩張", "已售完") — unless the post also describes a new album

When in doubt, skip. A short digest of confirmed albums beats a noisy one.

## Per-shop posting styles

`sources.yaml` tags each shop with a `posting_style`. Use this to know how aggressively to look for multi-album content:

| Style | Shops | Behavior |
|---|---|---|
| `single` | Tokyobuybuydiary | One album per post, rich descriptions. Rarely multi-album. |
| `regular_batch` | THTRECORDs, beethobearrecords | Often 2 albums in one post. |
| `occasional_batch` | uourecords | Round-up posts with 3-4 mixed items (albums + zines + cassettes + merch). Common opener: "few works from X artists in stock". Read the full post — non-album items must be skipped, but album items must all be captured. |

The default assumption is `single`. When parsing `regular_batch` or `occasional_batch` posts, consciously look for additional album sections beyond the first one before moving to the next post.

## Multi-album posts

A single Facebook post sometimes covers **2-4 albums in one post**. **Read the entire post text before deciding what's there.** Don't stop after finding the first album.

Common signals of a multi-album post:
- Bullet markers like `·`, `◉`, or `-` introducing distinct sections
- Artist/album titles formatted as `蘇郁涵 / OVER the MOONs` or `Huang Shan / A cappella` between sections
- Multiple `@username` tags at the bottom (one per artist)

When the post mentions multiple distinct artist+album pairs:

- Produce one record per album. Skip any items that aren't albums (zines, books, merchandise) — those are filler even if they appear in the same list.
- Each record gets the **same `post_url`** and `source_page` (it's one Facebook post).
- The dedup script keys on `post_url`, so the post won't be re-processed regardless of how many albums it yielded.

### Image matching (vision preferred for multi-album posts)

For any post yielding **2 or more records**, the strongly preferred approach is to visually inspect each candidate cover photo via the `Read` tool. Vision is the ground truth — heuristics like OCR matching or positional inference work most of the time but fail silently in edge cases (this is how today's bug 2026-05-13 shipped).

**Preferred procedure (vision):**

1. **Find the cached photo for each `media[]` entry.** `scrape_apify.py` caches every FB CDN photo to `data/images/{date}/`. The filename is derived from the photo URL via this exact logic (mirrors `scrape_apify.py:cache_images`):

   ```python
   import re, hashlib
   def cache_filename(url: str) -> str:
       m = re.search(r"/(\d+_\d+)_\d+_[a-z]\.", url)
       key = m.group(1) if m else hashlib.md5(url.encode()).hexdigest()[:16]
       return f"{key}.jpg"
   ```

   The regex matches the standard FB CDN format and yields a `photo_id` like `696478286_1833243471393424` (the first two underscore-separated numeric segments). When the URL format is unusual (rare — 0/60 today), the fallback is `md5(url)[:16]`. Either way you get a single filename.

2. **`Read` each cached file.** Multimodal vision shows you the cover art directly — titles, artist names, label logos.

3. **Match each parsed album to the photo that visibly shows its cover.** Write that media entry's `image.uri` (the full FB CDN URL) into the record's `image_url` field. Never write the local cache path — `fetch_covers.py` re-derives the cache lookup downstream from the URL.

**Graceful fallback when vision can't be used:**

If any of these conditions hold, fall back to **positional matching** (Nth album in the post's numbered/bulleted list → Nth `__typename: "Photo"` entry in `media[]`) and note it in self-audit:

- The cached file does not exist (scrape was partial, files were cleaned up, etc.)
- The photo URL is not on FB CDN (very rare; `cache_images` only caches fbcdn URLs)
- `Read` fails on the cached file (corrupted, zero bytes)

Positional is correct most of the time — posters typically upload covers in the same order they list the albums. Vision is just stronger when it works.

**Edge cases to handle (regardless of vision/positional):**

- **Same album, multiple photos** (front + back + insert shots of one record): pick the clearest front-cover photo. The others are unused.
- **More photos than albums** (5 photos for 3 albums — extras are price tags, store interior, signed posters): assign only the photos that show actual covers. Skip the rest. Vision is especially useful here — positional alone would mis-assign.
- **Fewer photos than albums** (1 collage photo showing 3 covers, or a single store-shelf shot): all records share the same `image_url`. Acceptable when truly no separate photos exist. Note in self-audit.

**Why vision matters:** Today's bug (2026-05-13) was a Tokyobuybuydiary batch post with 4 Sakanaction albums and 4 distinct cover photos. The parser used Facebook's OCR text (e.g. `"May be art of text that says 'VICL-65644 VICL'"`) which was too noisy, then defaulted to assigning the first photo to all 4 records. Loading each image and looking at it would have caught this — the 4 covers are visually distinct (`sakanaction` self-titled = black/blue wave halves; `DocumentaLy` = body-part-text figure; `Adapt` = eye on grid with VICL catalog stamp; `834.194` = blue swirls with title text).

**Anti-patterns (do NOT do this):**

- Assigning `media[1]`'s URL to every album in a multi-album post because OCR is unhelpful or because the page is tagged `single` style. The `posting_style` tag is a prior, not a hard rule.
- Using FB's `media[].ocrText` as the primary signal for matching covers to titles. It's accessibility metadata ("May be an illustration of xray and text"), not document OCR.

## Real-world examples

### Example 1: single album, Chinese-only post

Raw post excerpt:
```
text: "如果被去年 The Cosmic Tones Research Trio 的同名專輯徹底圈粉的朋友，這次由團員
       Roman Norfleet 與 Andre Raiah（Be Present Art Group）組成的雙人團 The MerKaBa
       Brotherhood，其新作同樣不要錯過。... 黑膠由紐約 Mississippi Records 發行。
       ◉ The MerKaBa Brotherhood - The MerKaBa Brotherhood
       ☉ 𝟴𝟱𝟬$
       ☉ 𝗟𝗣
       ☉ 預計 𝟱 月下旬到貨"
pageName: "Tokyobuybuydiary"
url: "https://www.facebook.com/Tokyobuybuydiary/posts/pfbid0TGUgo..."
media: [{ image: { uri: "https://...690296566_..." } }, ...]
```

Parsed record:
```json
{
  "artist": "The MerKaBa Brotherhood",
  "album": "The MerKaBa Brotherhood",
  "genre": "Spiritual Jazz / Ambient",
  "year": "2026",
  "description_zh": "如果被去年 The Cosmic Tones Research Trio 的同名專輯徹底圈粉的朋友，這次由團員 Roman Norfleet 與 Andre Raiah（Be Present Art Group）組成的雙人團 The MerKaBa Brotherhood，其新作同樣不要錯過。這張帶有靈性實驗的同名專輯，以薩克斯風、鍵盤與打擊樂編制構成，聲響漂浮在 Ambient、Spiritual Jazz 之間。黑膠由紐約 Mississippi Records 發行。",
  "description_en": "If you were captivated by The Cosmic Tones Research Trio's self-titled album last year, don't miss this new work from duo members Roman Norfleet and Andre Raiah (Be Present Art Group). This spiritually experimental album features saxophone, keyboards, and percussion — sound floating between Ambient and Spiritual Jazz. Released on vinyl by NYC's Mississippi Records.",
  "source_page": "Tokyobuybuydiary",
  "post_url": "https://www.facebook.com/Tokyobuybuydiary/posts/pfbid0TGUgo...",
  "image_url": "https://...690296566_..."
}
```

Note: the prices, format, and "expected ship date" lines from the original are dropped — they're operational filler, not part of the description.

### Example 2: multi-album post with vision-based image matching

A single Tokyobuybuydiary post (2026-05-13) lists 4 Sakanaction reissues:

```
❶ サカナクション - sakanaction
❷ サカナクション - DocumentaLy
❸ サカナクション - アダプト
❹ サカナクション - 834.194
```

The `media[]` array has 4 `Photo` entries. FB's OCR is useless on every one of them (`"No photo description available."`, `"May be art of text that says 'VICL-65644 VICL'"`, etc.).

**Procedure:**

1. Extract photo IDs from each `media[].image.uri`:
   - `media[1]` URL `.../696478286_1833243471393424_8903...` → photo_id `696478286_1833243471393424`
   - `media[2]` URL `.../696758467_1833243478060090_3185...` → photo_id `696758467_1833243478060090`
   - `media[3]` URL `.../695038417_1833243538060084_9067...` → photo_id `695038417_1833243538060084`
   - `media[4]` URL `.../696782653_1833243498060088_1704...` → photo_id `696782653_1833243498060088`

2. Read each cached image:
   - `Read data/images/2026-05-13/696478286_1833243471393424.jpg` → black/blue wave halves split diagonally, "sakanaction" text top & bottom → self-titled
   - `Read data/images/2026-05-13/696758467_1833243478060090.jpg` → figure made of body-part text (hair/shoulder/elbow), "DocumentaLy" title in green → DocumentaLy
   - `Read data/images/2026-05-13/695038417_1833243538060084.jpg` → distorted eye on grid with X-Acto knife, "VICL-65644" stamped → Adapt (VICL-65644 is Adapt's catalog number)
   - `Read data/images/2026-05-13/696782653_1833243498060088.jpg` → blue ocean swirls, "sakanaction 834.194" title visible → 834.194

3. Assign each record its matched `image_url` (the full FB CDN URL from `media[].image.uri`, not the local cache path).

**Correct output:** 4 records with 4 distinct `image_url` values, each pointing to the FB CDN URL of the photo that actually shows that album's cover.

**Incorrect output (the original bug):** assigning `media[1].image.uri` to all 4 records because OCR was unhelpful and the parser shortcut. Produces 4 identical-looking entries on the site.

### Example 3: skip case — "Luv U" filler

```
text: "Luv U.\n\nTHT每週一二公休，請多加利用官網。"
```

Skip. No album.

### Example 4: skip case — stock update without new album

```
text: "上週到貨秒殺，Yusef Lateef 這張RSD稀有限定版彩膠，再釋出少量！"
```

This is a re-stock notice for a previously listed album. Whether to include depends on context — if the album was *already* digested in a prior run, dedup would have filtered the post. If it's reaching parsing, the artist+album is "new" to the digest, so include it (write a description that frames it as a re-stock if appropriate).

## Quality checks before writing the JSON

- Every record has non-empty `artist`, `album`, `source_page`, `post_url`
- `image_url` is present when at all possible (posts with no photo are rare; if so, leave empty string)
- Descriptions are concise and evocative — length matches source richness, typically 2-3 sentences, up to 4-5 for rich posts; don't pad, don't truncate
- No raw price lines, hashtags, or shop ordering instructions in descriptions
- JSON is valid (use `python3 -m json.tool` to verify)

## Self-audit pass (REQUIRED before writing data/seen/)

After producing the parsed JSON, re-read each source post and check three things. Fix in place if anything fails.

### 1. Multi-album coverage

For each unique `post_url` in your parsed JSON, count records produced. Then re-read the source post and count distinct album sections. They should match.

Heuristics for spotting album sections in a post:
- Bullet markers at the start of a line: `·`, `◉`, `-`, `▶`, `※`
- Artist/album lines with a separator: `Artist Name / Album Title`, `Artist Name - Album Title`
- Multiple `@username` tags at the bottom (one per artist tagged) — strong signal of a batch post

Example: if a uourecords post starts with "few works from Taiwanese artists in stock" and has 4 bullet markers, expect 3-4 records (one item is often a zine or cassette to skip; never zero).

### 2. Year discipline

Three-tier fallback — post text → general knowledge → Gemini search. Never use the post's own posting date.

| ✅ Pass | ❌ Fail |
|---|---|
| `year: "1976"` and post says `"1976 年神秘團體 REALITY"` | `year: "2026"` because post is dated 2026, but text doesn't mention 2026 |
| `year: "2025"` and post says `"2025年10月由... 發行"` | `year: "2024"` because the artist released something in 2024, but post doesn't say so |
| `year: "1982"` for Thriller — well-known, no search needed | — |
| `year: "2025"` for an obscure album — looked up via WebSearch or gemini-search | — |
| `year: ""` only when post text, knowledge, and search all fail | — |

### 3. Artist romanization vs OCR

For each record where the post has photos with `ocrText`, scan the OCR for Latin-script names. If the OCR text contains a name format that conflicts with your `artist` field, prefer the OCR.

| OCR shows | You wrote | Action |
|---|---|---|
| `YUHAN SU` | `Su Yu-Han (蘇郁涵)` | **Fix** to `Yuhan Su (蘇郁涵)` — OCR matches the artist's actual cover credit |
| `MOBB DEEP` | `Mobb Deep` | OK |
| (no Latin name in OCR) | `Su Yuhan (蘇郁涵)` | Acceptable — no canonical reference to compare |

This catches invented hyphenations and wrong word orders, the most common artist-name failure mode.

### 4. Image distinctness in multi-album posts

For each `post_url` that produced **2 or more records**, verify the cover assignments. The two checks below are progressive — Step 1 is a cheap structural check, Step 2 is a content check that requires vision.

**Step 1 — count check (always do this).** Count the distinct `image_url` values across the records from this post, and count `__typename: "Photo"` entries in the source `media[]`:

| Records (R) sharing how many image_urls | Distinct photos in media[] | Action |
|---|---|---|
| R distinct image_urls | ≥ R photos | Probably OK — proceed to Step 2 if you didn't already use vision |
| R records share 1 image_url | 1 photo | OK — post had only one cover image (e.g. true collage) |
| R records share 1 image_url | ≥ 2 photos | **Investigate.** This is today's-bug shape. Use vision to confirm whether the multiple photos really show different albums (very likely) or are unrelated content (rare — e.g. price tags, signed posters). Fix the assignment if needed. |
| Mixed (e.g. 4 records using 2 image_urls) | ≥ 4 photos | **Investigate** with vision — duplicates may be intentional (one photo legitimately shows two covers side-by-side) or accidental. |

**Step 2 — content check (use vision).** If you didn't already use the vision procedure during initial parsing, or if Step 1 flagged something, open the assigned cache file (`data/images/{date}/{cache_filename}.jpg`) for each unique `image_url` and confirm the visible cover art matches the album. If a photo shows a different album, reassign.

**When vision is unavailable** (cache files missing, non-fbcdn URLs), Step 1 is your only check — flag the post in your output but don't block on it. Positional matching is a reasonable fallback.

This catches today's-bug shape (one photo slapped on every album) and reversed/scrambled assignments.

## When to call it done

After writing `data/parsed/{date}.json`, also write `data/seen/{date}.json` — a flat JSON array of every post `url` you evaluated this run, both kept and skipped:

```json
[
  "https://www.facebook.com/Tokyobuybuydiary/posts/pfbid0...",
  "https://www.facebook.com/THTRECORDs/posts/pfbid0...",
  ...
]
```

The dedup script reads this on subsequent runs to filter out noise posts you already judged as non-album, so they never get re-evaluated. Forgetting this means tomorrow's run wastes tokens re-judging the same filler.

Then move on to step 5 (fetch covers) in the main workflow.
