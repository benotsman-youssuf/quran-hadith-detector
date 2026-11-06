# Quran & Hadith Detector Server

One HTTP API over two offline libraries:

- [`quran-detector`](https://pypi.org/project/quran-detector/) – rewrite of QDetect (El-Beltagy & Rafea, 2021)
- `hadith_engine` – vendored MIT detector (from [`RayenFekih/hadith-detector`](https://github.com/RayenFekih/hadith-detector))
  matching the six canonical hadith books

Everything runs locally; no API keys and no network calls at runtime.

## Run

The hadith index is generated from the pinned dataset and is not committed. Build it once:

```bash
pip install -r requirements.txt
pip install -r requirements-build.txt
python -m tools.prepare_hadith
python -m tools.build_index --corpus-dir data/prepared --output-dir hadith_engine/_data
uvicorn app:app --host 0.0.0.0 --port 8000     # UI: http://localhost:8000/   API docs: /docs
python -m pytest -q
```

The corpus build downloads about 130 MB of CSV, verifies SHA-256, and the index build takes roughly
1–2 minutes and produces a ~130 MB runtime index. Docker does both steps automatically:

```bash
docker build -t qh-detector .
docker run -p 8000:8000 qh-detector
```

## Deploy on Render

`render.yaml` defines a Docker web service with health check `/health`. The two in-memory
engines use roughly 700 MB once loaded, so use a 2 GB plan (`1c-2g`); Render's 512 MB free
plan will be OOM-killed during startup. The container binds to `$PORT` (Render sets it to
`10000`), and startup takes about 20–25 seconds while the hadith index is read.

Create a Blueprint from this repository in the Render dashboard, or create the service with
the Render CLI:

```bash
render services create --name quran-hadith-detector --type web_service \
  --repo https://github.com/OWNER/quran-hadith-detector --branch main \
  --runtime docker --plan 1c-2g --region frankfurt \
  --health-check-path /health --auto-deploy --confirm
```

## Data

The corpus is [`mhashim6/Open-Hadith-Data`](https://github.com/mhashim6/Open-Hadith-Data) at commit
`1515f6c`, using the diacritised *mushakkala* files. Only the narration column is kept; the *Tafseel*
commentary is discarded. The dataset is licensed under ODbL/DbCL; see `THIRD_PARTY_NOTICES.md`.

| Collection | Records |
|---|---:|
| Sahih al-Bukhari | 7,008 |
| Sahih Muslim | 5,362 |
| Sunan Abi Dawud | 4,590 |
| Jami` at-Tirmidhi | 3,891 |
| Sunan an-Nasa'i | 5,662 |
| Sunan Ibn Majah | 4,332 |
| **Total** | **30,845** |

Hadith numbers follow this edition and are not identical to sunnah.com numbering, so the API returns
`url: null` and the UI does not link to sunnah.com. No grades are included.

## Web UI (`/`)

Paste Arabic text (detection runs on paste, or press **Detect** / Ctrl+Enter).

- Your text stays exactly as pasted; Quran verses are highlighted green and hadith blue, with a
  legend and a labelled findings list so colour is not the only cue.
- Click any highlight (or any reference in the list) to open a side drawer with everything about that
  passage: the matched text, the source wording, match statistics, the Quran surah/ayah or every hadith
  reference (collection, hadith number, source edition, and the matching part in the source wording).
  The drawer also moves between detections and can switch that single passage to the source wording.
- The **Source wording** switch in the findings header swaps every highlighted passage for the source
  wording: Quran → the verse text with full diacritics, hadith → the matching part of the narration as
  stored in the source.
- The findings list below the highlighted text gives one card per detection, with collection and hadith
  number pills for hadith passages.
- **Copy** copies what you currently see (pasted or replaced).
- Options: turn each detector on/off, hadith minimum words, tolerate small hadith spelling differences.
- Light and dark themes are available from the top bar.

It is one static file (`static/index.html`, no external requests or fonts), so it works offline.

## API

```bash
curl -s localhost:8000/detect -H 'content-type: application/json' -d '{
  "text": "قال الله تعالى: وَاصْبِرْ وَمَا صَبْرُكَ إِلَّا بِاللَّهِ",
  "options": {"include_annotated_html": true}
}'
```

`POST /detect` returns `{counts, spans[], annotated_html?}`. Each span has `type` (`quran` or `hadith`),
character offsets `start`/`end` into **your original text** (`text[start:end] == span.text`),
`canonical` (the passage in its original wording, or `null`), `matched_words`, `errors`, and either `quran`
(`reference` like `النحل:127`) or `hadith` (a list of `hadith_key`, `collection`, `hadith_number`,
`book_number`, `url`, `source_text`, `source`, `numbering`).

`POST /detect/batch` takes `{"texts": [...]}` and returns one result per text.
`GET /health` returns library versions, the six collection names, and limits.

## Options (all optional, per request)

| option | default | meaning |
|---|---|---|
| `quran`, `hadith` | `true` | turn a detector on/off |
| `quran_min_match` | 3 | minimum matching words |
| `quran_allowed_error_pct` | 0.25 | tolerated word-error ratio |
| `hadith_min_words` | 9 | raise to cut generic openers, lower for short hadith |
| `hadith_max_errors` | 0 | spelling slips tolerated (try 1) |
| `hadith_max_missing_words` | 0 | omitted words tolerated (try 1) |
| `max_references` | 5 | sources returned per hadith span |
| `overlap_policy` | `keep_all` | `prefer_longer` returns disjoint spans only |
| `include_annotated_html` | `false` | HTML-escaped text with `<mark class="quran">` / `<mark class="hadith">` |

Env vars: `MAX_TEXT_CHARS` (default 200000), `MAX_BATCH` (default 100), `HADITH_INDEX_DIR`
(optional path to a built index; defaults to `hadith_engine/_data`).

## Known limits

- Textual matching only: paraphrases (matching by meaning) are not detected.
- "Original wording" for the Quran comes from the text bundled in `quran-detector` and covers only the matched
  part of a verse. It may differ from a printed mushaf in places (for example it writes the opening letters of
  Surat al-Baqarah as «الم،»). For anything you publish, check against a mushaf.
- Diacritic marks can be stored in a different order than you type them (shadda/fatha); compare with Unicode NFC.
- Hadith coverage is the six canonical books only; a miss does not mean it is not a hadith.
- Hadith numbering is Open-Hadith-Data's edition, not sunnah.com's; do not use the numbers as sunnah.com links.
- A match is not an authentication of the quoted wording. The dataset is a digitisation, not a scholarly
  critical edition; verify anything you publish against a printed edition.
- Short, very common phrases can be missed or over-matched; review a sample of results on your own data.
- A hadith whose wording differs between narrations may come back as a partial span
  (only the part long enough to reach `hadith_min_words` in one narration).
- Quran char offsets are recovered from the library's word indices by re-tokenising the way it does
  (`pad_symbols(text).split()`). If you upgrade `quran-detector`, re-run the tests.
- Calls to each engine are serialised with a lock; scale with multiple workers (`--workers N`).
