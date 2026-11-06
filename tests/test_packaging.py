"""Packaging tests (plain Python, no Qt). Never runs PyInstaller; never touches the real HOME."""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging"
LINUX = PKG / "linux"
INSTALL = LINUX / "install.sh"
UNINSTALL = LINUX / "uninstall.sh"
DESKTOP = LINUX / "rule34-downloader.desktop"
BUILD = ROOT / "scripts" / "build-linux.sh"
SPEC = PKG / "rule34-downloader.spec"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def listing(path):
    return sorted(str(p.relative_to(path)) for p in Path(path).rglob("*"))


def run_sh(script, args=(), prefix=None, home=None):
    env = {"PATH": os.environ["PATH"], "HOME": home or "/nonexistent-home"}
    if prefix is not None:
        env["PREFIX"] = prefix
    return subprocess.run(["bash", str(script), *args], env=env, cwd=ROOT,
                          capture_output=True, text=True, timeout=5)


def make_source(base, name="rule34-downloader"):
    src = Path(base) / "built"
    src.mkdir()
    exe = src / name
    exe.write_text("#!/bin/sh\necho stub\n")
    exe.chmod(0o755)
    (src / "_internal").mkdir()
    (src / "_internal" / "data.txt").write_text("x")
    return src


class PackagingTests(unittest.TestCase):
    def test_version_is_semver_string(self):
        from rule34_downloader.version import __version__
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")
        self.assertEqual(__version__, "1.0.0")

    def test_requirements_build_pins_pyinstaller_exactly(self):
        lines = (ROOT / "requirements-build.txt").read_text().splitlines()
        self.assertEqual(lines, ["-r requirements-gui.txt", "PyInstaller==6.22.3"])

    def test_entry_script_calls_gui_main(self):
        lines = [l for l in (PKG / "entry.py").read_text().splitlines() if l.strip()]
        self.assertEqual(lines, [
            "import sys",
            "from rule34_downloader.gui.app import main",
            'if __name__ == "__main__":',
            "    sys.exit(main())",
        ])

    def test_spec_file_compiles_and_bundles_the_logo(self):
        text = SPEC.read_text()
        compile(text, str(SPEC), "exec")
        for needle in ('name="rule34-downloader"', "console=False", "strip=False",
                       "upx=False", "exclude_binaries=True", "os.path.dirname(SPECPATH)",
                       "pathex=[ROOT]", "COLLECT(", '"rule34_downloader/gui/assets"',
                       "logo.svg", "entry.py"):
            self.assertIn(needle, text)
        self.assertEqual(text.count('name="rule34-downloader"'), 2)  # EXE and COLLECT
        self.assertNotIn("/home/", text)

    def test_spec_excludes_unneeded_qt_modules(self):
        text = SPEC.read_text()
        for mod in ("PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
                    "PySide6.QtWebEngineQuick", "PySide6.QtPdf", "PySide6.Qt3DCore",
                    "PySide6.QtMultimedia", "PySide6.QtQuick", "PySide6.QtQml",
                    "PySide6.QtCharts", "PySide6.QtDataVisualization", "tkinter"):
            self.assertIn(f'"{mod}"', text)

    def test_spec_uses_ico_only_on_windows(self):
        text = SPEC.read_text()
        self.assertRegex(text, r'icon=.*\.ico.*if sys\.platform == "win32" else None')

    def test_desktop_file_has_required_keys(self):
        text = DESKTOP.read_text()
        self.assertEqual(text.splitlines()[0], "[Desktop Entry]")
        for line in ("Type=Application", "Name=Rule34 Downloader",
                     "Comment=Download images from rule34.xxx by tag", "Exec=@EXEC@",
                     "Icon=rule34-downloader", "Terminal=false",
                     "Categories=Network;FileTransfer;", "StartupWMClass=rule34-downloader"):
            self.assertIn(line, text.splitlines())

    def test_desktop_file_exec_and_icon_names_match_scripts(self):
        self.assertEqual(DESKTOP.name, "rule34-downloader.desktop")
        for script in (INSTALL, UNINSTALL):
            self.assertIn('NAME="rule34-downloader"', script.read_text())
        self.assertIn("${tpl//@EXEC@/$exec_path}", INSTALL.read_text())
        self.assertNotIn("sed", INSTALL.read_text())

    def test_shell_scripts_pass_bash_n(self):
        for script in (BUILD, INSTALL, UNINSTALL):
            r = subprocess.run(["bash", "-n", str(script)], capture_output=True,
                               text=True, timeout=5)
            self.assertEqual(r.returncode, 0, f"{script}: {r.stderr}")

    def test_install_script_refuses_to_run_without_a_source_dir(self):
        with tempfile.TemporaryDirectory() as prefix, tempfile.TemporaryDirectory() as home:
            usage = "usage: install.sh <path to the built rule34-downloader directory>"
            r = run_sh(INSTALL, prefix=prefix, home=home)
            self.assertEqual(r.returncode, 2)
            self.assertIn(usage, r.stderr)
            r = run_sh(INSTALL, [home], prefix=prefix, home=home)  # dir without the binary
            self.assertEqual(r.returncode, 2)
            self.assertIn(usage, r.stderr)
            self.assertEqual(listing(prefix), [])
            self.assertEqual(listing(home), [])
            src = make_source(home)
            for bad in ("", "relative", "/"):
                r = run_sh(INSTALL, [str(src)], prefix=bad, home=home)
                self.assertEqual(r.returncode, 2, bad)
                self.assertIn("error: invalid PREFIX", r.stderr)

    def test_install_and_uninstall_roundtrip_in_a_temp_prefix(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as prefix, \
                tempfile.TemporaryDirectory() as home:
            src = make_source(base)
            r = run_sh(INSTALL, [str(src)], prefix=prefix, home=home)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), f"installed to {prefix}")
            p = Path(prefix)
            link = p / "bin" / "rule34-downloader"
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), str(p / "lib/rule34-downloader/rule34-downloader"))
            self.assertTrue(os.access(link, os.X_OK))
            self.assertTrue((p / "lib/rule34-downloader/_internal/data.txt").is_file())
            desktop = (p / "share/applications/rule34-downloader.desktop").read_text()
            self.assertIn(f"Exec={prefix}/bin/rule34-downloader\n", desktop)
            self.assertNotIn("@EXEC@", desktop)
            for n in SIZES:
                self.assertTrue((p / f"share/icons/hicolor/{n}x{n}/apps/rule34-downloader.png").is_file())
            r = run_sh(UNINSTALL, prefix=prefix, home=home)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), f"removed from {prefix}")
            self.assertFalse(link.is_symlink() or link.exists())
            self.assertFalse((p / "lib/rule34-downloader").exists())
            self.assertFalse((p / "share/applications/rule34-downloader.desktop").exists())
            for n in SIZES:
                self.assertFalse((p / f"share/icons/hicolor/{n}x{n}/apps/rule34-downloader.png").exists())
            self.assertEqual(listing(home), [])

    def test_install_with_space_and_percent_in_prefix_quotes_exec(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as home:
            src = make_source(base)
            prefix = Path(base) / "my 50% prefix"
            r = run_sh(INSTALL, [str(src)], prefix=str(prefix), home=home)
            self.assertEqual(r.returncode, 0, r.stderr)
            desktop = (prefix / "share/applications/rule34-downloader.desktop").read_text()
            expected = str(prefix / "bin/rule34-downloader").replace("%", "%%")
            self.assertIn(f'Exec="{expected}"\n', desktop)

    def test_install_never_writes_outside_the_prefix(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as prefix, \
                tempfile.TemporaryDirectory() as home:
            (Path(home) / ".config").mkdir()
            (Path(home) / ".config" / "keep.txt").write_text("k")
            before = listing(home)
            src = make_source(base)
            self.assertEqual(run_sh(INSTALL, [str(src)], prefix=prefix, home=home).returncode, 0)
            self.assertEqual(run_sh(INSTALL, [str(src)], prefix=prefix, home=home).returncode, 0)
            self.assertEqual(run_sh(UNINSTALL, prefix=prefix, home=home).returncode, 0)
            self.assertEqual(listing(home), before)
            self.assertEqual((Path(home) / ".config" / "keep.txt").read_text(), "k")
            self.assertEqual(listing(base), ["built", "built/_internal",
                                             "built/_internal/data.txt", "built/rule34-downloader"])

    def test_uninstall_when_nothing_installed_is_a_noop(self):
        with tempfile.TemporaryDirectory() as prefix, tempfile.TemporaryDirectory() as home:
            r = run_sh(UNINSTALL, prefix=prefix, home=home)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), f"removed from {prefix}")
            self.assertEqual(listing(prefix), [])
            self.assertEqual(listing(home), [])

    def test_gitignore_ignores_build_and_dist(self):
        lines = (ROOT / ".gitignore").read_text().splitlines()
        self.assertIn("build/", lines)
        self.assertIn("dist/", lines)

    def test_build_script_fails_clearly_without_pyinstaller(self):
        env = {"PATH": os.environ["PATH"], "HOME": "/nonexistent-home",
               "R34_PYTHON": "/nonexistent/python"}
        r = subprocess.run(["bash", str(BUILD)], env=env, cwd="/", capture_output=True,
                           text=True, timeout=5)
        self.assertEqual(r.returncode, 1)
        self.assertIn("error: PyInstaller is not available in /nonexistent/python", r.stderr)

    def test_no_secrets_in_packaging_files(self):
        files = [p for p in PKG.rglob("*") if p.is_file() and p.suffix in
                 {".py", ".sh", ".spec", ".desktop"}]
        files += [BUILD, ROOT / "requirements-build.txt"]
        self.assertGreaterEqual(len(files), 8)
        pattern = re.compile(r"api_key|r34dl\.json|\.config/rule34|[0-9a-f]{32,}", re.I)
        for f in files:
            self.assertIsNone(pattern.search(f.read_text()), str(f))


if __name__ == "__main__":
    unittest.main()
