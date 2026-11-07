"""Quran + Hadith detection server.

Combines two offline libraries behind one HTTP API:
  * quran-detector  (QDetect rewrite, El-Beltagy & Rafea 2021) -> Quran verses
  * hadith_engine   (vendored MIT detector, Sahih al-Bukhari + Sahih Muslim) -> Hadith quotations

Run:  uvicorn app:app --host 0.0.0.0 --port 8000
UI:   http://localhost:8000/        API docs: http://localhost:8000/docs
"""
from __future__ import annotations

import html
import os
import re
import threading
from pathlib import Path
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from hadith_engine import HadithDetector
from hadith_engine import __version__ as HADITH_ENGINE_VERSION
from pydantic import BaseModel, Field
from quran_detector import Settings as QuranSettings
from quran_detector import annotate as quran_annotate
from quran_detector import detect as quran_detect
from quran_detector.api import get_engine as get_quran_engine

MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "200000"))
MAX_BATCH = int(os.getenv("MAX_BATCH", "100"))
STATIC_DIR = Path(__file__).parent / "static"

HADITH_COVERAGE = ["bukhari", "muslim"]
HADITH_SOURCE = "Open-Hadith-Data (mhashim6)"

DISCLAIMER = (
    "Matches are textual only. Hadith coverage is Sahih al-Bukhari and Sahih Muslim "
    "as digitised by Open-Hadith-Data, so no match does not mean no hadith. Hadith "
    "numbers follow that edition, not sunnah.com. A match is not an authentication "
    "of the quoted wording: verify against a printed edition."
)

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


class Options(BaseModel):
    quran: bool = Field(True, description="Detect Quran verses")
    hadith: bool = Field(True, description="Detect hadith quotations")

    # quran-detector
    quran_min_match: int = Field(3, ge=1, le=20, description="Minimum matching words")
    quran_allowed_error_pct: float = Field(0.25, ge=0, le=1)
    quran_find_errors: bool = True
    quran_find_missing: bool = True

    # hadith engine. 9 avoids matching generic openers such as
    # "قال النبي صلى الله عليه وسلم" against unrelated narrations.
    hadith_min_words: int = Field(9, ge=2, le=50)
    hadith_max_errors: int = Field(0, ge=0, le=5, description="Spelling slips allowed")
    hadith_max_missing_words: int = Field(0, ge=0, le=5)
    max_references: int = Field(5, ge=1, le=50, description="Hadith sources returned per span")

    overlap_policy: Literal["keep_all", "prefer_longer"] = "keep_all"
    include_annotated_html: bool = False


class DetectRequest(BaseModel):
    text: str = Field(..., max_length=MAX_TEXT_CHARS)
    options: Options = Field(default_factory=Options)


class BatchRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1, max_length=MAX_BATCH)
    options: Options = Field(default_factory=Options)


class QuranRef(BaseModel):
    surah_name: str
    aya_start: int
    aya_end: int
    reference: str = Field(..., description="e.g. النحل:127 or البقرة:285-286")


class HadithRef(BaseModel):
    hadith_key: str = Field(..., description="e.g. bukhari:1")
    collection: str
    hadith_number: str
    book_number: int | None = None
    url: str | None = Field(None, description="Null: this edition has no sunnah.com mapping")
    source_text: str | None = Field(
        None, description="The matching part of this narration, in the source's own wording"
    )
    source: str = Field("Open-Hadith-Data", description="Digitised text source")
    numbering: str = Field(
        "Open-Hadith-Data edition", description="Hadith numbers follow this edition, not sunnah.com"
    )


class Span(BaseModel):
    type: Literal["quran", "hadith"]
    start: int = Field(..., description="Character offset in the input (inclusive)")
    end: int = Field(..., description="Character offset in the input (exclusive)")
    text: str
    canonical: str | None = Field(
        None, description="The matched part in its original wording (Uthmani Quran / source hadith)"
    )
    matched_words: int
    errors: int = 0
    missing_words: int = 0
    quran: QuranRef | None = None
    hadith: list[HadithRef] | None = None
    hadith_references_total: int | None = None
    overlaps_with: list[int] = Field(default_factory=list, description="Indexes of overlapping spans")


class DetectResponse(BaseModel):
    text_length: int
    counts: dict[str, int]
    spans: list[Span]
    annotated_html: str | None = None
    disclaimer: str = DISCLAIMER


class BatchResponse(BaseModel):
    results: list[DetectResponse]


