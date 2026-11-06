"""Download engine: paging, per-file downloads, workers, pause/stop, events."""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import requests

from .api import ApiClient, ApiError
from .files import classify, dest_path, ext_from_url, is_complete
from .query import build_query

KINDS = ("pictures", "gifs", "videos")
CHUNK = 64 * 1024
DOWNLOAD_TIMEOUT = (10, 60)


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

    def __post_init__(self):
        self.dest = Path(self.dest)
        self.types = set(self.types)
        if not self.types or not self.types <= set(KINDS):
            raise ValueError(f"types must be a non-empty subset of {KINDS}")
        if self.count is not None and (
            isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 1
        ):
            raise ValueError("count must be an int >= 1 (or None for all)")
        if isinstance(self.workers, bool) or not isinstance(self.workers, int) \
                or not 1 <= self.workers <= 5:
            raise ValueError("workers must be an int from 1 to 5")


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


class Job:
    def __init__(self, config: JobConfig, client: ApiClient, session=None,
                 on_event: Optional[Callable] = None):
        self.config = config
        self.client = client
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
        self.summary = Summary()

    @property
    def query(self) -> str:
        return self._query

    # -- control -------------------------------------------------------------
    def pause(self):
        if self._stop.is_set() or not self._gate.is_set():
            return
        self._gate.clear()
        self._emit("paused", {})

    def resume(self):
        if self._gate.is_set():
            return
        self._gate.set()
        self._emit("resumed", {})

    def stop(self):
        self._stop.set()
        self._gate.set()
        with self._cond:
            self._cond.notify_all()

    # -- helpers -------------------------------------------------------------
    def _emit(self, kind, data):
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
        cfg, s = self.config, self.summary
        base_requests = self.client.requests_made
        executor = ThreadPoolExecutor(max_workers=cfg.workers)
        try:
            if self._stop.is_set():
                self._emit("started", {"query": self._query, "total": None})
                s.reason = "stopped"
                return self._finish(base_requests)
            if not self._wait_gate():
                self._emit("started", {"query": self._query, "total": None})
                s.reason = "stopped"
                return self._finish(base_requests)
            if cfg.count is None:
                try:
                    s.total = self.client.total_count(self._query)
                except ApiError:
                    s.total = None
            self._emit("started", {"query": self._query, "total": s.total})
            s.reason = self._page_loop(executor)
        finally:
            executor.shutdown(wait=True)
            for sess in self._sessions:
                try:
                    sess.close()
                except Exception:
                    pass
        return self._finish(base_requests)

    def _finish(self, base_requests):
        self.summary.requests_made = self.client.requests_made - base_requests
        self._emit("finished", {"summary": self.summary})
        return self.summary

    def _page_loop(self, executor) -> str:
        cfg, s = self.config, self.summary
        seen = set()
        pid = 0
        while True:
            if not self._wait_gate():
                return "stopped"
            posts = self.client.fetch_posts(self._query, cfg.page_size, pid)
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
        # reserve a slot
        with self._cond:
            while True:
                if self._stop.is_set():
                    return "stopped"
                if cfg.count is not None and s.downloaded >= cfg.count:
                    return "count_reached"
                room = cfg.count is None or self._in_flight + s.downloaded < cfg.count
                if room and self._outstanding < cfg.workers:
                    break
                self._cond.wait()
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
    def _download_task(self, post_id, url, kind, dest):
        s = self.summary
        if not self._wait_gate():
            with self._cond:
                self._in_flight -= 1
                self._cond.notify_all()
            return
        try:
            nbytes = self._download(url, dest)
        except Exception as exc:
            with self._cond:
                s.failed += 1
                self._in_flight -= 1
                self._cond.notify_all()
            self._emit("file_failed", {"id": post_id, "error": self._safe(exc)})
            return
        with self._cond:
            s.downloaded += 1
            self._in_flight -= 1
            self._cond.notify_all()
        self._emit("file_done", {"id": post_id, "type": kind, "path": str(dest), "bytes": nbytes})
        if self.config.delay > 0:
            self._stop.wait(self.config.delay)

    def _download(self, url, dest: Path) -> int:
        part = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            resp = self._get_session().get(url, stream=True, timeout=DOWNLOAD_TIMEOUT)
            try:
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                total = 0
                with open(part, "wb") as fh:
                    for chunk in resp.iter_content(CHUNK):
                        if chunk:
                            fh.write(chunk)
                            total += len(chunk)
            finally:
                resp.close()
            if total == 0:
                raise RuntimeError("empty download")
            os.replace(part, dest)
            return total
        except BaseException:
            try:
                part.unlink()
            except FileNotFoundError:
                pass
            raise
