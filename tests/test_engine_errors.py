import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fakes import FakeClock, fake_waiter  # noqa: E402
from mock_api import API_KEY, USER_ID, MockServer  # noqa: E402
from rule34_downloader.api import ApiClient  # noqa: E402
from rule34_downloader.backoff import Backoff, GiveUpClock  # noqa: E402
from rule34_downloader.budget import RequestBudget  # noqa: E402
from rule34_downloader.config import Credentials  # noqa: E402
from rule34_downloader.engine import Job, JobConfig, Summary  # noqa: E402
from rule34_downloader.resilience import Adaptive, ThrottleGate  # noqa: E402

# mock ids: newest first 25, 24, ...; extension by id % 5 = jpg, jpeg, png, gif, mp4
# so with types={"pictures"} the downloadable ids are 25, 23, 22, 20, ...


class ErrBase(unittest.TestCase):
    total = 25

    def setUp(self):
        self.srv = MockServer(total=self.total).start()
        self.addCleanup(self.srv.stop)
        env = mock.patch.dict(os.environ, {"R34_API_URL": self.srv.api_url})
        env.start()
        self.addCleanup(env.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.events = []
        self._lock = threading.Lock()
        self.fc = FakeClock()

    def on_event(self, kind, data):
        with self._lock:
            self.events.append((kind, data))

    def kinds(self, kind):
        with self._lock:
            return [d for k, d in self.events if k == kind]

    def make(self, retries=True, fake=True, giveup=None, budget=None, backoff=None,
             adaptive=None, gate=None, on_event="default", **kw):
        kw.setdefault("delay", 0)
        kw.setdefault("count", 2)
        cfg = JobConfig(include=["cat"], dest=self.tmp / "dl", **kw)
        client = ApiClient(Credentials(USER_ID, API_KEY))
        if adaptive is None:
            adaptive = Adaptive(base_delay=0.0)   # slow-down must not cost real seconds
        if gate is None and fake:
            gate = ThrottleGate(clock=self.fc, waiter=fake_waiter(self.fc))
        cb = self.on_event if on_event == "default" else on_event
        return Job(cfg, client, on_event=cb, gate=gate, adaptive=adaptive, budget=budget,
                   backoff=backoff or Backoff(base=0.001, jitter=0),
                   giveup=giveup, retries=retries)

    def json_requests(self):
        return [q for q in self.srv.api_requests() if "json" in q]

    def in_thread(self, job):
        box = {}

        def target():
            try:
                box["summary"] = job.run()
            except BaseException as exc:  # pragma: no cover
                box["exc"] = exc
        t = threading.Thread(target=target, daemon=True)
        t.start()
        return t, box

    def wait_event(self, kind, timeout=2.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.kinds(kind):
                return True
            time.sleep(0.005)
        return False


class PageRetryTests(ErrBase):
    def test_job_retries_page_after_503_then_completes(self):
        self.srv.api_script = [(503, b"", {})]
        s = self.make().run()
        self.assertEqual(s.reason, "count_reached")
        self.assertEqual((s.downloaded, s.pages, s.retries), (2, 1, 1))
        self.assertEqual(s.requests_made, 2)

    def test_job_waiting_and_retry_events_have_ruled_payloads(self):
        self.srv.api_script = [(503, b"", {})]
        self.make().run()
        (retry,) = self.kinds("retry")
        self.assertEqual(set(retry), {"what", "pid", "attempt", "error"})
        self.assertEqual((retry["what"], retry["pid"], retry["attempt"]), ("page", 0, 1))
        self.assertTrue(retry["error"].startswith("HTTP 503"))
        self.assertEqual(self.kinds("waiting"),
                         [{"reason": "backoff", "seconds": 0.001, "attempt": 1}])

    def test_job_retry_after_header_sets_wait_exactly(self):
        self.srv.api_script = [(429, b"", {"Retry-After": "7"})]
        s = self.make().run()
        self.assertEqual(self.kinds("waiting"),
                         [{"reason": "retry-after", "seconds": 7.0, "attempt": 1}])
        self.assertEqual(s.waited_seconds, 7.0)
        self.assertEqual(s.reason, "count_reached")

    def test_job_permanent_auth_returns_permanent_error_summary(self):
        self.srv.api_script = [(200, b'"Missing authentication. Go to api"', {})]
        s = self.make().run()
        self.assertEqual((s.reason, s.error_kind), ("permanent_error", "auth"))
        self.assertIn("Missing authentication", s.error)
        self.assertEqual(len(self.srv.api_requests()), 1)
        self.assertEqual(s.retries, 0)
        self.assertEqual([d["kind"] for d in self.kinds("permanent_error")], ["auth"])

    def test_job_captcha_page_returns_permanent_error_summary_without_retry(self):
        self.srv.api_script = [(200, b"<html>Please solve the captcha</html>", {})]
        s = self.make().run()
        self.assertEqual((s.reason, s.error_kind), ("permanent_error", "captcha"))
        self.assertEqual(len(self.srv.api_requests()), 1)
        self.assertEqual(self.kinds("retry"), [])

    def test_job_http500_without_retries_flag_returns_server_unavailable_summary(self):
        self.srv.mode = "http500"
        s = self.make(retries=False).run()
        self.assertEqual(s.reason, "server_unavailable")
        self.assertEqual(s.error_kind, "http")
        self.assertEqual(s.requests_made, 1)
        (ev,) = self.kinds("server_unavailable")
        self.assertEqual((ev["policy"], ev["auto_retry_seconds"]), ("stop", None))

    def test_job_gives_up_after_window_policy_stop_returns_server_unavailable(self):
        self.srv.mode = "http500"
        s = self.make(giveup=GiveUpClock(window=10, clock=self.fc),
                      backoff=Backoff(base=5, jitter=0)).run()
        self.assertEqual(s.reason, "server_unavailable")
        self.assertTrue(s.error.startswith("server unavailable after"))
        self.assertEqual((s.requests_made, s.retries), (3, 2))
        self.assertEqual(len(self.kinds("server_unavailable")), 1)


class UnavailableTests(ErrBase):
    def paused_job(self, **kw):
        self.srv.api_script = [(500, b"", {})]
        return self.make(retries=False, unavailable_policy="pause", **kw)

    def test_job_policy_pause_waits_until_retry_now_then_completes(self):
        job = self.paused_job()
        t, box = self.in_thread(job)
        self.assertTrue(self.wait_event("server_unavailable"))
        time.sleep(0.15)
        self.assertTrue(t.is_alive())
        self.assertEqual(self.kinds("unavailable_retry"), [])
        job.retry_now()
        t.join(3)
        self.assertFalse(t.is_alive())
        s = box["summary"]
        self.assertEqual((s.reason, s.downloaded), ("count_reached", 2))
        self.assertEqual(self.kinds("unavailable_retry"), [{"by": "retry_now"}])
        self.assertEqual([q["pid"] for q in self.json_requests()], ["0", "0"])

    def test_job_policy_pause_auto_retry_resumes(self):
        job = self.paused_job(auto_retry_seconds=0.1)
        t0 = time.monotonic()
        s = job.run()
        self.assertGreaterEqual(time.monotonic() - t0, 0.1)
        self.assertEqual(s.reason, "count_reached")
        self.assertEqual(self.kinds("unavailable_retry"), [{"by": "auto_retry"}])
        (ev,) = self.kinds("server_unavailable")
        self.assertEqual((ev["policy"], ev["auto_retry_seconds"]), ("pause", 0.1))

    def test_job_stop_during_unavailable_wait_returns_stopped(self):
        job = self.paused_job()
        t, box = self.in_thread(job)
        self.assertTrue(self.wait_event("server_unavailable"))
        job.stop()
        t.join(3)
        self.assertFalse(t.is_alive())
        self.assertEqual(box["summary"].reason, "stopped")
        self.assertEqual(self.kinds("unavailable_retry"), [])

    def test_job_stop_during_backoff_returns_stopped(self):
        self.srv.mode = "http500"
        job = self.make(fake=False, backoff=Backoff(base=60, jitter=0))
        t, box = self.in_thread(job)
        self.assertTrue(self.wait_event("waiting"))
        job.stop()
        t.join(3)
        self.assertFalse(t.is_alive())
        self.assertEqual(box["summary"].reason, "stopped")
        self.assertEqual(len(self.srv.api_requests()), 1)


class EndingTests(ErrBase):
    def test_job_finished_event_emitted_once_on_every_ending(self):
        def count_finished(job):
            before = len(self.kinds("finished"))
            job.run()
            return len(self.kinds("finished")) - before

        self.assertEqual(count_finished(self.make()), 1)                    # count_reached
        self.srv.api_script = [(200, b'"Missing authentication."', {})]
        self.assertEqual(count_finished(self.make()), 1)                    # permanent
        self.srv.mode = "http500"
        self.assertEqual(count_finished(self.make(retries=False)), 1)       # unavailable
        self.srv.mode = None
        job = self.make()
        job.stop()
        self.assertEqual(count_finished(job), 1)                            # stopped
        calls = []

        def angry(kind, data):
            calls.append(kind)
            raise RuntimeError("callback boom")
        self.make(on_event=angry).run()
        self.assertEqual(calls.count("finished"), 1)                        # callback raises

    def test_job_unexpected_oserror_emits_finished_then_propagates(self):
        job = self.make()
        with mock.patch.object(ApiClient, "fetch_posts",
                               side_effect=OSError(f"disk full api_key={API_KEY}")):
            with self.assertRaises(OSError):
                job.run()
        (fin,) = self.kinds("finished")
        self.assertEqual(fin["summary"].reason, "error")
        self.assertIn("disk full", fin["summary"].error)
        self.assertNotIn(API_KEY, fin["summary"].error)


class AdaptiveBudgetTests(ErrBase):
    def test_job_overload_drops_effective_workers_to_one(self):
        self.srv.api_script = [(503, b"", {})]
        self.srv.slow = 0.02
        job = self.make(workers=3, count=6)
        s = job.run()
        self.assertEqual(s.reason, "count_reached")
        self.assertEqual(job.adaptive.effective_workers(3), 1)
        self.assertEqual(self.srv.max_active, 1)
        (ev,) = self.kinds("adaptive")
        self.assertEqual((ev["level"], ev["single_worker"]), (1, True))

    def test_job_delay_uses_adaptive_effective_delay(self):
        seen = []

        class Spy(Adaptive):
            def effective_delay(self, configured):
                seen.append(configured)
                return 0.0
        s = self.make(adaptive=Spy(), delay=0.5, count=3).run()
        self.assertEqual(s.downloaded, 3)
        self.assertEqual(seen, [0.5, 0.5, 0.5])

    def test_job_total_count_failure_is_non_fatal_and_costs_one_request(self):
        self.srv.total = 3
        self.srv.api_script = [(500, b"boom", {})]
        s = self.make(count=None).run()
        self.assertIsNone(s.total)
        self.assertEqual(s.reason, "end_of_results")
        self.assertEqual(s.requests_made, 2)
        reqs = self.srv.api_requests()
        self.assertNotIn("json", reqs[0])
        self.assertIn("json", reqs[1])
        self.assertEqual(self.kinds("retry"), [])

    def test_job_budget_exhaustion_waits_then_continues_and_counts_requests(self):
        bfc = FakeClock()
        budget = RequestBudget(limit=1, path=self.tmp / "state.json", clock=bfc,
                               waiter=fake_waiter(bfc))
        s = self.make(count=None, budget=budget).run()
        self.assertEqual(s.reason, "end_of_results")
        self.assertEqual(s.requests_made, 2)
        (w,) = self.kinds("waiting")
        self.assertEqual((w["reason"], w["seconds"], w["attempt"]), ("budget", 3600.0, 1))
        self.assertEqual(s.waited_seconds, 3600.0)

    def test_workers_100_stop_during_budget_wait(self):
        budget = RequestBudget(limit=1, path=self.tmp / "state.json",
                               waiter=lambda wait, stop: not stop.wait(wait))
        budget.acquire()                      # budget now at its limit: the engine must wait
        before = threading.active_count()
        job = self.make(count=None, workers=100, budget=budget)
        out = []
        th = threading.Thread(target=lambda: out.append(job.run()))
        th.start()
        time.sleep(0.3)
        t0 = time.monotonic()
        job.stop()
        th.join(5)
        self.assertFalse(th.is_alive())
        self.assertLess(time.monotonic() - t0, 2)
        self.assertEqual(out[0].reason, "stopped")
        self.assertLessEqual(threading.active_count(), before)

    def test_job_file_downloads_not_counted_in_budget(self):
        budget = RequestBudget(limit=10, path=self.tmp / "state.json")
        s = self.make(count=3, budget=budget).run()
        self.assertEqual(s.downloaded, 3)
        self.assertEqual(len(self.srv.file_log), 3)
        self.assertEqual(budget.used(), 1)


class FileRetryTests(ErrBase):
    def test_job_file_503_retried_then_succeeds(self):
        self.srv.file_script = {25: [503]}
        s = self.make(count=1).run()
        self.assertEqual((s.downloaded, s.failed, s.retries), (1, 0, 1))
        self.assertEqual(self.srv.file_log.count(25), 2)
        (r,) = self.kinds("retry")
        self.assertEqual((r["what"], r["id"], r["attempt"]), ("file", 25, 1))
        self.assertEqual(self.kinds("waiting")[0]["reason"], "backoff")

    def test_job_file_429_retry_after_trips_shared_gate_for_api_calls(self):
        self.srv.file_script = {25: [(429, {"Retry-After": "9"})]}
        gate = ThrottleGate(clock=self.fc, waiter=fake_waiter(self.fc))
        remaining = []

        def cb(kind, data):
            if kind == "waiting":
                remaining.append(gate.remaining())
            self.on_event(kind, data)
        s = self.make(count=1, gate=gate, on_event=cb).run()
        self.assertEqual(s.downloaded, 1)
        self.assertEqual(self.kinds("waiting"),
                         [{"reason": "retry-after", "seconds": 9.0, "attempt": 1}])
        self.assertEqual(remaining, [9.0])      # tripped on the SHARED gate
        self.assertEqual(gate.last_reason, "retry-after")

    def test_job_file_404_not_retried_counts_failed(self):
        self.srv.file_script = {25: [404]}
        s = self.make(count=2).run()
        self.assertEqual((s.failed, s.downloaded, s.retries), (1, 2, 0))
        self.assertEqual(self.srv.file_log.count(25), 1)
        (f,) = self.kinds("file_failed")
        self.assertEqual(f["id"], 25)

    def test_job_file_fails_after_three_attempts_counts_failed_job_continues(self):
        self.srv.file_script = {25: [503, 503, 503]}
        s = self.make(count=2).run()
        self.assertEqual((s.failed, s.downloaded, s.retries), (1, 2, 2))
        self.assertEqual(self.srv.file_log.count(25), 3)
        (f,) = self.kinds("file_failed")
        self.assertTrue(f["error"].startswith("after 3 attempts: HTTP 503"))

    def test_job_file_empty_download_not_retried(self):
        self.srv.file_script = {25: [(200, {}, b"")]}
        s = self.make(count=1).run()
        self.assertEqual((s.failed, s.downloaded, s.retries), (1, 1, 0))
        self.assertEqual(self.srv.file_log.count(25), 1)
        self.assertEqual(self.kinds("file_failed")[0]["error"], "empty download")

    def test_job_without_retries_flag_keeps_single_attempt_file_behavior(self):
        self.srv.file_script = {25: [503]}
        gate = ThrottleGate(clock=self.fc, waiter=fake_waiter(self.fc))
        job = self.make(retries=False, count=1, gate=gate)
        s = job.run()
        self.assertEqual((s.failed, s.downloaded, s.retries), (1, 1, 0))
        self.assertEqual(self.srv.file_log.count(25), 1)
        self.assertEqual(gate.remaining(), 0.0)
        self.assertEqual(job.adaptive.level, 0)
        self.assertEqual(self.kinds("waiting"), [])


class ModelTests(ErrBase):
    def test_jobconfig_rejects_bad_policy_and_auto_retry(self):
        for kw in ({"unavailable_policy": "retry"}, {"unavailable_policy": None},
                   {"auto_retry_seconds": 0}, {"auto_retry_seconds": -1},
                   {"auto_retry_seconds": True}, {"auto_retry_seconds": "5"}):
            with self.assertRaises(ValueError, msg=str(kw)):
                JobConfig(include=["cat"], dest=self.tmp, **kw)
        cfg = JobConfig(include=["cat"], dest=self.tmp)
        self.assertEqual((cfg.unavailable_policy, cfg.auto_retry_seconds), ("stop", None))
        JobConfig(include=["cat"], dest=self.tmp, unavailable_policy="pause",
                  auto_retry_seconds=1.5)

    def test_summary_has_error_retries_waited_fields(self):
        s = Summary()
        self.assertEqual((s.error, s.error_kind, s.retries, s.waited_seconds),
                         (None, None, 0, 0.0))
        self.srv.api_script = [(503, b"", {}), (429, b"", {"Retry-After": "4"})]
        s = self.make().run()
        self.assertEqual(s.retries, 2)
        self.assertEqual(s.waited_seconds, 0.001 + 4.0)

    def test_job_page_size_unchanged_across_retries(self):
        self.srv.api_script = [(503, b"", {}), (500, b"", {})]
        self.make(page_size=7).run()
        sizes = {q["limit"] for q in self.json_requests()}
        self.assertEqual(sizes, {"7"})
        self.assertEqual(len(self.json_requests()), 3)


if __name__ == "__main__":
    unittest.main()
