"""GUI shell tests (offscreen). Skipped when PySide6 cannot be imported."""
import hashlib
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "rule34_downloader" / "gui" / "assets" / "logo.svg"
LOGO_SHA256 = "029835980fc43e071c36029898add5c2b95c10eb1d4796d84b2f699ce31b2499"
FAKE_KEY = "k" * 32  # test-only dummy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false;*.warning=false")
try:
    from PySide6.QtCore import QPoint, QUrl
    from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QWidget
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


def rgb(img, x, y):
    c = img.pixelColor(x, y)
    return (c.red(), c.green(), c.blue())


def hex_rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def close(a, b, tol=2):
    return all(abs(p - q) <= tol for p, q in zip(a, b))


class TempConfigCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.cfg = self.tmp / "config.json"
        # never read real credentials (env or legacy ~/.config/r34dl.json)
        env = {k: v for k, v in os.environ.items() if k not in ("R34_USER_ID", "R34_API_KEY")}
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        h = mock.patch.object(Path, "home", return_value=self.tmp)
        h.start()
        self.addCleanup(h.stop)

    def save(self, uid="12345", key=FAKE_KEY):
        from rule34_downloader import config
        config.save_credentials(uid, key, path=self.cfg)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class GuiCase(TempConfigCase):
    @classmethod
    def setUpClass(cls):
        from rule34_downloader.gui.app import create_app
        cls.app = create_app([])

    def window(self):
        from rule34_downloader.gui.main_window import MainWindow
        w = MainWindow(config_file=self.cfg)
        self.addCleanup(w.close)
        w.show()
        self.app.processEvents()
        return w

    def dialog(self):
        from rule34_downloader.gui.settings_dialog import SettingsDialog
        d = SettingsDialog(config_file=self.cfg)
        self.addCleanup(d.close)
        d.show()
        self.app.processEvents()
        return d