# --------------------------------------------------------------------------- #
# Engines (loaded once, calls serialised: both are CPU-bound under the GIL anyway)
# --------------------------------------------------------------------------- #

_quran_lock = threading.Lock()
_hadith_lock = threading.Lock()
_hadith: HadithDetector | None = None


def _get_hadith() -> HadithDetector:
    global _hadith
    if _hadith is None:
        _hadith = HadithDetector(os.getenv("HADITH_INDEX_DIR") or None)
    return _hadith


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_quran_engine()
    _get_hadith()
    yield


app = FastAPI(
    title="Quran & Hadith Detector",
    version="0.1.0",
    description=DISCLAIMER,
    lifespan=lifespan,
)

# --------------------------------------------------------------------------- #
# Quran: map the library's word indices back to character offsets
# --------------------------------------------------------------------------- #

# quran-detector tokenises with pad_symbols(text).split(), where pad_symbols puts
# spaces around these two symbols. We reproduce that to recover char offsets.
_PAD_SYMBOLS = {"۞", "۝"}
_EDGE_PUNCT = set(" \t\r\n.,،:;؛!?؟…-–—*()[]{}«»\"'﴿﴾")


def _pad_with_offsets(text: str) -> tuple[str, list[int]]:
    out: list[str] = []
    back: list[int] = []  # padded index -> original index
    for i, ch in enumerate(text):
        if ch in _PAD_SYMBOLS:
            out.extend((" ", ch, " "))
            back.extend((i, i, i))
        else:
            out.append(ch)
            back.append(i)
    return "".join(out), back


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start] in _EDGE_PUNCT:
        start += 1
    while end > start and text[end - 1] in _EDGE_PUNCT:
        end -= 1
    return start, end


# annotate() on a bare fragment returns:  "<canonical>" (surah:ayah)
# with a leading/trailing "..." when only part of the verse was matched.
_ANNOTATED_RE = re.compile(r'^"(?:\.\.\.)?(?P<text>.*?)(?:\.\.\.)?" \([^)]*\)$', re.S)


def _quran_canonical(fragment: str, settings: QuranSettings) -> str | None:
    with _quran_lock:
        out = quran_annotate(fragment, settings=settings)
    m = _ANNOTATED_RE.match(out.strip())
    return m.group("text").strip() or None if m else None


def _detect_quran(text: str, o: Options) -> list[Span]:
    settings = QuranSettings(
        find_errors=o.quran_find_errors,
        find_missing=o.quran_find_missing,
        allowed_error_pct=o.quran_allowed_error_pct,
        min_match=o.quran_min_match,
    )
    with _quran_lock:
        records = quran_detect(text, settings=settings)

    padded, back = _pad_with_offsets(text)
    tokens = [(m.start(), m.end()) for m in re.finditer(r"\S+", padded)]

    spans: list[Span] = []
    for r in records:
        ws, we = r["start_in_text"], r["end_in_text"]
        if not (0 <= ws < we <= len(tokens)):
            continue  # defensive: library/tokeniser mismatch
        start = back[tokens[ws][0]]
        end = back[tokens[we - 1][1] - 1] + 1
        start, end = _trim(text, start, end)
        a1, a2 = r["aya_start"], r["aya_end"]
        ref = f"{r['surah_name']}:{a1}" if a1 == a2 else f"{r['surah_name']}:{a1}-{a2}"
        errs = r.get("errors") or []
        spans.append(
            Span(
                type="quran",
                start=start,
                end=end,
                text=text[start:end],
                canonical=_quran_canonical(text[start:end], settings),
                matched_words=we - ws,
                errors=sum(len(e) for e in errs),
                quran=QuranRef(surah_name=r["surah_name"], aya_start=a1, aya_end=a2, reference=ref),
            )
        )
    return spans


# --------------------------------------------------------------------------- #
# Hadith
# --------------------------------------------------------------------------- #


_INVISIBLE_RE = re.compile(r"[\u200e\u200f\u202a-\u202e]")


def _clean_source(s: str) -> str:
    """Drop directional marks and the quote characters used inside the corpus."""
    return " ".join(_INVISIBLE_RE.sub("", s).replace('"', " ").split())


