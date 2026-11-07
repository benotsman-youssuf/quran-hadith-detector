"""Extract plain text from a YouTube video's manual or automatic captions.

Only captions are used; audio is never downloaded and no speech-to-text model is
run. ``yt-dlp`` is imported lazily so the detector starts even when the optional
YouTube dependency is unavailable.
"""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "youtu.be",
        "music.youtube.com",
        "youtube-nocookie.com",
    }
)
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_TAG = re.compile(r"<[^>]+>")
_NUMBERED_CUE = re.compile(r"^\d+$")
_FORMAT_ORDER = ("json3", "vtt", "srv3", "srv1", "srv2", "ttml")


class TranscriptError(ValueError):
    """A user-facing transcript extraction failure."""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Transcript:
    url: str
    video_id: str
    title: str
    language: str
    automatic: bool
    text: str
    text_length: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_youtube_url(url: str) -> str:
    """Return the 11-character video id for a supported YouTube URL."""
    value = (url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise TranscriptError("Enter a full YouTube URL, for example https://youtu.be/VIDEO_ID")
    host = (parsed.hostname or "").lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    if host not in YOUTUBE_HOSTS:
        raise TranscriptError("Only youtube.com and youtu.be links are supported.")
    path = parsed.path.rstrip("/")
    if path == "/playlist":
        raise TranscriptError("Playlist links are not supported; use a single video link.")

    video_id = ""
    if host == "youtu.be":
        parts = [part for part in path.split("/") if part]
        video_id = parts[0] if parts else ""
    else:
        video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        if not video_id:
            parts = [part for part in path.split("/") if part]
            if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live", "v"}:
                video_id = parts[1]
    if not _VIDEO_ID.fullmatch(video_id):
        raise TranscriptError("This YouTube link does not contain a valid video id.")
    return video_id


def extract_transcript(
    url: str,
    *,
    lang: str = "ar",
    fallback: bool = True,
    max_chars: int = 200_000,
) -> Transcript:
    """Fetch the best available caption track and return it as plain text."""
    video_id = validate_youtube_url(url)
    language = (lang or "ar").strip().lower()
    if not language or len(language) > 20 or not re.fullmatch(r"[a-z0-9_-]+", language):
        raise TranscriptError("lang must be a short caption language code such as ar or en.")

    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover - dependency is in requirements.txt
        raise TranscriptError(
            "yt-dlp is not installed on the server; install the requirements first.",
            status_code=503,
        ) from exc

    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 1,
        "extractor_retries": 1,
        "cachedir": False,
    }
    canonical = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(canonical, download=False)
            if not isinstance(info, dict):
                raise TranscriptError("YouTube did not return video information.")
            manual = info.get("subtitles") or {}
            automatic = info.get("automatic_captions") or {}
            for is_auto, key, track in _candidates(manual, automatic, language, fallback):
                text = _download_track(ydl, track)
                if not text:
                    continue
                if len(text) > max_chars:
                    raise TranscriptError(
                        f"The transcript is {len(text):,} characters; "
                        f"the limit is {max_chars:,}.",
                        status_code=413,
                    )
                return Transcript(
                    url=canonical,
                    video_id=video_id,
                    title=str(info.get("title") or "YouTube video"),
                    language=key,
                    automatic=is_auto,
                    text=text,
                    text_length=len(text),
                )
    except TranscriptError:
        raise
    except Exception as exc:
        raise TranscriptError(_friendly_error(exc)) from exc

    raise TranscriptError(
        "This video has no usable manual or automatic captions. "
        "Try another language with lang, or a video with subtitles."
    )


def _candidates(
    manual: dict[str, Any], automatic: dict[str, Any], lang: str, fallback: bool
) -> Iterator[tuple[bool, str, dict[str, Any]]]:
    seen: set[tuple[str, str]] = set()
    groups = ((False, manual), (True, automatic))
    for is_auto, tracks in groups:
        for key, track in _matching_tracks(tracks, lang):
            identity = (key, str(track.get("url")))
            if identity not in seen:
                seen.add(identity)
                yield is_auto, key, track
    if not fallback:
        return
    for is_auto, tracks in groups:
        for key, track in _all_tracks(tracks):
            identity = (key, str(track.get("url")))
            if identity not in seen:
                seen.add(identity)
                yield is_auto, key, track


