"""Network-share helper tests. Every test uses a FAKE gio script in a temp dir."""
import os
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

from rule34_downloader import network_mounts as nm
from rule34_downloader.network_mounts import (
    build_uri, list_mounts, mount_share, parse_mount_list,
    validate_server, validate_share,
)

ROOT = Path(__file__).resolve().parent.parent
SECRET = "s3cret-pw-42"

LIST_TEXT = (
    "Drive(0): QEMU QEMU HARDDISK\n"
    "  Type: GProxyDrive (GProxyVolumeMonitorUDisks2)\n"
    "Mount(0): ai-data on 10.1.2.13 -> smb://10.1.2.13/ai-data/\n"
    "  Type: GDaemonMount\n"
    "Mount(1): data on host2 -> smb://host2/data/\n"
)

_HEAD = """#!/bin/bash
T="{tmp}"
if [ "$1" = "mount" ] && [ "$2" = "-l" ]; then
  echo "Drive(0): QEMU QEMU HARDDISK"
  echo "  Type: GProxyDrive (GProxyVolumeMonitorUDisks2)"
  echo "Mount(0): ai-data on 10.1.2.13 -> smb://10.1.2.13/ai-data/"
  echo "  Type: GDaemonMount"
  echo "Mount(1): data on host2 -> smb://host2/data/"
  [ -f "$T/mounted" ] && echo "Mount(2): my docs on nas -> smb://nas/my%20docs/"
  exit 0
fi
if [ "$1" = "info" ]; then
  case "$4" in
    *ai-data*) echo "display name: ai-data on 10.1.2.13"; echo "local path: $T/ai-data" ;;
    *host2*)   echo "display name: data on host2"; echo "local path: $T/data" ;;
    *nas*)     echo "display name: my docs on nas"; echo "local path: $T/docs" ;;
    *)         echo "display name: other" ;;
  esac
  exit 0
fi
"""

_AUTH = """printf 'Authentication Required\\r\\n'
printf 'Enter user and password for share \\xe2\\x80\\x9cs\\xe2\\x80\\x9d on \\xe2\\x80\\x9ch\\xe2\\x80\\x9d:\\r\\n'
printf 'User [x]: '
read -r u
printf 'Domain [WORKGROUP]: '
read -r d
printf 'Password: '
read -rs p
"""

_MODES = {
    "ok": _AUTH + """if [ "$p" = "%s" ]; then touch "$T/mounted"; exit 0; fi
echo
printf 'Authentication Required\\r\\n'
printf 'User [x]: '
sleep 2
exit 1
""" % SECRET,
    "unreachable": """echo "gio: $2: Failed to mount Windows share: Connection refused"
exit 1
""",
    "echo": _AUTH + """echo
echo "gio: bad $p here: Connection refused"
exit 1
""",
    "record": """printf '%s\\n' "$@" > "$T/argv"
env > "$T/env"
""" + _AUTH + """if [ "$p" = "%s" ]; then touch "$T/mounted"; exit 0; fi
exit 1
""" % SECRET,
    "hang": """echo $$ > "$T/pid"
sleep 30
""",
}


from platform_skips import linux_only  # noqa: E402

def make_gio(tmp, mode="ok"):
    tmp = Path(tmp)
    (tmp / "ai-data").mkdir(exist_ok=True)
    (tmp / "docs").mkdir(exist_ok=True)
    body = _HEAD.replace("{tmp}", str(tmp)) + 'if [ "$1" = "mount" ]; then\n' \
        + _MODES.get(mode, "") + "fi\n"
    path = tmp / "gio"
    path.write_text(body)
    path.chmod(0o755)
    return str(path)


class NetworkMountsTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = self._td.name
        warnings.simplefilter("ignore", DeprecationWarning)

    # --- parsing and listing -------------------------------------------
    def test_parse_mount_list_reads_names_and_uris(self):
        self.assertEqual(parse_mount_list(LIST_TEXT), [
            ("ai-data on 10.1.2.13", "smb://10.1.2.13/ai-data/"),
            ("data on host2", "smb://host2/data/"),
        ])

    def test_parse_mount_list_ignores_drives_and_volume_lines(self):
        text = ("Drive(0): QEMU QEMU HARDDISK\nVolume(0): x -> y\n"
                "  Type: GDaemonMount\nMount(x): bad -> smb://a/b/\n"
                "Mount(3): ok one -> smb://h/s/  \n")
        self.assertEqual(parse_mount_list(text), [("ok one", "smb://h/s/")])
        self.assertEqual(parse_mount_list(""), [])

    @linux_only

    def test_list_mounts_returns_only_mounts_with_existing_local_path(self):
        gio = make_gio(self.tmp)
        got = list_mounts(gio)
        self.assertEqual([(m.name, m.uri, m.path) for m in got], [
            ("ai-data on 10.1.2.13", "smb://10.1.2.13/ai-data/",
             os.path.join(self.tmp, "ai-data")),
        ])

    def test_list_mounts_without_gio_returns_empty(self):
        self.assertEqual(list_mounts("/nonexistent/gio"), [])
        with mock.patch.object(nm, "find_gio", return_value=None):
            self.assertEqual(list_mounts(), [])

    def test_list_mounts_survives_gio_timeout(self):
        gio = make_gio(self.tmp)
        err = subprocess.TimeoutExpired(gio, 1)
        with mock.patch.object(nm.subprocess, "run", side_effect=err):
            self.assertEqual(list_mounts(gio, timeout=0.1), [])
        with mock.patch.object(nm.subprocess, "run", side_effect=OSError("x")):
            self.assertEqual(list_mounts(gio), [])

    # --- validators and uri --------------------------------------------
    def test_validate_server_accepts_hostnames_and_ips(self):
        for ok in ("10.1.2.13", "nas", "my-nas.local", " host2 ", "A1"):
            self.assertIsNone(validate_server(ok), ok)
        self.assertEqual(validate_server("  "), "Enter the server name or address.")

    def test_validate_server_rejects_uri_and_shell_characters(self):
        for bad in ("smb://h", "h/x", "h\\x", "h:445", "u@h", "h?x", "h#x",
                    "h%20", "h h", "h'x", 'h"x', "a..b", "-h", "h-", "::1",
                    "h;ls", "a" * 254):
            self.assertEqual(validate_server(bad),
                             "Enter the server name or IPv4 address.", bad)

    def test_validate_share_allows_spaces_and_rejects_separators(self):
        for ok in ("data", "my docs", "C$", "a-b_c.d+e", " x "):
            self.assertIsNone(validate_share(ok), ok)
        self.assertEqual(validate_share(""), "Enter the share name.")
        msg = "The share name has characters that are not allowed."
        for bad in ("a/b", "a\\b", "a?b", "a#b", "a%b", "a:b", "a@b",
                    "a\nb", "caf\u00e9", "..", ".", "a" * 81):
            self.assertEqual(validate_share(bad), msg, repr(bad))

    def test_build_uri_quotes_share_and_carries_no_credentials(self):
        self.assertEqual(build_uri(" nas ", " my docs "), "smb://nas/my%20docs/")
        self.assertEqual(build_uri("10.1.2.13", "C$"), "smb://10.1.2.13/C%24/")
        self.assertNotIn("@", build_uri("h", "s"))

    # --- mounting through the fake gio ---------------------------------
    @linux_only
    def test_mount_share_succeeds_with_fake_gio_prompts(self):
        gio = make_gio(self.tmp, "ok")
        res = mount_share("nas", "my docs", "me", SECRET, "WG", gio=gio, timeout=10)
        self.assertEqual((res.ok, res.kind, res.message), (True, "ok", "Connected."))

    @linux_only

    def test_mount_share_wrong_password_reports_bad_credentials(self):
        gio = make_gio(self.tmp, "ok")
        res = mount_share("nas", "my docs", "me", "wrong-pw", gio=gio, timeout=10)
        self.assertFalse(res.ok)
        self.assertEqual(res.kind, "bad_credentials")
        self.assertEqual(res.message, "The user name or password was not accepted.")

    @linux_only

    def test_mount_share_unreachable_reports_gio_error_without_password(self):
        gio = make_gio(self.tmp, "unreachable")
        res = mount_share("127.0.0.1", "nonexistent", "me", SECRET, gio=gio, timeout=10)
        self.assertFalse(res.ok)
        self.assertEqual(res.kind, "unreachable")
        self.assertIn("Connection refused", res.message)
        self.assertNotIn(SECRET, res.message)
        self.assertLessEqual(len(res.message), 200)

    @linux_only

    def test_password_never_in_argv_env_or_result(self):
        gio = make_gio(self.tmp, "record")
        with mock.patch.dict(os.environ, {"R34_API_KEY": "sentinel-key"}):
            res = mount_share("nas", "my docs", "me", SECRET, gio=gio, timeout=10)
        self.assertTrue(res.ok, res)
        argv = Path(self.tmp, "argv").read_text()
        env = Path(self.tmp, "env").read_text()
        self.assertEqual(argv.split(), ["mount", "smb://nas/my%20docs/"])
        for dump in (argv, env, res.message):
            self.assertNotIn(SECRET, dump)
        self.assertNotIn("sentinel-key", env)
        self.assertNotIn("R34_API_KEY", env)
        keys = {line.split("=", 1)[0] for line in env.splitlines() if "=" in line}
        self.assertTrue(keys <= set(nm._gio_env()) | {"PWD", "SHLVL", "_", "OLDPWD", "TERM"},
                        keys)
        self.assertEqual(nm._gio_env()["LC_ALL"], "C.UTF-8")
        # a gio that echoes the password back: the message is scrubbed
        gio2 = make_gio(self.tmp, "echo")
        res2 = mount_share("nas", "docs", "me", SECRET, gio=gio2, timeout=10)
        self.assertNotIn(SECRET, res2.message)
        self.assertIn("***", res2.message)
        self.assertEqual(res2.kind, "unreachable")
        # an empty password never starts anything
        with mock.patch.object(nm.os, "execvpe") as ex:
            res3 = mount_share("nas", "docs", "me", "", gio=gio2)
        self.assertEqual((res3.ok, res3.kind, res3.message),
                         (False, "invalid", "Enter the password."))
        ex.assert_not_called()

    @linux_only

    def test_mount_share_times_out_and_kills_child(self):
        gio = make_gio(self.tmp, "hang")
        res = mount_share("nas", "docs", "me", SECRET, gio=gio, timeout=1.0)
        self.assertEqual((res.ok, res.kind), (False, "timeout"))
        self.assertEqual(res.message, "The server did not answer in time.")
        pid = int(Path(self.tmp, "pid").read_text().strip())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_mount_share_without_gio_reports_no_gio(self):
        res = mount_share("nas", "docs", "me", SECRET, gio="/nonexistent/gio")
        self.assertEqual((res.ok, res.kind, res.message),
                         (False, "no_gio", "The gio command is not installed."))
        bad = mount_share("nas/x", "docs", "me", SECRET, gio="/nonexistent/gio")
        self.assertEqual(bad.kind, "invalid")
        bad = mount_share("nas", "docs", "me\n", SECRET, gio="/nonexistent/gio")
        self.assertEqual((bad.kind, bad.message), ("invalid", "User or domain is not valid."))
        bad = mount_share("nas", "docs", "me", SECRET, "d" * 65, gio="/nonexistent/gio")
        self.assertEqual(bad.kind, "invalid")

    def test_module_imports_without_pty(self):
        code = (
            "import sys\n"
            "class Block:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name in ('pty', 'termios'):\n"
            "            raise ImportError('blocked ' + name)\n"
            "sys.meta_path.insert(0, Block())\n"
            "sys.path.insert(0, %r)\n"
            "import rule34_downloader.network_mounts as m\n"
            "assert m.build_uri('h', 's') == 'smb://h/s/'\n"
            "print('imported')\n" % str(ROOT)
        )
        out = subprocess.run([sys.executable, "-I", "-c", code],
                             capture_output=True, text=True, timeout=5)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("imported", out.stdout)


if __name__ == "__main__":
    unittest.main()
