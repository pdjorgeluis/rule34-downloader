"""Download tab search form tests (offscreen; no real dialogs, no network)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from test_gui_shell import GuiCase, HAVE_QT, close, hex_rgb  # noqa: E402

from rule34_downloader import form_model  # noqa: E402

if HAVE_QT:
    from rule34_downloader.gui.main_window import NO_ACCOUNT  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


class FormCase(GuiCase):
    def window(self):
        from rule34_downloader.gui.main_window import MainWindow
        w = MainWindow(config_file=self.cfg)
        self.addCleanup(w.close)
        w.show()
        self.app.processEvents()
        self.f = w.search_form
        return w

    def fill(self, tags="cat", folder=None):
        self.f.include_edit.setText(tags)
        self.f.folder_edit.setText(folder or str(self.tmp / "out"))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class FormTests(FormCase):
    def test_download_tab_holds_search_form(self):
        from rule34_downloader.gui.search_form import SearchForm
        w = self.window()
        page = w.tabs.widget(0)
        self.assertEqual(page.objectName(), "download_tab")
        form = page.findChild(SearchForm)
        self.assertIsNotNone(form)
        self.assertEqual(form.objectName(), "search_form")
        self.assertIs(form, w.search_form)
        from PySide6.QtWidgets import QLabel
        self.assertNotIn("arrives in the next release",
                         " ".join(l.text() for l in page.findChildren(QLabel)))

    def test_form_defaults(self):
        self.window()
        f = self.f
        self.assertEqual(f.include_edit.text(), "")
        self.assertEqual(f.include_edit.placeholderText(),
                         "tags to include, separated by spaces (use _ inside a tag, e.g. hyuuga_hinata)")
        self.assertEqual(f.exclude_edit.placeholderText(), "tags to exclude")
        self.assertEqual([f.order_combo.itemText(i) for i in range(f.order_combo.count())],
                         ["Newest", "Oldest"])
        self.assertEqual(f.order_combo.currentText(), "Newest")
        self.assertEqual((f.pictures_check.isChecked(), f.gifs_check.isChecked(),
                          f.videos_check.isChecked()), (True, False, False))
        self.assertTrue(f.count_number_radio.isChecked())
        self.assertEqual(f.count_spin.value(), 100)
        self.assertEqual(f.workers_spin.value(), 3)
        self.assertFalse(f.safety_check.isChecked())
        self.assertTrue(f.subfolder_check.isChecked())
        self.assertFalse(f.min_score_check.isChecked())
        self.assertFalse(f.min_score_spin.isEnabled())
        self.assertEqual(f.folder_edit.text(), form_model.default_folder())
        self.assertEqual(f.browse_button.text(), "Browse...")
        self.assertEqual(f.new_folder_button.text(), "New folder...")
        self.assertEqual(f.start_button.text(), "Start")

    def test_include_text_updates_query_preview_at_once(self):
        self.window()
        self.f.include_edit.setText("cat dog")
        self.assertEqual(self.f.query_preview.text(), "Search: cat dog")

    def test_exclude_and_min_score_appear_in_preview(self):
        self.window()
        f = self.f
        f.include_edit.setText("cat")
        f.exclude_edit.setText("fish")
        f.min_score_check.setChecked(True)
        f.min_score_spin.setValue(5)
        self.assertEqual(f.query_preview.text(), "Search: cat -fish score:>=5")

    def test_oldest_adds_sort_asc_to_preview(self):
        self.window()
        self.f.include_edit.setText("cat")
        self.f.order_combo.setCurrentIndex(1)
        self.assertEqual(self.f.query_preview.text(), "Search: cat sort:id:asc")

    def test_safety_checkbox_off_by_default_and_adds_exclusions(self):
        self.window()
        f = self.f
        f.include_edit.setText("cat")
        self.assertFalse(f.safety_check.isChecked())
        self.assertNotIn("-loli", f.query_preview.text())
        f.safety_check.setChecked(True)
        self.assertEqual(f.query_preview.text(),
                         "Search: cat -loli -shota -child -toddler -underage")

    def test_conflict_shows_error_label_and_disables_start(self):
        self.window()
        self.fill("cat")
        self.f.exclude_edit.setText("cat")
        self.assertFalse(self.f.error_label.isHidden())
        self.assertEqual(self.f.error_label.text(), "tag in both include and exclude: cat")
        self.assertFalse(self.f.start_button.isEnabled())
        self.f.exclude_edit.setText("dog")
        self.assertTrue(self.f.error_label.isHidden())
        self.assertTrue(self.f.start_button.isEnabled())

    def test_start_disabled_until_a_tag_is_entered(self):
        self.window()
        self.assertFalse(self.f.start_button.isEnabled())
        self.assertTrue(self.f.error_label.isHidden())
        self.f.include_edit.setText(" , ")
        self.assertFalse(self.f.start_button.isEnabled())
        self.assertTrue(self.f.error_label.isHidden())
        self.f.include_edit.setText("cat")
        self.assertTrue(self.f.start_button.isEnabled())

    def test_start_click_emits_job_config(self):
        self.window()
        self.fill("cat dog")
        got = []
        self.f.start_requested.connect(got.append)
        self.f.start_button.click()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].include, ["cat", "dog"])
        self.assertEqual(got[0].dest, self.tmp / "out")
        self.assertEqual((got[0].count, got[0].unavailable_policy), (100, "pause"))

    def test_count_all_radio_disables_the_number_box(self):
        self.window()
        self.assertTrue(self.f.count_spin.isEnabled())
        self.f.count_all_radio.setChecked(True)
        self.assertFalse(self.f.count_spin.isEnabled())
        self.assertEqual(self.f.values().count_mode, "all")
        self.f.count_number_radio.setChecked(True)
        self.assertTrue(self.f.count_spin.isEnabled())

    def test_unchecking_every_type_disables_start(self):
        self.window()
        self.fill()
        self.assertTrue(self.f.start_button.isEnabled())
        self.f.pictures_check.setChecked(False)
        self.assertFalse(self.f.start_button.isEnabled())
        self.assertEqual(self.f.error_label.text(), "Choose at least one file type.")

    def test_workers_spin_range_is_1_to_5(self):
        self.window()
        self.assertEqual((self.f.workers_spin.minimum(), self.f.workers_spin.maximum()), (1, 5))
        self.f.workers_spin.setValue(9)
        self.assertEqual(self.f.workers_spin.value(), 5)

    def test_browse_sets_folder(self):
        self.window()
        target = self.tmp / "picked"
        with mock.patch("rule34_downloader.gui.search_form.QFileDialog") as dlg:
            dlg.getExistingDirectory.return_value = str(target)
            self.f.browse_button.click()
        self.assertEqual(self.f.folder_edit.text(), str(target))
        args = dlg.getExistingDirectory.call_args[0]
        self.assertEqual(args[1], "Choose a folder")
        self.assertEqual(args[2], str(self.tmp))  # default folder does not exist: nearest ancestor

    def test_browse_cancel_keeps_folder(self):
        self.window()
        self.fill(folder=str(self.tmp))
        with mock.patch("rule34_downloader.gui.search_form.QFileDialog") as dlg:
            dlg.getExistingDirectory.return_value = ""
            self.f.browse_button.click()
        self.assertEqual(self.f.folder_edit.text(), str(self.tmp))

    def test_browse_requests_qt_dialog_not_native(self):
        from PySide6.QtWidgets import QFileDialog as RealDialog
        dont, only = RealDialog.Option.DontUseNativeDialog, RealDialog.Option.ShowDirsOnly
        self.window()
        with mock.patch("rule34_downloader.gui.search_form.QFileDialog") as dlg:
            dlg.getExistingDirectory.return_value = ""
            self.f.browse_button.click()
        args = dlg.getExistingDirectory.call_args[0]
        self.assertEqual(len(args), 4)
        self.assertTrue(args[3] & dont)
        self.assertTrue(args[3] & only)

    def _probe_real_dialog(self):
        """Click Browse unpatched; a timer inspects the dialog, records, rejects it."""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QDialogButtonBox, QFileDialog
        self.window()
        self.fill(folder=str(self.tmp))
        rec, errors, clock = {}, [], {"n": 0}

        def poll():
            try:
                dlg = next((w for w in QApplication.topLevelWidgets()
                            if isinstance(w, QFileDialog) and w.isVisible()), None)
                if dlg is None:
                    clock["n"] += 1
                    if clock["n"] < 60:
                        QTimer.singleShot(50, poll)
                    return
                box = dlg.findChild(QDialogButtonBox)
                rec["texts"] = [b.text().replace("&", "") for b in box.buttons()]
                rec["size"] = (dlg.width(), dlg.height())
                dlg.reject()
            except Exception as exc:  # Qt would swallow it
                errors.append(exc)
                for w in QApplication.topLevelWidgets():
                    if isinstance(w, QFileDialog):
                        w.reject()

        QTimer.singleShot(0, poll)
        self.f.browse_button.click()
        if errors:
            raise errors[0]
        self.assertTrue(rec, "dialog never appeared")
        self.assertEqual(self.f.folder_edit.text(), str(self.tmp))
        return rec

    def test_browse_real_dialog_has_choose_and_cancel_buttons(self):
        rec = self._probe_real_dialog()
        self.assertEqual(sorted(rec["texts"]), ["Cancel", "Choose"])

    def test_browse_real_dialog_fits_a_small_screen(self):
        w, h = self._probe_real_dialog()["size"]
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)
        self.assertLessEqual(w, 900)
        self.assertLessEqual(h, 700)

    def _new_folder(self, name, ok=True):
        with mock.patch("rule34_downloader.gui.search_form.QInputDialog") as dlg:
            dlg.getText.return_value = (name, ok)
            self.f.new_folder_button.click()

    def test_new_folder_creates_directory_and_selects_it(self):
        self.window()
        self.fill(folder=str(self.tmp))
        self._new_folder("my pics")
        self.assertTrue((self.tmp / "my pics").is_dir())
        self.assertEqual(self.f.folder_edit.text(), str(self.tmp / "my pics"))
        self.assertTrue(self.f.error_label.isHidden())

    def test_new_folder_bad_name_creates_nothing(self):
        self.window()
        self.fill(folder=str(self.tmp))
        before = sorted(p.name for p in self.tmp.iterdir())
        for bad in ("../evil", "a:b", ".."):
            self._new_folder(bad)
            self.assertEqual(self.f.error_label.text(), "That folder name is not allowed.")
            self.assertEqual(self.f.folder_edit.text(), str(self.tmp))
        self._new_folder("   ")  # blank: nothing happens
        self._new_folder("fine", ok=False)  # cancelled: nothing happens
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), before)

    def test_new_folder_existing_name_selects_it(self):
        self.window()
        (self.tmp / "there").mkdir()
        self.fill(folder=str(self.tmp))
        self._new_folder("there")
        self.assertEqual(self.f.folder_edit.text(), str(self.tmp / "there"))
        self.assertTrue(self.f.error_label.isHidden())

    def test_prefs_saved_on_start_and_restored_by_new_window(self):
        from rule34_downloader.gui.main_window import MainWindow
        w = self.window()
        self.fill("cat", str(self.tmp / "dl"))
        self.f.order_combo.setCurrentIndex(1)
        self.f.gifs_check.setChecked(True)
        self.f.workers_spin.setValue(4)
        self.f.count_all_radio.setChecked(True)
        self.f.start_button.click()
        w2 = MainWindow(config_file=self.cfg)
        self.addCleanup(w2.close)
        g = w2.search_form
        self.assertEqual(g.include_edit.text(), "")
        self.assertEqual(g.folder_edit.text(), str(self.tmp / "dl"))
        self.assertEqual(g.order_combo.currentText(), "Oldest")
        self.assertTrue(g.gifs_check.isChecked())
        self.assertEqual(g.workers_spin.value(), 4)
        self.assertTrue(g.count_all_radio.isChecked())

    def test_prefs_file_is_written_next_to_the_config_file(self):
        self.window()
        self.fill()
        self.assertFalse((self.tmp / "ui.json").exists())
        self.f.start_button.click()
        self.assertTrue((self.tmp / "ui.json").is_file())
        explicit = self.tmp / "elsewhere" / "p.json"
        from rule34_downloader.gui.main_window import MainWindow
        w = MainWindow(config_file=self.cfg, prefs_file=explicit)
        self.addCleanup(w.close)
        self.assertEqual(w.prefs_file, explicit)

    def test_destination_label_shows_search_subfolder(self):
        self.window()
        self.fill("cat dog", str(self.tmp / "out"))
        self.assertEqual(self.f.dest_preview.text(), f"Saves to: {self.tmp / 'out' / 'cat+dog'}")
        self.f.subfolder_check.setChecked(False)
        self.assertEqual(self.f.dest_preview.text(), f"Saves to: {self.tmp / 'out'}")

    def test_include_field_background_is_white(self):
        w = self.window()
        w.resize(900, 700)
        self.app.processEvents()
        img = self.f.include_edit.grab().toImage()
        self.assertTrue(close((img.pixelColor(img.width() - 6, img.height() // 2).red(),
                               img.pixelColor(img.width() - 6, img.height() // 2).green(),
                               img.pixelColor(img.width() - 6, img.height() // 2).blue()),
                              hex_rgb("#ffffff")))

    def test_start_click_shows_engine_not_connected_message(self):
        w = self.window()
        self.fill()
        self.f.start_button.click()
        self.assertEqual(w.statusBar().currentMessage(), NO_ACCOUNT)

    def test_screenshot_status_bar_is_not_blank(self):
        from PySide6.QtGui import QImage
        env = {k: v for k, v in os.environ.items() if k not in ("R34_USER_ID", "R34_API_KEY")}
        env.update(HOME=str(self.tmp), R34_CONFIG=str(self.cfg), QT_QPA_PLATFORM="offscreen")
        out = self.tmp / "shot.png"
        r = subprocess.run([sys.executable, "-m", "rule34_downloader.gui", "--screenshot", str(out)],
                           cwd=str(REPO), env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        img = QImage(str(out))
        self.assertFalse(img.isNull())
        dark = 0
        for y in range(img.height() - 24, img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                if max(c.red(), c.green(), c.blue()) < 0x60:
                    dark += 1
        self.assertGreaterEqual(dark, 50)

    def test_request_start_with_invalid_values_is_a_noop(self):
        self.window()
        got = []
        self.f.start_requested.connect(got.append)
        self.assertFalse(self.f.request_start())
        self.assertEqual(got, [])
        self.fill()
        self.assertTrue(self.f.request_start())
        self.assertEqual(len(got), 1)

    def test_action_error_clears_on_next_user_change(self):
        self.window()
        self.fill(folder=str(self.tmp))
        self._new_folder("a/b")
        self.assertEqual(self.f.error_label.text(), "That folder name is not allowed.")
        self.f.exclude_edit.setText("x")
        self.assertTrue(self.f.error_label.isHidden())

    def test_min_score_zero_is_valid_and_ticks_the_check(self):
        self.window()
        self.fill()
        self.f.min_score_check.setChecked(True)
        self.assertTrue(self.f.min_score_spin.isEnabled())
        self.assertEqual(self.f.query_preview.text(), "Search: cat score:>=0")
        self.assertTrue(self.f.start_button.isEnabled())
        self.f.set_values(form_model.FormValues(include_text="cat", folder=str(self.tmp),
                                                min_score=None))
        self.assertFalse(self.f.min_score_check.isChecked())
        self.assertFalse(self.f.min_score_spin.isEnabled())
        self.f.set_values(form_model.FormValues(include_text="cat", folder=str(self.tmp),
                                                min_score=0))
        self.assertTrue(self.f.min_score_check.isChecked())
        self.assertEqual(self.f.values().min_score, 0)

    def test_save_prefs_failure_does_not_block_start(self):
        w = self.window()
        self.fill()
        with mock.patch.object(form_model, "save_prefs", side_effect=OSError("disk full")):
            self.f.start_button.click()
        self.assertEqual(w.statusBar().currentMessage(), NO_ACCOUNT)


if __name__ == "__main__":
    unittest.main()
