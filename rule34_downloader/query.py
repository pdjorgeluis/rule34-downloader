"""Query builder for tag searches (pure, offline)."""
from __future__ import annotations

import re
from typing import Iterable

SAFETY_EXCLUDE = ("loli", "shota", "child", "toddler", "underage")
_FORBIDDEN_CHARS = "~(),"
_MANAGED_PREFIXES = ("rating:", "sort:", "score:")


def normalize_tag(t: str) -> str:
    tag = re.sub(r"\s+", "_", t.strip().lower())
    if not tag:
        raise ValueError("empty tag")
    if any(c in tag for c in _FORBIDDEN_CHARS):
        raise ValueError(f"tag {tag!r} contains one of ~ ( ) , (OR syntax is not supported)")
    if tag.startswith("-"):
        raise ValueError(f"tag {tag!r} starts with '-': use the exclude list instead")
    if tag.startswith(_MANAGED_PREFIXES):
        raise ValueError(
            f"tag {tag!r}: rating:/sort:/score: are managed by other parameters or dropped from v1"
        )
    return tag


def _norm_unique(tags: Iterable[str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        n = normalize_tag(t)
        if n not in out:
            out.append(n)
    return out


def build_query(include, exclude=(), min_score=None, order="newest", safety=False) -> str:
    inc = _norm_unique(include)
    if not inc:
        raise ValueError("include must contain at least one tag")
    exc = _norm_unique(exclude)
    overlap = [t for t in inc if t in exc]
    if overlap:
        raise ValueError(f"tag in both include and exclude: {overlap[0]}")
    if order not in ("newest", "oldest"):
        raise ValueError(f"order must be 'newest' or 'oldest', got {order!r}")
    if min_score is not None and (
        isinstance(min_score, bool) or not isinstance(min_score, int) or min_score < 0
    ):
        raise ValueError("min_score must be an int >= 0")
    if safety:
        exc += [t for t in SAFETY_EXCLUDE if t not in exc]
    parts = list(inc) + ["-" + t for t in exc]
    if min_score is not None:
        parts.append(f"score:>={min_score}")
    if order == "oldest":
        parts.append("sort:id:asc")
    return " ".join(parts)


def parse_tag_text(text: str) -> list[str]:
    return [p for p in re.split(r"[\s,]+", text) if p]
