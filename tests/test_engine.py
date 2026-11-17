import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mock_api import API_KEY, USER_ID, MockServer, file_body  # noqa: E402
from rule34_downloader.api import ApiClient, ApiError  # noqa: E402
from rule34_downloader.config import Credentials  # noqa: E402
from rule34_downloader.engine import Job, JobConfig  # noqa: E402

ALL = {"pictures", "gifs", "videos"}


class EngineBase(unittest.TestCase):
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

    def on_event(self, kind, data):
        with self._lock:
            self.events.append((kind, data))

    def kinds(self, kind):
        with self._lock:
            return [d for k, d in self.events if k == kind]

    def make(self, include=("cat",), on_event="default", **kw):
        kw.setdefault("delay", 0)
        cfg = JobConfig(include=list(include), dest=self.tmp, **kw)
        client = ApiClient(Credentials(USER_ID, API_KEY))
        cb = self.on_event if on_event == "default" else on_event
        return Job(cfg, client, on_event=cb)

    def files(self, sub="cat/pictures"):
        d = self.tmp / sub
        return sorted(p.name for p in d.iterdir()) if d.exists() else []

    def ids(self, sub="cat/pictures"):
        return sorted(int(n.split(".")[0]) for n in self.files(sub))


class EngineTests(EngineBase):
    def test_downloads_count_files_newest_first(self):
        s = self.make(count=3).run()
        self.assertEqual((s.downloaded, s.reason), (3, "count_reached"))
        self.assertEqual(self.ids(), [21, 22, 25])
        data = (self.tmp / "cat/pictures/25.jpg").read_bytes()
        self.assertEqual(data, file_body(25))

    def test_oldest_downloads_ascending_ids(self):
        self.make(count=3, order="oldest").run()
        self.assertEqual(self.ids(), [1, 2, 5])
        done = [d["id"] for d in self.kinds("file_done")]
        self.assertEqual(done, [1, 2, 5])

    def test_pagination_across_pages(self):
        s = self.make(count=8, page_size=5).run()
        self.assertEqual(s.downloaded, 8)
        pids = [q["pid"] for q in self.srv.api_requests()]
        self.assertEqual(pids, [str(i) for i in range(len(pids))])
        self.assertGreaterEqual(s.pages, 2)

    def test_all_stops_at_end_of_results(self):
        self.srv.total = 12
        s = self.make(page_size=5).run()
        self.assertEqual(s.reason, "end_of_results")
        self.assertEqual(s.downloaded, 8)
        self.assertEqual(s.total, 12)

    def test_exact_page_boundary_end_of_results(self):
        self.srv.total = 10
        s = self.make(page_size=5).run()
        self.assertEqual(s.reason, "end_of_results")
        self.assertEqual(s.pages, 2)
        fetches = [q for q in self.srv.api_requests() if "json" in q]
        self.assertEqual(len(fetches), 3)
        self.assertEqual(s.requests_made, 4)

    def test_unknown_tag_finishes_with_zero(self):
        s = self.make(include=["zzqxnone"], count=5).run()
        self.assertEqual((s.downloaded, s.pages, s.reason), (0, 0, "end_of_results"))
        self.assertEqual(self.kinds("finished")[0]["summary"], s)

    def test_types_pictures_only_skips_gifs_and_videos(self):
        self.srv.total = 10
        s = self.make().run()
        self.assertEqual((s.downloaded, s.skipped_type), (6, 4))
        self.assertEqual(len(self.kinds("file_skipped")), 4)
        self.assertEqual(self.files("cat/gifs"), [])

    def test_types_each_in_own_subfolder(self):
        self.srv.total = 10
        self.make(types=ALL).run()
        self.assertEqual(self.ids("cat/pictures"), [1, 2, 5, 6, 7, 10])
        self.assertEqual(self.ids("cat/gifs"), [3, 8])
        self.assertEqual(self.ids("cat/videos"), [4, 9])

    def _precreate(self, post_id, name, content=b"old"):
        p = self.tmp / "cat/pictures" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p

    def test_count_counts_only_new_files_not_existing(self):
        self._precreate(25, "25.jpg")
        self._precreate(22, "22.png")
        s = self.make(count=2).run()
        self.assertEqual((s.downloaded, s.existing), (2, 2))
        self.assertEqual(self.ids(), [20, 21, 22, 25])
        self.assertEqual((self.tmp / "cat/pictures/25.jpg").read_bytes(), b"old")

    def test_resume_skips_existing_complete_files(self):
        self.srv.total = 10
        self.make().run()
        self.srv.file_log.clear()
        s = self.make().run()
        self.assertEqual((s.downloaded, s.existing), (0, 6))
        self.assertEqual(self.srv.file_log, [])

    def test_zero_byte_file_is_redownloaded(self):
        p = self._precreate(25, "25.jpg", b"")
        s = self.make(count=1).run()
        self.assertEqual(s.downloaded, 1)
        self.assertEqual(p.read_bytes(), file_body(25))

    def test_part_file_ignored_and_replaced(self):
        part = self._precreate(25, "25.jpg.part", b"garbage")
        s = self.make(count=1).run()
        self.assertEqual(s.downloaded, 1)
        self.assertFalse(part.exists())
        self.assertEqual((self.tmp / "cat/pictures/25.jpg").read_bytes(), file_body(25))

    def test_failed_download_does_not_count_and_job_continues(self):
        self.srv.fail_ids = {22}
        s = self.make(count=3).run()
        self.assertEqual((s.downloaded, s.failed, s.reason), (3, 1, "count_reached"))
        self.assertEqual(self.ids(), [20, 21, 25])
        self.assertEqual(self.kinds("file_failed")[0]["id"], 22)

    def test_failed_download_leaves_no_part_file(self):
        self.srv.fail_ids = {25, 22}
        self.make(count=2).run()
        leftovers = [p for p in self.tmp.rglob("*") if p.name.endswith(".part")]
        self.assertEqual(leftovers, [])
        self.assertNotIn(25, self.ids())

    def test_workers_5_download_all_without_duplicates(self):
        s = self.make(types=ALL, workers=5).run()
        self.assertEqual(s.downloaded, 25)
        self.assertEqual(sorted(self.srv.file_log), list(range(1, 26)))
        self.assertEqual(len(self.kinds("file_done")), 25)

    def test_workers_100_download_all_without_duplicates(self):
        s = self.make(types=ALL, workers=100).run()
        self.assertEqual(s.downloaded, 25)
        self.assertEqual(sorted(self.srv.file_log), list(range(1, 26)))
        self.assertEqual(len(self.kinds("file_done")), 25)

    def test_workers_boundary_100_ok_101_rejected(self):
        self.assertEqual(JobConfig(include=["cat"], dest=self.tmp, workers=100).workers, 100)
        with self.assertRaisesRegex(ValueError, "workers must be an int from 1 to 100"):
            JobConfig(include=["cat"], dest=self.tmp, workers=101)

    def test_workers_never_exceed_count(self):
        self.srv.slow = 0.05
        s = self.make(count=3, workers=5).run()
        self.assertEqual(s.downloaded, 3)
        self.assertEqual(len(self.srv.file_log), 3)
        self.assertEqual(len(set(self.srv.file_log)), 3)
        self.assertEqual(len(self.ids()), 3)

    def test_workers_run_concurrently(self):
        self.srv.slow = 0.2
        self.srv.total = 8
        self.make(types=ALL, workers=4).run()
        self.assertGreaterEqual(self.srv.max_active, 2)
        self.assertLessEqual(self.srv.max_active, 4)

    def test_workers_out_of_range_rejected(self):
        for w in (0, 101, -1):
            with self.assertRaises(ValueError):
                JobConfig(include=["cat"], dest=self.tmp, workers=w)

    def test_stop_returns_stopped_summary(self):
        self.srv.slow = 0.02
        self.srv.total = 100
        holder = {}

        def cb(kind, data):
            if kind == "file_done" and len(self.kinds("file_done")) >= 1:
                holder["job"].stop()
            self.on_event(kind, data)

        job = self.make(types=ALL, workers=2, on_event=cb)
        holder["job"] = job
        s = job.run()
        self.assertEqual(s.reason, "stopped")
        self.assertGreaterEqual(s.downloaded, 1)
        self.assertLess(s.downloaded, 100)
        self.assertEqual([p for p in self.tmp.rglob("*.part")], [])
        self.assertEqual(self.kinds("finished")[-1]["summary"], s)

    def test_pause_blocks_new_downloads_until_resume(self):
        self.srv.total = 20
        job = self.make(types=ALL)
        job.pause()
        res = {}
        t = threading.Thread(target=lambda: res.update(s=job.run()))
        t.start()
        time.sleep(0.3)
        self.assertEqual(self.srv.api_requests(), [])
        self.assertEqual(self.srv.file_log, [])
        job.resume()
        t.join(10)
        self.assertEqual(res["s"].downloaded, 20)

        # pause in the middle of a run
        self.srv.file_log.clear()
        self.srv.slow = 0.05
        self.events.clear()
        tmp2 = self.tmp / "second"
        cfg = JobConfig(include=["cat"], dest=tmp2, types=ALL, delay=0)
        started = threading.Event()

        def cb(kind, data):
            if kind == "file_done":
                started.set()

        job2 = Job(cfg, ApiClient(Credentials(USER_ID, API_KEY)), on_event=cb)
        t2 = threading.Thread(target=lambda: res.update(s2=job2.run()))
        t2.start()
        self.assertTrue(started.wait(5))
        job2.pause()
        time.sleep(0.4)
        n1 = len(self.srv.file_log)
        time.sleep(0.4)
        n2 = len(self.srv.file_log)
        self.assertEqual(n1, n2)
        self.assertLess(n2, 20)
        job2.resume()
        t2.join(15)
        self.assertEqual(res["s2"].downloaded, 20)

    def test_events_sequence(self):
        job = self.make(count=2)
        s = job.run()
        with self._lock:
            kinds = [k for k, _ in self.events]
        self.assertEqual(kinds[0], "started")
        self.assertEqual(kinds[-1], "finished")
        self.assertLess(kinds.index("page"), kinds.index("file_done"))
        self.assertEqual(kinds.count("finished"), 1)
        started = self.events[0][1]
        self.assertEqual(started["query"], job.query)
        self.assertIsNone(started["total"])
        page = self.kinds("page")[0]
        self.assertEqual((page["pid"], page["posts"]), (0, 25))
        done = self.kinds("file_done")[0]
        self.assertEqual(set(done), {"id", "type", "path", "bytes"})
        self.assertIs(self.events[-1][1]["summary"], s)

    def test_callback_exception_does_not_kill_job(self):
        def boom(kind, data):
            raise RuntimeError("callback bug")

        s = self.make(count=3, on_event=boom).run()
        self.assertEqual(s.downloaded, 3)

    def test_by_search_false_layout(self):
        self.make(count=1, by_search=False).run()
        self.assertEqual(self.ids("pictures"), [25])
        self.assertFalse((self.tmp / "cat").exists())

    def test_safety_adds_exclusions_to_query_sent(self):
        job = self.make(count=1, safety=True)
        job.run()
        sent = self.srv.api_requests()[0]["tags"]
        self.assertEqual(sent, job.query)
        for t in ("loli", "shota", "child", "toddler", "underage"):
            self.assertIn("-" + t, sent.split())

    def test_total_fetched_only_when_count_is_none(self):
        self.srv.total = 6
        s = self.make().run()
        self.assertEqual(s.total, 6)
        self.assertEqual(len([q for q in self.srv.api_requests() if "json" not in q]), 1)
        self.srv.log.clear()
        s = self.make(count=1).run()
        self.assertIsNone(s.total)
        self.assertEqual([q for q in self.srv.api_requests() if "json" not in q], [])

    def test_count_zero_or_negative_rejected(self):
        for c in (0, -3):
            with self.assertRaises(ValueError):
                JobConfig(include=["cat"], dest=self.tmp, count=c)

    def test_types_empty_rejected(self):
        with self.assertRaises(ValueError):
            JobConfig(include=["cat"], dest=self.tmp, types=set())
        with self.assertRaises(ValueError):
            JobConfig(include=["cat"], dest=self.tmp, types={"audio"})

    def test_stop_interrupts_delay_sleep(self):
        done = threading.Event()

        def cb(kind, data):
            if kind == "file_done":
                done.set()

        job = self.make(count=3, delay=30, on_event=cb)
        res = {}
        t = threading.Thread(target=lambda: res.update(s=job.run()))
        t0 = time.monotonic()
        t.start()
        self.assertTrue(done.wait(5))
        time.sleep(0.2)
        job.stop()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual(res["s"].reason, "stopped")
        self.assertEqual(res["s"].downloaded, 1)

    def test_duplicate_post_ids_ignored(self):
        self.srv.custom_posts = [10, 9, 8, 8, 7, 6]
        s = self.make(types=ALL, page_size=3).run()
        self.assertEqual(s.downloaded, 5)
        self.assertEqual(self.srv.file_log.count(8), 1)
        self.assertEqual(len(self.kinds("file_done")), 5)

    def test_stop_before_run_makes_no_api_calls(self):
        job = self.make(count=3)
        job.stop()
        s = job.run()
        self.assertEqual((s.reason, s.pages, s.downloaded, s.requests_made), ("stopped", 0, 0, 0))
        self.assertEqual(self.srv.api_requests(), [])
        with self._lock:
            kinds = [k for k, _ in self.events]
        self.assertEqual(kinds, ["started", "finished"])