class ThemeTests(GuiCase):
    def test_theme_constants_exact_hex(self):
        from rule34_downloader.gui import theme
        expected = dict(MINT="#aae5a3", WHITE="#ffffff", BUTTON_GRAY="#f0eeef",
                        BUTTON_BORDER="#767676", LINK_BLUE="#000099", TEXT="#000000",
                        TEAL_DARK="#0e3d44", TEAL_NEON="#3ff3d4", ERROR_RED="#990000",
                        DISABLED_TEXT="#777777")
        for name, value in expected.items():
            self.assertEqual(getattr(theme, name), value, name)

    def test_stylesheet_mentions_every_palette_color(self):
        from rule34_downloader.gui import theme
        sheet = theme.stylesheet()
        colors = [theme.MINT, theme.WHITE, theme.BUTTON_GRAY, theme.BUTTON_BORDER,
                  theme.LINK_BLUE, theme.TEXT, theme.TEAL_DARK, theme.TEAL_NEON,
                  theme.ERROR_RED, theme.DISABLED_TEXT]
        for c in colors:
            self.assertIn(c, sheet)
        self.assertEqual(set(re.findall(r"#[0-9a-fA-F]{3,8}\b", sheet)), set(colors))
        self.assertNotRegex(sheet, r"rgba?\(")

    def test_window_background_pixel_is_mint(self):
        w = self.window()
        page = w.tabs.currentWidget()
        img = w.grab().toImage()
        p = page.mapTo(w, QPoint(page.width() - 12, page.height() - 12))
        self.assertTrue(close(rgb(img, p.x(), p.y()), hex_rgb("#aae5a3")), rgb(img, p.x(), p.y()))

    def test_header_band_pixel_is_dark_teal(self):
        w = self.window()
        img = w.grab().toImage()
        h = w.header
        p = h.mapTo(w, QPoint(h.width() // 2 + 40, 3))
        self.assertTrue(close(rgb(img, p.x(), p.y()), hex_rgb("#0e3d44")), rgb(img, p.x(), p.y()))

    def test_line_edit_pixel_is_white(self):
        d = self.dialog()
        edit = d.key_edit
        img = edit.grab().toImage()
        x, y = edit.width() - 8, edit.height() // 2
        self.assertTrue(close(rgb(img, x, y), (255, 255, 255)), rgb(img, x, y))

    def test_button_pixel_is_button_gray(self):
        w = self.window()
        b = w.settings_button
        img = b.grab().toImage()
        x, y = 4, b.height() // 2
        self.assertTrue(close(rgb(img, x, y), hex_rgb("#f0eeef")), rgb(img, x, y))

    def test_link_color_is_link_blue_in_palette(self):
        from PySide6.QtGui import QPalette
        d = self.dialog()
        c = d.options_link.palette().color(QPalette.Link)
        self.assertEqual((c.red(), c.green(), c.blue()), hex_rgb("#000099"))


class WindowTests(GuiCase):
    def test_window_title_is_rule34_downloader(self):
        self.assertEqual(self.window().windowTitle(), "Rule34 Downloader")

    def test_window_has_download_and_help_tabs(self):
        w = self.window()
        self.assertEqual([w.tabs.tabText(i) for i in range(w.tabs.count())], ["Download", "Help"])
        self.assertEqual(w.tabs.widget(0).objectName(), "download_tab")
        self.assertEqual(w.tabs.widget(1).objectName(), "help_tab")
        from rule34_downloader.gui.search_form import SearchForm
        self.assertIsNotNone(w.tabs.widget(0).findChild(SearchForm))
        from PySide6.QtWidgets import QTextBrowser
        browser = w.tabs.widget(1).findChild(QTextBrowser, "help_browser")
        self.assertIsNotNone(browser)
        self.assertIn("Getting started", browser.toPlainText())

    def test_header_title_label_text(self):
        w = self.window()
        self.assertEqual(w.header_title.text(), "Rule34 Downloader")
        self.assertEqual(w.settings_button.text(), "Settings")

    def test_header_logo_renders_nonblank(self):
        w = self.window()
        pm = w.header_logo.pixmap()
        self.assertEqual((pm.width(), pm.height()), (48, 48))
        from rule34_downloader.gui.main_window import logo_icon
        for size in (48, 16):
            img = logo_icon().pixmap(size, size).toImage()
            colors = {img.pixel(x, y) for x in range(size) for y in range(size)}
            self.assertGreater(len(colors), 2, size)
            self.assertTrue(any(img.pixelColor(x, y).alpha() > 0
                                for x in range(size) for y in range(size)))

    def test_min_size_800x640(self):
        m = self.window().minimumSize()
        self.assertEqual((m.width(), m.height()), (800, 640))

    def test_status_bar_without_account(self):
        w = self.window()
        self.assertEqual(w.statusBar().currentMessage(),
                         "No account saved - open Settings to add your user ID and API key")

    def test_status_bar_with_account_masks_key(self):
        self.save("4242", FAKE_KEY)
        w = self.window()
        msg = w.statusBar().currentMessage()
        self.assertEqual(msg, "Account: 4242 - key saved")
        self.assertNotIn(FAKE_KEY, msg)
        d = w.open_settings(modal=False)
        self.addCleanup(d.close)
        d.user_id_edit.setText("777")
        d.key_edit.setText("z" * 20)
        d.save_button.click()
        self.assertEqual(w.statusBar().currentMessage(), "Account: 777 - key saved")


class SettingsTests(GuiCase):
    def test_settings_prefills_user_id_and_never_shows_key(self):
        self.save("999", FAKE_KEY)
        d = self.dialog()
        self.assertEqual(d.user_id_edit.text(), "999")
        self.assertEqual(d.key_edit.text(), "")
        self.assertEqual(d.key_edit.placeholderText(), "key saved - leave empty to keep it")
        shown = [d.user_id_edit.text(), d.key_edit.text(), d.key_edit.placeholderText(),
                 d.error_label.text(), repr(d), d.windowTitle()]
        shown += [lab.text() for lab in d.findChildren(QLabel)]
        for s in shown:
            self.assertNotIn(FAKE_KEY, s)

    def test_settings_key_field_is_password_by_default_and_toggles(self):
        d = self.dialog()
        self.assertEqual(d.key_edit.echoMode(), QLineEdit.Password)
        self.assertEqual(d.show_key_check.text(), "Show key")
        d.show_key_check.setChecked(True)
        self.assertEqual(d.key_edit.echoMode(), QLineEdit.Normal)
        d.show_key_check.setChecked(False)
        self.assertEqual(d.key_edit.echoMode(), QLineEdit.Password)

    def test_settings_rejects_non_numeric_user_id(self):
        d = self.dialog()
        for bad in ("", "abc", "12a", "1" * 13, " 12"):
            d.error_label.hide()
            d.user_id_edit.setText(bad)
            d.key_edit.setText(FAKE_KEY)
            d.save_button.click()
            self.assertFalse(d.error_label.isHidden(), bad)
            self.assertTrue(d.isVisible(), bad)
            self.assertFalse(self.cfg.exists(), bad)
        d.user_id_edit.setText("1" * 12)
        d.save_button.click()
        self.assertTrue(self.cfg.exists())

    def test_settings_rejects_short_or_spaced_key(self):
        d = self.dialog()
        d.user_id_edit.setText("123")
        for bad in ("a" * 15, "a" * 10 + " " + "b" * 10, "a" * 20 + "\n", ""):
            d.error_label.hide()
            d.key_edit.setText(bad)
            d.save_button.click()
            self.assertFalse(d.error_label.isHidden(), repr(bad))
            self.assertNotIn(bad.strip() or "\0", d.error_label.text())
            self.assertFalse(self.cfg.exists(), repr(bad))
        # user ID error wins over key error
        d.user_id_edit.setText("x")
        d.key_edit.setText("short")
        d.save_button.click()
        self.assertIn("user ID", d.error_label.text())

    def test_settings_save_writes_config_0600_roundtrip(self):
        from rule34_downloader import config
        d = self.dialog()
        seen = []
        d.credentials_saved.connect(lambda: seen.append(1))
        d.user_id_edit.setText("31337")
        d.key_edit.setText(FAKE_KEY)
        d.save_button.click()
        self.assertTrue(d.error_label.isHidden())
        self.assertFalse(d.isVisible())
        self.assertEqual(seen, [1])
        creds = config.load_credentials(env={}, path=self.cfg, legacy_path=self.tmp / "none")
        self.assertEqual((creds.user_id, creds.api_key), ("31337", FAKE_KEY))
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.cfg.stat().st_mode), 0o600)

    def test_settings_blank_key_keeps_existing_key(self):
        from rule34_downloader import config
        self.save("1", FAKE_KEY)
        d = self.dialog()
        d.user_id_edit.setText("2")
        d.key_edit.setText("")
        d.save_button.click()
        self.assertTrue(d.error_label.isHidden())
        creds = config.load_credentials(env={}, path=self.cfg, legacy_path=self.tmp / "none")
        self.assertEqual((creds.user_id, creds.api_key), ("2", FAKE_KEY))

    def test_settings_options_link_opens_options_page(self):
        d = self.dialog()
        self.assertEqual(d.options_link.text().count("Where do I find these?"), 1)
        with mock.patch("rule34_downloader.gui.settings_dialog.QDesktopServices.openUrl") as op:
            d.open_options_page()
            d.options_link.linkActivated.emit("x")
        self.assertEqual(op.call_count, 2)
        url = op.call_args[0][0]
        self.assertEqual(url.toString(), "https://rule34.xxx/index.php?page=account&s=options")


