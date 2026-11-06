import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rule34_downloader.backoff import Backoff, GiveUpClock
from rule34_downloader.errors import PermanentError, ServerUnavailable, TemporaryError
from rule34_downloader.resilience import (
    Adaptive, ResilientClient, Stopped, ThrottleGate, check_file_reply,
)
from fakes import FakeClock, fake_waiter  # noqa: E402


class GateTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.waits = []
        base = fake_waiter(self.clock)

        def waiter(s, stop):
            self.waits.append(s)
            return base(s, stop)
        self.gate = ThrottleGate(clock=self.clock, waiter=waiter)

    def test_gate_wait_returns_immediately_when_not_tripped(self):
        self.assertTrue(self.gate.wait())
        self.assertEqual(self.waits, [])

    def test_gate_trip_blocks_until_time_passes(self):
        self.gate.trip(2.0, "backoff")
        self.assertEqual(self.gate.remaining(), 2.0)
        self.assertTrue(self.gate.wait())
        self.assertEqual(self.clock(), 2.0)
        self.assertEqual(self.gate.remaining(), 0.0)
        self.gate.trip(0)
        self.gate.trip(-3)
        self.assertEqual(self.gate.remaining(), 0.0)

    def test_gate_trip_keeps_the_later_deadline(self):
        self.gate.trip(10.0, "long")
        self.gate.trip(3.0, "short")
        self.assertEqual(self.gate.remaining(), 10.0)
        self.assertEqual(self.gate.last_reason, "long")
        self.gate.trip(20.0, "longer")
        self.assertEqual(self.gate.remaining(), 20.0)
        self.assertEqual(self.gate.last_reason, "longer")

    def test_gate_wait_interrupted_by_stop(self):
        stop = threading.Event()
        self.gate.trip(5.0)
        stop.set()
        self.assertFalse(self.gate.wait(stop))
        # stop set during the wait
        stop.clear()
        base = fake_waiter(self.clock)

        def waiter(s, st):
            st.set()
            return base(s, st)
        g = ThrottleGate(clock=self.clock, waiter=waiter)
        g.trip(5.0)
        self.assertFalse(g.wait(stop))
        # a tripped gate with stop set does not block when time already passed
        self.assertTrue(ThrottleGate(clock=self.clock).wait(stop))

    def test_gate_retry_now_releases_waiters(self):
        gate = ThrottleGate(clock=self.clock, waiter=lambda s, stop: not stop.wait(0.01),
                            slice_seconds=0.01)
        gate.trip(1000.0)
        stop = threading.Event()
        results = []
        threads = [threading.Thread(target=lambda: results.append(gate.wait(stop)))
                   for _ in range(2)]
        for t in threads:
            t.start()
        gate.retry_now()
        for t in threads:
            t.join(2)
        self.assertEqual(results, [True, True])
        self.assertEqual(gate.remaining(), 0.0)

    def test_gate_shared_by_threads_all_wait(self):
        arrived = threading.Barrier(3)
        gate = ThrottleGate(clock=self.clock, waiter=lambda s, stop: (arrived.wait(2), self.clock.advance(s))[1] or True,
                            slice_seconds=100.0)
        gate.trip(7.0)
        results = []

        def work():
            results.append(gate.wait())
        threads = [threading.Thread(target=work) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(3)
        self.assertEqual(results, [True, True, True])
        self.assertGreaterEqual(self.clock(), 7.0)
        self.assertEqual(gate.remaining(), 0.0)

    def test_gate_wait_uses_slices_of_at_most_quarter_second(self):
        self.gate.trip(1.0)
        self.assertTrue(self.gate.wait())
        self.assertTrue(self.waits)
        self.assertLessEqual(max(self.waits), 0.25)
        self.assertAlmostEqual(sum(self.waits), 1.0)


class AdaptiveTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(1000.0)
        self.a = Adaptive(clock=self.clock)

    def test_adaptive_starts_at_level_zero_uses_configured_values(self):
        self.assertEqual(self.a.level, 0)
        self.assertEqual(self.a.effective_workers(5), 5)
        self.assertEqual(self.a.effective_delay(0.5), 0.5)
        self.assertIsInstance(self.a.effective_delay(2), float)

    def test_adaptive_overload_sets_one_worker_and_doubles_delay(self):
        self.assertTrue(self.a.overload())
        self.assertEqual((self.a.level, self.a.effective_workers(5)), (1, 1))
        self.assertEqual(self.a.effective_delay(1.0), 2.0)

    def test_adaptive_second_overload_quadruples_delay_capped_at_level_3(self):
        delays = []
        for _ in range(5):
            self.assertIn(self.a.overload(), (True, False))
            delays.append(self.a.effective_delay(1.0))
            self.clock.advance(2.0)
        self.assertEqual(delays, [2.0, 4.0, 8.0, 8.0, 8.0])
        self.assertEqual(self.a.level, 3)
        self.assertFalse(self.a.overload())
        self.assertEqual(self.a.level, 3)

    def test_adaptive_steps_up_one_level_per_600_clean_seconds(self):
        for _ in range(3):
            self.a.overload()
            self.clock.advance(2.0)
        self.assertEqual(self.a.level, 3)
        self.clock.advance(598.0 + 1)  # 600+ since the last overload
        self.assertEqual(self.a.level, 2)
        self.clock.advance(600.0)
        self.assertEqual(self.a.level, 1)
        self.clock.advance(600.0)
        self.assertEqual(self.a.level, 0)

    def test_adaptive_overload_resets_recovery_timer(self):
        self.a.overload()
        self.clock.advance(599.0)
        self.assertEqual(self.a.level, 1)
        self.a.overload()  # level 2, timer restarts
        self.clock.advance(599.0)
        self.assertEqual(self.a.level, 2)
        self.clock.advance(2.0)
        self.assertEqual(self.a.level, 1)

    def test_adaptive_zero_delay_uses_half_second_base(self):
        self.a.overload()
        self.assertEqual(self.a.effective_delay(0), 1.0)
        self.clock.advance(2.0)
        self.a.overload()
        self.assertEqual(self.a.effective_delay(0), 2.0)

    def test_adaptive_burst_within_debounce_counts_once(self):
        self.assertTrue(self.a.overload())
        self.clock.advance(0.5)
        self.assertFalse(self.a.overload())
        self.assertFalse(self.a.overload())
        self.assertEqual(self.a.level, 1)
        self.clock.advance(1.0)
        self.assertTrue(self.a.overload())
        self.assertEqual(self.a.level, 2)


class FakeBudget:
    def __init__(self, remaining=5, next_in=0.0, acquire_result=True):
        self.left, self.next_in, self.result = remaining, next_in, acquire_result
        self.acquired = 0

    def remaining(self):
        return self.left

    def next_available_in(self):
        return self.next_in

    def acquire(self, stop=None):
        if not self.result:
            return False
        self.acquired += 1
        self.left = max(0, self.left - 1)
        return True


class Wrapped:
    credentials = "creds"

    def __init__(self, script):
        self.script, self.calls, self.requests_made = list(script), [], 0

    def fetch_posts(self, query, page_size, pid):
        self.calls.append((query, page_size, pid))
        self.requests_made += 1
        item = self.script.pop(0) if self.script else [{"id": 1}]
        if isinstance(item, BaseException):
            raise item
        return item

    def total_count(self, query):
        self.calls.append(("count", query))
        item = self.script.pop(0) if self.script else 42
        if isinstance(item, BaseException):
            raise item
        return item


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.events = []

    def make(self, script, window=1800.0, **kw):
        self.wrapped = Wrapped(script)
        kw.setdefault("gate", ThrottleGate(clock=self.clock, waiter=fake_waiter(self.clock)))
        kw.setdefault("backoff", Backoff(jitter=0))
        kw.setdefault("giveup", GiveUpClock(window=window, clock=self.clock))
        kw.setdefault("adaptive", Adaptive(clock=self.clock))
        kw.setdefault("emit", lambda k, d: self.events.append((k, d)))
        return ResilientClient(self.wrapped, **kw)

    def kinds(self):
        return [k for k, _ in self.events]

    def test_retry_then_success_returns_posts(self):
        c = self.make([TemporaryError("a", status=503), TemporaryError("b", status=503), [{"id": 9}]])
        self.assertEqual(c.fetch_posts("cat", 100, 3), [{"id": 9}])
        self.assertEqual(len(self.wrapped.calls), 3)
        self.assertEqual(round(self.clock()), 15)
        self.assertEqual(c.credentials, "creds")
        self.assertEqual(c.requests_made, 3)
        retry = [d for k, d in self.events if k == "retry"]
        self.assertEqual([r["attempt"] for r in retry], [1, 2])
        self.assertEqual(retry[0]["what"], "page")
        self.assertEqual(retry[0]["pid"], 3)
        self.assertIn("adaptive", self.kinds())

    def test_adaptive_event_payload_has_single_worker_and_delay_factor(self):
        c = self.make([TemporaryError("a", status=503), TemporaryError("b", status=503), []])
        c.fetch_posts("cat", 100, 0)
        adaptive = [d for k, d in self.events if k == "adaptive"]
        self.assertEqual(adaptive, [
            {"level": 1, "single_worker": True, "delay_factor": 2.0},
            {"level": 2, "single_worker": True, "delay_factor": 4.0},
        ])
        self.assertEqual(list(adaptive[0]), ["level", "single_worker", "delay_factor"])

    def test_retry_after_used_exactly_for_wait(self):
        c = self.make([TemporaryError("slow", status=429, retry_after=7.0), [{"id": 1}]])
        c.fetch_posts("cat", 100, 0)
        self.assertEqual(self.clock(), 7.0)
        waiting = [d for k, d in self.events if k == "waiting"]
        self.assertEqual(waiting, [{"reason": "retry-after", "seconds": 7.0, "attempt": 1}])

    def test_each_retry_acquires_budget(self):
        b = FakeBudget()
        c = self.make([TemporaryError("a"), TemporaryError("b"), [{"id": 1}]], budget=b)
        c.fetch_posts("cat", 100, 0)
        self.assertEqual(b.acquired, 3)

    def test_permanent_error_not_retried_and_raised(self):
        c = self.make([PermanentError("no", status=403, kind="captcha")])
        with self.assertRaises(PermanentError):
            c.fetch_posts("cat", 100, 0)
        self.assertEqual(len(self.wrapped.calls), 1)
        self.assertEqual(self.events, [("permanent_error", {"kind": "captcha", "message": "no"})])

    def test_gives_up_with_server_unavailable_after_window(self):
        c = self.make([TemporaryError("boom", status=503)] * 99, window=30.0)
        with self.assertRaises(ServerUnavailable) as cm:
            c.fetch_posts("cat", 100, 0)
        self.assertEqual(cm.exception.status, 503)
        self.assertEqual(str(cm.exception), "server unavailable after 35s: boom")
        self.assertEqual(len(self.wrapped.calls), 4)

    def test_giveup_window_resets_after_success(self):
        c = self.make([TemporaryError("x"), [{"id": 1}], TemporaryError("y"), [{"id": 2}]],
                      window=30.0)
        self.assertEqual(c.fetch_posts("a", 10, 0), [{"id": 1}])
        self.clock.advance(1000.0)
        self.assertEqual(c.fetch_posts("a", 10, 1), [{"id": 2}])
        self.assertEqual(c.giveup.elapsed(), 0.0)

    def test_page_size_is_never_changed_on_retry(self):
        c = self.make([TemporaryError("a"), TemporaryError("b"), [{"id": 1}]])
        c.fetch_posts("cat", 1000, 0)
        self.assertEqual({call[1] for call in self.wrapped.calls}, {1000})

    def test_total_count_single_attempt_no_retry(self):
        c = self.make([TemporaryError("down", status=503)])
        with self.assertRaises(TemporaryError):
            c.total_count("cat")
        self.assertEqual(len(self.wrapped.calls), 1)
        self.assertEqual(self.events, [])
        self.assertEqual(c.giveup.elapsed(), 0.0)
        self.wrapped.script = [42]
        self.assertEqual(c.total_count("cat"), 42)

    def test_stop_during_backoff_raises_stopped(self):
        stop = threading.Event()
        base = fake_waiter(self.clock)

        def waiter(s, st):
            st.set()
            return base(s, st)
        gate = ThrottleGate(clock=self.clock, waiter=waiter)
        c = self.make([TemporaryError("a"), [{"id": 1}]], gate=gate, stop=stop)
        with self.assertRaises(Stopped):
            c.fetch_posts("cat", 100, 0)
        self.assertEqual(len(self.wrapped.calls), 1)
        # stop already set while the gate is tripped: total_count raises Stopped too
        gate2 = ThrottleGate(clock=self.clock, waiter=base)
        gate2.trip(5.0)
        with self.assertRaises(Stopped):
            self.make([], gate=gate2, stop=stop).total_count("q")

    def test_budget_exhaustion_emits_waiting_event_then_proceeds(self):
        b = FakeBudget(remaining=0, next_in=12.5)
        c = self.make([[{"id": 1}]], budget=b)
        self.assertEqual(c.fetch_posts("cat", 100, 0), [{"id": 1}])
        self.assertEqual(self.events,
                         [("waiting", {"reason": "budget", "seconds": 12.5, "attempt": 1})])
        self.assertEqual(b.acquired, 1)
        stopped = self.make([[{"id": 1}]], budget=FakeBudget(acquire_result=False))
        with self.assertRaises(Stopped):
            stopped.fetch_posts("cat", 100, 0)

    def test_unclassified_exception_propagates(self):
        c = self.make([ValueError("bug")])
        with self.assertRaises(ValueError):
            c.fetch_posts("cat", 100, 0)
        self.assertEqual(len(self.wrapped.calls), 1)
        self.assertEqual(self.events, [])

    def test_emit_exception_is_swallowed_and_retries_can_be_disabled(self):
        def bad(kind, data):
            raise RuntimeError("ui died")
        c = self.make([TemporaryError("a"), [{"id": 1}]], emit=bad)
        self.assertEqual(c.fetch_posts("cat", 100, 0), [{"id": 1}])
        off = self.make([TemporaryError("a"), [{"id": 1}]], retries_enabled=False)
        with self.assertRaises(TemporaryError):
            off.fetch_posts("cat", 100, 0)


class FileReplyTest(unittest.TestCase):
    def kind(self, status, headers=None):
        try:
            check_file_reply(status, headers)
        except (TemporaryError, PermanentError) as e:
            return type(e), e
        return None, None

    def test_file_reply_429_408_425_and_5xx_are_temporary(self):
        for s in (408, 425, 429, 500, 502, 503, 599):
            cls, e = self.kind(s)
            self.assertIs(cls, TemporaryError, s)
            self.assertEqual((e.kind, e.status, str(e)), ("http", s, f"HTTP {s}"))
        _, e = self.kind(429, {"retry-after": "9"})
        self.assertEqual(e.retry_after, 9.0)
        self.assertIsNone(self.kind(503, {"Retry-After": "9"})[1].retry_after)

    def test_file_reply_other_status_is_permanent_client(self):
        for s in (100, 301, 400, 403, 404, 410):
            cls, e = self.kind(s)
            self.assertIs(cls, PermanentError, s)
            self.assertEqual((e.kind, str(e)), ("client", f"HTTP {s}"))

    def test_file_reply_200_is_ok(self):
        for s in (200, 204, 206, 299):
            self.assertIsNone(check_file_reply(s))


if __name__ == "__main__":
    unittest.main()
