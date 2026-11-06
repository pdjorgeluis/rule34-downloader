"""Progress panel: bar, counters, live log, budget, banner, action buttons, result."""
from __future__ import annotations

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from .. import files
from ..budget import RequestBudget
from ..run_model import (
    banner_text, budget_snapshot, clean_log_line, counters_text, enabled_actions,
    finish_headline, progress_fraction, progress_text, query_text, result_folder,
)

ACTIVE = ("running", "waiting", "paused", "unavailable", "stopping")
BUDGET_MS = 5000


def _default_budget():
    return RequestBudget(limit=55)


class RunPanel(QWidget):
    def __init__(self, runner, budget_factory=None, parent=None):
        super().__init__(parent)
        self.setObjectName("run_panel")
        self.runner = runner
        self.budget_factory = budget_factory or _default_budget
        self.job_config = None
        self._count = None
        self._done = False

        def mk(cls, name, *args):
            w = cls(*args)
            w.setObjectName(name)
            return w

        self.query_label = mk(QLabel, "query_label")
        self.dest_label = mk(QLabel, "dest_label")
        self.banner = mk(QLabel, "banner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        self.progress_bar = mk(QProgressBar, "progress_bar")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFormat("%p%")
        self.counters_label = mk(QLabel, "counters_label")
        self.budget_label = mk(QLabel, "budget_label")
        self.log_view = mk(QPlainTextEdit, "log_view")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.result_label = mk(QLabel, "result_label")
        self.result_label.setWordWrap(True)
        self.result_label.setProperty("error", "false")
        self.result_label.hide()

        self.pause_button = mk(QPushButton, "pause_button", "Pause")
        self.resume_button = mk(QPushButton, "resume_button", "Resume")
        self.retry_button = mk(QPushButton, "retry_button", "Retry now")
        self.stop_button = mk(QPushButton, "stop_button", "Stop")
        self.open_folder_button = mk(QPushButton, "open_folder_button", "Open folder")
        self.back_button = mk(QPushButton, "back_button", "Back to search")

        lay = QVBoxLayout(self)
        for w in (self.query_label, self.dest_label, self.banner, self.progress_bar,
                  self.counters_label, self.budget_label):
            lay.addWidget(w)
        lay.addWidget(self.log_view, 1)
        lay.addWidget(self.result_label)
        row = QHBoxLayout()
        for b in (self.pause_button, self.resume_button, self.retry_button, self.stop_button):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.open_folder_button)
        row.addWidget(self.back_button)
        lay.addLayout(row)

        self.pause_button.clicked.connect(lambda _c=False: self.runner.pause())
        self.resume_button.clicked.connect(lambda _c=False: self.runner.resume())
        self.retry_button.clicked.connect(lambda _c=False: self.runner.retry_now())
        self.stop_button.clicked.connect(lambda _c=False: self.runner.stop())
        self.open_folder_button.clicked.connect(lambda _c=False: self.open_folder())

        self.budget_timer = QTimer(self)
        self.budget_timer.setObjectName("budget_timer")
        self.budget_timer.setInterval(BUDGET_MS)
        self.budget_timer.timeout.connect(self.refresh_budget)

        runner.log_line.connect(self._on_log_line)
        runner.stats_changed.connect(self._on_stats_changed)
        runner.state_changed.connect(self._on_state_changed)
        runner.finished.connect(self._on_finished)
        self._set_buttons("idle")
        self.refresh_budget()

    # -- public ----------------------------------------------------------------
    def begin(self, job_config) -> None:
        self.job_config = job_config
        self._count = job_config.count
        self._done = False
        self.query_label.setText(f"Search: {query_text(job_config)}")
        dest = job_config.dest
        if job_config.by_search:
            dest = dest / files.search_folder_name(job_config.include)
        self.dest_label.setText(f"Saves to: {dest}")
        self.log_view.clear()
        self.result_label.setText("")
        self.result_label.hide()
        self.banner.setText("")
        self.banner.hide()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.counters_label.setText(counters_text(self.runner.stats))
        self._set_buttons("idle")
        self.refresh_budget()
        self.budget_timer.start()

    def refresh_budget(self) -> None:
        try:
            used, limit = budget_snapshot(self.budget_factory())
        except Exception:
            used, limit = 0, 0
        if (used, limit) == (0, 0):
            self.budget_label.setText("API requests this hour: unknown")
        else:
            self.budget_label.setText(f"API requests this hour: {used} / {limit}")

    def open_folder(self) -> None:
        if self.job_config is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(result_folder(self.job_config))))

    # -- slots -----------------------------------------------------------------
    def _on_log_line(self, line) -> None:
        self.log_view.appendPlainText(clean_log_line(line))

    def _on_stats_changed(self) -> None:
        stats = self.runner.stats
        self.counters_label.setText(counters_text(stats))
        text = banner_text(stats)
        self.banner.setText(text)
        self.banner.setVisible(bool(text))
        self._update_progress(stats)
        self._set_buttons(stats.state)

    def _on_state_changed(self, _state) -> None:
        stats = self.runner.stats
        self._update_progress(stats)
        self._set_buttons(stats.state)
        self.refresh_budget()

    def _on_finished(self, summary) -> None:
        if self._done:
            return
        self._done = True
        self.budget_timer.stop()
        stats = self.runner.stats
        fraction = progress_fraction(stats, self._count)
        self.progress_bar.setRange(0, 100)
        if fraction is not None:
            value = int(fraction * 100)
        elif getattr(summary, "reason", None) in ("count_reached", "end_of_results"):
            value = 100
        else:
            value = 0
        self.progress_bar.setValue(value)
        text, is_error = finish_headline(summary)
        self.result_label.setText(text)
        self.result_label.setProperty("error", "true" if is_error else "false")
        self.result_label.style().unpolish(self.result_label)
        self.result_label.style().polish(self.result_label)
        self.result_label.show()
        self.banner.setText("")
        self.banner.hide()
        self.counters_label.setText(counters_text(stats))
        self.refresh_budget()
        self._set_buttons(stats.state)

    # -- helpers ---------------------------------------------------------------
    def _update_progress(self, stats) -> None:
        if stats.state == "finished":
            return
        if progress_fraction(stats, self._count) is None:
            if stats.state in ACTIVE:
                self.progress_bar.setRange(0, 0)
            return
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(int(progress_text(stats, self._count).rstrip("%")))

    def _set_buttons(self, state) -> None:
        actions = enabled_actions(state)
        self.pause_button.setEnabled("pause" in actions)
        self.resume_button.setEnabled("resume" in actions)
        self.retry_button.setEnabled("retry_now" in actions)
        self.stop_button.setEnabled("stop" in actions)
        self.back_button.setEnabled(state in ("idle", "finished"))
        can_open = (state == "finished" and self.job_config is not None
                    and result_folder(self.job_config).exists())
        self.open_folder_button.setEnabled(can_open)
