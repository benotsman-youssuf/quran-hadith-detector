import json
import unicodedata

import pytest
from fastapi.testclient import TestClient

from app import app
from youtube_transcript import (
    Transcript,
    TranscriptError,
    _parse_json3,
    _parse_vtt,
    validate_youtube_url,
)

def nfc(x):
    """Diacritic marks can be stored in different orders (shadda/fatha); compare canonically."""
    return unicodedata.normalize("NFC", x)


VERSE = "وَاصْبِرْ وَمَا صَبْرُكَ إِلَّا بِاللَّهِ"
HADITH = (
    "إنما الأعمال بالنيات وإنما لكل امرئ ما نوى فمن كانت هجرته إلى الله ورسوله "
    "فهجرته إلى الله ورسوله"
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # runs lifespan -> loads both engines once
        yield c


def post(client, text, **options):
    r = client.post("/detect", json={"text": text, "options": options})
    assert r.status_code == 200, r.text
    return r.json()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["hadith_coverage"] == [
        "bukhari",
        "muslim",
    ]
    assert body["hadith_engine"]
    assert body["hadith_source"].startswith("Open-Hadith-Data")


def test_quran_offsets_map_back_to_original_text(client):
    text = f"قال لي صديقي في العزاء: {VERSE}، فهدأت نفسي"
    data = post(client, text)
    quran = [s for s in data["spans"] if s["type"] == "quran"]
    assert len(quran) == 1
    s = quran[0]
    assert s["quran"]["reference"] == "النحل:127"
    assert text[s["start"] : s["end"]] == s["text"]
    assert s["text"] == VERSE  # trailing «،» trimmed, diacritics kept


BUKHARI_1 = (
    "عَلْقَمَةَ بْنَ وَقَّاصٍ اللَّيْثِيَّ يَقُولُ سَمِعْتُ عُمَرَ بْنَ الْخَطَّابِ "
    "رَضِيَ اللَّهُ عَنْهُ عَلَى الْمِنْبَرِ قَالَ سَمِعْتُ"
)

# One distinctive slice from the first narration of each book. Every slice is
# unique to its source text, so a missing or mis-built collection fails loudly.
COLLECTION_QUERIES = {
    "bukhari": BUKHARI_1,
    "muslim": (
        "شَيْبَةَ حَدَّثَنَا وَكِيعٌ عَنْ شُعْبَةَ عَنْ الْحَكَمِ عَنْ عَبْدِ الرَّحْمَنِ "
        "بْنِ أَبِي لَيْلَى عَنْ سَمُرَةَ بْنِ"
    ),
}


def test_hadith_detected_with_source_metadata(client):
    text = f"قال النبي صلى الله عليه وسلم: {BUKHARI_1}"
    data = post(client, text, max_references=10)
    hadith = [s for s in data["spans"] if s["type"] == "hadith"]
    assert hadith, data
    refs = [r for s in hadith for r in s["hadith"]]
    assert any(r["hadith_key"] == "bukhari:1" for r in refs), refs
    assert all(r["url"] is None for r in refs)
    assert all(r["source"] == "Open-Hadith-Data" for r in refs)
    assert all(r["numbering"] == "Open-Hadith-Data edition" for r in refs)


def test_both_sahih_collections_are_indexed(client):
    for collection, query in COLLECTION_QUERIES.items():
        data = post(client, query, quran=False, max_references=10)
        keys = {r["hadith_key"] for s in data["spans"] for r in s["hadith"]}
        assert f"{collection}:1" in keys, (collection, keys, data)


def test_mixed_text_gets_both_types(client):
    text = f"قال الله تعالى: {VERSE}. وقال النبي صلى الله عليه وسلم: {HADITH}"
    data = post(client, text)
    assert data["counts"]["quran"] == 1
    assert data["counts"]["hadith"] >= 1
    for s in data["spans"]:
        assert text[s["start"] : s["end"]] == s["text"]


def test_no_match(client):
    data = post(client, "ذهبت اليوم إلى السوق واشتريت بعض الخضار وعدت إلى البيت مبكرا")
    assert data["spans"] == []
    assert data["counts"] == {"quran": 0, "hadith": 0}


def test_quran_text_is_not_reported_as_hadith(client):
    data = post(client, f"قال تعالى: {VERSE}")
    assert data["counts"] == {"quran": 1, "hadith": 0}


def test_toggle_types_off(client):
    text = f"{VERSE}. {HADITH}"
    assert post(client, text, quran=False)["counts"]["quran"] == 0
    assert post(client, text, hadith=False)["counts"]["hadith"] == 0


def test_fuzzy_hadith_option(client):
    typo = "إنما الأعمال بالنياث وإنما لكل امرئ ما نوى"
    strict = post(client, typo, quran=False)
    fuzzy = post(client, typo, quran=False, hadith_min_words=5, hadith_max_errors=1)
    assert max((s["matched_words"] for s in fuzzy["spans"]), default=0) > max(
        (s["matched_words"] for s in strict["spans"]), default=0
    )


def test_annotated_html_is_escaped_and_marked(client):
    text = f"<b>x</b> {VERSE}"
    data = post(client, text, include_annotated_html=True)
    html_out = data["annotated_html"]
    assert "&lt;b&gt;" in html_out and "<b>" not in html_out
    assert '<mark class="quran" data-ref="النحل:127">' in html_out


def test_overlap_policy_prefer_longer_returns_disjoint_spans(client):
    text = f"قال النبي صلى الله عليه وسلم: {HADITH}"
    data = post(client, text, overlap_policy="prefer_longer")
    spans = sorted(data["spans"], key=lambda s: s["start"])
    for a, b in zip(spans, spans[1:]):
        assert a["end"] <= b["start"]


def test_batch(client):
    r = client.post("/detect/batch", json={"texts": [VERSE, "لا شيء هنا"]})
    assert r.status_code == 200
    res = r.json()["results"]
    assert res[0]["counts"]["quran"] == 1 and res[1]["counts"]["quran"] == 0


def test_validation_limits(client):
    assert client.post("/detect", json={"text": "x", "options": {"hadith_min_words": 0}}).status_code == 422
    assert client.post("/detect/batch", json={"texts": []}).status_code == 422


def test_quran_canonical_is_uthmani_fragment(client):
    data = post(client, f"قال تعالى: {VERSE}")
    q = next(s for s in data["spans"] if s["type"] == "quran")
    assert nfc(q["canonical"]) == nfc(VERSE)  # diacritics restored, no "..." markers or reference text


def test_quran_canonical_restores_spelling_from_plain_text(client):
    plain = "ربنا اتنا في الدنيا حسنة وفي الاخرة حسنة وقنا عذاب النار"
    q = next(s for s in post(client, plain, hadith=False)["spans"] if s["type"] == "quran")
    assert nfc(q["canonical"]).startswith(nfc("رَبَّنَا آتِنَا"))
    assert q["canonical"] != q["text"]


def test_hadith_canonical_is_clean_source_wording(client):
    data = post(client, HADITH, quran=False)
    h = data["spans"][0]
    assert h["canonical"]
    assert h["canonical"] == h["hadith"][0]["source_text"]
    assert not any(ch in h["canonical"] for ch in '"\u200f\u200e')
    assert nfc("امْرِئٍ") in nfc(h["canonical"])  # diacritised source wording


def test_ui_is_served_at_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Quran &amp; hadith detector" in r.text
    assert 'id="replaceAll"' in r.text
    # clicking a highlight opens the details drawer
    assert 'id="drawer"' in r.text
    assert 'id="drawer-body"' in r.text
    assert "openDrawer" in r.text
    # YouTube transcript import is available in the compose card
    assert 'id="ytUrl"' in r.text
    assert "/youtube/transcript" in r.text


@pytest.mark.parametrize(
    ("url", "video_id"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?t=10", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&list=abc", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ],
)
def test_youtube_url_validation(url, video_id):
    assert validate_youtube_url(url) == video_id


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/watch?v=dQw4w9WgXcQ",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://www.youtube.com/playlist?list=abc",
        "https://www.youtube.com/watch?v=short",
        "not a url",
    ],
)
def test_youtube_url_rejects_unsupported_links(url):
    with pytest.raises(TranscriptError):
        validate_youtube_url(url)


