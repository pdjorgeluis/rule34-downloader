"""Resilience layer: shared throttle gate, adaptive slow-down, retrying client.

Standard library plus this package only (no network code lives here).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Mapping, Optional

from .backoff import Backoff, GiveUpClock
from .budget import default_waiter
from .errors import (
    PermanentError, ServerUnavailable, TemporaryError, parse_retry_after,
)


class Stopped(Exception):
    """The stop event was set while the client was waiting."""


class StopEvent(threading.Event):
    """An Event that can wake other waiters when it is set.

    A thread blocked on its own private wake-up Event registers it here with
    add_listener(); set() sets every registered listener, so one blocking wait
    covers "deadline, some other signal, or stop" without polling."""

    def __init__(self):
        super().__init__()
        self._listeners = []
        self._listeners_lock = threading.Lock()

    def add_listener(self, wake: threading.Event) -> None:
        with self._listeners_lock:
            self._listeners.append(wake)
        if self.is_set():              # set() may have run before we registered
            wake.set()

    def remove_listener(self, wake: threading.Event) -> None:
        with self._listeners_lock:
            try:
                self._listeners.remove(wake)
            except ValueError:
                pass

    def set(self) -> None:
        super().set()
        with self._listeners_lock:
            listeners = list(self._listeners)
        for wake in listeners:
            wake.set()


class ThrottleGate:
    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 waiter: Optional[Callable[[float, Optional[threading.Event]], bool]] = None,
                 slice_seconds: float = 0.25):
        self._clock = clock
        self._waiter = waiter or default_waiter
        # only used when `stop` is a plain threading.Event (cannot wake us):
        # then the wait falls back to slices of this length. A StopEvent never polls.
        self.slice_seconds = slice_seconds
        self._lock = threading.Lock()
        self._not_before = 0.0
        self._wakes = set()          # private wake-up events of blocked waiters
        self.last_reason = ""

    def _wake_all(self) -> None:     # caller holds self._lock
        for wake in self._wakes:
            wake.set()

    def trip(self, seconds: float, reason: str = "") -> None:
        if seconds <= 0:
            return
        with self._lock:
            until = self._clock() + seconds
            if until > self._not_before:
                self._not_before = until
                self.last_reason = reason
                self._wake_all()     # waiters re-read the longer deadline

    def remaining(self) -> float:
        with self._lock:
            return max(0.0, self._not_before - self._clock())

    def retry_now(self) -> None:
        with self._lock:
            self._not_before = 0.0
            self._wake_all()

    def wait(self, stop: Optional[threading.Event] = None) -> bool:
        """Block until the deadline passes (True), or stop is set (False).
        A retry_now or a later, longer trip wakes the waiter to re-check."""
        if stop is not None and not isinstance(stop, StopEvent):
            return self._wait_polling(stop)
        wake = threading.Event()
        with self._lock:
            self._wakes.add(wake)
        if stop is not None:
            stop.add_listener(wake)
        try:
            while True:
                wake.clear()                 # clear first, then check: no lost wake-up
                left = self.remaining()
                if left <= 0:
                    return True
                if stop is not None and stop.is_set():
                    return False
                self._waiter(left, wake)
        finally:
            if stop is not None:
                stop.remove_listener(wake)
            with self._lock:
                self._wakes.discard(wake)

    def _wait_polling(self, stop: threading.Event) -> bool:
        while True:
            left = self.remaining()
            if left <= 0:
                return True
            if stop.is_set():
                return False
            if not self._waiter(min(left, self.slice_seconds), stop):
                return False


class StartLimiter:
    """One shared pace for file starts: at least `interval` seconds between
    two granted starts, whichever worker asks. Starts are handed out in the
    order of arrival by reserving the next slot."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 waiter: Optional[Callable[[float, Optional[threading.Event]], bool]] = None):
        self._clock = clock
        self._waiter = waiter or default_waiter
        self._lock = threading.Lock()
        self._last: Optional[float] = None      # start time of the latest reserved slot

    def acquire(self, stop: Optional[threading.Event], interval: float) -> bool:
        if stop is not None and stop.is_set():
            return False
        with self._lock:
            now = self._clock()
            slot = now if self._last is None else max(now, self._last + interval)
            self._last = slot
        left = slot - now
        if left <= 0:
            return True
        wake = threading.Event()
        listening = isinstance(stop, StopEvent)
        if listening:
            stop.add_listener(wake)
        try:
            while True:
                wake.clear()                 # clear first, then check: no lost wake-up
                if stop is not None and stop.is_set():
                    return False
                left = slot - self._clock()
                if left <= 0:
                    return True
                if listening or stop is None:
                    self._waiter(left, wake)
                elif not self._waiter(left, stop):    # plain Event: legacy path
                    return False
        finally:
            if listening:
                stop.remove_listener(wake)


