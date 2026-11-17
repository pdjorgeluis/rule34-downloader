"""Browse dialog: mounted shares in the side panel and the Connect dialog.

Offscreen; no real gio, no real mount, no network (a loud guard fails any attempt).
"""
import os
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from test_gui_shell import GuiCase, HAVE_QT, dispose, redirect_qt_settings  # noqa: E402

from rule34_downloader.network_mounts import Mount, MountResult  # noqa: E402

if HAVE_QT:
    from PySide6.QtCore import Qt, QTimer, QUrl  # noqa: E402
    from PySide6.QtCore import QSettings  # noqa: E402
    from PySide6.QtWidgets import (  # noqa: E402
        QApplication, QDialog, QDialogButtonBox, QFileDialog, QLineEdit, QListView,
    )
    from rule34_downloader.gui import folder_dialog  # noqa: E402

MOD = "rule34_downloader.gui.folder_dialog"
GUARD = "real gio must not run in tests"
NAME = "ai-data on 10.1.2.13"
URI = "smb://10.1.2.13/ai-data/"


from platform_skips import linux_only  # noqa: E402

@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class FolderDialogCase(GuiCase):
    def setUp(self):
        super().setUp()
        self.share = self.tmp / "ai-data"
        self.share.mkdir()
        self.mount = Mount(NAME, URI, str(self.share))
        self.list_fake = mock.Mock(return_value=[self.mount])
        self.gio_fake = mock.Mock(return_value="/nonexistent/gio")
        self.mount_fake = mock.Mock(side_effect=AssertionError(GUARD))
        for target, fake in (("list_mounts", self.list_fake), ("find_gio", self.gio_fake),
                             ("mount_share", self.mount_fake)):
            p = mock.patch(f"{MOD}.{target}", fake)
            p.start()
            self.addCleanup(p.stop)
        for target in ("rule34_downloader.network_mounts.subprocess.run",
                       "rule34_downloader.network_mounts.os.execvpe"):
            p = mock.patch(target, side_effect=AssertionError(GUARD))
            p.start()
            self.addCleanup(p.stop)

    def build(self, start=None):
        dlg = folder_dialog.build_folder_dialog(None, start or str(self.tmp))
        self.addCleanup(dispose, dlg)
        dlg.show()
        self.app.processEvents()
        return dlg

    def rows(self, dlg):
        model = dlg.findChild(QListView, "sidebar").model()
        return [model.index(i, 0).data() for i in range(model.rowCount())]

    def pump(self, until, seconds=2.0):
        end = time.monotonic() + seconds
        while not until() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()

    def connect_dialog(self, fn):
        cd = folder_dialog.ConnectDialog(None, mount_fn=fn)
        self.addCleanup(self._finish, cd)
        cd.show()
        self.app.processEvents()
        return cd

    def _finish(self, cd):
        if cd._thread is not None:
            cd._thread.join(3)
        self.app.processEvents()
        dispose(cd)

    def fill(self, cd, server="10.1.2.13", share="ai-data", user="bob",
             password="s3cret!", domain="WG"):
        cd.server_edit.setText(server)
        cd.share_edit.setText(share)
        cd.user_edit.setText(user)
        cd.password_edit.setText(password)
        cd.domain_edit.setText(domain)

    def wait_result(self, cd):
        self.pump(lambda: cd._thread is not None and not cd._thread.is_alive()
                  and cd.connect_button.isEnabled() or cd.result() == QDialog.DialogCode.Accepted,
                  3.0)
        if cd._thread is not None:
            cd._thread.join(3)
        self.pump(lambda: False, 0.05)

    def extra_button(self, dlg):
        box = dlg.findChild(QDialogButtonBox)
        return next((b for b in box.buttons() if "Connect" in b.text()), None)

    @linux_only

    def test_build_dialog_is_non_native_directory_dialog_with_dirs_only(self):
        dlg = self.build()
        self.assertEqual(dlg.fileMode(), QFileDialog.FileMode.Directory)
        self.assertTrue(dlg.testOption(QFileDialog.Option.ShowDirsOnly))
        self.assertTrue(dlg.testOption(QFileDialog.Option.DontUseNativeDialog))
        self.assertEqual(dlg.windowTitle(), "Choose a folder")
        self.assertEqual(dlg.directory().absolutePath(), str(self.tmp))

    def test_windows_uses_the_native_dialog_so_network_shares_are_reachable(self):
        from rule34_downloader.gui import folder_dialog
        with mock.patch.object(folder_dialog, "_use_native_dialog", return_value=True):
            dlg = self.build()
        self.assertFalse(dlg.testOption(QFileDialog.Option.DontUseNativeDialog))
        with mock.patch.object(folder_dialog, "_use_native_dialog", return_value=False):
            dlg = self.build()
        self.assertTrue(dlg.testOption(QFileDialog.Option.DontUseNativeDialog))

    @linux_only

    def test_sidebar_lists_each_mount_with_its_name(self):
        dlg = self.build()
        urls = [u.toLocalFile() for u in dlg.sidebarUrls()]
        self.assertIn(str(self.share), urls)
        self.assertEqual(self.list_fake.call_count, 1)
        dlg._share_labels.apply()
        self.assertIn(NAME, self.rows(dlg))
        model = dlg.findChild(QListView, "sidebar").model()
        ours = [model.index(i, 0).data() for i in range(model.rowCount())
                if model.index(i, 0).data(Qt.ItemDataRole.UserRole + 1)
                == QUrl.fromLocalFile(str(self.share))]
        self.assertEqual(ours, [NAME])

    @linux_only

    def test_sidebar_label_survives_navigation(self):
        dlg = self.build()
        dlg._share_labels.apply()
        self.assertIn(NAME, self.rows(dlg))
        dlg.setDirectory(str(self.tmp))
        self.pump(lambda: False, 0.2)
        QTimer.singleShot(0, lambda: None)
        self.pump(lambda: NAME in self.rows(dlg), 2.0)
        self.assertIn(NAME, self.rows(dlg))

    def test_no_gio_means_no_connect_button_and_no_mount_entries(self):
        self.gio_fake.return_value = None
        self.list_fake.return_value = []
        dlg = self.build()
        self.assertIsNone(self.extra_button(dlg))
        self.assertEqual(sorted(b.text().replace("&", "")
                                for b in dlg.findChild(QDialogButtonBox).buttons()),
                         ["Cancel", "Choose"])
        self.assertNotIn(str(self.share), [u.toLocalFile() for u in dlg.sidebarUrls()])
        self.assertNotIn(NAME, self.rows(dlg))

    def test_connect_button_added_when_gio_present_and_does_not_close_dialog(self):
        seen = []

        class Stub:
            connected_uri = ""

            def __init__(self, parent=None, mount_fn=None):
                seen.append(parent)

            def exec(self):
                return 0

            def deleteLater(self):
                pass

        dlg = self.build()
        button = self.extra_button(dlg)
        self.assertIsNotNone(button)
        self.assertEqual(button.text(), "Connect to server\u2026")
        with mock.patch(f"{MOD}.ConnectDialog", Stub):
            button.click()
        self.app.processEvents()
        self.assertEqual(seen, [dlg])
        self.assertTrue(dlg.isVisible())

    @linux_only

    def test_connect_success_refreshes_sidebar_and_enters_the_share(self):
        other = self.tmp / "elsewhere"
        other.mkdir()
        self.list_fake.side_effect = [[], [self.mount]]

        class Stub:
            connected_uri = URI

            def __init__(self, parent=None, mount_fn=None):
                pass

            def exec(self):
                return 1

            def deleteLater(self):
                pass

        dlg = self.build(str(other))
        self.assertNotIn(str(self.share), [u.toLocalFile() for u in dlg.sidebarUrls()])
        with mock.patch(f"{MOD}.ConnectDialog", Stub):
            self.extra_button(dlg).click()
        self.app.processEvents()
        self.assertIn(str(self.share), [u.toLocalFile() for u in dlg.sidebarUrls()])
        self.assertEqual(dlg.directory().absolutePath(), str(self.share))
        self.assertIn(NAME, self.rows(dlg))

    def test_connect_dialog_validates_before_mounting(self):
        fn = mock.Mock(return_value=MountResult(True, "ok", ""))
        cd = self.connect_dialog(fn)
        self.fill(cd, server="bad server!")
        cd.connect_button.click()
        self.assertEqual(cd.status_label.text(), "Enter the server name or IPv4 address.")
        self.fill(cd, share="a/b")
        cd.server_edit.setText("10.1.2.13")
        cd.connect_button.click()
        self.assertEqual(cd.status_label.text(),
                         "The share name has characters that are not allowed.")
        fn.assert_not_called()
        self.assertIsNone(cd._thread)

    def test_connect_dialog_requires_user_and_password(self):
        fn = mock.Mock(return_value=MountResult(True, "ok", ""))
        cd = self.connect_dialog(fn)
        self.fill(cd, user="")
        cd.connect_button.click()
        self.assertEqual(cd.status_label.text(), "Enter the user name.")
        self.fill(cd, password="")
        cd.user_edit.setText("bob")
        cd.connect_button.click()
        self.assertEqual(cd.status_label.text(), "Enter the password.")
        fn.assert_not_called()

    def test_connect_dialog_passes_values_to_mount_fn_and_clears_password(self):
        fn = mock.Mock(return_value=MountResult(True, "ok", "Connected."))
        cd = self.connect_dialog(fn)
        self.fill(cd, server=" 10.1.2.13 ", share=" ai-data ", domain=" WG ")
        cd.connect_button.click()
        self.assertEqual(cd.password_edit.text(), "")
        self.wait_result(cd)
        fn.assert_called_once_with("10.1.2.13", "ai-data", "bob", "s3cret!", "WG")
        self.assertEqual(cd.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(cd.connected_uri, URI)
        self.assertEqual(cd.status_label.text(), "Connected.")
        self.assertEqual(cd.password_edit.text(), "")

    def test_connect_dialog_failure_shows_fixed_text_per_kind_and_re_enables(self):
        expected = {
            "bad_credentials": "The user name or password was not accepted.",
            "unreachable": "Could not reach the server. Check the name and your network.",
            "timeout": "The server did not answer in time.",
            "no_gio": "The gio command is not installed.",
            "invalid": "Check the server, share and user names.",
            "weird": "Could not connect.",
        }
        for kind, text in expected.items():
            with self.subTest(kind=kind):
                cd = self.connect_dialog(
                    lambda *a, k=kind: MountResult(False, k, "raw helper words"))
                self.fill(cd)
                cd.connect_button.click()
                self.wait_result(cd)
                self.assertEqual(cd.status_label.text(), text)
                self.assertTrue(cd.connect_button.isEnabled())
                self.assertTrue(cd.server_edit.isEnabled())
                self.assertEqual(cd.server_edit.text(), "10.1.2.13")
                self.assertEqual(cd.user_edit.text(), "bob")
                self.assertEqual(cd.password_edit.text(), "")
                self.assertTrue(cd.password_edit.hasFocus() or cd.password_edit.isEnabled())
        cd = self.connect_dialog(mock.Mock(side_effect=RuntimeError("boom")))
        self.fill(cd)
        cd.connect_button.click()
        self.wait_result(cd)
        self.assertEqual(cd.status_label.text(), "Could not connect.")

    def test_connect_dialog_blocks_close_while_working(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def slow(*args):
            release.wait(4)
            return MountResult(False, "timeout", "")

        cd = self.connect_dialog(slow)
        self.fill(cd)
        cd.connect_button.click()
        self.assertEqual(cd.status_label.text(), "Connecting...")
        self.assertFalse(cd.connect_button.isEnabled())
        self.assertFalse(cd.cancel_button.isEnabled())
        cd.reject()
        cd.close()
        self.app.processEvents()
        self.assertTrue(cd.isVisible())
        release.set()
        self.wait_result(cd)
        self.assertTrue(cd.connect_button.isEnabled())
        self.assertEqual(cd.status_label.text(), "The server did not answer in time.")
        cd.reject()
        self.assertFalse(cd.isVisible())

    def test_connect_dialog_password_field_is_masked(self):
        cd = self.connect_dialog(mock.Mock())
        self.assertEqual(cd.password_edit.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(cd.server_edit.placeholderText(), "10.1.2.13 or nas")
        self.assertEqual(cd.share_edit.placeholderText(), "share name")
        self.assertEqual(cd.domain_edit.placeholderText(), "optional")
        self.assertEqual(cd.windowTitle(), "Connect to server")
        self.assertGreaterEqual(cd.minimumWidth(), 420)
        self.assertEqual(cd.connect_button.text(), "Connect")
        self.assertEqual(cd.cancel_button.text(), "Cancel")

    def test_failure_text_never_contains_password_or_transcript(self):
        cd = self.connect_dialog(lambda *a: MountResult(
            False, "bad_credentials", "Authentication Required User [x]: Password: s3cret!"))
        self.fill(cd)
        cd.connect_button.click()
        self.wait_result(cd)
        text = cd.status_label.text()
        self.assertEqual(text, "The user name or password was not accepted.")
        self.assertNotIn("s3cret!", text)
        self.assertNotIn("Authentication Required", text)
        self.assertNotIn("s3cret!", cd.toolTip() + cd.status_label.toolTip())
        self.assertEqual(cd.password_edit.text(), "")

    def test_tests_never_run_real_gio(self):
        with self.assertRaises(AssertionError):
            subprocess.run(["x"])
        with self.assertRaises(AssertionError):
            os.execvpe("x", ["x"], {})
        self.assertIs(folder_dialog.list_mounts, self.list_fake)
        self.assertIs(folder_dialog.find_gio, self.gio_fake)
        self.assertIs(folder_dialog.mount_share, self.mount_fake)


    # --- card 6d: fixed side panel, never saved, settings redirected -------------

    def qt_settings(self):
        return QSettings(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, "QtProject")

    def clear_saved_dialog_state(self):
        st = self.qt_settings()
        st.remove("FileDialog")
        st.sync()
        self.addCleanup(lambda: (self.qt_settings().remove("FileDialog"), self.qt_settings().sync()))

    def make_mount(self, name, label):
        d = self.tmp / name
        d.mkdir()
        return Mount(label, f"smb://10.1.2.13/{name}/", str(d))

    @linux_only

    def test_settings_are_redirected_to_a_temp_dir_in_gui_tests(self):
        name = self.qt_settings().fileName()
        self.assertFalse(name.startswith(os.path.expanduser("~/.config")))
        self.assertTrue(name.startswith(redirect_qt_settings()))

    @linux_only

    def test_sidebar_rows_stay_fixed_after_repeated_opens(self):
        # 5 loops measured under 3 s on this machine
        self.clear_saved_dialog_state()
        two = [self.make_mount("one", "one on nas"), self.make_mount("two", "two on nas")]
        self.list_fake.return_value = two
        for _ in range(5):
            dlg = self.build()
            self.assertEqual(self.rows(dlg), ["Computer", os.path.basename(os.path.expanduser("~")),
                                              "one on nas", "two on nas"])
            self.assertEqual(len(dlg.sidebarUrls()), 4)
            dispose(dlg)
        dlg = self.build()
        self.assertEqual(len(self.rows(dlg)), 4)

    @linux_only

    def test_stale_saved_shortcuts_are_ignored(self):
        self.clear_saved_dialog_state()
        st = self.qt_settings()
        st.beginGroup("FileDialog")
        st.setValue("shortcuts", [f"file:///junk/path{i}" for i in range(50)])
        st.endGroup()
        st.sync()
        dlg = self.build()
        self.assertEqual(len(self.rows(dlg)), 3)
        self.assertEqual(len(dlg.sidebarUrls()), 3)

    @linux_only

    def test_mount_paths_are_not_saved_when_dialog_is_destroyed(self):
        self.clear_saved_dialog_state()
        dlg = self.build()
        dlg.reject()
        self.app.processEvents()
        dispose(dlg)
        st = self.qt_settings()
        st.sync()
        self.assertTrue(os.path.exists(st.fileName()), "Qt did not write its settings file")
        saved = st.value("FileDialog/shortcuts")
        self.assertTrue(saved, "Qt saved no shortcuts, so this check would prove nothing")
        saved = [saved] if isinstance(saved, str) else list(saved)
        self.assertIn("file:", saved)
        for url in saved:
            self.assertNotIn(self.mount.path.rstrip("/"), url)
            self.assertNotIn("ai-data", url)

    @linux_only

    def test_duplicate_mounts_give_one_row(self):
        self.list_fake.return_value = [self.mount, self.mount]
        dlg = self.build()
        self.assertEqual(len(self.rows(dlg)), 3)
        self.assertEqual(self.rows(dlg).count(NAME), 1)


if __name__ == "__main__":
    unittest.main()
