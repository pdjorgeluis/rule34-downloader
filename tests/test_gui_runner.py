"""JobRunner tests (offscreen, mock server only, temp budget file, patched HOME)."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false;*.warning=false")

from test_gui_shell import HAVE_QT  # noqa: E402

if HAVE_QT:
    from PySide6.QtCore import QThread  # noqa: E402
    from PySide6.QtWidgets import QApplication  # noqa: E402

from mock_api import API_KEY, USER_ID, MockServer  # noqa: E402
from rule34_downloader.api import ApiClient  # noqa: E402
from rule34_downloader.backoff import Backoff, GiveUpClock  # noqa: E402
from rule34_downloader.budget import RequestBudget  # noqa: E402
from rule34_downloader.config import Credentials  # noqa: E402
from rule34_downloader.engine import Job, JobConfig  # noqa: E402


class Recorder:
    """Connects to every public signal of a runner and remembers the order."""

    def __init__(self, runner):
        self.log = []          # (signal name, payload)
        self.threads = []
        self.on_finished = None
        runner.event.connect(self._ev)
        runner.stats_changed.connect(lambda: self.log.append(("stats_changed", None)))
        runner.log_line.connect(lambda s: self.log.append(("log_line", s)))
        runner.state_changed.connect(lambda s: self.log.append(("state_changed", s)))
        runner.finished.connect(self._fin)

    def _ev(self, kind, data):
        self.threads.append(QThread.currentThread())
        self.log.append(("event", (kind, data)))

    def _fin(self, summary):
        self.log.append(("finished", summary))
        if self.on_finished:
            self.on_finished(summary)

    def of(self, name):
        return [p for n, p in self.log if n == name]

    def kinds(self):
        return [p[0] for p in self.of("event")]

    def states(self):
        return self.of("state_changed")

    def text(self):
        return repr(self.log)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class RunnerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from rule34_downloader.gui.runner import JobRunner
        self.srv = MockServer(total=25).start()
        self.addCleanup(self.srv.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        p = mock.patch.dict(os.environ, {"R34_API_URL": self.srv.api_url})
        p.start()
        self.addCleanup(p.stop)
        h = mock.patch.object(Path, "home", return_value=self.tmp)
        h.start()
        self.addCleanup(h.stop)
        self.creds = Credentials(USER_ID, API_KEY)
        self.runner = JobRunner()
        self.rec = Recorder(self.runner)
        self.addCleanup(self.cleanup_runner)

    def cleanup_runner(self):
        self.runner.stop()
        self.runner.wait(10000)
        self.app.processEvents()

    # -- helpers -----------------------------------------------------------------
    def cfg(self, **kw):
        kw.setdefault("delay", 0)
        kw.setdefault("count", 3)
        kw.setdefault("dest", self.tmp / "dl")
        return JobConfig(include=["cat"], **kw)

    def factory(self, retries=True):
        budget_path = self.tmp / "budget.json"

        def make(cfg, creds, on_event):
            return Job(cfg, ApiClient(creds), on_event=on_event,
                       budget=RequestBudget(path=budget_path),
                       giveup=GiveUpClock(window=30), backoff=Backoff(base=0.01, jitter=0),
                       retries=retries)
        return make

    def pump(self, cond, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if cond():
                return True
            time.sleep(0.002)
        self.app.processEvents()
        return bool(cond())

    def run_to_end(self, cfg=None, factory=None):
        self.assertTrue(self.runner.start(cfg or self.cfg(), self.creds,
                                          factory or self.factory()))
        self.assertTrue(self.pump(lambda: self.rec.of("finished")), self.rec.text()[-800:])
        return self.rec.of("finished")[-1]

    # -- tests -------------------------------------------------------------------
    def test_runner_start_runs_job_and_emits_finished_once(self):
        s = self.run_to_end()
        self.assertEqual((s.reason, s.downloaded), ("count_reached", 3))
        self.pump(lambda: False, 0.1)
        self.assertEqual(len(self.rec.of("finished")), 1)
        self.assertFalse(self.runner.is_running())
        self.assertIs(self.runner.stats.summary, s)
        self.assertEqual(self.runner.stats.state, "finished")
        self.assertEqual(self.rec.kinds()[0], "started")
        self.assertEqual(self.rec.kinds()[-1], "finished")

    def test_runner_downloads_into_dest_and_counts(self):
        self.run_to_end()
        files = [p for p in (self.tmp / "dl").rglob("*") if p.is_file()]
        self.assertEqual(len(files), 3)
        st = self.runner.stats
        self.assertEqual(st.downloaded, 3)
        self.assertGreater(st.bytes_done, 0)
        self.assertGreaterEqual(st.pages, 1)
        self.assertEqual(sum(1 for l in st.log if l.startswith("Saved ")), 3)
        self.assertEqual(len(self.rec.of("log_line")), len(st.log))

    def test_runner_events_arrive_on_gui_thread(self):
        self.run_to_end()
        self.assertTrue(self.rec.threads)
        for t in self.rec.threads:
            self.assertEqual(t, self.app.thread())

    def test_runner_start_while_running_returns_false(self):
        self.srv.slow = 0.05
        self.assertTrue(self.runner.start(self.cfg(count=6), self.creds, self.factory()))
        self.assertTrue(self.runner.is_running())
        n = len(self.rec.log)
        thread = self.runner._thread
        self.assertFalse(self.runner.start(self.cfg(), self.creds, self.factory()))
        self.assertIs(self.runner._thread, thread)
        self.assertEqual(len(self.rec.log), n)
        self.runner.stop()
        self.assertTrue(self.pump(lambda: self.rec.of("finished")))
        self.assertEqual(len(self.rec.of("finished")), 1)

    def test_runner_stop_ends_with_stopped_reason(self):
        self.srv.slow = 0.05
        self.runner.start(self.cfg(count=20), self.creds, self.factory())
        self.assertTrue(self.pump(lambda: "file_done" in self.rec.kinds()))
        self.runner.stop()
        self.assertEqual(self.runner.stats.state, "stopping")
        self.assertIn("stopping", self.rec.states())
        self.assertTrue(self.pump(lambda: self.rec.of("finished")))
        self.assertEqual(self.rec.of("finished")[0].reason, "stopped")
        self.assertEqual(self.rec.states()[-1], "finished")

    def test_runner_pause_and_resume_emit_state_changes(self):
        self.srv.slow = 0.05
        self.runner.start(self.cfg(count=8), self.creds, self.factory())
        self.assertTrue(self.pump(lambda: "file_done" in self.rec.kinds()))
        self.runner.pause()
        self.assertTrue(self.pump(lambda: "paused" in self.rec.states()))
        self.assertEqual(self.runner.stats.state, "paused")
        self.runner.resume()
        self.assertTrue(self.pump(lambda: self.rec.states().count("running") >= 2))
        self.assertTrue(self.pump(lambda: self.rec.of("finished")))
        self.assertEqual(self.rec.of("finished")[0].reason, "count_reached")

    def test_runner_retry_now_releases_unavailable_wait(self):
        self.srv.api_script = [(500, b"", {})]
        cfg = self.cfg(count=2, unavailable_policy="pause")
        self.runner.start(cfg, self.creds, self.factory(retries=False))
        self.assertTrue(self.pump(lambda: "unavailable" in self.rec.states()))
        self.assertEqual(self.runner.stats.state, "unavailable")
        self.assertFalse(self.rec.of("finished"))
        self.runner.retry_now()
        self.assertTrue(self.pump(lambda: self.rec.of("finished")))
        s = self.rec.of("finished")[0]
        self.assertEqual((s.reason, s.downloaded), ("count_reached", 2))
        self.assertIn("running", self.rec.states()[self.rec.states().index("unavailable"):])

    def test_runner_permanent_error_finishes_with_kind(self):
        self.srv.api_script = [(200, b'"Missing authentication. Go to api"', {})]
        s = self.run_to_end()
        self.assertEqual(s.reason, "permanent_error")
        self.assertEqual(s.error_kind, "auth")
        self.assertIn("permanent_error", self.rec.kinds())
        self.assertTrue(any(l.startswith("Error (auth)") for l in self.runner.stats.log))
        self.assertEqual(self.runner.stats.state, "finished")

    def test_runner_creates_missing_dest_folder(self):
        dest = self.tmp / "a" / "b" / "c"
        self.assertFalse(dest.exists())
        s = self.run_to_end(self.cfg(dest=dest, count=1))
        self.assertEqual(s.downloaded, 1)
        self.assertTrue(dest.is_dir())

    def test_runner_unwritable_dest_finishes_with_error_summary(self):
        blocker = self.tmp / "afile"
        blocker.write_text("x")
        self.assertTrue(self.runner.start(self.cfg(dest=blocker / "sub"), self.creds,
                                          self.factory()))
        self.assertFalse(self.runner.is_running())
        self.assertTrue(self.pump(lambda: self.rec.of("finished")))
        s = self.rec.of("finished")[0]
        self.assertEqual(s.reason, "error")
        self.assertTrue(s.error)
        self.assertEqual(self.runner.stats.state, "finished")
        self.assertIs(self.runner.stats.summary, s)
        self.assertTrue(self.runner.stats.log[-1].startswith("Error: "))
        self.assertEqual(self.rec.states(), ["finished"])

    def test_runner_factory_exception_finishes_with_error_summary(self):
        def bad(cfg, creds, on_event):
            raise RuntimeError(f"cannot build {creds.api_key}")
        self.assertTrue(self.runner.start(self.cfg(), self.creds, bad))
        self.assertTrue(self.pump(lambda: self.rec.of("finished")))
        s = self.rec.of("finished")[0]
        self.assertEqual(s.reason, "error")
        self.assertIn("cannot build", s.error)
        self.assertNotIn(API_KEY, s.error)
        self.assertFalse(self.runner.is_running())
        self.assertEqual(self.srv.api_requests(), [])

    def test_runner_can_run_twice_in_a_row(self):
        s1 = self.run_to_end()
        self.assertEqual(s1.downloaded, 3)
        self.rec.log.clear()
        s2 = self.run_to_end(self.cfg(dest=self.tmp / "dl2", count=2))
        self.assertEqual(s2.downloaded, 2)
        self.assertEqual(self.runner.stats.downloaded, 2)
        self.assertEqual(len(self.rec.of("finished")), 1)

    def test_runner_state_sequence_for_a_normal_run(self):
        self.run_to_end()
        self.assertEqual(self.rec.states(), ["running", "finished"])
        names = [n for n, _ in self.rec.log]
        self.assertEqual(names[0], "state_changed")
        self.assertEqual(names[-1], "finished")
        # per-event order: event, its log lines, state change, stats_changed
        i = self.rec.log.index(("event", ("finished", {"summary": self.rec.of("finished")[0]})))
        self.assertEqual(self.rec.log[i + 1], ("log_line", "Finished"))
        self.assertEqual(self.rec.log[i + 2], ("state_changed", "finished"))
        self.assertEqual(self.rec.log[i + 3], ("stats_changed", None))

    def test_runner_stop_when_idle_is_noop(self):
        self.runner.stop()
        self.runner.pause()
        self.runner.resume()
        self.runner.retry_now()
        self.assertEqual(self.rec.log, [])
        self.assertEqual(self.runner.stats.state, "idle")
        self.run_to_end()
        n = len(self.rec.log)
        self.runner.stop()
        self.runner.retry_now()
        self.app.processEvents()
        self.assertEqual(len(self.rec.log), n)

    def test_runner_wait_returns_after_finish(self):
        self.assertTrue(self.runner.wait(100))
        self.srv.slow = 0.02
        self.runner.start(self.cfg(count=3), self.creds, self.factory())
        self.assertTrue(self.runner.wait(10000))
        self.assertTrue(self.pump(lambda: self.rec.of("finished")))
        self.assertFalse(self.runner.is_running())
        self.assertTrue(self.runner.wait(100))

    def test_runner_never_emits_key_text(self):
        self.run_to_end()

        def leaky(cfg, creds, on_event):
            raise RuntimeError(f"boom {creds.api_key} api_key={creds.api_key}")
        self.runner.start(self.cfg(), self.creds, leaky)
        self.assertTrue(self.pump(lambda: len(self.rec.of("finished")) == 2))
        self.assertNotIn(API_KEY, self.rec.text())
        self.assertNotIn(API_KEY, repr(self.runner.stats))
        self.assertNotIn(API_KEY, " ".join(self.runner.stats.log))

    def test_runner_stale_events_from_previous_run_are_dropped(self):
        old_id = []
        done = []

        def again(summary):
            if done:
                return
            done.append(summary)
            old_id.append(self.runner._run_id)
            self.assertTrue(self.runner.start(self.cfg(dest=self.tmp / "second", count=1),
                                              self.creds, self.factory()))
            self.runner._event_in.emit(old_id[0], "file_done",
                                       {"id": 9999, "type": "x", "bytes": 1})
            self.runner._event_in.emit(old_id[0], "finished", {"summary": None})
        self.rec.on_finished = again
        self.runner.start(self.cfg(count=1), self.creds, self.factory())
        self.assertTrue(self.pump(lambda: len(self.rec.of("finished")) == 2))
        self.assertNotIn(9999, [p[1].get("id") for p in self.rec.of("event")
                                if isinstance(p[1], dict)])
        self.assertEqual(self.runner.stats.downloaded, 1)
        self.assertFalse(any("9999" in l for l in self.runner.stats.log))
        self.assertEqual(self.rec.of("finished")[1].downloaded, 1)

    def test_runner_start_inside_finished_slot_succeeds(self):
        results = []

        def again(summary):
            if results:
                return
            results.append(self.runner.start(self.cfg(dest=self.tmp / "again", count=1),
                                             self.creds, self.factory()))
        self.rec.on_finished = again
        self.runner.start(self.cfg(count=1), self.creds, self.factory())
        self.assertTrue(self.pump(lambda: len(self.rec.of("finished")) == 2))
        self.assertEqual(results, [True])
        self.assertEqual(self.rec.of("finished")[1].downloaded, 1)

    def test_runner_error_paths_are_delivered_queued_not_reentrant(self):
        calls = []

        def bad(cfg, creds, on_event):
            raise RuntimeError("nope")
        self.rec.on_finished = lambda s: calls.append("finished")
        ret = self.runner.start(self.cfg(), self.creds, bad)
        calls.append("returned")
        self.assertTrue(ret)
        self.assertEqual(calls, ["returned"])
        self.assertEqual(self.rec.of("finished"), [])
        self.assertTrue(self.pump(lambda: self.rec.of("finished")))
        self.assertEqual(calls, ["returned", "finished"])


if __name__ == "__main__":
    unittest.main()
