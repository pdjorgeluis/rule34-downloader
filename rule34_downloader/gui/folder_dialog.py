"""Folder chooser for Browse: lists mounted network shares and can connect to one.

The password lives only in the password line edit and in the local variable
handed to the worker thread; it is cleared right after it is read and again
when the dialog closes. Nothing typed here is saved anywhere.
"""
from __future__ import annotations

import os
import sys
import threading
import urllib.parse

from PySide6.QtCore import QDir, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListView, QPushButton, QVBoxLayout,
)

from ..network_mounts import (
    Mount, MountResult, build_uri, find_gio, list_mounts, mount_share,
    validate_server, validate_share,
)

CONNECT_TEXT = "Connect to server\u2026"

_FAILURE_TEXT = {
    "bad_credentials": "The user name or password was not accepted.",
    "unreachable": "Could not reach the server. Check the name and your network.",
    "timeout": "The server did not answer in time.",
    "no_gio": "The gio command is not installed.",
    "invalid": "Check the server, share and user names.",
}
_FAILURE_DEFAULT = "Could not connect."


def _same_uri(a: str, b: str) -> bool:
    return urllib.parse.unquote(a).rstrip("/") == urllib.parse.unquote(b).rstrip("/")


class _ShareLabels:
    """Keeps the mounted shares' names in the side panel (Qt resets them on navigation)."""

    def __init__(self, dlg: QFileDialog):
        self._dlg = dlg
        self.mounts: list = []

    def apply(self) -> None:
        try:
            view = self._dlg.findChild(QListView, "sidebar")
            model = view.model() if view is not None else None
            if model is None or not self.mounts:
                return
            for m in self.mounts:
                want = QUrl.fromLocalFile(m.path)
                base = os.path.basename(m.path.rstrip("/"))
                rows = [model.index(i, 0) for i in range(model.rowCount())]
                match = [ix for ix in rows if ix.data(Qt.ItemDataRole.UserRole + 1) == want]
                if not match:
                    match = [ix for ix in rows
                             if not isinstance(ix.data(Qt.ItemDataRole.UserRole + 1), QUrl)
                             and ix.data(Qt.ItemDataRole.DisplayRole) == base]
                for ix in match:
                    model.setData(ix, m.name, Qt.ItemDataRole.DisplayRole)
        except Exception:  # labelling is cosmetic; never break the dialog
            return

    def later(self, *_ignored) -> None:
        QTimer.singleShot(0, self.apply)


def _default_urls() -> list:
    """Qt's own default side panel on Linux: Computer and Home."""
    return [QUrl("file:"), QUrl.fromLocalFile(QDir.homePath())]


def _distinct_mounts(mounts) -> list:
    """One mount per path (first wins); the home folder is already in the panel."""
    home = QDir.homePath().rstrip("/")
    seen, out = set(), []
    for m in mounts:
        key = m.path.rstrip("/")
        if key == home or key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


def build_folder_dialog(parent, start_dir: str) -> QFileDialog:
    dlg = QFileDialog(parent, "Choose a folder", start_dir)
    dlg.setFileMode(QFileDialog.FileMode.Directory)
    # Avoid the native dialog: the GTK chooser can open larger than the screen and hide its confirm button.
    dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
    dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    linux = sys.platform.startswith("linux")

    labels = _ShareLabels(dlg)
    dlg._share_labels = labels

    def refresh() -> list:
        mounts = list_mounts()
        if not linux:
            return mounts
        # Never start from the dialog's current list: Qt restores saved shortcuts there.
        shown = _distinct_mounts(mounts)
        labels.mounts = shown
        dlg.setSidebarUrls(_default_urls() + [QUrl.fromLocalFile(m.path) for m in shown])
        labels.apply()
        return mounts

    def forget_mounts(*_ignored) -> None:
        # Qt saves the side panel when the dialog is destroyed: leave it plain.
        if linux:
            dlg.setSidebarUrls(_default_urls())

    dlg.finished.connect(forget_mounts)

    refresh()
    QTimer.singleShot(0, labels.apply)
    dlg.directoryEntered.connect(labels.later)
    dlg.currentChanged.connect(labels.later)

    if find_gio():
        box = dlg.findChild(QDialogButtonBox)
        if box is not None:
            button = box.addButton(CONNECT_TEXT, QDialogButtonBox.ButtonRole.ActionRole)

            def connect_clicked() -> None:
                cd = ConnectDialog(dlg)
                accepted = bool(cd.exec())
                uri = cd.connected_uri
                cd.deleteLater()
                if not accepted:
                    return
                for m in refresh():
                    if _same_uri(m.uri, uri):
                        dlg.setDirectory(m.path)
                        break

            button.clicked.connect(lambda *_: connect_clicked())
    return dlg


