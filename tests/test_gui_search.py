"""Search-then-Download UI tests (offscreen; fake client and fake budget only, no network)."""
import os
import socket
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from test_gui_shell import GuiCase, HAVE_QT, dispose  # noqa: E402

from rule34_downloader import form_model as fm  # noqa: E402

if HAVE_QT:
    from rule34_downloader.gui.runner import SearchResult  # noqa: E402

OK_321 = None  # filled lazily (needs Qt for SearchResult)


class FakeBudget:
    limit = 55

    def __init__(self, remaining=40, wait=0):
        self._remaining = remaining
        self._wait = wait
        self.acquired = 0

    def used(self):
        return self.limit - self._remaining + self.acquired

    def remaining(self):
        return self._remaining

    def acquire(self):
        self.acquired += 1
        return True

    def next_available_in(self):
        return self._wait


class FakeClient:
    """Records the query; optionally blocks on an Event."""
    calls = []
    gate = None

    def __init__(self, creds=None):
        pass

    def total_count(self, query):
        FakeClient.calls.append(query)
        if FakeClient.gate is not None:
            FakeClient.gate.wait(10)
        return 321


class SearchCase(GuiCase):
    def setUp(self):
        super().setUp()
        for target in ("socket.socket.connect", "socket.getaddrinfo"):
            p = mock.patch(target, side_effect=AssertionError("network must not be used in tests"))
            p.start()
            self.addCleanup(p.stop)
        FakeClient.calls = []
        FakeClient.gate = None
        self.addCleanup(setattr, FakeClient, "gate", None)
        self.prefs = self.tmp / "ui.json"

    def window(self, budget=None, search_factory=FakeClient):
        from rule34_downloader.gui.main_window import MainWindow
        self.budget = budget or FakeBudget()
        w = MainWindow(config_file=self.cfg, prefs_file=self.prefs,
                       budget_factory=lambda: self.budget, search_factory=search_factory)
        self.addCleanup(self.cleanup_window, w)
        w.show()
        self.app.processEvents()
        self.f = w.search_form
        return w

    def cleanup_window(self, w):
        w.searcher.wait()
        dispose(w)

    def fill(self, tags="cat"):
        self.f.include_edit.setText(tags)
        self.f.folder_edit.setText(str(self.tmp / "out"))

    def ok(self, total=321):
        return SearchResult(True, total, "ok")

    def found(self, total=321):
        """Run a (pretend) search for the current form and deliver the result."""
        self.assertTrue(self.f.begin_search())
        self.f.set_search_result(self.ok(total))

    def state(self):
        return self.f.found_label.property("state")

    def pump(self, cond, timeout=3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if cond():
                return True
            time.sleep(0.002)
        self.app.processEvents()
        return bool(cond())


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class SearchFormTests(SearchCase):
    def window(self):
        """A bare form: no window, so search_requested reaches nobody."""
        from rule34_downloader.gui.search_form import SearchForm
        f = SearchForm(values=fm.FormValues(folder=str(self.tmp / "out")))
        self.addCleanup(dispose, f)
        f.show()
        self.app.processEvents()
        self.f = f
        return f

    def test_download_and_search_buttons_exist_with_these_texts(self):
        self.window()
        self.assertEqual(self.f.start_button.text(), "Download")
        self.assertEqual(self.f.search_button.text(), "Search")
        self.assertEqual(self.f.start_button.objectName(), "start_button")
        self.assertLess(self.f.search_button.geometry().x(), self.f.start_button.geometry().x())
        self.assertEqual(self.f.found_label.text(), fm.IDLE_TEXT)
        self.assertEqual(self.state(), "idle")
        self.assertTrue(self.f.found_label.wordWrap())
        order = [self.f.layout().itemAt(i).widget() for i in range(self.f.layout().count())
                 if self.f.layout().itemAt(i).widget() is not None]
        names = [w.objectName() for w in order]
        self.assertLess(names.index("dest_preview"), names.index("found_label"))
        self.assertLess(names.index("found_label"), names.index("error_label"))

    def test_download_is_disabled_until_a_search_matches_the_form(self):
        self.window()
        self.fill()
        self.assertFalse(self.f.start_button.isEnabled())
        self.assertIsNone(self.f.found_total())
        self.f.mark_found(321)
        self.assertTrue(self.f.start_button.isEnabled())
        self.assertEqual(self.f.found_total(), 321)
        self.f.exclude_edit.setText("dog")
        self.assertFalse(self.f.start_button.isEnabled())
        self.assertIsNone(self.f.found_total())

    def test_search_button_needs_valid_tags(self):
        self.window()
        self.assertFalse(self.f.search_button.isEnabled())
        self.f.include_edit.setText(" , ")
        self.assertFalse(self.f.search_button.isEnabled())
        self.f.include_edit.setText("cat")
        self.assertTrue(self.f.search_button.isEnabled())
        self.f.exclude_edit.setText("cat")
        self.assertFalse(self.f.search_button.isEnabled())
        self.assertFalse(self.f.begin_search())
        self.assertEqual(self.state(), "idle")

    def test_search_click_emits_the_count_query_and_shows_searching(self):
        self.window()
        self.fill("cat dog")
        self.f.order_combo.setCurrentIndex(1)
        self.f.exclude_edit.setText("bird")
        got = []
        self.f.search_requested.connect(got.append)
        self.f.search_button.click()
        self.assertEqual(got, [fm.count_query(self.f.values())])
        self.assertEqual(got, ["cat dog -bird"])
        self.assertEqual(self.f.found_label.text(), fm.SEARCHING_TEXT)
        self.assertEqual(self.state(), "searching")

    def test_enter_in_the_tag_box_runs_search(self):
        self.window()
        self.fill()
        got = []
        self.f.search_requested.connect(got.append)
        self.f.include_edit.returnPressed.emit()
        self.f.include_edit.returnPressed.emit()
        self.assertEqual(got, ["cat"])
        self.assertEqual(self.state(), "searching")

    def test_search_result_shows_found_message_and_enables_download(self):
        self.window()
        self.fill()
        self.f.begin_search()
        self.f.set_search_result(self.ok(321))
        self.assertEqual(self.f.found_label.text(), fm.found_message(self.f.values(), 321))
        self.assertTrue(self.f.found_label.text().startswith("Found 321 posts. Will download up to 100 files"))
        self.assertEqual(self.state(), "fresh")
        self.assertTrue(self.f.start_button.isEnabled())
        self.assertTrue(self.f.search_button.isEnabled())

    def test_zero_posts_keeps_download_disabled(self):
        self.window()
        self.fill()
        self.f.begin_search()
        self.f.set_search_result(self.ok(0))
        self.assertEqual(self.f.found_label.text(), "No posts found. Check the tags.")
        self.assertEqual(self.state(), "fresh")
        self.assertFalse(self.f.start_button.isEnabled())
        got = []
        self.f.start_requested.connect(got.append)
        self.assertFalse(self.f.request_start())
        self.assertEqual(got, [])

    def test_changing_tags_exclusions_score_or_safety_makes_the_result_stale(self):
        self.window()
        self.fill()
        changes = [
            (lambda: self.f.include_edit.setText("cat dog"), lambda: self.f.include_edit.setText("cat")),
            (lambda: self.f.exclude_edit.setText("bird"), lambda: self.f.exclude_edit.setText("")),
            (lambda: self.f.min_score_check.setChecked(True), lambda: self.f.min_score_check.setChecked(False)),
            (lambda: self.f.safety_check.setChecked(True), lambda: self.f.safety_check.setChecked(False)),
        ]
        for change, restore in changes:
            self.f.mark_found(321)
            self.assertEqual(self.state(), "fresh")
            change()
            self.assertEqual(self.state(), "stale")
            self.assertEqual(self.f.found_label.text(), fm.STALE_TEXT)
            self.assertFalse(self.f.start_button.isEnabled())
            restore()
            self.assertEqual(self.state(), "stale")
            self.assertEqual(self.f.found_label.text(), fm.STALE_TEXT)
            self.assertFalse(self.f.start_button.isEnabled())

    def test_changing_order_types_count_workers_or_folder_keeps_the_result_and_updates_the_text(self):
        self.window()
        self.fill()
        self.f.mark_found(321)
        seen = {self.f.found_label.text()}
        changes = [
            lambda: self.f.order_combo.setCurrentIndex(1),
            lambda: self.f.gifs_check.setChecked(True),
            lambda: self.f.count_spin.setValue(7),
            lambda: self.f.count_all_radio.setChecked(True),
            lambda: self.f.workers_spin.setValue(5),
            lambda: self.f.folder_edit.setText(str(self.tmp / "elsewhere")),
        ]
        for change in changes:
            change()
            self.assertEqual(self.state(), "fresh")
            self.assertTrue(self.f.start_button.isEnabled())
            self.assertEqual(self.f.found_label.text(), fm.found_message(self.f.values(), 321))
            seen.add(self.f.found_label.text())
        self.assertGreaterEqual(len(seen), 4)
        self.assertIn("oldest", fm.found_message(self.f.values(), 321))

    def test_result_for_an_older_query_is_ignored(self):
        self.window()
        self.fill()
        self.f.begin_search()
        self.f.include_edit.setText("dog")
        self.assertEqual(self.state(), "searching")
        self.f.set_search_result(self.ok(321))
        self.assertEqual(self.state(), "stale")
        self.assertEqual(self.f.found_label.text(), fm.STALE_TEXT)
        self.assertFalse(self.f.start_button.isEnabled())
        self.assertIsNone(self.f.found_total())
        # restored while still searching: accepted
        self.f.begin_search()
        self.f.include_edit.setText("cat")
        self.f.include_edit.setText("dog")
        self.f.set_search_result(self.ok(5))
        self.assertEqual(self.state(), "fresh")
        # a result nobody is waiting for is ignored
        self.f.set_search_result(self.ok(9))
        self.assertEqual(self.f.found_total(), 5)

    def test_search_error_kinds_show_fixed_texts_and_keep_download_disabled(self):
        self.window()
        self.fill()
        for kind, wait in (("auth", 0), ("captcha", 0), ("budget", 600), ("unavailable", 0), ("error", 0)):
            self.f.mark_found(5)
            self.f.begin_search()
            self.f.set_search_result(SearchResult(False, None, kind, wait))
            self.assertEqual(self.state(), "error")
            self.assertEqual(self.f.found_label.text(), fm.search_error_text(kind, wait))
            self.assertFalse(self.f.start_button.isEnabled())
            self.assertIsNone(self.f.found_total())
        self.assertEqual(self.f.found_label.text(), fm.search_error_text("error"))
        self.f.set_search_error("no_account")
        self.assertEqual(self.f.found_label.text(), fm.search_error_text("no_account"))
        self.f.exclude_edit.setText("dog")
        self.assertEqual(self.state(), "idle")
        self.assertEqual(self.f.found_label.text(), fm.IDLE_TEXT)

    def test_request_start_is_refused_without_a_fresh_result(self):
        self.window()
        self.fill()
        got = []
        self.f.start_requested.connect(got.append)
        self.assertFalse(self.f.request_start())
        self.f.begin_search()
        self.assertFalse(self.f.request_start())
        self.f.set_search_result(self.ok(3))
        self.assertTrue(self.f.request_start())
        self.f.exclude_edit.setText("dog")
        self.assertFalse(self.f.request_start())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].include, ["cat"])

    def test_search_button_is_disabled_while_searching(self):
        self.window()
        self.fill()
        self.f.begin_search()
        self.assertFalse(self.f.search_button.isEnabled())
        self.assertFalse(self.f.begin_search())
        self.f.set_search_result(self.ok(3))
        self.assertTrue(self.f.search_button.isEnabled())

