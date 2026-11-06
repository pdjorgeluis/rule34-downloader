"""Main window: header band, Download/Help tabs, status bar."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QPushButton, QStatusBar, QTabWidget,
    QVBoxLayout, QWidget,
)

from .. import config, form_model
from .search_form import SearchForm
from .settings_dialog import SettingsDialog

LOGO_PATH = Path(__file__).parent / "assets" / "logo.svg"
TITLE = "Rule34 Downloader"
NO_ACCOUNT = "No account saved - open Settings to add your user ID and API key"
READY = "Ready: the downloader engine is connected in the next release."


def logo_icon() -> QIcon:
    return QIcon(str(LOGO_PATH))


class MainWindow(QMainWindow):
    def __init__(self, config_file=None, prefs_file=None):
        super().__init__()
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
        self.tabs.addTab(self._download_page(self.search_form), "Download")
        self.tabs.addTab(
            self._page("help_tab", "Help: usage instructions arrive in a later release."),
            "Help")
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

    def _on_start_requested(self, _job_config) -> None:
        try:
            form_model.save_prefs(self.search_form.values(), self.prefs_file)
        except Exception:
            pass  # preferences are optional
        self.statusBar().showMessage(READY)

    @staticmethod
    def _page(name: str, text: str) -> QWidget:
        page = QWidget()
        page.setObjectName(name)
        page.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(page)
        label = QLabel(text)
        label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lay.addWidget(label)
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