def gui_env(tmp):
    env = {k: v for k, v in os.environ.items() if k not in ("R34_USER_ID", "R34_API_KEY")}
    env.update(QT_QPA_PLATFORM="offscreen", R34_CONFIG=str(Path(tmp) / "cfg.json"), HOME=str(tmp))
    return env


def run_gui(tmp, *args):
    return subprocess.run([sys.executable, "-m", "rule34_downloader.gui", *args],
                          cwd=ROOT, env=gui_env(tmp), capture_output=True, text=True, timeout=60)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class AppTests(TempConfigCase):
    def test_smoke_flag_exits_zero_and_prints_smoke_ok(self):
        r = run_gui(self.tmp, "--smoke")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "smoke ok"), r.stderr)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), [])

    def test_screenshot_flag_writes_png(self):
        out = self.tmp / "shot.png"
        r = run_gui(self.tmp, "--screenshot", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")
        data = out.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        w = int.from_bytes(data[16:20], "big")
        h = int.from_bytes(data[20:24], "big")
        self.assertGreaterEqual((w, h), (720, 520))
        self.assertFalse((self.tmp / "cfg.json").exists())

    def test_screenshot_bad_directory_returns_1(self):
        r = run_gui(self.tmp, "--screenshot", str(self.tmp / "nope" / "x.png"))
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.startswith("error: cannot write screenshot:"), r.stderr)


@unittest.skipUnless(HAVE_QT, "GUI shell tests need PySide6")
class NonGuiTests(unittest.TestCase):
    def test_non_gui_package_imports_without_pyside6(self):
        code = ("import sys; sys.modules['PySide6'] = None\n"
                "import rule34_downloader.cli, rule34_downloader.engine, rule34_downloader.config\n"
                "print('imported')")
        r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "imported"), r.stderr)


@unittest.skipUnless(HAVE_QT, "GUI shell tests need PySide6")
class LogoTests(unittest.TestCase):
    def test_logo_svg_sha256_matches_steward_asset(self):
        self.assertEqual(hashlib.sha256(LOGO.read_bytes()).hexdigest(), LOGO_SHA256)

    def test_logo_svg_is_wellformed_xml_and_has_no_script_or_external_refs(self):
        root = ET.fromstring(LOGO.read_bytes())
        for el in root.iter():
            tag = el.tag.rsplit("}", 1)[-1].lower()
            self.assertNotIn(tag, ("script", "image", "foreignobject", "use"))
            for k, v in el.attrib.items():
                self.assertFalse(k.lower().startswith("on"), k)
                self.assertNotRegex(v, r"(?i)https?://|javascript:")


if __name__ == "__main__":
    unittest.main()
