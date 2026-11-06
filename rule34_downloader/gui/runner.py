"""Thread bridge: runs an engine ``Job`` on its own thread, reports on the GUI thread.

No widgets. Engine events (emitted from engine threads) are forwarded through a
queued Qt signal, so every ``RunStats`` mutation and every public signal happens
on the GUI thread.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QCoreApplication, QObject, Qt, QThread, Signal, Slot

from ..api import ApiClient
from ..backoff import Backoff, GiveUpClock
from ..budget import RequestBudget
from ..config import redact
from ..engine import Job, Summary
from ..errors import PermanentError, TemporaryError
from ..run_model import RunStats, apply_event, mark_stopping


def default_job_factory(config, credentials, on_event):
    """Mirror the command line: same budget, give-up window and retries."""
    return Job(config, ApiClient(credentials), on_event=on_event,
               budget=RequestBudget(limit=55), giveup=GiveUpClock(window=1800),
               backoff=Backoff(), retries=True)


class _Worker(QObject):
    """Lives on the run thread; blocks in ``job.run()``."""

    done = Signal(object, str)   # (Summary | None, error message or "")

    def __init__(self, job, redact_text):
        super().__init__()
        self._job = job
        self._redact = redact_text

    @Slot()
    def run(self):
        summary, err = None, ""
        try:
            summary = self._job.run()
        except Exception as exc:  # safety net: the engine should not raise
            err = self._redact(str(exc))
        self.done.emit(summary, err)
        # hand ourselves back to the GUI thread so deleteLater() works, then end the loop
        app = QCoreApplication.instance()
        if app is not None:
            self.moveToThread(app.thread())
        QThread.currentThread().quit()


class JobRunner(QObject):
    event = Signal(str, object)
    stats_changed = Signal()
    log_line = Signal(str)
    state_changed = Signal(str)
    finished = Signal(object)

    _event_in = Signal(int, str, object)   # engine thread -> GUI thread
    _error_in = Signal(int, str)           # queued error-path delivery

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stats = RunStats()
        self._thread = None
        self._worker = None
        self._job = None
        self._run_id = 0
        self._got_finished = False
        self._final = None
        self._key = None
        self._event_in.connect(self._on_event_in, Qt.QueuedConnection)
        self._error_in.connect(self._on_error_in, Qt.QueuedConnection)

    # -- public ------------------------------------------------------------------
    def is_running(self) -> bool:
        return self._thread is not None

    def start(self, job_config, credentials, job_factory=None) -> bool:
        if self._thread is not None:
            return False
        self._run_id += 1
        rid = self._run_id
        self._key = getattr(credentials, "api_key", None)
        self.stats = RunStats()
        self._job = None
        self._got_finished = False
        self._final = None
        try:
            job_config.dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._error_in.emit(rid, self._safe(str(exc)))
            return True

        def on_event(kind, data, _rid=rid):
            try:
                self._event_in.emit(_rid, kind, data)
            except RuntimeError:   # runner already deleted
                pass

        try:
            job = (job_factory or default_job_factory)(job_config, credentials, on_event)
        except Exception as exc:
            self._error_in.emit(rid, self._safe(str(exc)))
            return True
        self._job = job
        thread = QThread()
        worker = _Worker(job, self._safe)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        thread.finished.connect(worker.deleteLater)
        worker.done.connect(self._on_done, Qt.QueuedConnection)
        self._thread, self._worker = thread, worker
        self.stats.state = "running"
        self.state_changed.emit("running")
        thread.start()
        return True

    def pause(self):
        if self._job is not None:
            self._job.pause()

    def resume(self):
        if self._job is not None:
            self._job.resume()

    def retry_now(self):
        if self._job is not None:
            self._job.retry_now()

    def stop(self):
        if self._job is None or self.stats.state in ("idle", "finished"):
            return
        self._job.stop()
        before = self.stats.state
        mark_stopping(self.stats)
        if self.stats.state != before:
            self.state_changed.emit(self.stats.state)

    def wait(self, timeout_ms: int = 10000) -> bool:
        thread = self._thread
        if thread is None:
            return True
        return bool(thread.wait(timeout_ms))

    # -- internals ---------------------------------------------------------------
    def _safe(self, text: str) -> str:
        return redact(text, self._key)

    def _handle(self, kind, data, lines=None):
        """Apply one event and emit the public signals in the contracted order."""
        stats = self.stats
        before = stats.state
        new = apply_event(stats, kind, data) if lines is None else lines
        self.event.emit(kind, data)
        for line in new:
            self.log_line.emit(line)
        if stats.state != before:
            self.state_changed.emit(stats.state)
        self.stats_changed.emit()

    @Slot(int, str, object)
    def _on_event_in(self, rid, kind, data):
        if rid != self._run_id or self._got_finished:
            return
        if kind == "finished":
            self._got_finished = True
            summary = data.get("summary") if isinstance(data, dict) else None
            self._final = summary
        self._handle(kind, data)

    @Slot(object, str)
    def _on_done(self, summary, err):
        thread, worker = self._thread, self._worker
        rid = self._run_id
        if not self._got_finished:
            self._got_finished = True
            final = Summary(reason="error", error=err or "job ended without a summary")
            self._final = final
            if err:
                self.stats.log.append(f"Error: {err}"[:300])
                self.log_line.emit(f"Error: {err}"[:300])
            self._handle("finished", {"summary": final})
        if self._run_id != rid:   # a slot above started a new run (cannot: still held)
            return
        final = self._final
        if thread is not None:
            thread.quit()
            thread.wait()
            thread.deleteLater()
        if worker is not None:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass
        self._thread = self._worker = self._job = None
        self.finished.emit(final)

    @Slot(int, str)
    def _on_error_in(self, rid, message):
        if rid != self._run_id:
            return
        line = _clip_line(f"Error: {message}")
        summary = Summary(reason="error", error=message)
        self.stats.log.append(line)
        self.stats.summary = summary
        before = self.stats.state
        self.stats.state = "finished"
        self.log_line.emit(line)
        if before != "finished":
            self.state_changed.emit("finished")
        self.stats_changed.emit()
        self.finished.emit(summary)


def _clip_line(line: str) -> str:
    return line if len(line) <= 300 else line[:299] + "…"


@dataclass(frozen=True)
class SearchResult:
    ok: bool
    total: Optional[int]
    kind: str   # "ok", "auth", "captcha", "budget", "unavailable" or "error"
    wait_seconds: float = 0.0


class SearchRunner(QObject):
    """One-request post count on a daemon thread; result delivered on the GUI thread."""

    finished = Signal(int, object)     # (token, SearchResult)
    _result_in = Signal(int, object)   # worker thread -> GUI thread

    def __init__(self, parent=None):
        super().__init__(parent)
        self._token = 0
        self._cancelled_token = 0
        self._running = False
        self._thread = None
        # created on the GUI thread; the worker only emits the queued signal
        self._result_in.connect(self._on_result, Qt.ConnectionType.QueuedConnection)

    def is_running(self) -> bool:
        return self._running

    def start(self, query, credentials, client_factory=None, budget=None) -> Optional[int]:
        if self._running:
            return None
        self._token += 1
        token = self._token
        self._running = True
        thread = threading.Thread(
            target=self._work, args=(token, query, credentials, client_factory, budget),
            daemon=True)
        self._thread = thread
        thread.start()
        return token

    def cancel(self) -> None:
        self._cancelled_token = self._token

    def wait(self, timeout_ms: int = 5000) -> bool:
        thread = self._thread
        if thread is not None:
            thread.join(timeout_ms / 1000.0)
        QCoreApplication.processEvents()
        if thread is not None and not thread.is_alive():
            # the queued result may need a second pass of the event loop; bounded
            deadline = time.monotonic() + 0.5
            while self._running and time.monotonic() < deadline:
                time.sleep(0.005)
                QCoreApplication.processEvents()
        return not self.is_running()

    def _work(self, token, query, credentials, client_factory, budget):
        result = self._search(query, credentials, client_factory, budget)
        try:
            self._result_in.emit(token, result)
        except RuntimeError:   # runner already deleted
            pass

    @staticmethod
    def _search(query, credentials, client_factory, budget) -> SearchResult:
        try:
            budget = budget if budget is not None else RequestBudget(limit=55)
            if budget.remaining() <= 0 or not budget.acquire():
                return SearchResult(False, None, "budget", budget.next_available_in())
            client = (client_factory or ApiClient)(credentials)
            total = client.total_count(query)
            return SearchResult(True, int(total), "ok")
        except PermanentError as exc:
            if exc.kind == "auth":
                return SearchResult(False, None, "auth")
            if exc.kind == "captcha":
                return SearchResult(False, None, "captcha")
            return SearchResult(False, None, "error")
        except TemporaryError:
            return SearchResult(False, None, "unavailable")
        except Exception:
            return SearchResult(False, None, "error")

    @Slot(int, object)
    def _on_result(self, token, result):
        self._running = False
        if token == self._token and token != self._cancelled_token:
            self.finished.emit(token, result)