def _detect_hadith(text: str, o: Options) -> list[Span]:
    detector = _get_hadith()
    with _hadith_lock:
        detections = detector.detect(
            text,
            min_match_words=o.hadith_min_words,
            max_errors=o.hadith_max_errors,
            max_missing_words=o.hadith_max_missing_words,
        )
    spans: list[Span] = []
    for d in detections:
        refs = [
            HadithRef(
                hadith_key=r.record.hadith_key,
                collection=r.record.collection,
                hadith_number=str(r.record.hadith_number),
                book_number=r.record.book_number or None,
                url=r.record.sunnah_url,
                source_text=_clean_source(r.occurrences[0].text) if r.occurrences else None,
            )
            for r in d.references[: o.max_references]
        ]
        spans.append(
            Span(
                type="hadith",
                start=d.start,
                end=d.end,
                text=text[d.start : d.end],
                canonical=refs[0].source_text if refs else None,
                matched_words=d.matched_words,
                errors=d.errors,
                missing_words=d.missing_words,
                hadith=refs,
                hadith_references_total=len(d.references),
            )
        )
    return spans


# --------------------------------------------------------------------------- #
# Combine
# --------------------------------------------------------------------------- #


def _overlap(a: Span, b: Span) -> bool:
    return a.start < b.end and b.start < a.end


def _pick_non_overlapping(spans: list[Span]) -> list[Span]:
    """Greedy: longest (by words) wins; ties go to the earlier span."""
    chosen: list[Span] = []
    for s in sorted(spans, key=lambda s: (-s.matched_words, s.start)):
        if not any(_overlap(s, c) for c in chosen):
            chosen.append(s)
    return sorted(chosen, key=lambda s: (s.start, s.end))


def _annotate(text: str, spans: list[Span]) -> str:
    parts: list[str] = []
    pos = 0
    for s in spans:
        parts.append(html.escape(text[pos : s.start]))
        ref = s.quran.reference if s.quran else (s.hadith[0].hadith_key if s.hadith else "")
        parts.append(
            f'<mark class="{s.type}" data-ref="{html.escape(ref, quote=True)}">'
            f"{html.escape(text[s.start : s.end])}</mark>"
        )
        pos = s.end
    parts.append(html.escape(text[pos:]))
    return "".join(parts)


def run_detection(text: str, o: Options) -> DetectResponse:
    spans: list[Span] = []
    if o.quran:
        spans += _detect_quran(text, o)
    if o.hadith:
        spans += _detect_hadith(text, o)
    spans.sort(key=lambda s: (s.start, s.end))

    if o.overlap_policy == "prefer_longer":
        spans = _pick_non_overlapping(spans)
    else:
        for i, a in enumerate(spans):
            a.overlaps_with = [j for j, b in enumerate(spans) if j != i and _overlap(a, b)]

    annotated = None
    if o.include_annotated_html:
        annotated = _annotate(text, _pick_non_overlapping(spans))

    return DetectResponse(
        text_length=len(text),
        counts={
            "quran": sum(s.type == "quran" for s in spans),
            "hadith": sum(s.type == "hadith" for s in spans),
        },
        spans=spans,
        annotated_html=annotated,
    )


# --------------------------------------------------------------------------- #
# Routes (plain `def` -> FastAPI runs them in a worker thread)
# --------------------------------------------------------------------------- #


@app.post("/detect", response_model=DetectResponse, summary="Detect Quran verses and hadith in text")
def detect_endpoint(req: DetectRequest) -> DetectResponse:
    return run_detection(req.text, req.options)


@app.post("/detect/batch", response_model=BatchResponse, summary="Detect in many texts at once")
def detect_batch(req: BatchRequest) -> BatchResponse:
    for i, t in enumerate(req.texts):
        if len(t) > MAX_TEXT_CHARS:
            raise HTTPException(422, f"texts[{i}] exceeds {MAX_TEXT_CHARS} characters")
    return BatchResponse(results=[run_detection(t, req.options) for t in req.texts])


def _ver(pkg: str) -> str | None:
    try:
        return version(pkg)
    except PackageNotFoundError:
        return None


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "quran_detector": _ver("quran-detector"),
        "hadith_engine": HADITH_ENGINE_VERSION,
        "hadith_source": HADITH_SOURCE,
        "hadith_coverage": HADITH_COVERAGE,
        "limits": {"max_text_chars": MAX_TEXT_CHARS, "max_batch": MAX_BATCH},
    }


@app.get("/", include_in_schema=False)
def ui() -> FileResponse:
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(404, "static/index.html is missing")
    return FileResponse(index, media_type="text/html; charset=utf-8")
