"""Search-then-download core: count query, found texts, SearchRunner (fakes only, no network)."""
import os
import socket
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false;*.warning=false")

from test_gui_shell import HAVE_QT, dispose  # noqa: E402

if HAVE_QT:
    from PySide6.QtCore import QCoreApplication  # noqa: E402
    from PySide6.QtWidgets import QApplication  # noqa: E402

from rule34_downloader.errors import PermanentError, ServerUnavailable, TemporaryError  # noqa: E402
from rule34_downloader.form_model import (  # noqa: E402
    FormValues, count_query, found_message, search_error_text)

ALL = frozenset({"pictures", "gifs", "videos"})


class PureCase(unittest.TestCase):
    def test_count_query_ignores_order_and_includes_exclusions_score_safety(self):
        base = FormValues(include_text="a b", exclude_text="c", min_score=5, safety=True)
        q = count_query(base)
        self.assertIn("a", q)
        self.assertIn("-c", q)
        self.assertIn("score:>=5", q)
        self.assertNotIn("sort:id", q)
        other = FormValues(include_text="a b", exclude_text="c", min_score=5, safety=True,
                           order="oldest", types=ALL, count_mode="all", count=7,
                           folder="/x", workers=5)
        self.assertEqual(q, count_query(other))
        self.assertNotEqual(q, count_query(FormValues(include_text="a b", exclude_text="c",
                                                      min_score=5, safety=False)))

    def test_count_query_is_empty_for_invalid_form(self):
        self.assertEqual(count_query(FormValues(include_text="  ")), "")
        self.assertEqual(count_query(FormValues(include_text="a", exclude_text="a")), "")
        self.assertEqual(count_query(FormValues(include_text="a", min_score=-1)), "")
        self.assertNotEqual(count_query(FormValues(include_text="a", folder="", workers=99)), "")

    def test_found_message_number_mode_caps_at_count_and_names_order(self):
        v = FormValues(types=ALL, count=100)
        self.assertEqual(found_message(v, 50819),
                         "Found 50,819 posts. Will download up to 100 files, newest first.")
        self.assertEqual(found_message(v, 40),
                         "Found 40 posts. Will download up to 40 files, newest first.")
        v = FormValues(types=ALL, count=2000, order="oldest")
        self.assertEqual(found_message(v, 5000),
                         "Found 5,000 posts. Will download up to 2,000 files, oldest first.")

    def test_found_message_all_mode_and_type_note(self):
        v = FormValues(types=ALL, count_mode="all")
        self.assertEqual(found_message(v, 1234),
                         "Found 1,234 posts. Will download every post that matches the file "
                         "types you ticked, newest first.")
        v = FormValues(types=frozenset({"gifs", "pictures"}), count_mode="all", order="oldest")
        self.assertTrue(found_message(v, 3).endswith(
            " Only pictures and GIFs are saved, so the number of files can be lower."))
        v = FormValues(types=frozenset({"videos"}))
        self.assertIn(" Only videos are saved,", found_message(v, 3))
        v = FormValues(types=frozenset({"pictures", "gifs", "videos"}) - {"gifs"})
        self.assertIn(" Only pictures and videos are saved,", found_message(v, 3))

    def test_found_message_zero_and_singular(self):
        self.assertEqual(found_message(FormValues(), 0), "No posts found. Check the tags.")
        self.assertEqual(found_message(FormValues(types=ALL), 1),
                         "Found 1 post. Will download up to 1 file, newest first.")

    def test_search_error_texts_are_fixed_per_kind(self):
        self.assertEqual(search_error_text("auth"),
                         "The site did not accept your user ID or key. Check Settings.")
        self.assertEqual(search_error_text("captcha"),
                         "The site asked for a captcha. Open the site in your browser, "
                         "solve it, then try again.")
        self.assertEqual(search_error_text("budget", 0),
                         "No requests left this hour. Try again in about 1 minute.")
        self.assertEqual(search_error_text("budget", 59),
                         "No requests left this hour. Try again in about 1 minute.")
        self.assertEqual(search_error_text("budget", 60),
                         "No requests left this hour. Try again in about 1 minute.")
        self.assertEqual(search_error_text("budget", 61),
                         "No requests left this hour. Try again in about 2 minutes.")
        self.assertEqual(search_error_text("budget", 600),
                         "No requests left this hour. Try again in about 10 minutes.")
        self.assertEqual(search_error_text("unavailable"),
                         "The site did not answer. Try again in a moment.")
        self.assertEqual(search_error_text("no_account"),
                         "No account saved - open Settings to add your user ID and API key")
        self.assertEqual(search_error_text("whatever"), "The search failed. Try again.")


class FakeClient:
    def __init__(self, result=42, exc=None, gate=None):
        self.result, self.exc, self.gate = result, exc, gate
        self.queries = []

    def total_count(self, query):
        self.queries.append(query)
        if self.gate is not None:
            self.gate.wait(5)
        if self.exc is not None:
            raise self.exc
        return self.result


