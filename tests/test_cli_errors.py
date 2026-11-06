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
from rule34_downloader import budget as budget_mod  # noqa: E402
from rule34_downloader.cli import main  # noqa: E402

_REAL_STATE_PATH = budget_mod.state_path
_GUARD_CALLS = []   # (R34_STATE was set?, resolved path) for every state_path() call


def _guarded_state_path(env=None, platform=None):
    path = _REAL_STATE_PATH(env, platform)
    _GUARD_CALLS.append((bool(os.environ.get("R34_STATE")), path))
    if not os.environ.get("R34_STATE"):
        raise AssertionError("state_path() consulted without R34_STATE: real state file at risk")
    return path


_STATE_GUARD = mock.patch.object(budget_mod, "state_path", _guarded_state_path)


def setUpModule():
    _STATE_GUARD.start()


def tearDownModule():
    _STATE_GUARD.stop()


def run_cli(argv, env):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, env), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


class CliErrorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.srv = MockServer(total=25).start()
        self.addCleanup(self.srv.stop)
        self.env = {
            "R34_API_URL": self.srv.api_url, "R34_USER_ID": USER_ID, "R34_API_KEY": API_KEY,
            "R34_STATE": str(self.tmp / "state.json"), "R34_BACKOFF_BASE": "0.001",
        }

    def run_main(self, *args, env=None):
        argv = list(args) + ["--types", "pictures,gifs,videos", "--delay", "0",
                             "-o", str(self.tmp / "o")]
        return run_cli(argv, env or self.env)

    @staticmethod
    def done(out):
        return [ln for ln in out.splitlines() if ln.startswith("Done:")]

    def test_cli_exit_7_when_server_unavailable(self):
        self.srv.api_script = [(503, b"", {})] * 200
        rc, out, err = self.run_main("--give-up-minutes", "0.0005", "cat", "-n", "2")
        self.assertEqual(rc, 7)
        self.assertEqual(err.strip().splitlines()[0],
                         "error: server unavailable; re-run the same command later to resume")
        (d,) = self.done(out)
        self.assertTrue(d.endswith("reason=server_unavailable"))
        self.assertIn("server unavailable:", out)

    def test_cli_exit_5_permanent_error_prints_kind(self):
        self.srv.api_script = [(200, b'"Missing authentication. Go to api.rule34.xxx"', {})]
        rc, out, err = self.run_main("cat", "-n", "2")
        self.assertEqual(rc, 5)
        self.assertTrue(err.startswith("error: auth: "), err)
        self.assertIn("Missing authentication", err)
        self.assertIn("permanent error (auth):", out)
        self.assertTrue(self.done(out)[0].endswith("reason=permanent_error"))

    def test_cli_retries_then_succeeds_prints_waiting_and_retry_lines(self):
        self.srv.api_script = [(503, b"", {}), (503, b"", {})]
        rc, out, err = self.run_main("cat", "-n", "2")
        self.assertEqual(rc, 0, err)
        lines = out.splitlines()
        self.assertEqual(sum(ln.startswith("waiting: backoff 0s (attempt") for ln in lines), 2)
        self.assertTrue(any(ln.startswith("retry: page 0 attempt 1: HTTP 503") for ln in lines))
        self.assertIn("slowing down: level 1", lines)
        self.assertEqual(self.done(out), [
            "Done: downloaded=2 existing=0 skipped_type=0 failed=0 pages=1 requests=3 "
            "retries=2 reason=count_reached"])

    def test_cli_api_per_hour_and_give_up_flags_reach_the_job(self):
        seen = {}

        class Spy:
            def __init__(self, *a, **kw):
                seen.update(kw)
                raise OSError("stop here")
        with mock.patch("rule34_downloader.engine.Job", Spy):
            rc, _, err = self.run_main("--api-per-hour", "10", "--give-up-minutes", "5",
                                       "cat", "-n", "1")
        self.assertEqual(rc, 6)
        self.assertEqual(seen["budget"].limit, 10)
        self.assertEqual(seen["budget"].path, self.tmp / "state.json")
        self.assertEqual(seen["giveup"].window, 300.0)
        self.assertEqual(seen["backoff"].base, 0.001)
        self.assertIs(seen["retries"], True)

    def test_cli_rejects_bad_api_per_hour_and_give_up_exit_2(self):
        no_creds = {"R34_CONFIG": str(self.tmp / "none.json"), "HOME": str(self.tmp)}
        for extra in (["--api-per-hour", "0"], ["--api-per-hour", "-3"],
                      ["--api-per-hour", "x"], ["--give-up-minutes", "0"],
                      ["--give-up-minutes", "-1"], ["--give-up-minutes", "nan"]):
            for dry in ([], ["--dry-run"]):
                rc, _, _ = run_cli(["cat"] + extra + dry, no_creds)
                self.assertEqual(rc, 2, (extra, dry))
        for bad in ("abc", "0", "-1"):
            rc, _, err = self.run_main("cat", "-n", "1", env=dict(self.env, R34_BACKOFF_BASE=bad))
            self.assertEqual(rc, 2, bad)
            self.assertIn("R34_BACKOFF_BASE", err)
        rc, _, _ = run_cli(["cat", "--dry-run"], {"R34_BACKOFF_BASE": "abc"})
        self.assertEqual(rc, 0)       # dry-run never reads it

    def test_cli_error_output_has_no_key(self):
        self.srv.fail_ids = {25}
        self.srv.api_script = [(500, ("boom api_key=%s" % API_KEY).encode(), {})]
        rc, out, err = self.run_main("cat", "-n", "2")
        self.assertEqual(rc, 0)
        self.assertNotIn(API_KEY, out + err)
        self.srv.api_script = [(200, ('"Missing authentication %s"' % API_KEY).encode(), {})]
        rc, out, err = self.run_main("cat", "-n", "2")
        self.assertEqual(rc, 5)
        self.assertNotIn(API_KEY, out + err)

    def test_cli_dry_run_unchanged_by_new_flags(self):
        env = {"R34_API_URL": "http://127.0.0.1:9", "R34_CONFIG": str(self.tmp / "none.json")}
        rc, out, _ = run_cli(
            ["Hinata Hyuga", "naruto", "--exclude", "dog", "--min-score", "5",
             "--order", "oldest", "--api-per-hour", "10", "--give-up-minutes", "5",
             "--dry-run", "-o", str(self.tmp / "out")], env)
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines(), [
            "query: hinata_hyuga naruto -dog score:>=5 sort:id:asc",
            "types: pictures", f"dest: {self.tmp / 'out'}"])
        self.assertEqual(self.srv.api_requests(), [])

    def test_cli_tests_never_touch_the_real_state_file(self):
        self.assertIs(budget_mod.state_path, _guarded_state_path)
        _GUARD_CALLS.clear()
        rc, _, _ = self.run_main("cat", "-n", "1")
        self.assertEqual(rc, 0)
        self.assertTrue((self.tmp / "state.json").exists())
        # every lookup this module ever made had R34_STATE set and landed in a temp dir
        self.assertTrue(_GUARD_CALLS)
        real = Path.home() / ".local" / "state" / "rule34-downloader" / "state.json"
        for had_env, path in _GUARD_CALLS:
            self.assertTrue(had_env)
            self.assertNotEqual(path, real)


if __name__ == "__main__":
    unittest.main()
