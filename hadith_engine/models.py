"""Typed matching results, with independent metadata snapshots."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass(frozen=True)
class HadithRecord:
    hadith_key: str
    urn: int = 0
    seq: int = 0
    collection: str = ""
    language: str = "ar"
    hadith_number: str = ""
    number_sort: int = 0
    book_number: int = 0
    in_book_number: int = 0
    sunnah_url: str | None = None
    text: str = ""
    grades: list[dict[str, Any]] = field(default_factory=list)
    n_grades: int = 0
    grade_summary: str | None = None
    is_muallaq: bool = False
    additional_metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def _from_source(cls, source: dict[str, Any]) -> HadithRecord:
        """Materialise a result from a full or compact source record.

        Runtime records only carry identity, sequence and text; the remaining
        fields are restored from their documented defaults here.
        """
        hadith_key = source["hadith_key"]
        _, _, key_number = hadith_key.partition(":")
        collection = source.get("collection") or hadith_key.partition(":")[0]
        hadith_number = source.get("hadith_number") or key_number
        number_sort = source.get("number_sort")
        if number_sort is None:
            number_sort = int(hadith_number) if hadith_number.isdigit() else 0
        seq = source.get("seq") or number_sort or 1
        names = {f.name for f in fields(cls)} - {"additional_metadata"}
        return cls(
            hadith_key=hadith_key,
            urn=source.get("urn") or seq,
            seq=seq,
            collection=collection,
            language=source.get("language", "ar"),
            hadith_number=hadith_number,
            number_sort=number_sort,
            book_number=source.get("book_number", 0),
            in_book_number=source.get("in_book_number", number_sort),
            sunnah_url=source.get("sunnah_url"),
            text=source.get("text", ""),
            grades=deepcopy(source.get("grades", [])),
            n_grades=source.get("n_grades", 0),
            grade_summary=source.get("grade_summary"),
            is_muallaq=source.get("is_muallaq", False),
            additional_metadata={
                key: deepcopy(value) for key, value in source.items() if key not in names
            },
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> HadithRecord:
        """Restore a structurally validated, isolated snapshot without matching."""
        from ._serialization import record

        return record(value)

    def to_dict(self) -> dict[str, Any]:
        """Reproduce original source fields and values, without sharing mutable data."""
        result = asdict(self)
        extra = result.pop("additional_metadata")
        result.update(extra)
        return result


@dataclass(frozen=True)
class WordAlignment:
    input_token: int
    source_token: int
    input_start: int
    input_end: int
    source_start: int
    source_end: int
    errors: int

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> WordAlignment:
        """Restore a structurally validated, isolated snapshot without matching."""
        from ._serialization import word

        return word(value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OmittedSourceToken:
    source_token: int
    start: int
    end: int
    text: str
    term: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> OmittedSourceToken:
        """Restore a structurally validated, isolated snapshot without matching."""
        from ._serialization import omitted

        return omitted(value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceOccurrence:
    start: int
    end: int
    text: str
    errors: int = 0
    missing_words: int = 0
    alignment: tuple[WordAlignment, ...] = ()
    omitted_source_tokens: tuple[OmittedSourceToken, ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SourceOccurrence:
        """Restore a structurally validated, isolated snapshot without matching."""
        from ._serialization import occurrence

        return occurrence(value)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["alignment"] = [asdict(word) for word in self.alignment]
        result["omitted_source_tokens"] = [asdict(word) for word in self.omitted_source_tokens]
        return result


@dataclass(frozen=True)
class HadithReference:
    record: HadithRecord
    occurrences: tuple[SourceOccurrence, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> HadithReference:
        """Restore a structurally validated, isolated snapshot without matching."""
        from ._serialization import reference

        return reference(value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record": self.record.to_dict(),
            "occurrences": [occurrence.to_dict() for occurrence in self.occurrences],
        }


@dataclass(frozen=True)
class Detection:
    start: int
    end: int
    text: str
    matched_words: int
    references: tuple[HadithReference, ...]
    errors: int = 0
    missing_words: int = 0

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Detection:
        """Restore a structurally validated, isolated snapshot without matching."""
        from ._serialization import detection

        return detection(value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "matched_words": self.matched_words,
            "errors": self.errors,
            "missing_words": self.missing_words,
            "references": [reference.to_dict() for reference in self.references],
        }