class FakeBudget:
    def __init__(self, remaining=10, acquire_ok=True, wait=1200.0):
        self._remaining, self._ok, self._wait = remaining, acquire_ok, wait
        self.acquires = 0

    def remaining(self):
        return self._remaining

    def acquire(self, stop=None):
        self.acquires += 1
        return self._ok

    def next_available_in(self):
        return self._wait


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class SearchRunnerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from rule34_downloader.gui.runner import SearchRunner
        for target in ("socket.socket.connect", "socket.getaddrinfo"):
            p = mock.patch(target, side_effect=AssertionError("network must not be used in tests"))
            p.start()
            self.addCleanup(p.stop)
        self.runner = SearchRunner()
        self.addCleanup(self._teardown)
        self.got = []
        self.runner.finished.connect(lambda t, r: self.got.append((t, r)))

    def _teardown(self):
        self.runner.wait(2000)
        dispose(self.runner)

    def run_search(self, client, budget=None, query="q"):
        budget = budget if budget is not None else FakeBudget()
        token = self.runner.start(query, object(), client_factory=lambda c: client, budget=budget)
        self.assertIsNotNone(token)
        deadline = time.monotonic() + 3
        while self.runner.is_running() and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        self.runner.wait(1000)
        return token

    def test_search_runner_returns_total_for_fake_client(self):
        client = FakeClient(result=50819)
        token = self.run_search(client)
        self.assertEqual(len(self.got), 1)
        t, r = self.got[0]
        self.assertEqual(t, token)
        self.assertEqual((r.ok, r.total, r.kind), (True, 50819, "ok"))
        self.assertFalse(self.runner.is_running())

    def test_search_runner_budget_empty_gives_budget_result_without_request(self):
        made = []
        budget = FakeBudget(remaining=0, wait=125.0)
        self.runner.start("q", object(), client_factory=lambda c: made.append(c), budget=budget)
        self.runner.wait(3000)
        r = self.got[0][1]
        self.assertEqual((r.ok, r.total, r.kind, r.wait_seconds), (False, None, "budget", 125.0))
        self.assertEqual(made, [])
        self.assertEqual(budget.acquires, 0)
        # cancelled wait inside acquire gives the same result, still no client
        made2 = []
        b2 = FakeBudget(acquire_ok=False, wait=30.0)
        self.runner.start("q", object(), client_factory=lambda c: made2.append(c), budget=b2)
        self.runner.wait(3000)
        self.assertEqual(self.got[1][1].kind, "budget")
        self.assertEqual(made2, [])

    def test_search_runner_maps_auth_captcha_unavailable_and_other_errors(self):
        cases = [
            (PermanentError("x", kind="auth"), "auth"),
            (PermanentError("x", kind="captcha"), "captcha"),
            (PermanentError("x", kind="client"), "error"),
            (TemporaryError("x"), "unavailable"),
            (ServerUnavailable("x"), "unavailable"),
            (ValueError("x"), "error"),
        ]
        for exc, kind in cases:
            self.got.clear()
            self.run_search(FakeClient(exc=exc))
            self.assertEqual(self.got[0][1].kind, kind, repr(exc))
            self.assertFalse(self.got[0][1].ok)
        self.got.clear()
        self.run_search(FakeClient(result="not a number"))
        self.assertEqual(self.got[0][1].kind, "error")

    def test_search_runner_uses_one_request_and_one_budget_slot(self):
        client, budget = FakeClient(), FakeBudget()
        self.run_search(client, budget, query="hinata -x")
        self.assertEqual(client.queries, ["hinata -x"])
        self.assertEqual(budget.acquires, 1)

    def test_search_runner_drops_cancelled_result(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        client = FakeClient(gate=gate)
        self.runner.start("q", object(), client_factory=lambda c: client, budget=FakeBudget())
        self.runner.cancel()
        gate.set()
        self.assertTrue(self.runner.wait(3000))
        self.assertEqual(self.got, [])
        self.assertFalse(self.runner.is_running())
        # the runner is usable again afterwards
        self.run_search(FakeClient())
        self.assertEqual(len(self.got), 1)

    def test_search_runner_result_never_contains_key_or_raw_text(self):
        exc = PermanentError("HTTP 401 ... api_key=SECRETKEY123", kind="auth")
        self.run_search(FakeClient(exc=exc))
        t, r = self.got[0]
        self.assertEqual(r.kind, "auth")
        for blob in (repr(r), repr(self.got)):
            self.assertNotIn("SECRETKEY123", blob)
            self.assertNotIn("HTTP", blob)
        text = search_error_text(r.kind, r.wait_seconds)
        self.assertEqual(text, "The site did not accept your user ID or key. Check Settings.")
        self.assertNotIn("SECRETKEY123", text)

    def test_search_runner_second_start_while_running_is_refused(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        client = FakeClient(gate=gate)
        first = self.runner.start("q", object(), client_factory=lambda c: client,
                                  budget=FakeBudget())
        self.assertIsNotNone(first)
        self.assertTrue(self.runner.is_running())
        self.assertIsNone(self.runner.start("other", object(), client_factory=lambda c: client,
                                            budget=FakeBudget()))
        gate.set()
        self.assertTrue(self.runner.wait(3000))
        self.assertEqual([t for t, _ in self.got], [first])
        self.assertEqual(client.queries, ["q"])

    def test_search_runner_tests_cannot_reach_the_network(self):
        with self.assertRaises(AssertionError):
            socket.create_connection(("127.0.0.1", 9))
        with self.assertRaises(AssertionError):
            socket.getaddrinfo("example.com", 80)
        self.run_search(FakeClient())
        self.assertEqual(self.got[0][1].kind, "ok")


if __name__ == "__main__":
    unittest.main()