def test_youtube_json3_parser_joins_segments():
    payload = json.dumps(
        {"events": [{"segs": [{"utf8": "إنما "}, {"utf8": "الأعمال"}]}, {"segs": [{"utf8": "بالنيات"}]}]}
    )
    assert _parse_json3(payload) == "إنما الأعمال بالنيات"


def test_youtube_vtt_parser_strips_markup_and_timestamps():
    payload = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:01.000\n"
        "<v Speaker>إنما الأعمال</v>\n\n"
        "00:00:01.000 --> 00:00:02.000\n"
        "بالنيات\n"
    )
    assert _parse_vtt(payload) == "إنما الأعمال بالنيات"


def test_youtube_transcript_endpoint(client, monkeypatch):
    text = "إنما الأعمال بالنيات"
    fake = Transcript(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        video_id="dQw4w9WgXcQ",
        title="Test video",
        language="ar",
        automatic=True,
        text=text,
        text_length=len(text),
    )
    monkeypatch.setattr("app.extract_transcript", lambda *args, **kwargs: fake)
    r = client.post(
        "/youtube/transcript",
        json={"url": "https://youtu.be/dQw4w9WgXcQ", "lang": "ar"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["video_id"] == "dQw4w9WgXcQ"
    assert body["language"] == "ar"
    assert body["automatic"] is True
    assert body["text"] == text
    assert body["text_length"] == len(text)


def test_youtube_transcript_endpoint_rejects_other_hosts(client):
    r = client.post(
        "/youtube/transcript",
        json={"url": "https://example.com/watch?v=dQw4w9WgXcQ"},
    )
    assert r.status_code == 422
    assert "youtube.com" in r.json()["detail"].lower()
