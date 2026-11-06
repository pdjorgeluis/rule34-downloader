"""Run model tests: pure Python, no Qt, no network."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rule34_downloader import run_model as rm  # noqa: E402
from rule34_downloader.budget import RequestBudget  # noqa: E402
from rule34_downloader.engine import Summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def fresh(state="running"):
    s = rm.RunStats()
    s.state = state
    return s


class ApplyEventTests(unittest.TestCase):
    def test_apply_started_sets_query_total_and_running(self):
        s = rm.RunStats()
        self.assertEqual(s.state, "idle")
        lines = rm.apply_event(s, "started", {"query": "cat", "total": 10})
        self.assertEqual((s.state, s.query, s.total), ("running", "cat", 10))
        self.assertEqual(lines, ["Searching: cat (10 posts)"])
        self.assertEqual(list(s.log), lines)
        s2 = rm.RunStats()
        self.assertEqual(rm.apply_event(s2, "started", {"query": "dog", "total": 0}),
                         ["Searching: dog"])
        self.assertIsNone(s2.total)
        s3 = rm.RunStats()
        self.assertEqual(rm.apply_event(s3, "started", {"query": "x", "total": "7"}),
                         ["Searching: x"])

    def test_apply_file_done_counts_bytes_and_logs(self):
        s = fresh()
        lines = rm.apply_event(s, "file_done",
                               {"id": 1, "type": "pictures", "path": "/x/1.jpg", "bytes": 2048})
        self.assertEqual(lines, ["Saved 1 (pictures, 2 KB)"])
        self.assertEqual((s.downloaded, s.bytes_done), (1, 2048))
        rm.apply_event(s, "file_done", {"id": 2, "type": "gifs", "bytes": True})
        self.assertEqual((s.downloaded, s.bytes_done), (2, 2048))
        self.assertEqual(rm.apply_event(s, "file_done", {"id": 3, "bytes": 1500})[0],
                         "Saved 3 (None, 1 KB)")
        rm.apply_event(s, "file_done", {"id": 4, "bytes": -5})
        self.assertEqual(s.bytes_done, 2048 + 1500)

    def test_apply_file_existing_and_skipped_count(self):
        s = fresh()
        self.assertEqual(rm.apply_event(s, "file_existing", {"id": 1}), [])
        self.assertEqual(rm.apply_event(s, "file_skipped", {"id": 2, "ext": "webm"}), [])
        self.assertEqual((s.existing, s.skipped, len(s.log)), (1, 1, 0))

    def test_apply_file_failed_counts_and_logs_error(self):
        s = fresh()
        self.assertEqual(rm.apply_event(s, "file_failed", {"id": 9, "error": "HTTP 500"}),
                         ["Failed 9: HTTP 500"])
        self.assertEqual(s.failed, 1)
        self.assertEqual(rm.apply_event(s, "file_failed", {}), ["Failed None: None"])

    def test_apply_page_counts_pages(self):
        s = fresh()
        self.assertEqual(rm.apply_event(s, "page", {"pid": 0, "posts": 100}), [])
        rm.apply_event(s, "page", {"pid": 1, "posts": 5})
        self.assertEqual(s.pages, 2)

    def test_apply_retry_counts_and_logs(self):
        s = fresh()
        self.assertEqual(
            rm.apply_event(s, "retry", {"what": "file", "id": 7, "pid": 3, "attempt": 2,
                                        "error": "HTTP 503"}),
            ["Retry file 7 (attempt 2): HTTP 503"])
        self.assertEqual(
            rm.apply_event(s, "retry", {"what": "page", "pid": 3, "attempt": 1, "error": "t"}),
            ["Retry page 3 (attempt 1): t"])
        self.assertEqual(s.retries, 2)

    def test_apply_waiting_sets_waiting_state_with_seconds(self):
        s = fresh()
        lines = rm.apply_event(s, "waiting", {"reason": "rate limit", "seconds": 12.7,
                                              "attempt": 1})
        self.assertEqual(lines, ["Waiting 12 s (rate limit)"])
        self.assertEqual((s.state, s.wait_seconds, s.wait_reason), ("waiting", 12.7, "rate limit"))
        rm.apply_event(s, "waiting", {"reason": "r", "seconds": "abc"})
        self.assertEqual(s.wait_seconds, 0)
        for st in ("paused", "unavailable", "stopping"):
            s = fresh(st)
            rm.apply_event(s, "waiting", {"reason": "r", "seconds": 5})
            self.assertEqual((s.state, s.wait_seconds, s.wait_reason), (st, 5, "r"))

    def test_apply_waiting_then_next_file_done_returns_to_running(self):
        s = fresh()
        rm.apply_event(s, "waiting", {"reason": "r", "seconds": 5})
        rm.apply_event(s, "file_done", {"id": 1, "bytes": 1})
        self.assertEqual(s.state, "running")
        s = fresh("paused")
        rm.apply_event(s, "file_done", {"id": 1, "bytes": 1})
        self.assertEqual(s.state, "paused")

    def test_apply_adaptive_sets_level_and_logs(self):
        s = fresh()
        self.assertEqual(rm.apply_event(s, "adaptive", {"level": 2}),
                         ["Slowing down: level 2"])
        self.assertEqual(s.adaptive_level, 2)

    def test_apply_paused_and_resumed_switch_state(self):
        s = fresh()
        self.assertEqual(rm.apply_event(s, "paused", {}), ["Paused"])
        self.assertEqual(s.state, "paused")
        self.assertEqual(rm.apply_event(s, "resumed", {}), ["Resumed"])
        self.assertEqual(s.state, "running")
        s = fresh("waiting")
        rm.apply_event(s, "resumed", {})
        self.assertEqual(s.state, "waiting")

    def test_apply_server_unavailable_sets_unavailable_state(self):
        s = fresh()
        lines = rm.apply_event(s, "server_unavailable",
                               {"message": "503", "policy": "pause", "auto_retry_seconds": None})
        self.assertEqual(lines, ["Server unavailable: 503"])
        self.assertEqual((s.state, s.unavailable_message), ("unavailable", "503"))

    def test_apply_unavailable_retry_returns_to_running(self):
        s = fresh("unavailable")
        self.assertEqual(rm.apply_event(s, "unavailable_retry", {"by": "user"}),
                         ["Retrying (user)"])
        self.assertEqual(s.state, "running")
        s = fresh("paused")
        rm.apply_event(s, "unavailable_retry", {"by": "timer"})
        self.assertEqual(s.state, "paused")

    def test_apply_permanent_error_logs_and_keeps_state_until_finished(self):
        s = fresh()
        self.assertEqual(rm.apply_event(s, "permanent_error", {"kind": "auth", "message": "bad"}),
                         ["Error (auth): bad"])
        self.assertEqual(s.state, "running")
        rm.apply_event(s, "finished", {"summary": Summary(reason="permanent_error")})
        self.assertEqual(s.state, "finished")

    def test_apply_finished_sets_finished_and_summary(self):
        s = fresh()
        summ = Summary(downloaded=3)
        self.assertEqual(rm.apply_event(s, "finished", {"summary": summ}), ["Finished"])
        self.assertEqual(s.state, "finished")
        self.assertIs(s.summary, summ)

    def test_apply_unknown_event_is_ignored_without_raising(self):
        s = fresh()
        before = (s.state, s.downloaded, len(s.log))
        for kind, data in (("nonsense", {}), ("file_done", None), ("file_done", [1]),
                           ("file_done", "x"), (None, {}), (5, 5)):
            self.assertEqual(rm.apply_event(s, kind, data), [])
        self.assertEqual((s.state, s.downloaded, len(s.log)), before)
        self.assertEqual(rm.apply_event(s, "file_done", {}), ["Saved None (None, 0 KB)"])

    def test_mark_stopping_sets_stopping_state(self):
        for st, want in (("running", "stopping"), ("waiting", "stopping"),
                         ("paused", "stopping"), ("unavailable", "stopping"),
                         ("idle", "idle"), ("finished", "finished"), ("stopping", "stopping")):
            s = fresh(st)
            rm.mark_stopping(s)
            self.assertEqual(s.state, want, st)
        s = fresh("stopping")
        for kind, data in (("resumed", {}), ("unavailable_retry", {"by": "x"}),
                           ("paused", {}), ("started", {"query": "q"}),
                           ("file_done", {"id": 1, "bytes": 1}),
                           ("server_unavailable", {"message": "m"})):
            rm.apply_event(s, kind, data)
            self.assertEqual(s.state, "stopping", kind)
        self.assertEqual((s.downloaded, s.unavailable_message), (1, "m"))
        rm.apply_event(s, "finished", {"summary": None})
        self.assertEqual(s.state, "finished")

    def test_log_is_bounded_to_500_lines_oldest_dropped(self):
        s = fresh()
        for i in range(520):
            rm.apply_event(s, "file_failed", {"id": i, "error": "e"})
        self.assertEqual(len(s.log), 500)
        self.assertEqual(s.log[0], "Failed 20: e")
        self.assertEqual(s.log[-1], "Failed 519: e")

    def test_log_line_truncates_long_messages_to_300_chars(self):
        s = fresh()
        lines = rm.apply_event(s, "file_failed", {"id": 1, "error": "x" * 1000})
        self.assertEqual(len(lines[0]), 300)
        self.assertTrue(lines[0].endswith("…"))
        self.assertEqual(s.log[-1], lines[0])
        short = rm.apply_event(s, "file_failed", {"id": 1, "error": "y" * 280})
        self.assertFalse(short[0].endswith("…"))

    def test_apply_events_after_finished_are_ignored(self):
        s = fresh()
        rm.apply_event(s, "finished", {"summary": None})
        n = len(s.log)
        self.assertEqual(rm.apply_event(s, "file_done", {"id": 1, "bytes": 5}), [])
        self.assertEqual(rm.apply_event(s, "started", {"query": "q"}), [])
        self.assertEqual((s.state, s.downloaded, s.query, len(s.log)), ("finished", 0, "", n))


class DerivedTests(unittest.TestCase):
    def test_progress_fraction_count_mode(self):
        s = fresh()
        s.downloaded, s.existing = 2, 1
        self.assertEqual(rm.progress_fraction(s, 4), 0.75)
        s.total = 100
        self.assertEqual(rm.progress_fraction(s, 6), 0.5)

    def test_progress_fraction_all_mode_uses_total(self):
        s = fresh()
        s.total = 10
        s.downloaded, s.existing, s.skipped, s.failed = 1, 1, 1, 1
        self.assertEqual(rm.progress_fraction(s, None), 0.4)
        for bad in (0, -3, True, "5", 2.5):
            self.assertEqual(rm.progress_fraction(s, bad), 0.4)

    def test_progress_fraction_unknown_total_is_none(self):
        s = fresh()
        self.assertIsNone(rm.progress_fraction(s, None))
        s.total = 0
        self.assertIsNone(rm.progress_fraction(s, None))

    def test_progress_fraction_never_exceeds_one(self):
        s = fresh()
        s.downloaded, s.total = 50, 10
        self.assertEqual(rm.progress_fraction(s, 5), 1.0)
        self.assertEqual(rm.progress_fraction(s, None), 1.0)

    def test_enabled_actions_per_state(self):
        want = {"idle": {"start"}, "running": {"pause", "stop"},
                "waiting": {"pause", "retry_now", "stop"}, "paused": {"resume", "stop"},
                "unavailable": {"retry_now", "stop"}, "stopping": set(), "finished": {"start"}}
        self.assertEqual(tuple(want), rm.STATES)
        for st, acts in want.items():
            got = rm.enabled_actions(st)
            self.assertIsInstance(got, frozenset)
            self.assertEqual(got, frozenset(acts), st)
        self.assertEqual(rm.enabled_actions("bogus"), frozenset())

    def test_finish_headline_for_each_reason(self):
        base = "5 downloaded, 2 already there, 3 skipped, 1 failed"
        kw = dict(downloaded=5, existing=2, skipped_type=3, failed=1)
        for reason in ("count_reached", "end_of_results"):
            self.assertEqual(rm.finish_headline(Summary(reason=reason, **kw)),
                             ("Finished: " + base, False))
        self.assertEqual(rm.finish_headline(Summary(reason="stopped", **kw)),
                         ("Stopped: " + base, False))
        self.assertEqual(
            rm.finish_headline(Summary(reason="permanent_error", error="bad key",
                                       error_kind="auth")),
            ("Stopped by an error (auth): bad key", True))
        self.assertEqual(rm.finish_headline(Summary(reason="server_unavailable", error="503")),
                         ("Server unavailable: 503", True))
        self.assertEqual(rm.finish_headline(Summary(reason="error", error="boom")),
                         ("Unexpected error: boom", True))
        self.assertEqual(rm.finish_headline(Summary(reason="weird")),
                         ("Unexpected end: weird", True))

    def test_budget_snapshot_counts_used_and_remaining(self):
        with tempfile.TemporaryDirectory() as d:
            b = RequestBudget(limit=7, path=Path(d) / "state.json")
            self.assertEqual(rm.budget_snapshot(b), (0, 7))
            self.assertTrue(b.acquire())
            self.assertTrue(b.acquire())
            self.assertEqual(rm.budget_snapshot(b), (2, 7))
        self.assertEqual(rm.budget_snapshot(None), (0, 0))

        class Broken:
            limit = 5

            def used(self):
                raise RuntimeError("no")
        self.assertEqual(rm.budget_snapshot(Broken()), (0, 0))

    def test_run_model_import_pulls_in_no_qt(self):
        code = ("import sys; sys.path.insert(0, %r); import rule34_downloader.run_model; "
                "bad = [m for m in sys.modules if m.startswith('PySide6')]; "
                "print(bad); sys.exit(1 if bad else 0)" % str(REPO))
        r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