class RecordingLimiter:
    """Stands in for StartLimiter: records the interval, never blocks."""

    def __init__(self):
        self.intervals = []

    def acquire(self, stop, interval):
        self.intervals.append(interval)
        return not stop.is_set()


class SharedLimiterTests(EngineBase):
    def job(self, limiter, adaptive=None, **kw):
        kw.setdefault("delay", 0)
        cfg = JobConfig(include=["cat"], dest=self.tmp, **kw)
        client = ApiClient(Credentials(USER_ID, API_KEY))
        return Job(cfg, client, on_event=self.on_event, limiter=limiter, adaptive=adaptive)

    def test_engine_interval_is_delay_over_workers(self):
        lim = RecordingLimiter()
        s = self.job(lim, count=4, delay=2.0, workers=4).run()
        self.assertEqual(s.downloaded, 4)
        self.assertEqual(lim.intervals, [0.5] * 4)
        lim = RecordingLimiter()
        self.job(lim, count=1, delay=0.5, workers=1).run()
        self.assertEqual(lim.intervals, [0.5])

    def test_adaptive_level_raises_interval(self):
        from rule34_downloader.resilience import Adaptive
        ad = Adaptive(base_delay=0.5)
        self.assertTrue(ad.overload())          # level 1: 1 worker, delay x2
        lim = RecordingLimiter()
        self.job(lim, adaptive=ad, count=2, delay=2.0, workers=4).run()
        self.assertEqual(lim.intervals, [4.0, 4.0])   # 2.0*2 / 1 worker

    def test_download_worker_does_not_sleep_after_file(self):
        job = self.job(RecordingLimiter(), count=2, delay=30.0, workers=1)
        waits = []
        real_wait = job._stop.wait
        job._stop.wait = lambda *a, **k: (waits.append(a), real_wait(*a, **k))[1]
        t0 = time.monotonic()
        s = job.run()
        self.assertLess(time.monotonic() - t0, 5)
        self.assertEqual(s.downloaded, 2)
        self.assertEqual(waits, [])

    def test_real_limiter_spaces_file_starts_across_workers(self):
        s = self.job(None, count=4, delay=0.2, workers=2).run()
        self.assertEqual(s.downloaded, 4)
        done = [d for d in self.kinds("file_done")]
        self.assertEqual(len(done), 4)