@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class SearchWindowTests(SearchCase):
    def run_search(self, w):
        self.f.search_button.click()
        w.searcher.wait()
        self.pump(lambda: self.state() != "searching")

    def test_search_without_account_shows_the_message_and_makes_no_request(self):
        from rule34_downloader.gui.main_window import NO_ACCOUNT
        w = self.window()
        self.fill()
        self.f.search_button.click()
        self.assertEqual(self.state(), "error")
        self.assertEqual(self.f.found_label.text(), NO_ACCOUNT)
        self.assertEqual(w.statusBar().currentMessage(), NO_ACCOUNT)
        self.assertFalse(w.searcher.is_running())
        self.assertEqual(FakeClient.calls, [])
        self.assertEqual(self.budget.acquired, 0)
        self.assertFalse(self.f.start_button.isEnabled())

    def test_search_runs_through_the_runner_and_updates_the_form(self):
        self.save()
        w = self.window()
        self.fill()
        self.pump(lambda: True)
        self.f.exclude_edit.setText("dog")
        before = w.run_panel.budget_label.text()
        self.budget._remaining = 30
        self.run_search(w)
        self.assertEqual(FakeClient.calls, ["cat -dog"])
        self.assertTrue(self.f.found_label.text().startswith(
            "Found 321 posts. Will download up to "))
        self.assertEqual(self.state(), "fresh")
        self.assertTrue(self.f.start_button.isEnabled())
        self.assertNotEqual(w.run_panel.budget_label.text(), before)
        self.assertEqual(w.run_panel.budget_label.text(), "API requests this hour: 26 / 55")

    def test_search_with_empty_budget_shows_the_wait_text_without_a_request(self):
        self.save()
        w = self.window(budget=FakeBudget(remaining=0, wait=600))
        self.fill()
        self.run_search(w)
        self.assertEqual(self.state(), "error")
        self.assertEqual(self.f.found_label.text(),
                         "No requests left this hour. Try again in about 10 minutes.")
        self.assertEqual(FakeClient.calls, [])
        self.assertEqual(self.budget.acquired, 0)
        self.assertFalse(self.f.start_button.isEnabled())

    def test_download_passes_the_found_total_to_the_run_panel(self):
        self.save()
        w = self.window(search_factory=FakeClient)
        w.job_factory = lambda cfg, creds, on_event: mock.MagicMock(run=lambda: None)
        self.fill()
        self.run_search(w)
        with mock.patch.object(w.run_panel, "begin", wraps=w.run_panel.begin) as begin, \
                mock.patch.object(w.runner, "start"):
            self.f.start_button.click()
        self.assertEqual(begin.call_count, 1)
        self.assertEqual(begin.call_args.kwargs, {"found_total": 321})
        self.assertEqual(w.run_panel.panel_found_label.text(), "Found 321 posts")

    def test_run_panel_found_line_is_hidden_when_unknown_and_shown_when_known(self):
        from rule34_downloader.engine import JobConfig
        w = self.window()
        p = w.run_panel
        job = JobConfig(dest=self.tmp / "out", include=["cat"])
        self.assertTrue(p.panel_found_label.isHidden())
        p.begin(job)
        self.assertTrue(p.panel_found_label.isHidden())
        self.assertEqual(p.panel_found_label.text(), "")
        p.begin(job, found_total=1234)
        self.assertFalse(p.panel_found_label.isHidden())
        self.assertEqual(p.panel_found_label.text(), "Found 1,234 posts")
        p.begin(job, found_total=1)
        self.assertEqual(p.panel_found_label.text(), "Found 1 post")
        p.begin(job)
        self.assertTrue(p.panel_found_label.isHidden())
        self.assertEqual(p.panel_found_label.text(), "")
        p.budget_timer.stop()

    def test_closing_the_window_waits_for_a_running_search(self):
        self.save()
        gate = threading.Event()
        FakeClient.gate = gate
        self.addCleanup(gate.set)
        w = self.window()
        self.fill()
        self.f.search_button.click()
        self.assertTrue(self.pump(lambda: bool(FakeClient.calls)))
        self.assertTrue(w.searcher.is_running())
        threading.Timer(0.3, gate.set).start()
        start = time.monotonic()
        w.close()
        self.assertLess(time.monotonic() - start, 5)
        gate.set()
        w.searcher.wait()
        self.assertFalse(w.searcher.is_running())

    def test_search_tests_cannot_reach_the_real_budget_or_network(self):
        with self.assertRaises(AssertionError):
            socket.create_connection(("127.0.0.1", 9))
        with self.assertRaises(AssertionError):
            socket.getaddrinfo("example.invalid", 80)
        self.save()
        w = self.window()
        self.fill()
        with mock.patch("rule34_downloader.gui.main_window.RequestBudget",
                        side_effect=AssertionError("real budget")):
            self.run_search(w)
        self.assertEqual(self.state(), "fresh")
        self.assertEqual(FakeClient.calls, ["cat"])
        self.assertEqual(self.budget.acquired, 1)


if __name__ == "__main__":
    unittest.main()
