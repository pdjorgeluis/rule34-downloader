"""Documentation tests (plain Python, no Qt). They read project files only."""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rule34_downloader import budget, config, help_content, version  # noqa: E402

README = ROOT / "README.md"
BUILD_MD = ROOT / "BUILD.md"
PS1 = ROOT / "scripts" / "build-windows.ps1"

README_SECTIONS = [
    "What it does", "Install on Linux", "Run from source", "First start",
    "Using the window", "Command line", "Where your data lives",
    "Limits and good behavior", "Troubleshooting", "Project layout",
    "Tests", "Content notice",
]
BUILD_SECTIONS = [
    "Requirements", "Set up the development environment", "Run the tests",
    "Regenerate the icons", "Build for Linux",
    "Install and uninstall on Linux", "Build for Windows (untested recipe)",
    "Release checklist", "Troubleshooting builds",
]


def text(path):
    return Path(path).read_text(encoding="utf-8")


def strip_fences(md):
    return re.sub(r"^```.*?^```[ \t]*$", "", md, flags=re.S | re.M)


def headings(md, level):
    prefix = "#" * level + " "
    return [ln[len(prefix):].strip() for ln in strip_fences(md).splitlines()
            if ln.startswith(prefix)]


def ps1_code_lines():
    return [ln for ln in text(PS1).splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


class DocsTests(unittest.TestCase):
    def test_readme_has_the_required_sections_in_order(self):
        md = text(README)
        self.assertEqual(headings(md, 1), ["Rule34 Downloader"])
        self.assertEqual(headings(md, 2), README_SECTIONS)

    def test_build_md_has_the_required_sections_in_order(self):
        md = text(BUILD_MD)
        self.assertEqual(headings(md, 1), ["Building Rule34 Downloader"])
        self.assertEqual(headings(md, 2), BUILD_SECTIONS)

    def test_readme_mentions_every_cli_flag_from_help(self):
        with tempfile.TemporaryDirectory() as home:
            env = {"PATH": os.environ.get("PATH", ""), "HOME": home}
            out = subprocess.run(
                [sys.executable, "-m", "rule34_downloader", "--help"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(out.returncode, 0)
        flags = set(re.findall(r"--[a-z-]+", out.stdout))
        self.assertIn("--dry-run", flags)
        readme = text(README)
        for flag in sorted(flags):
            self.assertIn(flag, readme, flag)

    def test_docs_reference_only_existing_files(self):
        checked = 0
        for doc in (README, BUILD_MD):
            for token in re.findall(r"(?:scripts|packaging)/[A-Za-z0-9_./-]+",
                                    text(doc)):
                token = token.rstrip(".,)")
                if token.startswith("build/"):
                    continue
                self.assertTrue((ROOT / token).exists(), f"{doc.name}: {token}")
                checked += 1
        self.assertGreater(checked, 3)

    def test_docs_quote_the_real_config_and_state_paths(self):
        env = {"APPDATA": "A", "LOCALAPPDATA": "L", "HOME": "H"}
        cfg_nt = config.config_path(env=env, platform="nt")
        st_nt = budget.state_path(env=env, platform="nt")
        self.assertEqual(cfg_nt.parts[-2:], ("Rule34Downloader", "config.json"))
        self.assertEqual(st_nt.parts[-2:], ("Rule34Downloader", "state.json"))
        env_posix = {"XDG_CONFIG_HOME": "/x/c", "XDG_STATE_HOME": "/x/s"}
        cfg_px = config.config_path(env=env_posix, platform="posix")
        st_px = budget.state_path(env=env_posix, platform="posix")
        self.assertEqual(cfg_px.parts[-2:], ("rule34-downloader", "config.json"))
        self.assertEqual(st_px.parts[-2:], ("rule34-downloader", "state.json"))
        readme = text(README)
        for human in (
            "~/.config/rule34-downloader/config.json",
            "~/.config/rule34-downloader/ui.json",
            "~/.local/state/rule34-downloader/state.json",
            "%APPDATA%\\Rule34Downloader\\config.json",
            "%APPDATA%\\Rule34Downloader\\ui.json",
            "%LOCALAPPDATA%\\Rule34Downloader\\state.json",
            "R34_CONFIG", "R34_STATE", "R34_USER_ID", "R34_API_KEY",
        ):
            self.assertIn(human, readme, human)

    def test_docs_version_mentions_match_version_py(self):
        found = 0
        for doc in (README, BUILD_MD):
            for ver in re.findall(r"rule34-downloader-(\d+\.\d+\.\d+)", text(doc)):
                self.assertEqual(ver, version.__version__, doc.name)
                found += 1
        for ver in re.findall(r"rule34-downloader-(\d+\.\d+\.\d+)", text(PS1)):
            self.assertEqual(ver, version.__version__)
        self.assertGreaterEqual(found, 3)

    def test_docs_code_fences_are_balanced(self):
        for doc in (README, BUILD_MD):
            fences = [ln for ln in text(doc).splitlines()
                      if ln.strip().startswith("```")]
            self.assertEqual(len(fences) % 2, 0, doc.name)
            self.assertTrue(fences, doc.name)

    def test_docs_contain_no_key_like_string(self):
        for doc in (README, BUILD_MD, PS1):
            body = text(doc)
            self.assertIsNone(re.search(r"[0-9a-fA-F]{32}", body), doc.name)
            self.assertNotIn("7011107", body, doc.name)
            self.assertIsNone(
                re.search(r"api_key\s*[=:]\s*\S{8,}", body), doc.name)
            self.assertNotIn("/home/", body, doc.name)

    def test_docs_state_the_rate_limit_numbers(self):
        readme = text(README)
        self.assertIn("60 API requests per hour", readme)
        self.assertIn("caps itself at 55", readme)
        self.assertIn("1000 posts", readme)
        self.assertIn("The default is 55", readme)

    def test_readme_explains_tag_syntax_with_underscore(self):
        readme = text(README)
        self.assertIn("hyuuga_hinata", readme)
        self.assertIn("A space between tags means AND", readme)
        self.assertIn("OR is not supported", readme)
        for word in ("rating:", "sort:", "score:"):
            self.assertIn(word, readme)

    def test_build_md_marks_windows_as_untested(self):
        md = text(BUILD_MD)
        self.assertIn("This Windows recipe has not been run yet: "
                      "it was written on Linux and is UNTESTED.", md)
        body = md.split("## Build for Windows (untested recipe)", 1)[1]
        body = body.split("\n## ", 1)[0]
        self.assertIn("UNTESTED", body)
        self.assertIn("py -3.14 -m venv .venv", body)
        self.assertIn(r"build\rule34-downloader-1.0.0-windows-x86_64.zip", body)
        self.assertIn("Compress-Archive", body)
        self.assertIn("UNTESTED", text(PS1))

    def test_windows_script_has_required_tokens(self):
        ps1 = text(PS1)
        for token in (
            "$ErrorActionPreference = 'Stop'", "$PSScriptRoot", "Resolve-Path",
            "Set-Location $Root", "R34_PYTHON", ".venv\\Scripts\\python.exe",
            "-m PyInstaller --version", "$LASTEXITCODE",
            "error: PyInstaller is not available in",
            "-m PyInstaller --noconfirm --clean --distpath build\\dist "
            "--workpath build\\work packaging\\rule34-downloader.spec",
            "QT_QPA_PLATFORM", "offscreen", "Start-Process", "--screenshot",
            "-Wait -PassThru", ".ExitCode", "Test-Path $Smoke",
            "error: frozen exe smoke test failed", "SMOKE OK (frozen exe)",
            "rule34_downloader.version import __version__", "AMD64", "x86_64",
            "Compress-Archive", "-Force", "BUILD OK", "UNTESTED",
            "[Console]::Error.WriteLine",
        ):
            self.assertIn(token, ps1, token)
        first = ps1_code_lines()[0]
        self.assertEqual(first, "$ErrorActionPreference = 'Stop'")
        self.assertNotIn("Write-Error", ps1)

    def test_windows_script_deletes_only_inside_build(self):
        lines = [ln for ln in text(PS1).splitlines() if "Remove-Item" in ln
                 and not ln.lstrip().startswith("#")]
        self.assertEqual(len(lines), 1, lines)
        self.assertIsNotNone(re.search(r"Remove-Item\s+\$Smoke\b", lines[0]))
        defs = [ln for ln in text(PS1).splitlines()
                if re.match(r"\s*\$Smoke\s*=", ln)]
        self.assertEqual(len(defs), 1)
        self.assertIn("build", defs[0])

    def test_windows_script_has_balanced_braces_and_quotes(self):
        code = "\n".join(ps1_code_lines())
        self.assertEqual(code.count("{"), code.count("}"))
        self.assertEqual(code.count("("), code.count(")"))
        self.assertEqual(code.count('"') % 2, 0)
        self.assertEqual(code.count("'") % 2, 0)

    def test_windows_script_final_line_is_build_ok(self):
        last = ps1_code_lines()[-1]
        self.assertTrue(last.startswith('Write-Host "BUILD OK'), last)
        self.assertIn("build\\rule34-downloader-$Version-windows-$Arch.zip", last)

    def test_help_wording_fix_is_present(self):
        sections = dict(help_content.SECTIONS)
        body = sections["Messages and what they mean"]
        self.assertIn(
            "- Stopped by an error: the app could not continue. "
            "The kind of error follows in brackets, then the message.", body)
        self.assertNotIn("The reason follows in brackets.", body)


if __name__ == "__main__":
    unittest.main()
