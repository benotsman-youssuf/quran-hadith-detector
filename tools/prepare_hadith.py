"""Build a deterministic prepared corpus from pinned Open-Hadith-Data CSV files.

Sahih al-Bukhari and Sahih Muslim are downloaded at a pinned commit, verified
by SHA-256, and converted to the four artifacts the index builder consumes:

    records.jsonl  tokens.jsonl  validation.json  manifest.json

Both generated JSONL files are deliberately compact: records keep only the
fields the runtime uses, and tokens are arrays instead of repeated key/value
objects. Run with ``python -m tools.prepare_hadith``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import tempfile
import unicodedata
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from hadith_engine._text import NORMALIZATION_VERSION, tokenize

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "tools" / "hadith-sources.json"
DEFAULT_RAW = ROOT / "data" / "raw"
DEFAULT_OUTPUT = ROOT / "data" / "prepared"
OUTPUTS = ("records.jsonl", "tokens.jsonl", "validation.json", "manifest.json")
VERSIONS = {"records": 2, "tokens": 2, "manifest": 1, "normalization": NORMALIZATION_VERSION}

# Directional controls, zero-width characters and byte-order marks are layout
# hints in the source files, not part of the narration.
_IGNORED = frozenset(
    "\u061c\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e"
    "\u2066\u2067\u2068\u2069\ufeff"
)


class DataError(ValueError):
    """Invalid source data or a failed build."""


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_lock(path: Path) -> dict[str, Any]:
    lock = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(lock, dict) or lock.get("lock_version", 1) != 1:
        raise DataError("Invalid source lock")
    for key in ("repository", "revision", "configs"):
        if key not in lock:
            raise DataError(f"Source lock is missing {key}")
    if not isinstance(lock["configs"], list) or not lock["configs"]:
        raise DataError("Source lock has no configurations")
    return lock


def download(lock: dict[str, Any], config: dict[str, Any], raw_dir: Path) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / f"{config['name']}.csv"
    if target.is_file() and checksum(target) == config["sha256"]:
        return target
    if target.is_file():
        target.unlink()
    url = (
        f"https://raw.githubusercontent.com/{lock['repository']}/"
        f"{lock['revision']}/{config['path']}"
    )
    staging = target.with_suffix(".csv.part")
    with urllib.request.urlopen(url, timeout=300) as response, staging.open("wb") as out:
        shutil.copyfileobj(response, out)
    if checksum(staging) != config["sha256"]:
        staging.unlink()
        raise DataError(f"Checksum mismatch for {config['name']}")
    staging.replace(target)
    return target


def clean_text(value: str) -> str:
    text = "".join(" " if ch in _IGNORED else ch for ch in value)
    return " ".join(text.split())


def read_csv(path: Path, collection: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as stream:
        for line_number, row in enumerate(csv.reader(stream), 1):
            if len(row) < 2:
                raise DataError(f"{collection}: short row at line {line_number}")
            number = row[0].strip()
            text = clean_text(row[1])
            if not number.isdigit() or not text:
                raise DataError(f"{collection}: invalid row at line {line_number}")
            if number in seen:
                raise DataError(f"{collection}: duplicate number {number}")
            seen.add(number)
            rows.append((number, text))
    if not rows:
        raise DataError(f"{collection}: empty source file")
    return rows


def derive(
    configs: list[dict[str, Any]], raw_dir: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for config in sorted(configs, key=lambda c: c["collection"]):
        collection = config["collection"]
        path = raw_dir / f"{config['name']}.csv"
        rows = read_csv(path, collection)
        for seq, (number, text) in enumerate(rows, 1):
            records.append(
                {
                    "hadith_key": f"{collection}:{number}",
                    "collection": collection,
                    "hadith_number": number,
                    "seq": seq,
                    "text": text,
                }
            )

    keys = {r["hadith_key"] for r in records}
    identities = {(r["collection"], r["seq"]) for r in records}
    if len(keys) != len(records) or len(identities) != len(records):
        raise DataError("Duplicate hadith identity after conversion")
    if records != sorted(records, key=lambda r: (r["collection"], r["seq"])):
        raise DataError("Records are not in deterministic collection/sequence order")

    token_rows: list[dict[str, Any]] = []
    collection_counts: Counter[str] = Counter()
    null_counts: Counter[str] = Counter()
    text_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    token_count = empty_tokens = 0
    for record in records:
        result = tokenize(record["text"])
        token_rows.append(
            {
                "hadith_key": record["hadith_key"],
                "tokens": [
                    [token.start, token.end, token.segment, token.term]
                    if token.segment
                    else [token.start, token.end, token.term]
                    for token in result.tokens
                ],
            }
        )
        for token in result.tokens:
            if not (0 <= token.start < token.end <= len(record["text"])):
                raise DataError(f"Invalid token span: {record['hadith_key']}")
        for warning in result.warnings:
            warning_counts[warning.kind] += 1
        collection_counts[record["collection"]] += 1
        text_counts[record["text"]] += 1
        for field, value in record.items():
            if value is None:
                null_counts[field] += 1
        token_count += len(result.tokens)
        empty_tokens += not result.tokens

    duplicate_groups: dict[str, list[str]] = {}
    for record in records:
        if text_counts[record["text"]] > 1:
            digest = hashlib.sha256(record["text"].encode("utf-8")).hexdigest()
            duplicate_groups.setdefault(digest, []).append(record["hadith_key"])

    report = {
        "validation_version": 1,
        "integrity": "passed",
        "records": len(records),
        "collection_counts": dict(collection_counts),
        "null_counts": dict(null_counts),
        "duplicate_text_groups": duplicate_groups,
        "duplicate_text_extra_records": sum(n - 1 for n in text_counts.values() if n > 1),
        "tokens": token_count,
        "records_without_tokens": empty_tokens,
        "warning_counts": dict(warning_counts),
        "normalization": NORMALIZATION_VERSION,
        "unicode": unicodedata.unidata_version,
    }
    return records, token_rows, report


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("wb") as stream:
        for row in rows:
            stream.write(canonical(row))


def build(
    output: Path, lock_path: Path, raw_dir: Path, offline: bool = False
) -> dict[str, Any]:
    output = output.resolve()
    if output.exists():
        raise DataError("Output directory already exists; choose a new directory")
    lock = load_lock(lock_path)
    raw_dir.mkdir(parents=True, exist_ok=True)

    for config in lock["configs"]:
        target = raw_dir / f"{config['name']}.csv"
        if offline:
            if not target.is_file():
                raise DataError(f"Missing cached source: {target}")
            if checksum(target) != config["sha256"]:
                raise DataError(f"Checksum mismatch for {config['name']}")
        else:
            download(lock, config, raw_dir)

    records, token_rows, report = derive(lock["configs"], raw_dir)
    if report["records_without_tokens"]:
        raise DataError("Some records produced no matching tokens")

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".prepare-", dir=output.parent))
    try:
        write_jsonl(staging / "records.jsonl", records)
        write_jsonl(staging / "tokens.jsonl", token_rows)
        (staging / "validation.json").write_bytes(canonical(report))
        artifacts = {
            name: checksum(staging / name) for name in OUTPUTS if name != "manifest.json"
        }
        manifest = {
            "versions": VERSIONS,
            "source_lock": lock,
            "artifacts": artifacts,
        }
        manifest["build_fingerprint"] = hashlib.sha256(canonical(manifest)).hexdigest()
        (staging / "manifest.json").write_bytes(canonical(manifest))
        if output.exists():
            raise DataError("Output appeared during build; refusing overwrite")
        staging.rename(output)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument(
        "--offline", action="store_true", help="Use cached CSVs and never download"
    )
    args = parser.parse_args()
    try:
        manifest = build(args.output_dir, args.lock, args.raw_dir, args.offline)
    except (OSError, DataError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"prepared corpus: passed; {manifest['artifacts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
