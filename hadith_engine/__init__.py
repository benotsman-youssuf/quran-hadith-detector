"""Offline Arabic Hadith detection with bundled or explicitly supplied resources."""

from ._convenience import annotate, clear_cache, detect, get_hadith
from .config import DetectorConfig
from .detector import HadithDetector
from .models import (
    Detection,
    HadithRecord,
    HadithReference,
    OmittedSourceToken,
    SourceOccurrence,
    WordAlignment,
)

__version__ = "0.2.0"

__all__ = [
    "__version__",
    "HadithDetector",
    "DetectorConfig",
    "detect",
    "annotate",
    "get_hadith",
    "clear_cache",
    "HadithRecord",
    "SourceOccurrence",
    "HadithReference",
    "Detection",
    "WordAlignment",
    "OmittedSourceToken",
]
