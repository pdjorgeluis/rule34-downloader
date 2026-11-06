"""Main window: header band, Download/Help tabs, status bar."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QPushButton, QStatusBar, QTabWidget,
    QVBoxLayout, QWidget,
)

from .. import config
from .settings_dialog import SettingsDialog

LOGO_PATH = Path(__file__).parent / "assets" / "logo.svg"
TITLE = "Rule34 Downloader"
NO_ACCOUNT = "No account saved - open Settings to add your user ID and API key"


def logo_icon() -> QIcon:
    return QIcon(str(LOGO_PATH))


class MainWindow(QMainWindow):
    def __init__(self, config_file=None):
        super().__init__()
        self.config_file = Path(config_file) if config_file is not None else None
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
        self.tabs.addTab(
            self._page("download_tab", "Download: the search form arrives in the next release."),
            "Download")
        self.tabs.addTab(
            self._page("help_tab", "Help: usage instructions arrive in a later release."),
            "Help")
        outer.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self.setStatusBar(QStatusBar())
        self.refresh_status()

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
