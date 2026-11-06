"""Progress panel + window wiring tests (offscreen, mock server only, no real dialogs)."""
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_gui_shell import GuiCase, HAVE_QT  # noqa: E402

from mock_api import API_KEY, USER_ID, MockServer  # noqa: E402
from rule34_downloader import config, form_model  # noqa: E402
from rule34_downloader.api import ApiClient  # noqa: E402
from rule34_downloader.backoff import Backoff, GiveUpClock  # noqa: E402
from rule34_downloader.budget import RequestBudget  # noqa: E402
from rule34_downloader.engine import Job, JobConfig, Summary  # noqa: E402
from rule34_downloader.run_model import STATES, RunStats, counters_text, enabled_actions  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

if HAVE_QT:
    from PySide6.QtCore import QObject, Signal  # noqa: E402
    from PySide6.QtWidgets import QLabel  # noqa: E402


class FakeBudget:
    limit = 55

    def used(self):
        return 7


def fake_budget_factory():
    return FakeBudget()


class PanelCase(GuiCase):
    def setUp(self):
        super().setUp()
        self.prefs = self.tmp / "ui.json"
        self.srv = None

    def window(self, job_factory=None, budget_factory=fake_budget_factory):
        from rule34_downloader.gui.main_window import MainWindow
        w = MainWindow(config_file=self.cfg, prefs_file=self.prefs,
                       job_factory=job_factory, budget_factory=budget_factory)
        self.addCleanup(self.cleanup_window, w)
        w.show()
        self.app.processEvents()
        self.panel, self.runner, self.form = w.run_panel, w.runner, w.search_form
        return w

    def cleanup_window(self, w):
        w.runner.stop()
        w.runner.wait(10000)
        self.app.processEvents()
        w.close()

    def job(self, **kw):
        kw.setdefault("dest", self.tmp / "out")
        kw.setdefault("include", ["cat"])
        return JobConfig(**kw)

    def fill(self, tags="cat", count=None):
        self.form.include_edit.setText(tags)
        self.form.folder_edit.setText(str(self.tmp / "out"))
        if count:
            self.form.count_number_radio.setChecked(True)
            self.form.count_spin.setValue(count)

    def creds(self):
        config.save_credentials(USER_ID, API_KEY, path=self.cfg)

    def server(self, **kw):
        self.srv = MockServer(**kw).start()
        self.addCleanup(self.srv.stop)
        p = mock.patch.dict(os.environ, {"R34_API_URL": self.srv.api_url})
        p.start()
        self.addCleanup(p.stop)
        budget_path = self.tmp / "budget.json"

        def make(cfg, creds, on_event):
            return Job(cfg, ApiClient(creds), on_event=on_event,
                       budget=RequestBudget(path=budget_path),
                       giveup=GiveUpClock(window=30), backoff=Backoff(base=0.01, jitter=0))
        return make

    def pump(self, cond, timeout=15.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if cond():
                return True
            time.sleep(0.002)
        self.app.processEvents()
        return bool(cond())

    def drive(self, state, **fields):
        """Push a runner state through the public signals."""
        for k, v in fields.items():
            setattr(self.runner.stats, k, v)
        self.runner.stats.state = state
        self.runner.state_changed.emit(state)
        self.runner.stats_changed.emit()

    def finish(self, summary):
        self.runner.stats.state = "finished"
        self.runner.stats.summary = summary
        self.runner.state_changed.emit("finished")
        self.runner.stats_changed.emit()
        self.runner.finished.emit(summary)

    def all_text(self, w):
        parts = [lab.text() for lab in w.findChildren(QLabel)]
        parts.append(self.panel.log_view.toPlainText())
        parts.append(w.statusBar().currentMessage())
        return "\n".join(parts)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class PanelTests(PanelCase):
    # -- stack and start ---------------------------------------------------------
    def test_download_stack_starts_on_the_form(self):
        w = self.window()
        self.assertEqual(w.download_stack.objectName(), "download_stack")
        self.assertEqual(w.download_stack.currentIndex(), 0)
        self.assertIs(w.download_stack.widget(0), w.search_form)
        self.assertIs(w.download_stack.widget(1), w.run_panel)
        self.assertEqual(w.run_panel.objectName(), "run_panel")

    def test_start_with_credentials_switches_to_panel_and_runs(self):
        factory = self.server(total=25)
        w = self.window(job_factory=factory)
        self.creds()
        self.fill(count=2)
        self.form.start_button.click()
        self.assertEqual(w.download_stack.currentIndex(), 1)
        self.assertTrue(self.pump(lambda: not self.panel.result_label.isHidden()))
        self.assertEqual(w.download_stack.currentIndex(), 1)
        self.assertTrue(self.panel.result_label.text().startswith("Finished: 2 downloaded"))

    def test_start_without_credentials_stays_on_form_and_shows_message(self):
        from rule34_downloader.gui.main_window import NO_ACCOUNT
        w = self.window()
        self.fill()
        with mock.patch.object(self.runner, "start") as start:
            self.form.start_button.click()
        start.assert_not_called()
        self.assertEqual(w.download_stack.currentIndex(), 0)
        self.assertEqual(w.statusBar().currentMessage(), NO_ACCOUNT)
        self.assertFalse(self.runner.is_running())
        self.assertIsNone(self.panel.job_config)

    def test_start_saves_prefs_before_running(self):
        self.window()
        self.creds()
        self.fill()
        order = []
        with mock.patch.object(form_model, "save_prefs",
                               side_effect=lambda *a, **k: order.append("save")), \
                mock.patch.object(self.runner, "start",
                                  side_effect=lambda *a, **k: order.append("start") or True):
            self.form.start_button.click()
        self.assertEqual(order, ["save", "start"])

    # -- labels, bar, counters, log ----------------------------------------------
    def test_panel_shows_query_and_destination(self):
        self.window()
        self.panel.begin(self.job(include=["cat", "dog"], exclude=["fish"], min_score=5))
        self.assertEqual(self.panel.query_label.text(), "Search: cat dog -fish score:>=5")
        self.assertEqual(self.panel.dest_label.text(),
                         f"Saves to: {self.tmp / 'out' / 'cat+dog'}")
        self.panel.begin(self.job(by_search=False))
        self.assertEqual(self.panel.dest_label.text(), f"Saves to: {self.tmp / 'out'}")

    def test_progress_bar_is_busy_when_total_unknown(self):
        self.window()
        self.panel.begin(self.job())
        self.runner.stats.state = "running"
        self.runner.stats_changed.emit()
        bar = self.panel.progress_bar
        self.assertEqual((bar.minimum(), bar.maximum()), (0, 0))

    def test_progress_bar_shows_percent_in_count_mode(self):
        self.window()
        self.panel.begin(self.job(count=4))
        self.runner.stats.downloaded = 1
        self.drive("running")
        bar = self.panel.progress_bar
        self.assertEqual((bar.minimum(), bar.maximum(), bar.value()), (0, 100, 25))
        self.assertEqual(bar.format(), "%p%")

    def test_counters_label_updates_on_events(self):
        self.window()
        self.panel.begin(self.job())
        self.runner.stats.downloaded = 2
        self.runner.stats.bytes_done = 2048
        self.runner.stats_changed.emit()
        self.assertEqual(self.panel.counters_label.text(), counters_text(self.runner.stats))
        self.assertIn("Downloaded 2", self.panel.counters_label.text())
        self.assertIn("Size 2.0 KB", self.panel.counters_label.text())

    def test_log_view_appends_cleaned_lines_and_is_bounded(self):
        self.window()
        self.panel.begin(self.job())
        self.runner.log_line.emit("Retry page 0 (attempt 1): HTTP 503: ")
        self.assertEqual(self.panel.log_view.toPlainText(), "Retry page 0 (attempt 1): HTTP 503")
        for i in range(700):
            self.runner.log_line.emit(f"line {i}")
        self.assertTrue(self.panel.log_view.isReadOnly())
        self.assertEqual(self.panel.log_view.maximumBlockCount(), 500)
        self.assertLessEqual(self.panel.log_view.blockCount(), 500)
        self.assertTrue(self.panel.log_view.toPlainText().endswith("line 699"))

    # -- buttons -----------------------------------------------------------------
    def test_buttons_follow_enabled_actions_in_each_state(self):
        self.window()
        folder = self.tmp / "out"
        folder.mkdir()
        self.panel.begin(self.job())
        p = self.panel
        for state in STATES:
            self.drive(state)
            acts = enabled_actions(state)
            self.assertEqual(
                (p.pause_button.isEnabled(), p.resume_button.isEnabled(),
                 p.retry_button.isEnabled(), p.stop_button.isEnabled()),
                ("pause" in acts, "resume" in acts, "retry_now" in acts, "stop" in acts), state)
            self.assertEqual(p.back_button.isEnabled(), state in ("idle", "finished"), state)
            self.assertEqual(p.open_folder_button.isEnabled(), state == "finished", state)

    def test_stop_button_calls_runner_stop(self):
        self.window()
        self.panel.begin(self.job())
        self.drive("running")
        with mock.patch.object(self.runner, "stop") as stop:
            self.panel.stop_button.click()
        stop.assert_called_once_with()

    def test_pause_resume_and_retry_buttons_call_the_runner(self):
        self.window()
        self.panel.begin(self.job())
        for state, button, method in (("running", "pause_button", "pause"),
                                      ("paused", "resume_button", "resume"),
                                      ("unavailable", "retry_button", "retry_now")):
            self.drive(state)
            with mock.patch.object(self.runner, method) as m:
                getattr(self.panel, button).click()
            m.assert_called_once_with()

    # -- banner ------------------------------------------------------------------
    def test_banner_visible_when_waiting_and_hidden_when_calm(self):
        self.window()
        self.panel.begin(self.job())
        self.assertTrue(self.panel.banner.isHidden())
        self.drive("waiting", wait_seconds=12.7, wait_reason="retry-after")
        self.assertFalse(self.panel.banner.isHidden())
        self.assertEqual(self.panel.banner.text(), "Waiting 12 s (retry-after).")
        self.drive("running")
        self.assertTrue(self.panel.banner.isHidden())
        self.drive("paused", adaptive_level=2)
        self.assertEqual(self.panel.banner.text(),
                         "Paused. Press Resume to continue. Slowed down: level 2.")

    def test_banner_shows_unavailable_message(self):
        self.window()
        self.panel.begin(self.job())
        self.drive("unavailable", unavailable_message="server unavailable after 1800s: HTTP 503")
        self.assertFalse(self.panel.banner.isHidden())
        self.assertIn("Server unavailable: server unavailable after 1800s: HTTP 503",
                      self.panel.banner.text())
        self.assertIn("Retry now", self.panel.banner.text())


if __name__ == "__main__":
    unittest.main()
