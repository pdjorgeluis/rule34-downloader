"""Pure panel text helpers (no Qt)."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from rule34_downloader.engine import JobConfig  # noqa: E402
from rule34_downloader.run_model import (  # noqa: E402
    RunStats, banner_text, clean_log_line, counters_text, format_bytes,
    progress_text, query_text, result_folder,
)


class PanelTextTests(unittest.TestCase):
    def test_format_bytes_units(self):
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(1023), "1023 B")
        self.assertEqual(format_bytes(1024), "1.0 KB")
        self.assertEqual(format_bytes(5 * 1024 ** 2), "5.0 MB")
        self.assertEqual(format_bytes(3 * 1024 ** 3), "3.0 GB")
        self.assertEqual(format_bytes(-5), "0 B")
        self.assertEqual(format_bytes("x"), "0 B")
        self.assertEqual(format_bytes(None), "0 B")

    def test_counters_text_exact(self):
        s = RunStats()
        s.downloaded, s.existing, s.skipped, s.failed, s.retries = 12, 3, 4, 1, 2
        s.bytes_done = 1536000
        self.assertEqual(
            counters_text(s),
            "Downloaded 12  |  Already there 3  |  Skipped 4  |  Failed 1  |  "
            "Retries 2  |  Size 1.5 MB")

    def test_banner_waiting_text(self):
        s = RunStats()
        s.state, s.wait_seconds, s.wait_reason = "waiting", 12.7, "retry-after"
        self.assertEqual(banner_text(s), "Waiting 12 s (retry-after).")
        s.wait_seconds = "oops"
        self.assertEqual(banner_text(s), "Waiting 0 s (retry-after).")

    def test_banner_unavailable_text(self):
        s = RunStats()
        s.state = "unavailable"
        s.unavailable_message = "server unavailable after 1800s: HTTP 503"
        self.assertEqual(
            banner_text(s),
            "Server unavailable: server unavailable after 1800s: HTTP 503. "
            "Retrying automatically, or press Retry now.")

    def test_banner_paused_and_stopping_text(self):
        s = RunStats()
        s.state = "paused"
        self.assertEqual(banner_text(s), "Paused. Press Resume to continue.")
        s.state = "stopping"
        self.assertEqual(banner_text(s), "Stopping...")

    def test_banner_slowed_down_suffix_and_empty_when_calm(self):
        s = RunStats()
        s.state = "running"
        self.assertEqual(banner_text(s), "")
        s.adaptive_level = 2
        self.assertEqual(banner_text(s), "Slowed down: level 2.")
        s.state, s.wait_seconds, s.wait_reason = "waiting", 12.7, "retry-after"
        self.assertEqual(banner_text(s), "Waiting 12 s (retry-after). Slowed down: level 2.")
        s.state = "paused"
        self.assertEqual(banner_text(s),
                         "Paused. Press Resume to continue. Slowed down: level 2.")
        s.state = "unavailable"
        self.assertNotIn("Slowed", banner_text(s))

    def test_banner_empty_when_finished_or_idle(self):
        s = RunStats()
        s.adaptive_level = 3
        for state in ("idle", "finished"):
            s.state = state
            self.assertEqual(banner_text(s), "")

    def test_clean_log_line_strips_trailing_colon_and_spaces(self):
        self.assertEqual(clean_log_line("Retry page 0 (attempt 1): HTTP 503: "),
                         "Retry page 0 (attempt 1): HTTP 503")
        self.assertEqual(clean_log_line("done:::  \n"), "done")

    def test_clean_log_line_keeps_inner_colons(self):
        self.assertEqual(clean_log_line("Error (auth): bad key"), "Error (auth): bad key")

    def test_progress_text_percent_and_working(self):
        s = RunStats()
        s.downloaded = 1
        self.assertEqual(progress_text(s, 4), "25%")
        self.assertEqual(progress_text(s, None), "Working...")

    def _cfg(self, dest, **kw):
        return JobConfig(include=["cat", "dog"], dest=Path(dest), **kw)

    def test_result_folder_prefers_search_subfolder_when_it_exists(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "cat+dog").mkdir()
            self.assertEqual(result_folder(self._cfg(d)), Path(d) / "cat+dog")

    def test_result_folder_falls_back_to_base(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(result_folder(self._cfg(d)), Path(d))
            (Path(d) / "cat+dog").mkdir()
            self.assertEqual(result_folder(self._cfg(d, by_search=False)), Path(d))

    def test_query_text_from_job_config(self):
        c = JobConfig(include=["cat", "dog"], dest=Path("/tmp/x"), exclude=["fish"],
                      min_score=5, order="oldest")
        self.assertEqual(query_text(c), "cat dog -fish score:>=5 sort:id:asc")

    def test_panel_text_functions_import_no_qt(self):
        code = (f"import sys; sys.path.insert(0, {str(REPO)!r}); "
                "import rule34_downloader.run_model as m; "
                "print(any(k.split('.')[0] == 'PySide6' for k in sys.modules))")
        out = subprocess.run([sys.executable, "-I", "-c", code],
                             capture_output=True, text=True, timeout=5)
        self.assertEqual(out.stdout.strip(), "False", out.stderr)


if __name__ == "__main__":
    unittest.main()
