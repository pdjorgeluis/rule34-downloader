"""Help tab and polish GUI tests (offscreen). Skipped without PySide6."""
import os
import re
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false;*.warning=false")
try:
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QProgressBar, QTextBrowser
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

from rule34_downloader import help_content  # noqa: E402

TEN = ["MINT", "WHITE", "BUTTON_GRAY", "BUTTON_BORDER", "LINK_BLUE", "TEXT",
       "TEAL_DARK", "TEAL_NEON", "ERROR_RED", "DISABLED_TEXT"]


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class HelpGuiCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rule34_downloader.gui.app import create_app
        cls.app = create_app([])

    def tab(self, show=False):
        from rule34_downloader.gui.help_tab import HelpTab
        t = HelpTab()
        self.addCleanup(t.close)
        if show:
            t.resize(600, 300)
            t.show()
            self.app.processEvents()
        return t

    def test_help_tab_holds_a_help_browser(self):
        t = self.tab()
        self.assertEqual(t.objectName(), "help_tab_content")
        self.assertIsInstance(t.help_browser, QTextBrowser)
        self.assertEqual(t.help_browser.objectName(), "help_browser")

    def test_help_browser_shows_every_section_title(self):
        text = self.tab().help_browser.toPlainText()
        for title, _ in help_content.SECTIONS:
            self.assertIn(title, text)

    def test_help_toc_anchor_click_scrolls_without_opening_a_url(self):
        t = self.tab(show=True)
        bar = t.help_browser.verticalScrollBar()
        self.assertEqual(bar.value(), 0)
        with mock.patch("rule34_downloader.gui.help_tab.QDesktopServices.openUrl") as op:
            t.help_browser.anchorClicked.emit(QUrl("#sec-9"))
            self.app.processEvents()
            self.assertEqual(op.call_count, 0)
            t.help_browser.anchorClicked.emit(QUrl("#sec-4"))
            self.assertEqual(op.call_count, 0)
        t.help_browser.anchorClicked.emit(QUrl("#sec-9"))
        self.app.processEvents()
        self.assertGreater(bar.value(), 0)

    def test_options_link_opens_the_options_url_via_desktop_services(self):
        t = self.tab()
        with mock.patch("rule34_downloader.gui.help_tab.QDesktopServices.openUrl") as op:
            t.help_browser.anchorClicked.emit(QUrl(help_content.OPTIONS_URL))
        self.assertEqual(op.call_count, 1)
        self.assertEqual(op.call_args[0][0].toString(), help_content.OPTIONS_URL)

    def test_non_http_links_are_ignored(self):
        t = self.tab()
        with mock.patch("rule34_downloader.gui.help_tab.QDesktopServices.openUrl") as op:
            t.help_browser.anchorClicked.emit(QUrl("file:///etc/passwd"))
            t.help_browser.anchorClicked.emit(QUrl("javascript:alert(1)"))
        self.assertEqual(op.call_count, 0)

    def test_help_browser_is_read_only_with_white_background(self):
        t = self.tab(show=True)
        b = t.help_browser
        self.assertTrue(b.isReadOnly())
        self.assertFalse(b.openLinks())
        img = b.viewport().grab().toImage()
        c = img.pixelColor(img.width() - 3, img.height() - 3)
        self.assertEqual((c.red(), c.green(), c.blue()), (255, 255, 255))

    def test_include_placeholder_mentions_the_underscore(self):
        from rule34_downloader.gui.search_form import SearchForm
        f = SearchForm()
        self.addCleanup(f.close)
        self.assertEqual(
            f.include_edit.placeholderText(),
            "tags to include, separated by spaces (use _ inside a tag, e.g. hyuuga_hinata)")

    def test_progress_chunk_uses_the_neon_color(self):
        from rule34_downloader.gui import theme
        bar = QProgressBar()
        self.addCleanup(bar.close)
        bar.setRange(0, 100)
        bar.setValue(50)
        bar.resize(300, 30)
        bar.show()
        self.app.processEvents()
        img = bar.grab().toImage()
        want = tuple(int(theme.TEAL_NEON[i:i + 2], 16) for i in (1, 3, 5))
        c = img.pixelColor(10, img.height() // 2)
        got = (c.red(), c.green(), c.blue())
        self.assertTrue(all(abs(a - b) <= 2 for a, b in zip(got, want)), (got, want))

    def test_stylesheet_still_uses_only_the_ten_colors(self):
        from rule34_downloader.gui import theme
        used = {m.lower() for m in re.findall(r"#[0-9a-fA-F]{6}\b", theme.stylesheet())}
        self.assertEqual(used, {getattr(theme, n).lower() for n in TEN})

    def test_options_url_matches_the_settings_dialog_constant(self):
        from rule34_downloader.gui import settings_dialog
        self.assertEqual(help_content.OPTIONS_URL, settings_dialog.OPTIONS_URL)


if __name__ == "__main__":
    unittest.main()
