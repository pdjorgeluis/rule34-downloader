import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mock_api import API_KEY, USER_ID, MockServer  # noqa: E402
from rule34_downloader.cli import main  # noqa: E402


_STATE_DIR = tempfile.TemporaryDirectory()   # never the real state file
_STATE = str(Path(_STATE_DIR.name) / "state.json")


def run_cli(argv, env):
    env = {"R34_STATE": _STATE, **env}
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, env), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def server(self, total=25):
        srv = MockServer(total=total).start()
        self.addCleanup(srv.stop)
        self.env = {
            "R34_STATE": str(self.tmp / "state.json"),
            "R34_BACKOFF_BASE": "0.001",   # file 500s are retried now
            "R34_API_URL": srv.api_url,
            "R34_USER_ID": USER_ID,
            "R34_API_KEY": API_KEY,
        }
        return srv

    def test_cli_dry_run_prints_query_no_network(self):
        env = {"R34_API_URL": "http://127.0.0.1:9", "R34_CONFIG": str(self.tmp / "none.json")}
        out_dir = self.tmp / "out"
        rc, out, _ = run_cli(
            ["Hinata Hyuga", "naruto", "--exclude", "dog", "--min-score", "5",
             "--order", "oldest", "--dry-run", "-o", str(out_dir)], env)
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "query: hinata_hyuga naruto -dog score:>=5 sort:id:asc")
        self.assertEqual(lines[1], "types: pictures")
        self.assertEqual(lines[2], f"dest: {out_dir}")
        self.assertFalse(out_dir.exists())

    def test_cli_downloads_with_mock_server(self):
        srv = self.server()
        out_dir = self.tmp / "dl"
        rc, out, _ = run_cli(["cat", "-n", "3", "--delay", "0", "-o", str(out_dir)], self.env)
        self.assertEqual(rc, 0)
        done = [ln for ln in out.splitlines() if ln.startswith("Done:")]
        self.assertEqual(
            done,
            ["Done: downloaded=3 existing=0 skipped_type=2 failed=0 pages=1 requests=1 "
             "retries=0 reason=count_reached"],
        )
        self.assertEqual(len(list((out_dir / "cat" / "pictures").iterdir())), 3)
        self.assertEqual(len(srv.api_requests()), 1)

    def test_cli_missing_credentials_exit_4(self):
        target = self.tmp / "never"
        env = {"R34_CONFIG": str(self.tmp / "none.json"),
               "HOME": str(self.tmp), "R34_API_URL": "http://127.0.0.1:9"}
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in ("R34_USER_ID", "R34_API_KEY"):
                os.environ.pop(k, None)
            rc, _, err = run_cli(["cat", "-n", "1", "-o", str(target)], env)
        self.assertEqual(rc, 4)
        self.assertFalse(target.exists())
        self.assertIn("No credentials", err)

    def test_cli_bad_option_exit_2(self):
        env = {"R34_CONFIG": str(self.tmp / "none.json")}
        for argv in (["cat", "--workers", "101", "--dry-run"],
                     ["cat", "--bogus"],
                     ["cat", "-n", "0", "--dry-run"],
                     ["cat", "--types", "audio", "--dry-run"],
                     ["rating:safe", "--dry-run"],
                     []):
            rc, _, _ = run_cli(argv, env)
            self.assertEqual(rc, 2, argv)

    def test_cli_output_has_no_key(self):
        srv = self.server()
        srv.fail_ids = {25}
        rc, out, err = run_cli(["cat", "-n", "2", "--delay", "0", "-o", str(self.tmp / "o")],
                               self.env)
        self.assertEqual(rc, 0)
        self.assertNotIn(API_KEY, out + err)
        srv.mode = "http500"
        env = dict(self.env, R34_BACKOFF_BASE="0.001")
        rc, out, err = run_cli(["cat", "-n", "2", "--give-up-minutes", "0.0005",
                                "-o", str(self.tmp / "o2")], env)
        self.assertEqual(rc, 7)
        self.assertNotIn(API_KEY, out + err)

    def test_cli_unexpected_error_exit_6(self):
        self.server()
        with mock.patch("rule34_downloader.engine.Job.run",
                        side_effect=OSError(f"disk full api_key={API_KEY}")):
            rc, out, err = run_cli(["cat", "-n", "1", "-o", str(self.tmp / "o")], self.env)
        self.assertEqual(rc, 6)
        self.assertIn("error: disk full", err)
        self.assertNotIn(API_KEY, out + err)


if __name__ == "__main__":
    unittest.main()
