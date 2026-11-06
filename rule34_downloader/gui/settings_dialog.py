"""Settings dialog: user ID and API key (the key is never displayed)."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from .. import config

OPTIONS_URL = "https://rule34.xxx/index.php?page=account&s=options"
KEEP_HINT = "key saved - leave empty to keep it"
_USER_ID_RE = re.compile(r"^\d{1,12}$")
_WHITESPACE_RE = re.compile(r"\s")
MIN_KEY_LEN = 16


class SettingsDialog(QDialog):
    credentials_saved = Signal()

    def __init__(self, parent=None, config_file=None):
        super().__init__(parent)
        self.config_file = config_file
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)

        self.user_id_edit = QLineEdit()
        self.user_id_edit.setObjectName("user_id_edit")
        self.key_edit = QLineEdit()
        self.key_edit.setObjectName("key_edit")
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.show_key_check = QCheckBox("Show key")
        self.show_key_check.setObjectName("show_key_check")
        self.show_key_check.toggled.connect(self._toggle_echo)

        self.options_link = QLabel(f'<a href="{OPTIONS_URL}">Where do I find these?</a>')
        self.options_link.setObjectName("options_link")
        self.options_link.setTextFormat(Qt.RichText)
        self.options_link.linkActivated.connect(lambda _url: self.open_options_page())

        self.error_label = QLabel()
        self.error_label.setObjectName("error_label")
        self.error_label.setWordWrap(True)
        self.error_label.hide()

        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("save_button")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.save)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancel_button")
        self.cancel_button.clicked.connect(self.reject)

        form = QFormLayout()
        form.addRow("User ID", self.user_id_edit)
        form.addRow("API key", self.key_edit)
        form.addRow("", self.show_key_check)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.cancel_button)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.options_link)
        lay.addWidget(self.error_label)
        lay.addLayout(buttons)

        self._has_existing = False
        self._prefill()

    def _existing(self):
        try:
            return config.load_credentials(path=self.config_file)
        except config.MissingCredentials:
            return None

    def _prefill(self) -> None:
        creds = self._existing()
        self._has_existing = creds is not None
        if creds is not None:
            self.user_id_edit.setText(creds.user_id)
            self.key_edit.setPlaceholderText(KEEP_HINT)

    def _toggle_echo(self, shown: bool) -> None:
        self.key_edit.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)

    def open_options_page(self) -> None:
        QDesktopServices.openUrl(QUrl(OPTIONS_URL))

    def _fail(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()

    def save(self) -> None:
        user_id = self.user_id_edit.text()
        key = self.key_edit.text()
        if not _USER_ID_RE.match(user_id):
            return self._fail("The user ID must be 1 to 12 digits.")
        if key:
            if _WHITESPACE_RE.search(key):
                return self._fail("The API key must not contain spaces or line breaks.")
            if len(key) < MIN_KEY_LEN:
                return self._fail(f"The API key must be at least {MIN_KEY_LEN} characters.")
        else:
            existing = self._existing()
            if existing is None:
                return self._fail("Enter your API key.")
            key = existing.api_key
        config.save_credentials(user_id, key, path=self.config_file)
        self.error_label.hide()
        self.accept()
        self.credentials_saved.emit()
