"""Backoff and give-up arithmetic (pure; no sleeping, no I/O)."""
from __future__ import annotations

import random
import threading
import time
from typing import Callable, Optional


class Backoff:
    def __init__(self, base: float = 5.0, factor: float = 2.0, cap: float = 300.0,
                 jitter: float = 0.2, rng: Optional[random.Random] = None):
        if base <= 0 or factor < 1 or cap < base or not (0 <= jitter <= 1):
            raise ValueError("invalid backoff parameters")
        self.base, self.factor, self.cap, self.jitter = base, factor, cap, jitter
        self.rng = rng if rng is not None else random.Random()

    def delay(self, attempt: int, retry_after: Optional[float] = None) -> float:
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ValueError("attempt must be an int >= 1")
        if retry_after is not None:
            return retry_after
        value = self.base * self.factor ** min(attempt - 1, 64)
        if self.jitter:
            value *= self.rng.uniform(1 - self.jitter, 1 + self.jitter)
        return min(self.cap, value)


class GiveUpClock:
    def __init__(self, window: float = 1800.0, clock: Callable[[], float] = time.monotonic):
        if window <= 0:
            raise ValueError("window must be > 0")
        self.window, self._clock = window, clock
        self._start: Optional[float] = None
        self._lock = threading.Lock()

    def failure(self) -> None:
        with self._lock:
            if self._start is None:
                self._start = self._clock()

    def success(self) -> None:
        with self._lock:
            self._start = None

    def elapsed(self) -> float:
        with self._lock:
            return 0.0 if self._start is None else self._clock() - self._start

    def expired(self) -> bool:
        return self.elapsed() >= self.window
