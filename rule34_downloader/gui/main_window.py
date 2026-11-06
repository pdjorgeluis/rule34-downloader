"""Main window: header band, Download/Help tabs, status bar."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QStackedWidget,
    QStatusBar, QTabWidget, QVBoxLayout, QWidget,
)

from .. import config, form_model
from .help_tab import HelpTab
from .run_panel import RunPanel
from .runner import JobRunner
from .search_form import SearchForm
from .settings_dialog import SettingsDialog

LOGO_PATH = Path(__file__).parent / "assets" / "logo.svg"
TITLE = "Rule34 Downloader"
NO_ACCOUNT = "No account saved - open Settings to add your user ID and API key"


def logo_icon() -> QIcon:
    return QIcon(str(LOGO_PATH))


class MainWindow(QMainWindow):
    def __init__(self, config_file=None, prefs_file=None, job_factory=None,
                 budget_factory=None):
        super().__init__()
        self.job_factory = job_factory
        self.config_file = Path(config_file) if config_file is not None else None
        if prefs_file is not None:
            self.prefs_file = Path(prefs_file)
        elif self.config_file is not None:
            self.prefs_file = self.config_file.with_name("ui.json")
        else:
            self.prefs_file = config.config_path().with_name("ui.json")
        self.setWindowTitle(TITLE)
        self.setWindowIcon(logo_icon())
        self.setMinimumSize(720, 520)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = QWidget()
        self.header.setObjectName("header")
        self.header.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(self.header)
        row.setContentsMargins(12, 8, 12, 8)
        self.header_logo = QLabel()
        self.header_logo.setObjectName("header_logo")
        self.header_logo.setFixedSize(48, 48)
        self.header_logo.setPixmap(logo_icon().pixmap(48, 48))
        self.header_title = QLabel(TITLE)
        self.header_title.setObjectName("header_title")
        self.settings_button = QPushButton("Settings")
        self.settings_button.setObjectName("settings_button")
        self.settings_button.clicked.connect(lambda _checked=False: self.open_settings())
        row.addWidget(self.header_logo)
        row.addWidget(self.header_title)
        row.addStretch(1)
        row.addWidget(self.settings_button)
        outer.addWidget(self.header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("tabs")
        self.search_form = SearchForm(values=form_model.load_prefs(self.prefs_file))
        self.search_form.start_requested.connect(self._on_start_requested)
        self.runner = JobRunner(self)
        self.run_panel = RunPanel(self.runner, budget_factory)
        self.download_stack = QStackedWidget()
        self.download_stack.setObjectName("download_stack")
        self.download_stack.addWidget(self.search_form)
        self.download_stack.addWidget(self.run_panel)
        self.run_panel.back_button.clicked.connect(
            lambda _c=False: self.download_stack.setCurrentIndex(0))
        self.runner.finished.connect(self._on_run_finished)
        self.tabs.addTab(self._download_page(self.download_stack), "Download")
        self.tabs.addTab(self._help_page(), "Help")
        outer.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self.setStatusBar(QStatusBar())
        self.refresh_status()

    @staticmethod
    def _download_page(form: QWidget) -> QWidget:
        page = QWidget()
        page.setObjectName("download_tab")
        page.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(page)
        lay.addWidget(form)
        return page

    def _on_start_requested(self, job_config) -> None:
        try:
            form_model.save_prefs(self.search_form.values(), self.prefs_file)
        except Exception:
            pass  # preferences are optional
        try:
            creds = config.load_credentials(path=self.config_file)
        except config.MissingCredentials:
            self.statusBar().showMessage(NO_ACCOUNT)
            return
        if self.runner.is_running():
            return
        self.run_panel.begin(job_config)
        self.runner.start(job_config, creds, self.job_factory)
        del creds
        self.download_stack.setCurrentIndex(1)
        self.statusBar().showMessage(
            self.run_panel.dest_label.text().replace("Saves to:", "Downloading to", 1))
        self.settings_button.setEnabled(False)

    def _on_run_finished(self, _summary) -> None:
        self.statusBar().showMessage(self.run_panel.result_label.text())
        self.settings_button.setEnabled(True)

    def closeEvent(self, event) -> None:
        if self.runner.is_running():
            answer = QMessageBox.question(
                self, "Download running", "A download is running. Stop it and quit?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.runner.stop()
            if not self.runner.wait(10000):
                event.ignore()
                self.statusBar().showMessage(
                    "Still stopping - try closing again in a moment.")
                return
        self.run_panel.budget_timer.stop()
        event.accept()

    @staticmethod
    def _help_page() -> QWidget:
        page = QWidget()
        page.setObjectName("help_tab")
        page.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(page)
        lay.addWidget(HelpTab())
        return page

    def refresh_status(self) -> None:
        try:
            creds = config.load_credentials(path=self.config_file)
            text = f"Account: {creds.user_id} - key saved"
        except config.MissingCredentials:
            text = NO_ACCOUNT
        self.statusBar().showMessage(text)

    def open_settings(self, modal: bool = True) -> SettingsDialog:
        dialog = SettingsDialog(self, config_file=self.config_file)
        dialog.credentials_saved.connect(self.refresh_status)
        self.settings_dialog = dialog
        if not modal:
            dialog.open()
        else:
            dialog.exec()
        return dialog
