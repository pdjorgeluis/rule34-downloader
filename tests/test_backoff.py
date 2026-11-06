import random
import unittest

from rule34_downloader.backoff import Backoff, GiveUpClock


class CountingRng(random.Random):
    calls = 0

    def uniform(self, a, b):
        self.calls += 1
        return super().uniform(a, b)


class BackoffTest(unittest.TestCase):
    def test_backoff_sequence_5_10_20_40_capped_300(self):
        b = Backoff(jitter=0)
        self.assertEqual([b.delay(a) for a in range(1, 8)], [5, 10, 20, 40, 80, 160, 300])

    def test_backoff_jitter_within_20_percent_seeded(self):
        b = Backoff(rng=random.Random(42))
        for a in range(1, 5):
            d = b.delay(a)
            nominal = 5.0 * 2 ** (a - 1)
            self.assertTrue(0.8 * nominal <= d <= 1.2 * nominal, (a, d))
        self.assertEqual(Backoff(rng=random.Random(7)).delay(2), Backoff(rng=random.Random(7)).delay(2))
        self.assertLessEqual(Backoff(rng=random.Random(1)).delay(20), 300.0)

    def test_retry_after_overrides_computed_delay(self):
        self.assertEqual(Backoff().delay(3, retry_after=7.0), 7.0)
        self.assertEqual(Backoff().delay(1, retry_after=0.0), 0.0)
        self.assertEqual(Backoff(cap=10).delay(1, retry_after=3600.0), 3600.0)

    def test_backoff_rejects_bad_arguments(self):
        for kw in ({"base": 0}, {"base": -1}, {"factor": 0.5}, {"cap": 1.0}, {"jitter": -0.1}, {"jitter": 1.1}):
            with self.assertRaises(ValueError, msg=kw):
                Backoff(**kw)
        b = Backoff()
        for bad in (0, -1, 1.5, "2", None, True):
            with self.assertRaises(ValueError, msg=bad):
                b.delay(bad)

    def test_giveup_clock_expires_after_window(self):
        t = [100.0]
        c = GiveUpClock(window=30, clock=lambda: t[0])
        self.assertEqual(c.elapsed(), 0.0)
        self.assertFalse(c.expired())
        c.failure()
        t[0] = 120.0
        c.failure()  # keeps original start
        self.assertEqual(c.elapsed(), 20.0)
        self.assertFalse(c.expired())
        t[0] = 130.0
        self.assertTrue(c.expired())
        with self.assertRaises(ValueError):
            GiveUpClock(window=0)

    def test_giveup_clock_resets_on_success(self):
        t = [0.0]
        c = GiveUpClock(window=10, clock=lambda: t[0])
        c.failure()
        t[0] = 50.0
        self.assertTrue(c.expired())
        c.success()
        self.assertFalse(c.expired())
        self.assertEqual(c.elapsed(), 0.0)
        c.failure()
        t[0] = 55.0
        self.assertEqual(c.elapsed(), 5.0)

    def test_backoff_calls_rng_once_and_not_with_retry_after(self):
        r = CountingRng(1)
        b = Backoff(rng=r)
        b.delay(1)
        self.assertEqual(r.calls, 1)
        b.delay(2, retry_after=3.0)
        self.assertEqual(r.calls, 1)
        r2 = CountingRng(1)
        Backoff(jitter=0, rng=r2).delay(1)
        self.assertEqual(r2.calls, 0)

    def test_backoff_large_attempt_does_not_overflow(self):
        self.assertEqual(Backoff(jitter=0).delay(10**6), 300.0)
        self.assertEqual(Backoff(rng=random.Random(3)).delay(5000) <= 300.0, True)
        self.assertEqual(Backoff(factor=10.0, cap=1e300, jitter=0).delay(10**9) > 0, True)


if __name__ == "__main__":
    unittest.main()
