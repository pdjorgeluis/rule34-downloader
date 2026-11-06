"""Packaging tests that need Qt (icons, --version). Skipped without PySide6."""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "packaging" / "icons"
MAKE_ICONS = ROOT / "packaging" / "make_icons.py"
INSTALL = ROOT / "packaging" / "linux" / "install.sh"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtGui import QImage
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


def tree(path):
    return sorted(str(p.relative_to(path)) for p in Path(path).rglob("*"))


@unittest.skipUnless(HAVE_QT, "PySide6 not available")
class PackagingGuiTests(unittest.TestCase):
    def test_version_flag_prints_name_and_version(self):
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "HOME": home}
            r = subprocess.run([sys.executable, "-m", "rule34_downloader.gui", "--version"],
                               cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "Rule34 Downloader 1.0.0")

    def test_version_flag_creates_no_window_and_no_config(self):
        with tempfile.TemporaryDirectory() as home:
            env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
            env["HOME"] = home
            r = subprocess.run([sys.executable, "-m", "rule34_downloader.gui", "--version"],
                               cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "Rule34 Downloader 1.0.0")
            self.assertEqual(tree(home), [])

    def test_make_icons_writes_all_sizes_and_ico(self):
        with tempfile.TemporaryDirectory() as out:
            r = subprocess.run([sys.executable, str(MAKE_ICONS), "--out", out],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            expected = sorted([f"rule34-downloader-{n}.png" for n in SIZES]
                              + ["rule34-downloader.ico"])
            self.assertEqual(tree(out), expected)
            self.assertEqual(len(r.stdout.strip().splitlines()), 9)
            for n in SIZES:
                img = QImage(str(Path(out) / f"rule34-downloader-{n}.png"))
                self.assertEqual((img.width(), img.height()), (n, n))
            self.assertGreater((Path(out) / "rule34-downloader.ico").stat().st_size, 0)

    def test_icons_are_nonblank_and_have_the_right_size(self):
        for n in SIZES:
            img = QImage(str(ICONS / f"rule34-downloader-{n}.png"))
            self.assertFalse(img.isNull(), n)
            self.assertEqual((img.width(), img.height()), (n, n))
            self.assertGreater(img.pixelColor(n // 2, n // 2).alpha(), 0, n)
        self.assertTrue((ICONS / "rule34-downloader.ico").is_file())

    def test_icon_sizes_match_the_desktop_install_script(self):
        text = INSTALL.read_text()
        m = re.search(r"^SIZES=\(([\d\s]+)\)", text, re.M)
        self.assertIsNotNone(m, "install.sh must declare SIZES=(...)")
        in_script = sorted(int(x) for x in m.group(1).split())
        on_disk = sorted(int(re.search(r"-(\d+)\.png$", p.name).group(1))
                         for p in ICONS.glob("rule34-downloader-*.png"))
        self.assertEqual(in_script, on_disk)


if __name__ == "__main__":
    unittest.main()
