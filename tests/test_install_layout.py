"""Install-layout tests (plain Python, no Qt, no network).

The install scripts only ever run with PREFIX and HOME set to temp
directories. The build scripts are checked as text: PyInstaller is never run
here, and the Windows script cannot be run on this machine at all.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LINUX = ROOT / "packaging" / "linux"
ICONS = ROOT / "packaging" / "icons"
BUILD_SH = ROOT / "scripts" / "build-linux.sh"
PS1 = ROOT / "scripts" / "build-windows.ps1"
README = ROOT / "README.md"
BUILD_MD = ROOT / "BUILD.md"
NAME = "rule34-downloader"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
USAGE = "usage: install.sh <path to the built rule34-downloader directory>"


from platform_skips import linux_only  # noqa: E402

def section(text, title):
    """Return the body of the '## title' section."""
    m = re.search(r"^## %s\n(.*?)(?=^## |\Z)" % re.escape(title), text,
                  re.S | re.M)
    assert m, "missing section " + title
    return m.group(1)


def make_tree(top):
    """A fake tarball layout under top: stub binary plus the real installer."""
    app = Path(top) / NAME
    inst = app / "install"
    (inst / "icons").mkdir(parents=True)
    stub = app / NAME
    stub.write_text("#!/bin/sh\necho 'Rule34 Downloader 0.0.0'\n")
    stub.chmod(0o755)
    for f in ("install.sh", "uninstall.sh", NAME + ".desktop"):
        shutil.copy(LINUX / f, inst / f)
    for n in SIZES:
        shutil.copy(ICONS / ("%s-%d.png" % (NAME, n)), inst / "icons")
    return app


def run(cmd, prefix, home, **kw):
    env = {"PATH": os.environ["PATH"], "PREFIX": str(prefix),
           "HOME": str(home)}
    return subprocess.run(cmd, env=env, capture_output=True, text=True,
                          timeout=4, **kw)


class TarballLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.app = make_tree(t / "tree")
        self.prefix = t / "prefix"
        self.home = t / "home"
        self.prefix.mkdir()
        self.home.mkdir()

    def install(self, *args):
        return run(["bash", str(self.app / "install" / "install.sh"), *args],
                   self.prefix, self.home)

    @linux_only

    def test_install_script_finds_icons_next_to_itself_in_tarball_layout(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        for n in SIZES:
            icon = (self.prefix / "share/icons/hicolor" / ("%dx%d" % (n, n))
                    / "apps" / (NAME + ".png"))
            self.assertTrue(icon.is_file(), str(icon))

    @linux_only

    def test_install_without_args_uses_parent_when_it_holds_the_binary(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("installed to " + str(self.prefix), r.stdout)
        link = self.prefix / "bin" / NAME
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link),
                         str(self.prefix / "lib" / NAME / NAME))

    @linux_only

    def test_install_without_args_in_repo_layout_still_prints_usage(self):
        r = run(["bash", str(LINUX / "install.sh")], self.prefix, self.home)
        self.assertEqual(r.returncode, 2)
        self.assertIn(USAGE, r.stderr)
        self.assertEqual(list(self.prefix.iterdir()), [])

    @linux_only

    def test_tarball_layout_roundtrip_in_temp_prefix(self):
        for _ in range(2):  # the second run proves idempotence
            r = self.install()
            self.assertEqual(r.returncode, 0, r.stderr)
        desktop = (self.prefix / "share/applications" / (NAME + ".desktop"))
        self.assertIn("Exec=" + str(self.prefix / "bin" / NAME),
                      desktop.read_text())
        v = run([str(self.prefix / "bin" / NAME), "--version"],
                self.prefix, self.home)
        self.assertEqual(v.stdout.strip(), "Rule34 Downloader 0.0.0")
        u = run(["bash", str(self.app / "install" / "uninstall.sh")],
                self.prefix, self.home)
        self.assertEqual(u.returncode, 0, u.stderr)
        left = [p for p in self.prefix.rglob("*") if p.is_file()
                or p.is_symlink()]
        self.assertEqual(left, [])
        self.assertEqual(list(self.home.iterdir()), [])


class BuildScriptTextTests(unittest.TestCase):
    """Static text checks: the build is never run here."""

    def lines(self):
        return BUILD_SH.read_text().splitlines()

    def index_of(self, needle, start=0):
        for i, line in enumerate(self.lines()):
            if i >= start and needle in line:
                return i
        self.fail("not found: " + needle)

    def test_build_script_copies_installer_files_before_tar(self):
        tar = self.index_of("tar --owner")
        for needle in ("packaging/linux/install.sh",
                       "packaging/linux/uninstall.sh",
                       "packaging/linux/rule34-downloader.desktop",
                       "packaging/icons/rule34-downloader-",
                       "install/icons"):
            self.assertLess(self.index_of(needle), tar, needle)
        smoke = self.index_of("--smoke")
        self.assertLess(self.index_of("packaging/linux/install.sh"), smoke)
        self.assertNotIn(".ico", "\n".join(self.lines()[:tar]))

    def test_build_script_self_check_runs_before_the_final_line(self):
        ok = self.index_of('echo "INSTALL CHECK OK"')
        self.assertGreater(ok, self.index_of("tar --owner"))
        self.assertLess(ok, self.index_of('echo "BUILD OK'))
        text = BUILD_SH.read_text()
        self.assertIn('tar -xzf "$TARBALL" -C "$tmp"', text)
        self.assertIn("-u QT_QPA_PLATFORM", text)
        self.assertIn("grep -Fx --", text)
        self.assertNotIn("trap ", text)

    def test_build_script_last_line_is_still_build_ok(self):
        nonblank = [x for x in self.lines() if x.strip()]
        self.assertTrue(nonblank[-1].startswith('echo "BUILD OK'))


class WindowsScriptTextTests(unittest.TestCase):
    """Text only: this script has never run on Windows."""

    def lines(self):
        return [x.strip() for x in PS1.read_text().splitlines()]

    def test_windows_script_relaxes_error_preference_around_native_calls(self):
        lines = self.lines()
        relax = [i for i, x in enumerate(lines)
                 if x == "$ErrorActionPreference = 'Continue'"]
        self.assertGreaterEqual(len(relax), 3)
        for i in relax:
            self.assertIn("$prevEAP = $ErrorActionPreference", lines[:i])
            restore = [j for j in range(i + 1, len(lines))
                       if lines[j] == "$ErrorActionPreference = $prevEAP"]
            self.assertTrue(restore, "no restore after line %d" % (i + 1))
        self.assertIn("2>$null", PS1.read_text())

    def test_windows_script_still_starts_with_stop_preference(self):
        effective = [x for x in self.lines() if x and not x.startswith("#")]
        self.assertEqual(effective[0], "$ErrorActionPreference = 'Stop'")
        self.assertIn("windows-latest", PS1.read_text())


class DocsTests(unittest.TestCase):
    def test_readme_install_steps_use_the_tarball_installer(self):
        body = section(README.read_text(), "Install on Linux")
        self.assertIn("bash rule34-downloader/install/install.sh", body)
        self.assertIn("bash rule34-downloader/install/uninstall.sh", body)
        self.assertNotIn("packaging/linux/install.sh", body)
        self.assertIn("$PREFIX", body)
        self.assertIn("run the unpacked program directly", body)

    def test_build_md_describes_the_tarball_contents(self):
        body = section(BUILD_MD.read_text(), "Build for Linux")
        for needle in ("install/install.sh", "install/uninstall.sh",
                       "install/rule34-downloader.desktop", "install/icons/",
                       "Tarball contents", "SMOKE OK (frozen binary)",
                       "INSTALL CHECK OK",
                       "BUILD OK build/rule34-downloader-<version>-linux-"
                       "<arch>.tar.gz"):
            self.assertIn(needle, body, needle)
        self.assertLess(body.index("SMOKE OK"), body.index("INSTALL CHECK OK"))


if __name__ == "__main__":
    unittest.main()
