"""Download form model: values, validation, previews, JobConfig, preferences (pure, no Qt)."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import files, query
from .engine import JobConfig

KINDS = ("pictures", "gifs", "videos")
ORDERS = ("newest", "oldest")
COUNT_MODES = ("number", "all")
_DEFAULT_TYPES = frozenset({"pictures"})


@dataclass
class FormValues:
    include_text: str = ""
    exclude_text: str = ""
    min_score: int | None = None
    order: str = "newest"
    types: frozenset = _DEFAULT_TYPES
    count_mode: str = "number"
    count: int = 100
    folder: str = ""
    by_search: bool = True
    workers: int = 3
    safety: bool = False


def default_folder() -> str:
    return str(Path.home() / "Rule34Downloads")


def parse_tags(text: str) -> list[str]:
    return query.parse_tag_text(text)


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _build(v: FormValues) -> str:
    return query.build_query(parse_tags(v.include_text), parse_tags(v.exclude_text),
                             v.min_score, v.order, v.safety)


def validate(v: FormValues) -> list[str]:
    if not parse_tags(v.include_text):
        return ["Enter at least one tag."]
    try:
        _build(v)
    except ValueError as exc:
        return [str(exc)]
    types = v.types
    try:
        bad_types = not types or not set(types) <= set(KINDS)
    except TypeError:
        bad_types = True
    if bad_types:
        return ["Choose at least one file type."]
    if not _is_int(v.workers) or not 1 <= v.workers <= 5:
        return ["Workers must be between 1 and 5."]
    if v.count_mode == "number" and (not _is_int(v.count) or v.count < 1):
        return ["The number of files must be at least 1."]
    if not v.folder or not v.folder.strip():
        return ["Choose a folder."]
    p = Path(v.folder).expanduser()
    if p.exists() and not p.is_dir():
        return ["That folder path is a file."]
    return []


def query_preview(v: FormValues) -> str:
    try:
        return _build(v)
    except ValueError:
        return ""


def dest_preview(v: FormValues) -> str:
    try:
        include = parse_tags(v.include_text)
        if not include or not v.folder or not v.folder.strip():
            return ""
        base = Path(v.folder).expanduser()
        if v.by_search:
            base = base / files.search_folder_name(include)
        return str(base)
    except (ValueError, RuntimeError, OSError):
        return ""


def to_job_config(v: FormValues) -> JobConfig:
    errors = validate(v)
    if errors:
        raise ValueError(errors[0])
    count = None if v.count_mode == "all" else v.count
    return JobConfig(
        include=parse_tags(v.include_text),
        exclude=parse_tags(v.exclude_text),
        min_score=v.min_score,
        order=v.order,
        types=set(v.types),
        by_search=v.by_search,
        workers=v.workers,
        safety=v.safety,
        dest=Path(v.folder).expanduser(),
        count=count,
        page_size=1000 if count is None else min(1000, count),
        unavailable_policy="pause",
        auto_retry_seconds=300.0,
    )


_BAD_NAME_CHARS = set('/\\:*?"<>|')


def valid_new_folder_name(name) -> str | None:
    if not isinstance(name, str):
        return None
    n = name.strip()
    if not n or len(n) > 80 or n in (".", ".."):
        return None
    if any(c in _BAD_NAME_CHARS or ord(c) < 32 or ord(c) == 127 for c in n):
        return None
    if n.endswith((".", " ")):
        return None
    return n


# ---- preferences -----------------------------------------------------------

def prefs_to_dict(v: FormValues) -> dict:
    return {
        "order": v.order,
        "types": sorted(v.types),
        "count_mode": v.count_mode,
        "count": v.count,
        "folder": v.folder,
        "by_search": v.by_search,
        "workers": v.workers,
        "safety": v.safety,
        "min_score": v.min_score,
    }


def prefs_from_dict(d) -> FormValues:
    out = FormValues(folder=default_folder())
    if not isinstance(d, dict):
        return out
    if d.get("order") in ORDERS:
        out.order = d["order"]
    t = d.get("types")
    if isinstance(t, (list, tuple)) and t and all(isinstance(x, str) and x in KINDS for x in t):
        out.types = frozenset(t)
    if d.get("count_mode") in COUNT_MODES:
        out.count_mode = d["count_mode"]
    c = d.get("count")
    if _is_int(c) and c >= 1:
        out.count = c
    f = d.get("folder")
    if isinstance(f, str) and f.strip():
        out.folder = f
    for key in ("by_search", "safety"):
        if isinstance(d.get(key), bool):
            setattr(out, key, d[key])
    w = d.get("workers")
    if _is_int(w) and 1 <= w <= 5:
        out.workers = w
    m = d.get("min_score")
    if _is_int(m) and 0 <= m <= 99999:
        out.min_score = m
    return out


def load_prefs(path) -> FormValues:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    return prefs_from_dict(data)


def save_prefs(v: FormValues, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(prefs_to_dict(v), indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            if os.name == "posix":
                os.fchmod(fh.fileno(), 0o600)
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
