# Quran & Hadith Detector Server

One HTTP API over two offline libraries:

- [`quran-detector`](https://pypi.org/project/quran-detector/) – rewrite of QDetect (El-Beltagy & Rafea, 2021)
- `hadith_engine` – vendored MIT detector (from [`RayenFekih/hadith-detector`](https://github.com/RayenFekih/hadith-detector))
  matching Sahih al-Bukhari and Sahih Muslim

Detection runs locally with no API keys. The optional YouTube transcript importer uses
[`yt-dlp`](https://github.com/yt-dlp/yt-dlp) to fetch captions from YouTube; audio is never
downloaded and no speech-to-text model is run.

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

The corpus build downloads about 50 MB of CSV, verifies SHA-256, and the index build takes roughly
1–2 minutes and produces a compact runtime index. Docker does both steps automatically:

```bash
docker build -t qh-detector .
docker run -p 8000:8000 qh-detector
```

## Expose it publicly

The loaded engines use about 550 MB RSS, so run the server on a machine with at least 1 GB free
and put a tunnel in front of it. `tools/serve_public.sh` starts Uvicorn on `127.0.0.1` and a
Cloudflare quick tunnel, then prints the public URL:

```bash
pip install -r requirements.txt
python -m tools.prepare_hadith
python -m tools.build_index --corpus-dir data/prepared --output-dir hadith_engine/_data
CLOUDFLARED=/path/to/cloudflared PYTHON=.venv/bin/python tools/serve_public.sh
```

Install `cloudflared` from the
[Cloudflare Tunnel docs](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/).
Quick tunnels need no account, but they have no uptime guarantee and the `trycloudflare.com`
hostname changes whenever the process restarts. For a stable hostname, use a named Cloudflare
tunnel with your own domain.

`render.yaml` is still included as an alternative: it defines a Docker web service with health
check `/health`. The container binds to `$PORT`, and the startup peak is about 550 MB, so use a
plan with at least 1 GB (for example `1c-2g`); Render's 512 MB free plan is just below the
measured requirement.

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
`1515f6c`, using the diacritised *mushakkala* files and keeping only the two *Sahih* collections.
Only the narration column is kept; the *Tafseel* commentary is discarded. Generated records and
tokens are written as compact JSONL (short arrays and no repeated per-token keys). The dataset is
licensed under ODbL/DbCL; see `THIRD_PARTY_NOTICES.md`.

| Collection | Records |
|---|---:|
| Sahih al-Bukhari | 7,008 |
| Sahih Muslim | 5,362 |
| **Total** | **12,370** |

Hadith numbers follow this edition and are not identical to sunnah.com numbering, so the API returns
`url: null` and the UI does not link to sunnah.com. No grades are included.

## Web UI (`/`)

Paste Arabic text (detection runs on paste, or press **Detect** / Ctrl+Enter), or paste a
YouTube link and choose **Fetch** to load the video's captions into the editor.

- The **or import a YouTube transcript** row fetches the Arabic captions when available and falls
  back to another caption language. The transcript is placed in the editor and detection runs on it.
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

The UI is one static file (`static/index.html`, no external requests or fonts), so detection works
offline. Only the optional YouTube transcript import needs network access.

## YouTube transcript import

`POST /youtube/transcript` fetches a video's manual or automatic captions with
[`yt-dlp`](https://github.com/yt-dlp/yt-dlp) and returns plain text. It does not download audio and
does not run speech-to-text, so the video must already have captions.

```bash
curl -s localhost:8000/youtube/transcript -H 'content-type: application/json' -d '{
  "url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "lang": "ar",
  "fallback": true
}'
```

The response is `{url, video_id, title, language, automatic, text, text_length}`. `language` is the
caption track actually used; `automatic` is `true` when YouTube's auto-generated captions were the
source. Arabic (`ar`) is requested by default. With `fallback: true` the endpoint uses another
available caption language when Arabic is missing. Transcripts longer than `MAX_TEXT_CHARS`
(200,000 by default) are rejected with HTTP 413.

The UI button **Fetch** in the compose card calls the same endpoint, fills the editor, and runs
detection on the result.

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
`POST /youtube/transcript` takes `{"url": "...", "lang": "ar", "fallback": true}` and returns the
video's caption text (see the section above).
`GET /health` returns library versions, the two collection names, and limits.

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
- Hadith coverage is Sahih al-Bukhari and Sahih Muslim only; a miss does not mean it is not a hadith.
- Hadith numbering is Open-Hadith-Data's edition, not sunnah.com's; do not use the numbers as sunnah.com links.
- A match is not an authentication of the quoted wording. The dataset is a digitisation, not a scholarly
  critical edition; verify anything you publish against a printed edition.
- Short, very common phrases can be missed or over-matched; review a sample of results on your own data.
- A hadith whose wording differs between narrations may come back as a partial span
  (only the part long enough to reach `hadith_min_words` in one narration).
- Quran char offsets are recovered from the library's word indices by re-tokenising the way it does
  (`pad_symbols(text).split()`). If you upgrade `quran-detector`, re-run the tests.
- YouTube import depends on the video having captions. Audio-only videos and videos without subtitles
  are not transcribed. YouTube can also block or rate-limit extraction from some networks.
- Calls to each engine are serialised with a lock; scale with multiple workers (`--workers N`).
