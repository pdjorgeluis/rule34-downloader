"""Help tab: a read-only browser over the pure help content."""
from __future__ import annotations

from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QTextBrowser, QVBoxLayout, QWidget

from ..help_content import help_html


class HelpTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("help_tab_content")
        self.help_browser = QTextBrowser()
        self.help_browser.setObjectName("help_browser")
        self.help_browser.setReadOnly(True)
        self.help_browser.setOpenLinks(False)
        self.help_browser.setOpenExternalLinks(False)
        self.help_browser.setHtml(help_html())
        self.help_browser.anchorClicked.connect(self._on_anchor_clicked)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.help_browser)

    def _on_anchor_clicked(self, url) -> None:
        if url.toString().startswith("#"):
            self.help_browser.scrollToAnchor(url.fragment())
        elif url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
