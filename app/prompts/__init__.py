"""Load versioned UTF-8 prompt resources independently of the working directory."""

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Load a bundled Markdown prompt; edits take effect after process restart."""
    if name not in {"assistant.md", "classification.md", "pdf_answer.md"}:
        raise ValueError(f"Unknown prompt resource: {name}")
    content = (Path(__file__).resolve().parent / name).read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"Empty prompt resource: {name}")
    return content