class Adaptive:
    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 recover_after: float = 600.0, max_level: int = 3,
                 base_delay: float = 0.5, debounce: float = 1.0):
        self._clock = clock
        self.recover_after, self.max_level = recover_after, max_level
        self.base_delay, self.debounce = base_delay, debounce
        self._lock = threading.Lock()
        self._level = 0
        self._last: Optional[float] = None

    def _recover(self) -> None:
        while (self._level > 0 and self._last is not None
               and self._clock() - self._last >= self.recover_after):
            self._level -= 1
            self._last += self.recover_after

    @property
    def level(self) -> int:
        with self._lock:
            self._recover()
            return self._level

    def overload(self) -> bool:
        with self._lock:
            self._recover()
            now = self._clock()
            debounced = self._last is not None and now - self._last < self.debounce
            self._last = now
            if debounced or self._level >= self.max_level:
                return False
            self._level += 1
            return True

    def effective_workers(self, configured: int) -> int:
        return configured if self.level == 0 else 1

    def effective_delay(self, configured: float) -> float:
        level = self.level
        if level == 0:
            return float(configured)
        return float(max(configured, self.base_delay) * 2 ** level)


def check_file_reply(status: int, headers: Optional[Mapping] = None) -> None:
    """Classify a file-download status (helper only; no retry logic here)."""
    if 200 <= status < 300:
        return None
    message = f"HTTP {status}"
    if status == 429:
        retry_after = None
        for k, v in (headers or {}).items():
            if str(k).lower() == "retry-after":
                retry_after = parse_retry_after(v)
                break
        raise TemporaryError(message, status=status, retry_after=retry_after, kind="http")
    if status in (408, 425) or status >= 500:
        raise TemporaryError(message, status=status, kind="http")
    raise PermanentError(message, status=status, kind="client")


class ResilientClient:
    """Retrying wrapper around an API client (page fetches only)."""

    def __init__(self, client: Any, gate: Optional[ThrottleGate] = None, budget: Any = None,
                 backoff: Optional[Backoff] = None, giveup: Optional[GiveUpClock] = None,
                 adaptive: Optional[Adaptive] = None, stop: Optional[threading.Event] = None,
                 emit: Optional[Callable[[str, dict], None]] = None,
                 retries_enabled: bool = True):
        self._client = client
        self.gate = gate if gate is not None else ThrottleGate()
        self.budget = budget
        self.backoff = backoff if backoff is not None else Backoff()
        self.giveup = giveup if giveup is not None else GiveUpClock()
        self.adaptive = adaptive if adaptive is not None else Adaptive()
        self.stop = stop
        self._emit = emit
        self.retries_enabled = retries_enabled

    @property
    def credentials(self):
        return self._client.credentials

    @property
    def requests_made(self):
        return self._client.requests_made

    def _emit_safe(self, kind: str, data: dict) -> None:
        if self._emit is None:
            return
        try:
            self._emit(kind, data)
        except Exception:
            pass

    def _before_request(self, attempt: int) -> None:
        if not self.gate.wait(self.stop):
            raise Stopped("stopped while waiting on the throttle gate")
        if self.budget is not None:
            if self.budget.remaining() == 0:
                self._emit_safe("waiting", {"reason": "budget",
                                            "seconds": float(self.budget.next_available_in()),
                                            "attempt": attempt})
            if not self.budget.acquire(self.stop):
                raise Stopped("stopped while waiting on the request budget")

    def fetch_posts(self, query: str, page_size: int, pid: int) -> list:
        attempt = 1
        while True:
            self._before_request(attempt)
            try:
                posts = self._client.fetch_posts(query, page_size, pid)
            except TemporaryError as e:
                if not self.retries_enabled:
                    raise
                self.giveup.failure()
                if self.giveup.expired():
                    raise ServerUnavailable(
                        f"server unavailable after {int(self.giveup.elapsed())}s: {e}",
                        status=e.status) from e
                delay = float(self.backoff.delay(attempt, e.retry_after))
                if self.adaptive.overload():
                    self._emit_safe("adaptive", {
                        "level": self.adaptive.level,
                        "single_worker": self.adaptive.level >= 1,
                        "delay_factor": 2.0 ** self.adaptive.level,
                    })
                reason = "retry-after" if e.retry_after is not None else "backoff"
                self.gate.trip(delay, reason)
                self._emit_safe("retry", {"what": "page", "pid": pid, "attempt": attempt,
                                          "error": str(e)})
                self._emit_safe("waiting", {"reason": reason, "seconds": delay,
                                            "attempt": attempt})
                attempt += 1
                continue
            except PermanentError as e:
                self._emit_safe("permanent_error", {"kind": e.kind, "message": str(e)})
                raise
            self.giveup.success()
            return posts

    def total_count(self, query: str) -> int:
        self._before_request(1)
        return self._client.total_count(query)
