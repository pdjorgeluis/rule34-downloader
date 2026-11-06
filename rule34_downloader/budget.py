"""Persisted rolling request budget (pure stdlib; clock and waiter injectable)."""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional


def state_path(env=None, platform=None) -> Path:
    env = os.environ if env is None else env
    if env.get("R34_STATE"):
        return Path(env["R34_STATE"])
    if (platform or os.name) == "nt":
        base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Rule34Downloader" / "state.json"
    xdg = env.get("XDG_STATE_HOME")
    root = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return root / "rule34-downloader" / "state.json"


def default_waiter(seconds: float, stop: Optional[threading.Event]) -> bool:
    if stop is None:
        time.sleep(seconds)
        return True
    return not stop.wait(seconds)


class RequestBudget:
    def __init__(self, limit: int = 55, window: float = 3600.0, path=None,
                 clock: Callable[[], float] = time.time,
                 waiter: Optional[Callable[[float, Optional[threading.Event]], bool]] = None):
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self.limit, self.window = limit, window
        self.path = Path(path) if path is not None else state_path()
        self._clock = clock
        self._waiter = waiter or default_waiter
        self._lock = threading.Lock()

    def _load(self) -> List[float]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            raw = data["timestamps"]
            if not isinstance(raw, list):
                return []
        except (OSError, ValueError, KeyError, TypeError):
            return []
        now = self._clock()
        out = []
        for t in raw:
            if isinstance(t, bool) or not isinstance(t, (int, float)) or math.isnan(t):
                continue
            if 0 <= now - t < self.window:
                out.append(float(t))
        return out

    def _save(self, stamps: List[float]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"timestamps": stamps}, f)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def used(self) -> int:
        with self._lock:
            return len(self._load())

    def remaining(self) -> int:
        return max(0, self.limit - self.used())

    def next_available_in(self) -> float:
        with self._lock:
            return self._next_in(self._load())

    def _next_in(self, stamps: List[float]) -> float:
        if len(stamps) < self.limit:
            return 0.0
        return max(0.0, min(stamps) + self.window - self._clock())

    def acquire(self, stop: Optional[threading.Event] = None) -> bool:
        while True:
            with self._lock:
                stamps = self._load()
                if len(stamps) < self.limit:
                    stamps.append(self._clock())
                    self._save(stamps)
                    return True
                wait = self._next_in(stamps)
            if not self._waiter(wait, stop):
                return False
