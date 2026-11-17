"""Download engine: paging, per-file downloads, workers, pause/stop, events."""
from __future__ import annotations

import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import requests

from .api import ApiClient, ApiError
from .backoff import Backoff, GiveUpClock
from .errors import PermanentError, TemporaryError, classify_exception
from .files import classify, dest_path, ext_from_url, is_complete
from .query import build_query
from .resilience import (
    Adaptive, ResilientClient, Stopped, ThrottleGate, check_file_reply,
)

KINDS = ("pictures", "gifs", "videos")
CHUNK = 64 * 1024
DOWNLOAD_TIMEOUT = (10, 15)
FILE_ATTEMPTS = 3
UNAVAILABLE_SLICE = 0.05   # seconds; the ruling allows at most 0.25
POLICIES = ("stop", "pause")


class Interrupted(Exception):
    """Stop or Pause hit a file mid-download. Not a failure, not a retry."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason   # "stop" | "pause"


def _abort_response(resp):
    """Close a response from another thread; harmless if done twice.

    shutdown() on the socket wakes a reader blocked in recv(); close() alone
    may not."""
    try:
        sock = resp.raw._fp.fp.raw._sock
        sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass
    try:
        resp.close()
    except Exception:
        pass


@dataclass
class JobConfig:
    include: list
    dest: Path
    exclude: list = field(default_factory=list)
    min_score: Optional[int] = None
    order: str = "newest"
    types: set = field(default_factory=lambda: {"pictures"})
    by_search: bool = True
    count: Optional[int] = None
    workers: int = 1
    page_size: int = 100
    safety: bool = False
    delay: float = 0.5
    unavailable_policy: str = "stop"
    auto_retry_seconds: Optional[float] = None

    def __post_init__(self):
        if self.unavailable_policy not in POLICIES:
            raise ValueError(f"unavailable_policy must be one of {POLICIES}")
        a = self.auto_retry_seconds
        if a is not None and (isinstance(a, bool) or not isinstance(a, (int, float))
                              or not a > 0):
            raise ValueError("auto_retry_seconds must be None or a number > 0")
        self.dest = Path(self.dest)
        self.types = set(self.types)
        if not self.types or not self.types <= set(KINDS):
            raise ValueError(f"types must be a non-empty subset of {KINDS}")
        if self.count is not None and (
            isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 1
        ):
            raise ValueError("count must be an int >= 1 (or None for all)")
        if isinstance(self.workers, bool) or not isinstance(self.workers, int) \
                or not 1 <= self.workers <= 100:
            raise ValueError("workers must be an int from 1 to 100")


@dataclass
class Summary:
    downloaded: int = 0
    existing: int = 0
    skipped_type: int = 0
    failed: int = 0
    pages: int = 0
    requests_made: int = 0
    reason: str = "end_of_results"
    total: Optional[int] = None
    error: Optional[str] = None
    error_kind: Optional[str] = None
    retries: int = 0
    waited_seconds: float = 0.0


class Job:
    def __init__(self, config: JobConfig, client: ApiClient, session=None,
                 on_event: Optional[Callable] = None, *,
                 gate: Optional[ThrottleGate] = None, adaptive: Optional[Adaptive] = None,
                 budget=None, backoff: Optional[Backoff] = None,
                 giveup: Optional[GiveUpClock] = None, retries: bool = False):
        self.config = config
        self.client = client
        self._retries = bool(retries)
        self._session = session
        self._on_event = on_event
        self._query = build_query(config.include, config.exclude, config.min_score,
                                  config.order, config.safety)
        self._stop = threading.Event()
        self._gate = threading.Event()   # set = running, clear = paused
        self._gate.set()
        self._cond = threading.Condition()
        self._local = threading.local()
        self._sessions = []
        self._in_flight = 0
        self._outstanding = 0
        self._retry_evt = threading.Event()
        self._active = set()             # responses being read, guarded by _active_lock
        self._active_lock = threading.Lock()
        self._pause_epoch = 0
        self._finished = False
        self.summary = Summary()
        self._rc = ResilientClient(
            client, gate=gate, budget=budget, backoff=backoff, giveup=giveup,
            adaptive=adaptive, stop=self._stop, emit=self._emit,
            retries_enabled=self._retries)

    @property
    def gate(self) -> ThrottleGate:
        return self._rc.gate

    @property
    def adaptive(self) -> Adaptive:
        return self._rc.adaptive

    @property
    def query(self) -> str:
        return self._query

    # -- control -------------------------------------------------------------
    def pause(self):
        if self._stop.is_set() or not self._gate.is_set():
            return
        self._gate.clear()
        with self._active_lock:
            self._pause_epoch += 1
        self._abort_active()
        self._emit("paused", {})

    def resume(self):
        if self._gate.is_set():
            return
        self._gate.set()
        self._emit("resumed", {})

    def retry_now(self):
        """Skip the current backoff / unavailable wait and try again at once."""
        self._rc.gate.retry_now()
        self._retry_evt.set()

    def stop(self):
        self._stop.set()
        self._gate.set()
        self._abort_active()
        with self._cond:
            self._cond.notify_all()

    def _abort_active(self):
        with self._active_lock:
            active = list(self._active)
        for resp in active:
            _abort_response(resp)

    # -- helpers -------------------------------------------------------------
    def _emit(self, kind, data):
        if kind in ("retry", "waiting"):
            with self._cond:
                if kind == "retry":
                    self.summary.retries += 1
                else:
                    self.summary.waited_seconds += float(data.get("seconds", 0.0))
        elif kind == "adaptive":
            with self._cond:
                self._cond.notify_all()
        if self._on_event is None:
            return
        try:
            self._on_event(kind, data)
        except Exception:
            pass

    def _safe(self, text) -> str:
        from .config import redact
        return redact(str(text), self.client.credentials.api_key)

    def _wait_gate(self) -> bool:
        """Block while paused; return False when stopped."""
        self._gate.wait()
        return not self._stop.is_set()

    def _get_session(self):
        if self._session is not None:
            return self._session
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            self._local.session = s
            with self._cond:
                self._sessions.append(s)
        return s

    # -- run -----------------------------------------------------------------
    def run(self) -> Summary:
        s = self.summary
        base_requests = self.client.requests_made
        executor = ThreadPoolExecutor(max_workers=self.config.workers)
        try:
            s.reason = self._run_inner(executor)
        except BaseException as exc:
            self.stop()
            s.reason = "error"
            s.error = self._safe(exc)
            s.error_kind = "unexpected"
            self._cleanup(executor)
            self._finish(base_requests)
            raise
        self._cleanup(executor)
        return self._finish(base_requests)

    def _cleanup(self, executor):
        executor.shutdown(wait=True)
        for sess in self._sessions:
            try:
                sess.close()
            except Exception:
                pass

    def _run_inner(self, executor) -> str:
        cfg, s = self.config, self.summary
        if self._stop.is_set() or not self._wait_gate():
            self._emit("started", {"query": self._query, "total": None})
            return "stopped"
        if cfg.count is None:
            try:
                s.total = self._rc.total_count(self._query)
            except Stopped:
                self._emit("started", {"query": self._query, "total": None})
                return "stopped"
            except ApiError:
                s.total = None
        self._emit("started", {"query": self._query, "total": s.total})
        return self._page_loop(executor)

    def _finish(self, base_requests):
        self.summary.requests_made = self.client.requests_made - base_requests
        with self._cond:
            if self._finished:
                return self.summary
            self._finished = True
        self._emit("finished", {"summary": self.summary})
        return self.summary

    def _page_loop(self, executor) -> str:
        cfg, s = self.config, self.summary
        seen = set()
        pid = 0
        while True:
            if not self._wait_gate():
                return "stopped"
            try:
                posts = self._rc.fetch_posts(self._query, cfg.page_size, pid)
            except Stopped:
                return "stopped"
            except PermanentError as e:
                s.error, s.error_kind = self._safe(e), e.kind
                return "permanent_error"
            except TemporaryError as e:
                self._retry_evt.clear()
                self._emit("server_unavailable", {
                    "message": self._safe(e), "policy": cfg.unavailable_policy,
                    "auto_retry_seconds": cfg.auto_retry_seconds})
                if cfg.unavailable_policy == "stop":
                    s.error, s.error_kind = self._safe(e), e.kind
                    return "server_unavailable"
                by = self._unavailable_wait()
                if by is None:
                    return "stopped"
                self._rc.giveup.success()
                self._rc.gate.retry_now()
                self._emit("unavailable_retry", {"by": by})
                continue
            except ApiError as e:
                s.error, s.error_kind = self._safe(e), "unknown"
                self._emit("permanent_error", {"kind": "unknown", "message": self._safe(e)})
                return "permanent_error"
            if not posts:
                return "end_of_results"
            s.pages += 1
            self._emit("page", {"pid": pid, "posts": len(posts)})
            for post in posts:
                outcome = self._handle_post(post, seen, executor)
                if outcome:
                    return outcome
            if len(posts) < cfg.page_size:
                return self._drain_reason("end_of_results")
            if cfg.count is not None and s.downloaded + self._in_flight >= cfg.count:
                reason = self._drain_reason("")
                if reason:
                    return reason
            pid += 1

    def _unavailable_wait(self) -> Optional[str]:
        """Block until retry_now / auto-retry (returns who) or stop (returns None)."""
        auto = self.config.auto_retry_seconds
        start = time.monotonic()
        while True:
            if self._stop.is_set():
                return None
            if self._retry_evt.is_set():
                return "retry_now"
            left = None if auto is None else auto - (time.monotonic() - start)
            if left is not None and left <= 0:
                return "auto_retry"
            step = UNAVAILABLE_SLICE if left is None else min(UNAVAILABLE_SLICE, left)
            self._retry_evt.wait(step)

    def _drain_reason(self, default: str) -> str:
        """Wait for in-flight work, then decide how the job ended."""
        cfg = self.config
        with self._cond:
            while self._outstanding > 0 and not self._stop.is_set():
                self._cond.wait()
            if cfg.count is not None and self.summary.downloaded >= cfg.count:
                return "count_reached"
        return "stopped" if self._stop.is_set() else default

    def _handle_post(self, post, seen, executor):
        """Process one post; return an end reason when the job should end."""
        cfg, s = self.config, self.summary
        key = str(post.get("id"))
        if key in seen:
            return None
        if cfg.count is not None and s.downloaded >= cfg.count:
            return "count_reached"
        seen.add(key)
        if not self._wait_gate():
            return "stopped"
        url = post.get("file_url") or ""
        ext = ext_from_url(url) if url else ""
        kind = classify(ext) if ext else None
        if kind is None or kind not in cfg.types:
            with self._cond:
                s.skipped_type += 1
            self._emit("file_skipped", {"id": post.get("id"), "ext": ext})
            return None
        dest = dest_path(cfg.dest, cfg.include, kind, post.get("id"), ext, cfg.by_search)
        if is_complete(dest):
            with self._cond:
                s.existing += 1
            self._emit("file_existing", {"id": post.get("id")})
            return None
        # reserve a slot (effective concurrency may shrink while we wait)
        with self._cond:
            while True:
                if self._stop.is_set():
                    return "stopped"
                if cfg.count is not None and s.downloaded >= cfg.count:
                    return "count_reached"
                room = cfg.count is None or self._in_flight + s.downloaded < cfg.count
                if room and self._outstanding < self._rc.adaptive.effective_workers(cfg.workers):
                    break
                self._cond.wait(0.5)
            self._in_flight += 1
            self._outstanding += 1
        fut = executor.submit(self._download_task, post.get("id"), url, kind, dest)
        fut.add_done_callback(self._task_done)
        return None

    def _task_done(self, _fut):
        with self._cond:
            self._outstanding -= 1
            self._cond.notify_all()

    # -- worker --------------------------------------------------------------
    def _release(self):
        with self._cond:
            self._in_flight -= 1
            self._cond.notify_all()

    def _file_error(self, exc: Exception) -> Exception:
        if isinstance(exc, ApiError):
            return exc
        return classify_exception(exc, self.client.credentials.api_key) or exc

    def _download_task(self, post_id, url, kind, dest):
        s = self.summary
        if not self._wait_gate():
            self._release()
            return
        attempt = 1
        while True:
            if not self._rc.gate.wait(self._stop):
                self._release()          # stopped while waiting: abandon, no counter
                return
            try:
                nbytes = self._download(url, dest)
                break
            except Interrupted as intr:
                # not a failure, not a retry; the .part stays on disk
                if intr.reason == "stop" or not self._wait_gate():
                    self._release()
                    return
                continue
            except Exception as exc:
                err = self._file_error(exc)
                if (self._retries and isinstance(err, TemporaryError)
                        and attempt < FILE_ATTEMPTS):
                    self._file_retry(post_id, err, attempt)
                    attempt += 1
                    continue
                text = self._safe(err)
                if self._retries and isinstance(err, TemporaryError):
                    text = f"after {FILE_ATTEMPTS} attempts: {text}"
                with self._cond:
                    s.failed += 1
                    self._in_flight -= 1
                    self._cond.notify_all()
                self._emit("file_failed", {"id": post_id, "error": text})
                return
        with self._cond:
            s.downloaded += 1
            self._in_flight -= 1
            self._cond.notify_all()
        self._emit("file_done", {"id": post_id, "type": kind, "path": str(dest), "bytes": nbytes})
        delay = self._rc.adaptive.effective_delay(self.config.delay)
        if delay > 0:
            self._stop.wait(delay)

    def _file_retry(self, post_id, err: TemporaryError, attempt: int):
        rc = self._rc
        delay = float(rc.backoff.delay(attempt, err.retry_after))
        if rc.adaptive.overload():
            self._emit("adaptive", {
                "level": rc.adaptive.level,
                "single_worker": rc.adaptive.level >= 1,
                "delay_factor": 2.0 ** rc.adaptive.level,
            })
        reason = "retry-after" if err.retry_after is not None else "backoff"
        rc.gate.trip(delay, reason)
        self._emit("retry", {"what": "file", "id": post_id, "attempt": attempt,
                             "error": self._safe(err)})
        self._emit("waiting", {"reason": reason, "seconds": delay, "attempt": attempt})

    def _interrupt_reason(self, epoch):
        if self._stop.is_set():
            return "stop"
        with self._active_lock:
            paused = self._pause_epoch != epoch
        if paused or not self._gate.is_set():
            return "pause"
        return None

    def _check_interrupt(self, epoch):
        reason = self._interrupt_reason(epoch)
        if reason:
            raise Interrupted(reason)

    def _download(self, url, dest: Path) -> int:
        part = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        keep = False
        try:
            with self._active_lock:
                epoch = self._pause_epoch
            self._check_interrupt(epoch)
            resp = self._get_session().get(url, stream=True, timeout=DOWNLOAD_TIMEOUT)
            with self._active_lock:
                self._active.add(resp)
            try:
                try:
                    self._check_interrupt(epoch)   # stop/pause raced the connect
                    check_file_reply(resp.status_code, getattr(resp, "headers", None))
                    total = 0
                    with open(part, "wb") as fh:
                        for chunk in resp.iter_content(CHUNK):
                            self._check_interrupt(epoch)
                            if chunk:
                                fh.write(chunk)
                                total += len(chunk)
                    self._check_interrupt(epoch)
                except Interrupted:
                    keep = True
                    raise
                except Exception:
                    # the read broke because stop/pause closed the response
                    reason = self._interrupt_reason(epoch)
                    if reason:
                        keep = True
                        raise Interrupted(reason)
                    raise
            finally:
                with self._active_lock:
                    self._active.discard(resp)
                _abort_response(resp)
            if total == 0:
                raise RuntimeError("empty download")
            os.replace(part, dest)
            return total
        except BaseException:
            if keep:
                raise
            try:
                part.unlink()
            except FileNotFoundError:
                pass
            raise