def _matching_tracks(tracks: dict[str, Any], lang: str) -> Iterator[tuple[str, dict[str, Any]]]:
    keys = [str(key) for key in tracks]
    ordered = [key for key in keys if key.lower() == lang]
    ordered += [
        key
        for key in keys
        if key.lower().startswith(f"{lang}-") or key.lower().startswith(f"{lang}_")
    ]
    for key in dict.fromkeys(ordered):
        track = _pick_format(tracks.get(key))
        if track:
            yield key, track


def _all_tracks(tracks: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for key, formats in tracks.items():
        track = _pick_format(formats)
        if track:
            yield str(key), track


def _pick_format(formats: Any) -> dict[str, Any] | None:
    if not isinstance(formats, list):
        return None
    usable = [
        item
        for item in formats
        if isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"]
    ]
    for extension in _FORMAT_ORDER:
        for item in usable:
            if item.get("ext") == extension:
                return item
    return usable[0] if usable else None


def _download_track(ydl: Any, track: dict[str, Any]) -> str:
    try:
        with ydl.urlopen(track["url"]) as response:
            payload = response.read()
    except Exception as exc:
        raise TranscriptError(_friendly_error(exc)) from exc
    if isinstance(payload, bytes):
        body = payload.decode("utf-8", errors="replace")
    else:
        body = str(payload)
    extension = str(track.get("ext") or "")
    if extension == "json3" or body.lstrip().startswith("{"):
        return _parse_json3(body)
    if extension == "vtt" or body.lstrip().startswith("WEBVTT"):
        return _parse_vtt(body)
    return _parse_xml(body)


def _clean_line(value: str) -> str:
    value = html.unescape(_TAG.sub("", value))
    value = value.replace("\ufeff", " ").replace("\u200b", " ")
    return " ".join(value.split())


def _parse_json3(value: str) -> str:
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise TranscriptError("YouTube returned an invalid caption file.") from exc
    parts: list[str] = []
    for event in data.get("events") or []:
        if not isinstance(event, dict):
            continue
        line = "".join(
            str(segment.get("utf8") or "")
            for segment in event.get("segs") or []
            if isinstance(segment, dict)
        )
        line = _clean_line(line)
        if line:
            parts.append(line)
    return " ".join(parts)


def _parse_vtt(value: str) -> str:
    parts: list[str] = []
    skip = False
    for raw in value.splitlines():
        line = raw.strip()
        if not line:
            skip = False
            continue
        if line.startswith(("NOTE", "STYLE", "REGION")):
            skip = True
            continue
        if skip or line == "WEBVTT" or "-->" in line or _NUMBERED_CUE.fullmatch(line):
            continue
        line = _clean_line(line)
        if line and (not parts or parts[-1] != line):
            parts.append(line)
    return " ".join(parts)


def _parse_xml(value: str) -> str:
    try:
        root = ET.fromstring(value)
    except ET.ParseError as exc:
        raise TranscriptError("YouTube returned an invalid caption file.") from exc
    parts: list[str] = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "text" or not node.text:
            continue
        line = _clean_line(node.text)
        if line:
            parts.append(line)
    return " ".join(parts)


def _friendly_error(exc: Exception) -> str:
    message = str(getattr(exc, "msg", "") or exc).strip()
    if message.startswith("ERROR:"):
        message = message[6:].strip()
    message = " ".join(message.split())
    lowered = message.lower()
    if "sign in" in lowered or "not a bot" in lowered or "confirm you" in lowered:
        return "YouTube asked for sign-in verification. Try again later or from another network."
    if "private video" in lowered:
        return "This video is private."
    if "video unavailable" in lowered or "this video is not available" in lowered:
        return "YouTube says this video is unavailable."
    if "age" in lowered and "restrict" in lowered:
        return "This video is age-restricted and cannot be read without a signed-in session."
    if not message:
        return "Could not fetch the YouTube transcript."
    return message[:300]
