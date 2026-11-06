import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from rule34_downloader.budget import RequestBudget, default_waiter, state_path


class Fake:
    def __init__(self, t=0.0):
        self.t = t
        self.waits = []

    def clock(self):
        return self.t

    def waiter(self, seconds, stop):
        self.waits.append(seconds)
        if stop is not None and stop.is_set():
            return False
        self.t += seconds
        return True


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "sub" / "state.json"
        self.f = Fake()

    def make(self, **kw):
        kw.setdefault("limit", 2)
        kw.setdefault("window", 100.0)
        return RequestBudget(path=self.path, clock=self.f.clock, waiter=self.f.waiter, **kw)

    def stamps(self):
        return json.loads(self.path.read_text())["timestamps"]

    def test_acquire_records_timestamp_and_persists(self):
        b = self.make()
        self.f.t = 5.0
        self.assertTrue(b.acquire())
        self.assertEqual(self.stamps(), [5.0])
        self.assertEqual((b.used(), b.remaining()), (1, 1))

    def test_acquire_blocks_at_limit_until_oldest_expires(self):
        b = self.make()
        b.acquire(); self.f.t = 10.0; b.acquire()
        self.assertEqual(b.next_available_in(), 90.0)
        self.assertTrue(b.acquire())
        self.assertEqual(self.f.waits, [90.0])
        self.assertEqual(self.f.t, 100.0)
        self.assertEqual(b.used(), 2)

    def test_old_timestamps_pruned_at_exactly_window(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"timestamps": [0.0, 1.0]}))
        self.f.t = 99.9
        b = self.make()
        self.assertEqual(b.used(), 2)
        self.f.t = 100.0
        self.assertEqual(b.used(), 1)
        self.f.t = 101.0
        self.assertEqual(b.used(), 0)

    def test_state_survives_new_instance(self):
        self.make().acquire()
        self.assertEqual(self.make().used(), 1)

    def test_stop_interrupts_budget_wait(self):
        b = self.make()
        b.acquire(); b.acquire()
        stop = threading.Event()
        stop.set()
        self.assertFalse(b.acquire(stop))
        self.assertEqual(b.used(), 2)

    def test_default_limit_is_55(self):
        self.assertEqual(RequestBudget(path=self.path).limit, 55)

    def test_retries_count_against_budget(self):
        b = self.make(limit=3)
        for _ in range(3):
            b.acquire()
        self.assertEqual(b.remaining(), 0)
        b2 = self.make(limit=3)
        self.assertEqual(b2.next_available_in(), 100.0)

    def test_corrupt_state_file_treated_as_empty(self):
        self.path.parent.mkdir(parents=True)
        for junk in ("{not json", '{"timestamps": 5}', "[1,2]", '"x"', "", '{"timestamps": null}'):
            self.path.write_text(junk)
            b = self.make()
            self.assertEqual(b.used(), 0, junk)
        self.path.write_text("{bad")
        self.assertTrue(self.make().acquire())
        self.assertEqual(self.stamps(), [0.0])

    def test_state_path_env_override(self):
        self.assertEqual(state_path({"R34_STATE": "/x/s.json"}), Path("/x/s.json"))
        self.assertEqual(state_path({"R34_STATE": "", "XDG_STATE_HOME": "/xs"}, "posix"),
                         Path("/xs/rule34-downloader/state.json"))

    def test_state_path_windows_default(self):
        p = state_path({"LOCALAPPDATA": "C:/Local"}, platform="nt")
        self.assertEqual(p, Path("C:/Local") / "Rule34Downloader" / "state.json")
        p = state_path({}, platform="nt")
        self.assertEqual(p, Path.home() / "AppData" / "Local" / "Rule34Downloader" / "state.json")

    def test_state_path_linux_default(self):
        self.assertEqual(state_path({}, platform="posix"),
                         Path.home() / ".local" / "state" / "rule34-downloader" / "state.json")

    def test_limit_below_one_rejected(self):
        for bad in (0, -3):
            with self.assertRaises(ValueError):
                RequestBudget(limit=bad, path=self.path)

    def test_default_waiter_semantics(self):
        stop = threading.Event()
        self.assertTrue(default_waiter(0.01, stop))
        stop.set()
        self.assertFalse(default_waiter(5.0, stop))
        t0 = time.monotonic()
        self.assertTrue(default_waiter(0.01, None))
        self.assertLess(time.monotonic() - t0, 0.5)

    def test_future_and_invalid_timestamps_dropped(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"timestamps": [500.0, true, "7", null, [1], 60, 40, NaN]}')
        self.f.t = 50.0
        self.assertEqual(self.make().used(), 1)  # only 40 survives; 500 and 60 are in the future


    def test_acquire_with_stop_set_and_room_acquires(self):
        stop = threading.Event()
        stop.set()
        self.assertTrue(self.make().acquire(stop))
        self.assertEqual(self.stamps(), [0.0])


if __name__ == "__main__":
    unittest.main()
