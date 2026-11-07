"""The Download tab's search form. Builds a validated JobConfig; never starts a download."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QPushButton, QRadioButton, QSpinBox, QVBoxLayout,
    QWidget,
)

from .. import form_model as fm
from ..form_model import FormValues
from .folder_dialog import choose_folder
from .runner import SearchResult


def _name(widget, name):
    widget.setObjectName(name)
    return widget


class SearchForm(QWidget):
    values_changed = Signal()
    start_requested = Signal(object)
    search_requested = Signal(str)

    def __init__(self, parent=None, values=None):
        super().__init__(parent)
        self.setObjectName("search_form")
        self._action_error = ""
        self._quiet = False  # True while the form changes controls itself
        self._state = "idle"   # idle, searching, fresh, stale or error
        self._pending_key = ""
        self._found_key = ""
        self._total = None
        self._error_text = ""
        self._build()
        init = values if values is not None else FormValues(folder=fm.default_folder())
        self._apply(init)
        self._refresh()

    # ---- construction ---------------------------------------------------
    def _build(self):
        self.include_edit = _name(QLineEdit(), "include_edit")
        self.include_edit.setPlaceholderText("tags to include, separated by spaces (use _ inside a tag, e.g. hyuuga_hinata)")
        self.exclude_edit = _name(QLineEdit(), "exclude_edit")
        self.exclude_edit.setPlaceholderText("tags to exclude")
        self.min_score_check = _name(QCheckBox("Minimum score"), "min_score_check")
        self.min_score_spin = _name(QSpinBox(), "min_score_spin")
        self.min_score_spin.setRange(0, 99999)
        self.min_score_spin.setEnabled(False)

        self.order_combo = _name(QComboBox(), "order_combo")
        self.order_combo.addItems(["Newest", "Oldest"])
        self.pictures_check = _name(QCheckBox("Pictures"), "pictures_check")
        self.gifs_check = _name(QCheckBox("GIFs"), "gifs_check")
        self.videos_check = _name(QCheckBox("Videos"), "videos_check")
        self.count_number_radio = _name(QRadioButton("Number of files"), "count_number_radio")
        self.count_spin = _name(QSpinBox(), "count_spin")
        self.count_spin.setRange(1, 1000000)
        self.count_all_radio = _name(QRadioButton("All"), "count_all_radio")
        self._count_group = QButtonGroup(self)
        self._count_group.addButton(self.count_number_radio)
        self._count_group.addButton(self.count_all_radio)
        self.workers_spin = _name(QSpinBox(), "workers_spin")
        self.workers_spin.setRange(1, 100)
        self.safety_check = _name(
            QCheckBox("Exclude loli, shota, child, toddler, underage"), "safety_check")
        self.subfolder_check = _name(
            QCheckBox("Put files in a folder named after the search"), "subfolder_check")

        self.folder_edit = _name(QLineEdit(), "folder_edit")
        self.browse_button = _name(QPushButton("Browse..."), "browse_button")
        self.new_folder_button = _name(QPushButton("New folder..."), "new_folder_button")

        self.query_preview = _name(QLabel(), "query_preview")
        self.dest_preview = _name(QLabel(), "dest_preview")
        for lab in (self.query_preview, self.dest_preview):
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lab.setWordWrap(True)
        self.error_label = _name(QLabel(), "error_label")
        self.error_label.setWordWrap(True)
        self.found_label = _name(QLabel(), "found_label")
        self.found_label.setWordWrap(True)
        self.found_label.setProperty("state", "idle")
        self.search_button = _name(QPushButton("Search"), "search_button")
        self.start_button = _name(QPushButton("Download"), "start_button")

        root = QVBoxLayout(self)
        root.setSpacing(4)

        tags = QGroupBox("Tags")
        tl = QVBoxLayout(tags)
        tl.addWidget(self.include_edit)
        tl.addWidget(self.exclude_edit)
        row = QHBoxLayout()
        row.addWidget(self.min_score_check)
        row.addWidget(self.min_score_spin)
        row.addStretch(1)
        tl.addLayout(row)
        root.addWidget(tags)

        opts = QGroupBox("Options")
        ol = QVBoxLayout(opts)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Order:"))
        r1.addWidget(self.order_combo)
        r1.addSpacing(16)
        for w in (self.pictures_check, self.gifs_check, self.videos_check):
            r1.addWidget(w)
        r1.addStretch(1)
        ol.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(self.count_number_radio)
        r2.addWidget(self.count_spin)
        r2.addWidget(self.count_all_radio)
        r2.addSpacing(16)
        r2.addWidget(QLabel("Parallel workers:"))
        r2.addWidget(self.workers_spin)
        r2.addStretch(1)
        ol.addLayout(r2)
        ol.addWidget(self.safety_check)
        ol.addWidget(self.subfolder_check)
        root.addWidget(opts)

        folder = QGroupBox("Folder")
        fl = QHBoxLayout(folder)
        fl.addWidget(self.folder_edit, 1)
        fl.addWidget(self.browse_button)
        fl.addWidget(self.new_folder_button)
        root.addWidget(folder)

        root.addWidget(self.query_preview)
        root.addWidget(self.dest_preview)
        root.addWidget(self.found_label)
        root.addWidget(self.error_label)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.search_button)
        bottom.addWidget(self.start_button)
        root.addLayout(bottom)
        root.addStretch(1)

        self._controls = [
            self.include_edit, self.exclude_edit, self.min_score_check, self.min_score_spin,
            self.order_combo, self.pictures_check, self.gifs_check, self.videos_check,
            self.count_number_radio, self.count_all_radio, self.count_spin, self.workers_spin,
            self.safety_check, self.subfolder_check, self.folder_edit,
        ]
        for c in self._controls:
            for sig in ("textChanged", "toggled", "valueChanged", "currentIndexChanged"):
                if hasattr(c, sig):
                    getattr(c, sig).connect(self._on_user_change)
        self.min_score_check.toggled.connect(self.min_score_spin.setEnabled)
        self.count_all_radio.toggled.connect(
            lambda on: self.count_spin.setEnabled(not on))
        self.browse_button.clicked.connect(lambda _c=False: self.browse())
        self.new_folder_button.clicked.connect(lambda _c=False: self.new_folder())
        self.start_button.clicked.connect(lambda _c=False: self.request_start())
        self.search_button.clicked.connect(lambda _c=False: self.begin_search())
        self.include_edit.returnPressed.connect(self.begin_search)

    # ---- values ---------------------------------------------------------
    def values(self) -> FormValues:
        types = frozenset(
            k for k, w in (("pictures", self.pictures_check), ("gifs", self.gifs_check),
                           ("videos", self.videos_check)) if w.isChecked())
        return FormValues(
            include_text=self.include_edit.text(),
            exclude_text=self.exclude_edit.text(),
            min_score=self.min_score_spin.value() if self.min_score_check.isChecked() else None,
            order="oldest" if self.order_combo.currentIndex() == 1 else "newest",
            types=types,
            count_mode="all" if self.count_all_radio.isChecked() else "number",
            count=self.count_spin.value(),
            folder=self.folder_edit.text(),
            by_search=self.subfolder_check.isChecked(),
            workers=self.workers_spin.value(),
            safety=self.safety_check.isChecked(),
        )

    def _apply(self, v: FormValues) -> None:
        for c in self._controls:
            c.blockSignals(True)
        try:
            self.include_edit.setText(v.include_text)
            self.exclude_edit.setText(v.exclude_text)
            self.min_score_check.setChecked(v.min_score is not None)
            self.min_score_spin.setValue(v.min_score if v.min_score is not None else 0)
            self.min_score_spin.setEnabled(v.min_score is not None)
            self.order_combo.setCurrentIndex(1 if v.order == "oldest" else 0)
            self.pictures_check.setChecked("pictures" in v.types)
            self.gifs_check.setChecked("gifs" in v.types)
            self.videos_check.setChecked("videos" in v.types)
            self.count_spin.setValue(v.count)
            self.count_all_radio.setChecked(v.count_mode == "all")
            self.count_number_radio.setChecked(v.count_mode != "all")
            self.count_spin.setEnabled(v.count_mode != "all")
            self.workers_spin.setValue(v.workers)
            self.safety_check.setChecked(v.safety)
            self.subfolder_check.setChecked(v.by_search)
            self.folder_edit.setText(v.folder)
        finally:
            for c in self._controls:
                c.blockSignals(False)

    def set_values(self, v: FormValues) -> None:
        self._apply(v)
        self._refresh()
        self.values_changed.emit()

    # ---- refresh --------------------------------------------------------
    def _on_user_change(self, *_args) -> None:
        if self._quiet:
            return
        self._action_error = ""
        self._refresh()
        self.values_changed.emit()

    def _refresh(self) -> None:
        v = self.values()
        self.query_preview.setText("Search: " + fm.query_preview(v))
        self.dest_preview.setText("Saves to: " + fm.dest_preview(v))
        errors = fm.validate(v)
        include_empty = not fm.parse_tags(v.include_text)
        if self._action_error:
            shown = self._action_error
        elif errors and not include_empty:
            shown = errors[0]
        else:
            shown = ""
        self.error_label.setText(shown)
        self.error_label.setVisible(bool(shown))
        key = fm.count_query(v)
        if self._state == "fresh" and key != self._found_key:
            self._state = "stale"
            self._total = None
        elif self._state == "error" and key != self._found_key:
            self._state = "idle"
        self._show_found(v)
        self.search_button.setEnabled(bool(key) and self._state != "searching")
        self.start_button.setEnabled(self._can_download(v))

    def _can_download(self, v) -> bool:
        return (not fm.validate(v) and self._state == "fresh"
                and self._total is not None and self._total > 0)

    def _show_found(self, v) -> None:
        if self._state == "searching":
            text = fm.SEARCHING_TEXT
        elif self._state == "fresh":
            text = fm.found_message(v, self._total or 0)
        elif self._state == "stale":
            text = fm.STALE_TEXT
        elif self._state == "error":
            text = self._error_text
        else:
            text = fm.IDLE_TEXT
        self.found_label.setText(text)
        if self.found_label.property("state") != self._state:
            self.found_label.setProperty("state", self._state)
            style = self.found_label.style()
            style.unpolish(self.found_label)
            style.polish(self.found_label)

    # ---- search ---------------------------------------------------------
    def begin_search(self) -> bool:
        key = fm.count_query(self.values())
        if not key or self._state == "searching":
            return False
        self._state = "searching"
        self._pending_key = key
        self._total = None
        self._found_key = ""
        self._refresh()
        self.search_requested.emit(key)
        return True

    def set_search_result(self, result: SearchResult) -> None:
        if self._state != "searching":
            return
        key = fm.count_query(self.values())
        if key != self._pending_key:
            self._state = "stale"
            self._total = None
        elif result.ok:
            self._total = int(result.total or 0)
            self._found_key = key
            self._state = "fresh"
        else:
            self._error_text = fm.search_error_text(result.kind, result.wait_seconds)
            self._found_key = key
            self._state = "error"
        self._refresh()

    def set_search_error(self, kind: str) -> None:
        self._error_text = fm.search_error_text(kind)
        self._found_key = fm.count_query(self.values())
        self._total = None
        self._state = "error"
        self._refresh()

    def mark_found(self, total: int) -> None:
        self._total = int(total)
        self._found_key = fm.count_query(self.values())
        self._state = "fresh"
        self._refresh()

    def found_total(self):
        return self._total if self._state == "fresh" else None

    def request_start(self) -> bool:
        v = self.values()
        if not self._can_download(v):
            return False
        self.start_requested.emit(fm.to_job_config(v))
        return True

    # ---- folder actions -------------------------------------------------
    def _set_folder_from_action(self, text: str, error: str = "") -> None:
        self._quiet = True
        try:
            self.folder_edit.setText(text)
        finally:
            self._quiet = False
        self._action_error = error
        self._refresh()
        self.values_changed.emit()

    def _start_dir(self) -> str:
        text = self.folder_edit.text().strip()
        if text:
            p = Path(text).expanduser()
            while True:
                try:
                    if p.is_dir():
                        return str(p)
                except OSError:
                    pass
                if p.parent == p:
                    break
                p = p.parent
        return str(Path.home())

    def browse(self) -> None:
        chosen = choose_folder(self, self._start_dir())
        if chosen:
            self._set_folder_from_action(chosen)

    def new_folder(self) -> None:
        text, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if not ok or not text.strip():
            return
        name = fm.valid_new_folder_name(text)
        if name is None:
            self._action_error = "That folder name is not allowed."
            self._refresh()
            return
        current = self.folder_edit.text().strip()
        base = Path(current).expanduser() if current else Path(fm.default_folder())
        target = base / name
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._action_error = f"Could not create the folder: {exc.strerror or exc}"
            self._refresh()
            return
        self._set_folder_from_action(str(target))
