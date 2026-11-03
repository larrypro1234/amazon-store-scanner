"""The well-known brands list: brands.txt (comes with the app) plus my-brands.txt (yours, kept on updates)."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

HERE = Path(__file__).parent
BUILT_IN = HERE / "brands.txt"
MINE = HERE / "my-brands.txt"  # not part of the app's code, so updates never overwrite it


def tokens(name: str) -> tuple[str, ...]:
    """'L’Oréal Paris' -> ('loreal', 'paris'): accents, apostrophes and punctuation don't matter."""
    plain = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    plain = re.sub(r"['’`]", "", plain).replace("&", " and ").replace("+", " and ")
    return tuple(re.sub(r"[^a-z0-9]+", " ", plain).split())


def read_list(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [l.strip() for l in lines if l.strip() and not l.lstrip().startswith("#")]


def load() -> set[tuple[str, ...]]:
    return {t for t in map(tokens, read_list(BUILT_IN) + read_list(MINE)) if t}


def is_well_known(brand: str, known: set[tuple[str, ...]] | None = None) -> bool:
    """True when the brand is on the list, or starts with a listed brand ("L'Oreal Paris" -> "L'Oreal")."""
    known = load() if known is None else known
    t = tokens(brand)
    return any(t[:n] in known for n in range(1, len(t) + 1))


def save_mine(text: str) -> int:
    brands = [l.strip() for l in text.splitlines() if l.strip()]
    MINE.write_text("# Your extra well-known brands, one per line.\n" + "\n".join(brands) + "\n", encoding="utf-8")
    return len(brands)
