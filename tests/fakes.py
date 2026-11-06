"""Shared offline fixtures: a fake clock and a waiter that advances it."""
from __future__ import annotations

import threading
from typing import Callable, Optional


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.now += seconds


def fake_waiter(clock: FakeClock) -> Callable[[float, Optional[threading.Event]], bool]:
    def waiter(seconds: float, stop: Optional[threading.Event]) -> bool:
        if stop is not None and stop.is_set():
            return False
        clock.advance(seconds)
        return True
    return waiter
