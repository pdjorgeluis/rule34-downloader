"""File classification and folder layout (pure)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .query import normalize_tag

_KINDS = {
    "pictures": ("jpg", "jpeg", "png", "webp"),
    "gifs": ("gif",),
    "videos": ("mp4", "webm"),
}
_EXT_TO_KIND = {e: k for k, exts in _KINDS.items() for e in exts}


def classify(ext: str):
    e = ext.lower()
    if e.startswith("."):
        e = e[1:]
    return _EXT_TO_KIND.get(e)


def ext_from_url(url: str) -> str:
    path = url.split("#", 1)[0].split("?", 1)[0]
    if "://" in path:
        path = path.split("://", 1)[1]
        path = path.split("/", 1)[1] if "/" in path else ""
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[1].lower()


def search_folder_name(include: Iterable[str]) -> str:
    parts = [re.sub(r"[^a-z0-9_-]+", "_", normalize_tag(t)) for t in include]
    name = "+".join(parts)[:80]
    return name or "search"


def dest_path(base, include, kind, post_id, ext, by_search=True) -> Path:
    if kind not in _KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    if classify(ext) != kind:
        raise ValueError(f"extension {ext!r} does not belong to {kind!r}")
    e = ext.lower().lstrip(".")
    root = Path(base)
    if by_search:
        root = root / search_folder_name(include)
    return root / kind / f"{post_id}.{e}"


def is_complete(path) -> bool:
    p = Path(path)
    return p.is_file() and not p.name.endswith(".part") and p.stat().st_size > 0
