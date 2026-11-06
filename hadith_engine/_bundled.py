"""Resolve preinstalled resources without extraction, downloads or construction."""

from pathlib import Path


def directory(index_dir: str | Path | None) -> Path:
    if index_dir is not None:
        return Path(index_dir).resolve()
    bundled = Path(__file__).resolve().parent / "_data"
    if not (bundled / "manifest.json").is_file():
        raise FileNotFoundError(
            "Bundled Hadith resources are missing. Build the index with "
            "python -m tools.build_index, or pass index_dir explicitly."
        )
    return bundled