class StopPromptnessTests(EngineBase):
    """Review Focus: Stop while paused / in a retry backoff / in the
    unavailable wait ends the job within 2 s (no missed wake-ups)."""

    def run_in_thread(self, job):
        box = {}

        def target():
            box["s"] = job.run()
        t = threading.Thread(target=target, daemon=True)
        t.start()
        return t, box

    def wait_for(self, kind, timeout=3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.kinds(kind):
                return True
            time.sleep(0.005)
        return False

    def assert_stops_within_2s(self, job, t, box):
        t0 = time.monotonic()
        job.stop()
        t.join(2.0)
        self.assertFalse(t.is_alive(), "job did not end within 2 s of stop()")
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual(box["s"].reason, "stopped")

    def test_stop_while_paused_ends_within_2s(self):
        job = self.make(count=20, delay=0)
        t, box = self.run_in_thread(job)
        self.assertTrue(self.wait_for("page"))
        job.pause()
        time.sleep(0.2)
        self.assert_stops_within_2s(job, t, box)

    def test_stop_while_paused_before_first_page_ends_within_2s(self):
        job = self.make(count=20, delay=0)
        job.pause()
        t, box = self.run_in_thread(job)
        time.sleep(0.2)
        self.assert_stops_within_2s(job, t, box)

    def test_stop_during_file_retry_backoff_ends_within_2s(self):
        from rule34_downloader.backoff import Backoff
        self.srv.file_script = {i: [503] * 3 for i in range(1, 26)}
        cfg = JobConfig(include=["cat"], dest=self.tmp, delay=0, count=2, workers=2)
        job = Job(cfg, ApiClient(Credentials(USER_ID, API_KEY)), on_event=self.on_event,
                  retries=True, backoff=Backoff(base=600, cap=600, jitter=0))
        t, box = self.run_in_thread(job)
        self.assertTrue(self.wait_for("waiting"))
        self.assert_stops_within_2s(job, t, box)

    def test_stop_during_unavailable_wait_ends_within_2s(self):
        self.srv.api_script = [(500, b"", {})]
        cfg = JobConfig(include=["cat"], dest=self.tmp, delay=0, count=2,
                        unavailable_policy="pause")
        job = Job(cfg, ApiClient(Credentials(USER_ID, API_KEY)), on_event=self.on_event)
        t, box = self.run_in_thread(job)
        self.assertTrue(self.wait_for("server_unavailable"))
        time.sleep(0.1)
        self.assert_stops_within_2s(job, t, box)

    def test_unavailable_wait_wakes_on_stop_and_retry_now_without_polling(self):
        from rule34_downloader.budget import default_waiter
        for action, expect in (("stop", None), ("retry_now", "retry_now")):
            calls = []

            def waiter(seconds, ev, calls=calls):
                calls.append(seconds)
                return default_waiter(seconds, ev)
            cfg = JobConfig(include=["cat"], dest=self.tmp, delay=0)
            job = Job(cfg, ApiClient(Credentials(USER_ID, API_KEY)), waiter=waiter)
            box = {}
            t = threading.Thread(target=lambda: box.update(r=job._unavailable_wait()),
                                 daemon=True)
            t.start()
            time.sleep(0.5)                   # a 50 ms poll would call ~10 times
            getattr(job, action)()
            t.join(2.0)
            self.assertFalse(t.is_alive(), action)
            self.assertEqual(box["r"], expect, action)
            self.assertLessEqual(len(calls), 2, (action, calls))

    def test_unavailable_wait_auto_retry_needs_no_polling(self):
        calls = []
        cfg = JobConfig(include=["cat"], dest=self.tmp, delay=0, auto_retry_seconds=0.3)

        def waiter(seconds, ev):
            calls.append(seconds)
            return not ev.wait(seconds)
        job = Job(cfg, ApiClient(Credentials(USER_ID, API_KEY)), waiter=waiter)
        self.assertEqual(job._unavailable_wait(), "auto_retry")
        self.assertLessEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
