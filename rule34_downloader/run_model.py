"""Qt-free run model: turns engine events into counters, a state and log lines.

Pure standard library. The GUI runner feeds every engine event through
``apply_event``; panels read ``RunStats`` and the helpers below.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

STATES = ("idle", "running", "waiting", "paused", "unavailable", "stopping", "finished")
LOG_MAX_LINES = 500
LOG_MAX_CHARS = 300


@dataclass
class RunStats:
    state: str = "idle"
    query: str = ""
    total: Optional[int] = None
    downloaded: int = 0
    existing: int = 0
    skipped: int = 0
    failed: int = 0
    pages: int = 0
    retries: int = 0
    bytes_done: int = 0
    waited_seconds: float = 0.0
    adaptive_level: int = 0
    wait_seconds: float = 0.0
    wait_reason: str = ""
    unavailable_message: str = ""
    summary: object = None
    log: deque = field(default_factory=lambda: deque(maxlen=LOG_MAX_LINES))


# -- small helpers -------------------------------------------------------------

def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _num(v) -> float:
    """A real number or 0 (bool, text, None and non-finite values count as 0)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0
    if v != v or v in (float("inf"), float("-inf")):
        return 0
    return v


def _text(v) -> str:
    return "None" if v is None else str(v)


def _clip(line: str) -> str:
    return line if len(line) <= LOG_MAX_CHARS else line[:LOG_MAX_CHARS - 1] + "…"


def _go(stats: RunStats, new: str, only_from=None) -> None:
    """Change state unless stopping; with ``only_from`` only from those states."""
    if stats.state in ("stopping", "finished"):
        return
    if only_from is not None and stats.state not in only_from:
        return
    stats.state = new


def _apply(stats: RunStats, kind, d: dict) -> list:
    out = []
    if kind == "started":
        stats.query = _text(d.get("query")) if d.get("query") is not None else ""
        t = d.get("total")
        stats.total = t if _is_int(t) and t > 0 else None
        _go(stats, "running")
        out.append(f"Searching: {_text(d.get('query'))}"
                   + (f" ({stats.total} posts)" if stats.total is not None else ""))
    elif kind == "page":
        stats.pages += 1
    elif kind == "file_done":
        b = d.get("bytes")
        b = b if _is_int(b) and b >= 0 else 0
        stats.downloaded += 1
        stats.bytes_done += b
        _go(stats, "running", ("waiting",))
        out.append(f"Saved {_text(d.get('id'))} ({_text(d.get('type'))}, {b // 1024} KB)")
    elif kind == "file_existing":
        stats.existing += 1
    elif kind == "file_skipped":
        stats.skipped += 1
    elif kind == "file_failed":
        stats.failed += 1
        out.append(f"Failed {_text(d.get('id'))}: {_text(d.get('error'))}")
    elif kind == "retry":
        stats.retries += 1
        ident = d.get("id") if d.get("what") == "file" else d.get("pid")
        out.append(f"Retry {_text(d.get('what'))} {_text(ident)} "
                   f"(attempt {_text(d.get('attempt'))}): {_text(d.get('error'))}")
    elif kind == "waiting":
        secs = _num(d.get("seconds"))
        stats.wait_seconds = secs
        stats.wait_reason = _text(d.get("reason"))
        if stats.state not in ("paused", "unavailable"):
            _go(stats, "waiting")
        out.append(f"Waiting {int(secs)} s ({_text(d.get('reason'))})")
    elif kind == "adaptive":
        lv = d.get("level")
        stats.adaptive_level = lv if _is_int(lv) else 0
        out.append(f"Slowing down: level {_text(lv)}")
    elif kind == "paused":
        _go(stats, "paused")
        out.append("Paused")
    elif kind == "resumed":
        _go(stats, "running", ("paused",))
        out.append("Resumed")
    elif kind == "server_unavailable":
        stats.unavailable_message = _text(d.get("message"))
        _go(stats, "unavailable")
        out.append(f"Server unavailable: {_text(d.get('message'))}")
    elif kind == "unavailable_retry":
        _go(stats, "running", ("unavailable",))
        out.append(f"Retrying ({_text(d.get('by'))})")
    elif kind == "permanent_error":
        out.append(f"Error ({_text(d.get('kind'))}): {_text(d.get('message'))}")
    elif kind == "finished":
        stats.summary = d.get("summary")
        stats.state = "finished"
        out.append("Finished")
    return out


_KINDS = frozenset((
    "started", "page", "file_done", "file_existing", "file_skipped", "file_failed", "retry",
    "waiting", "adaptive", "paused", "resumed", "server_unavailable", "unavailable_retry",
    "permanent_error", "finished"))


def apply_event(stats: RunStats, kind, data) -> list:
    """Apply one engine event; return the new log lines. Never raises."""
    try:
        if not isinstance(data, dict) or kind not in _KINDS or stats.state == "finished":
            return []
        lines = [_clip(x) for x in _apply(stats, kind, data)]
        stats.log.extend(lines)
        return lines
    except Exception:
        return []


def mark_stopping(stats: RunStats) -> None:
    if stats.state not in ("finished", "idle"):
        stats.state = "stopping"


def progress_fraction(stats: RunStats, count) -> Optional[float]:
    done = stats.downloaded + stats.existing
    if _is_int(count) and count > 0:
        return min(1.0, done / count)
    t = stats.total
    if _is_int(t) and t > 0:
        return min(1.0, (done + stats.skipped + stats.failed) / t)
    return None


_ACTIONS = {
    "idle": {"start"},
    "running": {"pause", "stop"},
    "waiting": {"pause", "retry_now", "stop"},
    "paused": {"resume", "stop"},
    "unavailable": {"retry_now", "stop"},
    "stopping": set(),
    "finished": {"start"},
}


def enabled_actions(state) -> frozenset:
    try:
        return frozenset(_ACTIONS.get(state, ()))
    except TypeError:
        return frozenset()


def finish_headline(summary) -> tuple:
    """(text, is_error) describing how a run ended."""
    g = lambda name, default=None: getattr(summary, name, default)  # noqa: E731
    reason, error = g("reason"), g("error")
    base = (f"{g('downloaded', 0)} downloaded, {g('existing', 0)} already there, "
            f"{g('skipped_type', 0)} skipped, {g('failed', 0)} failed")
    if reason in ("count_reached", "end_of_results"):
        return "Finished: " + base, False
    if reason == "stopped":
        return "Stopped: " + base, False
    if reason == "permanent_error":
        return f"Stopped by an error ({g('error_kind')}): {error}", True
    if reason == "server_unavailable":
        return f"Server unavailable: {error}", True
    if error:
        return f"Unexpected error: {error}", True
    return f"Unexpected end: {reason}", True


def budget_snapshot(budget) -> tuple:
    """(used, limit) of an API budget; (0, 0) when missing or broken."""
    if budget is None:
        return 0, 0
    try:
        return int(budget.used()), int(budget.limit)
    except Exception:
        return 0, 0
