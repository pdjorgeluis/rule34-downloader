"""Windows CI workflow tests (plain Python). Text checks only; never runs the workflow."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "build-windows.yml"


class WindowsWorkflowTest(unittest.TestCase):
    try:
        import yaml
    except ImportError:
        yaml = None

    def test_runs_the_windows_build_script_on_a_windows_runner(self):
        text = WORKFLOW.read_text()
        self.assertIn("runs-on: windows-latest", text)
        self.assertIn("scripts\\build-windows.ps1", text)

    def test_regenerates_the_icons_before_the_build(self):
        # The icon files are binary and are not stored in the CI repo copy.
        text = WORKFLOW.read_text()
        self.assertIn("packaging\\make_icons.py", text)
        self.assertLess(text.index("make_icons.py"), text.index("build-windows.ps1"))

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_is_valid_yaml_and_uploads_the_zip(self):
        yaml = self.yaml
        doc = yaml.safe_load(WORKFLOW.read_text())
        steps = doc["jobs"]["build"]["steps"]
        uploads = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact")]
        self.assertTrue(any("windows" in s["with"]["path"] and s["with"]["path"].endswith(".zip")
                            for s in uploads))
        self.assertNotIn("secrets.", WORKFLOW.read_text())


if __name__ == "__main__":
    unittest.main()
