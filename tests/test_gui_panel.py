"""Progress panel + window wiring tests (offscreen, mock server only, no real dialogs)."""
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_gui_shell import GuiCase, HAVE_QT, dispose  # noqa: E402

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
        dispose(w)

    def job(self, **kw):
        kw.setdefault("dest", self.tmp / "out")
        kw.setdefault("include", ["cat"])
        return JobConfig(**kw)

    def fill(self, tags="cat", count=None):
        self.form.include_edit.setText(tags)
        self.form.folder_edit.setText(str(self.tmp / "out"))
        self.form.mark_found(321)
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


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class CoalescingTests(PanelCase):
    def _counting_panel(self):
        self.window()
        self.panel.begin(self.job(count=2000))
        calls = []
        real = self.panel._apply_stats
        self.panel._apply_stats = lambda: (calls.append(1), real())[1]
        return calls

    def test_1000_events_in_1s_cause_at_most_11_redraws(self):
        calls = self._counting_panel()
        timer = self.panel._stats_timer
        self.assertTrue(timer.isSingleShot())
        self.assertEqual(timer.interval(), 100)
        self.runner.stats.state = "running"
        for i in range(1000):
            self.runner.stats.downloaded = i + 1
            self.runner.stats_changed.emit()
            if (i + 1) % 100 == 0 and timer.isActive():
                timer.timeout.emit()  # one 100 ms tick of simulated time
        self.assertGreaterEqual(len(calls), 1)
        self.assertLessEqual(len(calls), 11)

    def test_terminal_states_apply_immediately(self):
        for state in ("finished", "paused", "stopping", "unavailable"):
            with self.subTest(state=state):
                self.window()
                self.panel.begin(self.job())
                stats = self.runner.stats
                stats.downloaded = 5
                stats.unavailable_message = "HTTP 503"
                stats.state = state
                self.runner.state_changed.emit(state)  # no stats_changed, no timer tick
                self.assertEqual(self.panel.counters_label.text(), counters_text(stats))
                self.assertIn("Downloaded 5", self.panel.counters_label.text())
                actions = enabled_actions(state)
                self.assertEqual(self.panel.resume_button.isEnabled(), "resume" in actions)
                self.assertEqual(self.panel.stop_button.isEnabled(), "stop" in actions)
                if state != "finished":
                    self.assertFalse(self.panel.banner.isHidden())
                    self.assertTrue(self.panel.banner.text())

    def test_on_finished_applies_immediately(self):
        self.window()
        self.panel.begin(self.job())
        self.runner.stats.downloaded = 3
        self.runner.stats_changed.emit()  # pending, not yet drawn
        self.runner.stats.state = "finished"
        self.runner.finished.emit(Summary(reason="stopped", downloaded=3))
        self.assertIn("Downloaded 3", self.panel.counters_label.text())

    def test_final_counters_are_exact_after_burst(self):
        self.window()
        self.panel.begin(self.job())
        stats = self.runner.stats
        stats.state = "running"
        for i in range(500):
            stats.downloaded = i + 1
            stats.bytes_done = (i + 1) * 1024
            self.runner.stats_changed.emit()
        self.finish(Summary(reason="stopped", downloaded=500))
        self.assertEqual(self.panel.counters_label.text(), counters_text(stats))
        self.assertIn("Downloaded 500", self.panel.counters_label.text())
        # and nothing changes when a pending tick fires later
        self.pump(lambda: False, timeout=0.3)
        self.assertEqual(self.panel.counters_label.text(), counters_text(stats))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class FlowTests(PanelCase):
    # -- result ------------------------------------------------------------------
    def test_result_label_shows_headline_when_finished(self):
        self.window()
        self.panel.begin(self.job())
        self.assertTrue(self.panel.result_label.isHidden())
        self.finish(Summary(reason="count_reached", downloaded=5))
        self.assertFalse(self.panel.result_label.isHidden())
        self.assertEqual(self.panel.result_label.text(),
                         "Finished: 5 downloaded, 0 already there, 0 skipped, 0 failed")
        self.assertEqual(self.panel.result_label.property("error"), "false")

    def test_result_label_error_uses_error_red(self):
        from rule34_downloader.gui import theme
        self.window()
        self.panel.begin(self.job())
        self.finish(Summary(reason="permanent_error", error="bad key", error_kind="auth"))
        self.assertEqual(self.panel.result_label.property("error"), "true")
        self.assertIn("Stopped by an error (auth): bad key", self.panel.result_label.text())
        self.assertIn('QLabel#result_label[error="true"] { color: %s; }' % theme.ERROR_RED,
                      theme.stylesheet())

    # -- back / settings ---------------------------------------------------------
    def test_back_button_returns_to_form_and_keeps_values(self):
        w = self.window()
        self.fill("cat dog", count=7)
        w.download_stack.setCurrentIndex(1)
        self.panel.begin(self.job())
        self.finish(Summary(reason="stopped"))
        self.assertTrue(self.panel.back_button.isEnabled())
        self.panel.back_button.click()
        self.assertEqual(w.download_stack.currentIndex(), 0)
        self.assertEqual(self.form.include_edit.text(), "cat dog")
        self.assertEqual(self.form.count_spin.value(), 7)

    def test_back_button_disabled_while_running(self):
        self.window()
        self.panel.begin(self.job())
        for state in ("running", "waiting", "paused", "unavailable", "stopping"):
            self.drive(state)
            self.assertFalse(self.panel.back_button.isEnabled(), state)

    def test_open_folder_button_opens_result_folder(self):
        self.window()
        folder = self.tmp / "out" / "cat"
        folder.mkdir(parents=True)
        self.panel.begin(self.job())
        self.drive("running")
        self.assertFalse(self.panel.open_folder_button.isEnabled())
        self.finish(Summary(reason="end_of_results"))
        self.assertTrue(self.panel.open_folder_button.isEnabled())
        with mock.patch("rule34_downloader.gui.run_panel.QDesktopServices.openUrl") as opener:
            self.panel.open_folder_button.click()
        opener.assert_called_once()
        self.assertEqual(opener.call_args[0][0].toLocalFile(), str(folder))

    def test_settings_button_disabled_while_running(self):
        w = self.window()
        self.creds()
        self.fill()
        with mock.patch.object(self.runner, "start", return_value=True):
            self.form.start_button.click()
        self.assertFalse(w.settings_button.isEnabled())
        self.finish(Summary(reason="stopped"))
        self.assertTrue(w.settings_button.isEnabled())

    # -- budget ------------------------------------------------------------------
    def test_budget_label_shows_used_of_limit(self):
        self.window()
        self.assertEqual(self.panel.budget_label.text(), "API requests this hour: 7 / 55")
        self.panel.budget_factory = lambda: (_ for _ in ()).throw(OSError("gone"))
        self.panel.begin(self.job())
        self.assertEqual(self.panel.budget_label.text(), "API requests this hour: unknown")
        self.panel.budget_factory = lambda: None
        self.panel.refresh_budget()
        self.assertEqual(self.panel.budget_label.text(), "API requests this hour: unknown")

    def test_budget_timer_runs_only_while_running(self):
        self.window()
        timer = self.panel.budget_timer
        self.assertFalse(timer.isActive())
        self.assertEqual(timer.interval(), 5000)
        self.panel.begin(self.job())
        self.assertTrue(timer.isActive())
        self.finish(Summary(reason="stopped"))
        self.assertFalse(timer.isActive())

    # -- close -------------------------------------------------------------------
    def _closing(self, w, answer, wait_ok=True):
        qmb = mock.MagicMock()
        qmb.question.return_value = getattr(qmb, answer)
        run = mock.patch.object(self.runner, "is_running", return_value=True)
        stop = mock.patch.object(self.runner, "stop")
        wait = mock.patch.object(self.runner, "wait", return_value=wait_ok)
        with mock.patch("rule34_downloader.gui.main_window.QMessageBox", qmb), run, stop as s, \
                wait as wt:
            closed = w.close()
        return closed, qmb, s, wt

    def test_close_while_running_asks_and_stops_on_yes(self):
        w = self.window()
        closed, qmb, stop, wait = self._closing(w, "Yes")
        qmb.question.assert_called_once()
        self.assertEqual(qmb.question.call_args[0][1:3],
                         ("Download running", "A download is running. Stop it and quit?"))
        stop.assert_called_once_with()
        wait.assert_called_once_with(10000)
        self.assertTrue(closed)
        self.assertFalse(self.panel.budget_timer.isActive())

    def test_close_while_running_stays_open_on_no(self):
        w = self.window()
        closed, qmb, stop, wait = self._closing(w, "No")
        self.assertFalse(closed)
        stop.assert_not_called()
        self.assertTrue(w.isVisible())
        closed, qmb, stop, wait = self._closing(w, "Yes", wait_ok=False)
        self.assertFalse(closed)
        stop.assert_called_once_with()
        self.assertTrue(w.isVisible())
        self.assertEqual(w.statusBar().currentMessage(),
                         "Still stopping - try closing again in a moment.")

    def test_close_when_idle_closes_without_asking(self):
        w = self.window()
        qmb = mock.MagicMock()
        with mock.patch("rule34_downloader.gui.main_window.QMessageBox", qmb):
            closed = w.close()
        self.assertTrue(closed)
        qmb.question.assert_not_called()

    # -- real runs ---------------------------------------------------------------
    def test_full_run_against_mock_server_end_to_end(self):
        factory = self.server(total=25)
        w = self.window(job_factory=factory)
        self.creds()
        self.fill(count=5)
        self.form.start_button.click()
        self.assertTrue(self.pump(lambda: not self.panel.result_label.isHidden()),
                        self.panel.log_view.toPlainText()[-500:])
        self.assertTrue(self.panel.result_label.text().startswith("Finished: 5 downloaded, "))
        files = [p for p in (self.tmp / "out").rglob("*") if p.is_file()]
        self.assertEqual(len(files), 5)
        self.assertTrue(all("cat" in p.parts for p in files))
        self.assertTrue(self.pump(lambda: w.settings_button.isEnabled()))
        self.assertEqual(w.statusBar().currentMessage(), self.panel.result_label.text())
        self.assertEqual(self.panel.progress_bar.value(), 100)
        self.assertTrue(self.panel.back_button.isEnabled())
        self.panel.back_button.click()
        self.assertEqual(w.download_stack.currentIndex(), 0)

    def test_status_bar_shows_downloading_then_headline(self):
        w = self.window()
        self.creds()
        self.fill()
        with mock.patch.object(self.runner, "start", return_value=True):
            self.form.start_button.click()
        self.assertEqual(w.statusBar().currentMessage(),
                         f"Downloading to {self.tmp / 'out' / 'cat'}")
        self.finish(Summary(reason="stopped", downloaded=1))
        self.assertEqual(w.statusBar().currentMessage(),
                         "Stopped: 1 downloaded, 0 already there, 0 skipped, 0 failed")

    def test_start_error_path_shows_error_headline(self):
        def boom(cfg, creds, on_event):
            raise RuntimeError("cannot build job")
        w = self.window(job_factory=boom)
        self.creds()
        self.fill()
        self.form.start_button.click()
        self.assertTrue(self.pump(lambda: not self.panel.result_label.isHidden()))
        self.assertEqual(self.panel.result_label.property("error"), "true")
        self.assertIn("cannot build job", self.panel.result_label.text())
        self.assertTrue(w.settings_button.isEnabled())
        self.assertTrue(self.panel.back_button.isEnabled())

    def test_second_run_resets_the_panel(self):
        self.window()
        self.panel.begin(self.job(count=4))
        self.runner.stats.downloaded = 3
        self.runner.log_line.emit("old line")
        self.drive("waiting", wait_seconds=3, wait_reason="x")
        self.finish(Summary(reason="count_reached", downloaded=3))
        self.runner.stats = RunStats()
        self.panel.begin(self.job(include=["dog"]))
        self.assertEqual(self.panel.log_view.toPlainText(), "")
        self.assertTrue(self.panel.result_label.isHidden())
        self.assertTrue(self.panel.banner.isHidden())
        self.assertEqual(self.panel.progress_bar.value(), 0)
        self.assertEqual(self.panel.counters_label.text(), counters_text(RunStats()))
        self.assertEqual(self.panel.query_label.text(), "Search: dog")
        self.assertTrue(self.panel.budget_timer.isActive())

    def test_panel_never_shows_key_text(self):
        factory = self.server(total=25)
        w = self.window(job_factory=factory)
        self.creds()
        self.fill(count=2)
        self.form.start_button.click()
        self.assertTrue(self.pump(lambda: not self.panel.result_label.isHidden()))
        self.assertTrue(self.pump(lambda: w.settings_button.isEnabled()))
        self.assertNotIn(API_KEY, self.all_text(w))

    def test_screenshot_flag_still_shows_the_form(self):
        env = {k: v for k, v in os.environ.items() if k not in ("R34_USER_ID", "R34_API_KEY")}
        env.update(HOME=str(self.tmp), R34_CONFIG=str(self.cfg), QT_QPA_PLATFORM="offscreen")
        out = self.tmp / "shot.png"
        r = subprocess.run([sys.executable, "-m", "rule34_downloader.gui", "--screenshot",
                            str(out)], cwd=str(REPO), env=env, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGreater(out.stat().st_size, 0)
        r = subprocess.run([sys.executable, "-m", "rule34_downloader.gui", "--smoke"],
                           cwd=str(REPO), env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "smoke ok"), r.stderr)

    # -- robustness --------------------------------------------------------------
    def test_panel_slots_survive_finished_without_begin(self):
        from rule34_downloader.gui.run_panel import RunPanel
        from rule34_downloader.gui.runner import JobRunner
        runner = JobRunner()
        panel = RunPanel(runner, fake_budget_factory)
        self.addCleanup(dispose, runner)
        self.addCleanup(dispose, panel)
        errors = []
        with mock.patch.object(sys, "excepthook", lambda *a: errors.append(a)):
            runner.log_line.emit("hello:")
            runner.stats_changed.emit()
            runner.state_changed.emit("running")
            runner.stats.state = "finished"
            runner.finished.emit(Summary(reason="stopped"))
            text = panel.result_label.text()
            runner.finished.emit(Summary(reason="count_reached", downloaded=9))
            self.app.processEvents()
        self.assertEqual(errors, [])
        self.assertEqual(panel.result_label.text(), text)
        self.assertTrue(text.startswith("Stopped:"))
        panel.open_folder()   # no job config yet: no exception

    def test_busy_bar_value_is_set_on_finish(self):
        self.window()
        bar = self.panel.progress_bar
        self.panel.begin(self.job())
        self.drive("running")
        self.assertEqual((bar.minimum(), bar.maximum()), (0, 0))
        self.finish(Summary(reason="end_of_results"))
        self.assertEqual((bar.minimum(), bar.maximum(), bar.value()), (0, 100, 100))
        self.runner.stats = RunStats()
        self.panel.begin(self.job())
        self.drive("running")
        self.finish(Summary(reason="stopped"))
        self.assertEqual((bar.minimum(), bar.maximum(), bar.value()), (0, 100, 0))
        self.runner.stats = RunStats()
        self.panel.begin(self.job(count=4))
        self.runner.stats.downloaded = 2
        self.finish(Summary(reason="stopped"))
        self.assertEqual(bar.value(), 50)

    def test_begin_runs_before_start_with_a_fake_runner(self):
        seen = {}
        holder = {}

        class FakeRunner(QObject):
            event = Signal(str, object)
            stats_changed = Signal()
            log_line = Signal(str)
            state_changed = Signal(str)
            finished = Signal(object)

            def __init__(fake, parent=None):
                super().__init__(parent)
                fake.stats = RunStats()

            def is_running(fake):
                return False

            def start(fake, cfg, creds, factory=None):
                panel = holder["w"].run_panel
                seen["query"] = panel.query_label.text()
                seen["timer"] = panel.budget_timer.isActive()
                fake.stats.state = "running"
                fake.state_changed.emit("running")
                seen["stop_enabled"] = panel.stop_button.isEnabled()
                return True

            def stop(fake):
                pass

            def wait(fake, ms=0):
                return True

            def pause(fake):
                pass

            resume = retry_now = pause

        from rule34_downloader.gui.main_window import MainWindow
        with mock.patch("rule34_downloader.gui.main_window.JobRunner", FakeRunner):
            w = MainWindow(config_file=self.cfg, prefs_file=self.prefs,
                           budget_factory=fake_budget_factory)
        holder["w"] = w
        self.addCleanup(dispose, w)
        w.show()
        self.creds()
        w.search_form.include_edit.setText("cat")
        w.search_form.folder_edit.setText(str(self.tmp / "out"))
        w.search_form.mark_found(321)
        w.search_form.start_button.click()
        self.assertEqual(seen["query"], "Search: cat")
        self.assertTrue(seen["timer"])
        self.assertTrue(seen["stop_enabled"])

    def test_start_while_running_changes_nothing(self):
        w = self.window()
        self.creds()
        self.fill()
        with mock.patch.object(self.runner, "is_running", return_value=True), \
                mock.patch.object(self.panel, "begin") as begin, \
                mock.patch.object(self.runner, "start") as start:
            self.form.start_button.click()
        begin.assert_not_called()
        start.assert_not_called()
        self.assertEqual(w.download_stack.currentIndex(), 0)
        self.assertTrue(w.settings_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