def choose_folder(parent, start_dir: str) -> str:
    dlg = build_folder_dialog(parent, start_dir)
    try:
        if dlg.exec():
            files = dlg.selectedFiles()
            return files[0] if files else ""
        return ""
    finally:
        dlg.deleteLater()


class _Carrier(QObject):
    done = Signal(object)


class ConnectDialog(QDialog):
    def __init__(self, parent=None, mount_fn=None):
        super().__init__(parent)
        self._mount_fn = mount_fn
        self._working = False
        self._thread = None
        self._server = ""
        self._share = ""
        self.connected_uri = ""
        self.setWindowTitle("Connect to server")
        self.setMinimumWidth(420)

        self.server_edit = self._edit("server_edit", "10.1.2.13 or nas")
        self.share_edit = self._edit("share_edit", "share name")
        self.user_edit = self._edit("user_edit", "")
        self.domain_edit = self._edit("domain_edit", "optional")
        self.password_edit = self._edit("password_edit", "")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)

        self.status_label = QLabel("")
        self.status_label.setObjectName("status_label")
        self.status_label.setWordWrap(True)
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("connect_button")
        self.connect_button.setDefault(True)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancel_button")

        form = QFormLayout()
        form.addRow("Server", self.server_edit)
        form.addRow("Share", self.share_edit)
        form.addRow("User", self.user_edit)
        form.addRow("Domain", self.domain_edit)
        form.addRow("Password", self.password_edit)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.connect_button)
        buttons.addWidget(self.cancel_button)
        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(self.status_label)
        root.addLayout(buttons)

        self._carrier = _Carrier(self)
        self._carrier.done.connect(self._on_result, Qt.ConnectionType.QueuedConnection)
        self.connect_button.clicked.connect(self._start)
        self.cancel_button.clicked.connect(self.reject)

    @staticmethod
    def _edit(name: str, placeholder: str) -> QLineEdit:
        e = QLineEdit()
        e.setObjectName(name)
        if placeholder:
            e.setPlaceholderText(placeholder)
        return e

    def _inputs(self):
        return (self.server_edit, self.share_edit, self.user_edit,
                self.domain_edit, self.password_edit)

    def _set_busy(self, busy: bool) -> None:
        self._working = busy
        for w in self._inputs() + (self.connect_button, self.cancel_button):
            w.setEnabled(not busy)

    def _start(self) -> None:
        if self._working:
            return
        server, share = self.server_edit.text().strip(), self.share_edit.text().strip()
        user = self.user_edit.text().strip()
        problem = (validate_server(server) or validate_share(share)
                   or ("" if user else "Enter the user name.")
                   or ("" if self.password_edit.text() else "Enter the password."))
        if problem:
            self.status_label.setText(problem)
            return
        password = self.password_edit.text()
        self.password_edit.clear()
        domain = self.domain_edit.text().strip()
        self._server, self._share = server, share
        self._set_busy(True)
        self.status_label.setText("Connecting...")
        fn = self._mount_fn if self._mount_fn is not None else mount_share
        carrier = self._carrier

        def work() -> None:
            try:
                result = fn(server, share, user, password, domain)
            except Exception:
                result = MountResult(False, "error", "")
            carrier.done.emit(result)

        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()

    def _on_result(self, result) -> None:
        self._working = False
        if getattr(result, "ok", False):
            self.connected_uri = build_uri(self._server, self._share)
            self.status_label.setText("Connected.")
            self._set_busy(False)
            self.accept()
            return
        self._set_busy(False)
        self.password_edit.clear()
        self.status_label.setText(
            _FAILURE_TEXT.get(getattr(result, "kind", ""), _FAILURE_DEFAULT))
        self.password_edit.setFocus()

    def reject(self) -> None:
        if self._working:
            return
        super().reject()

    def done(self, code) -> None:
        if self._working:
            return
        self.password_edit.clear()
        super().done(code)

    def closeEvent(self, event) -> None:
        if self._working:
            event.ignore()
            return
        self.password_edit.clear()
        super().closeEvent(event)
