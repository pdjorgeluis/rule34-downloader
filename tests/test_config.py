import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from rule34_downloader.config import (
    Credentials, MissingCredentials, config_path, load_credentials, redact, save_credentials,
)

FAKE_ID, FAKE_KEY = "12345", "fakekey-abc-999"


def _write(p, uid=FAKE_ID, key=FAKE_KEY):
    Path(p).write_text(json.dumps({"user_id": uid, "api_key": key}))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_config_path_env_override(self):
        self.assertEqual(config_path({"R34_CONFIG": "/t/c.json"}), Path("/t/c.json"))

    def test_config_path_linux_default(self):
        self.assertEqual(
            config_path({"XDG_CONFIG_HOME": "/xdg"}, platform="posix"),
            Path("/xdg/rule34-downloader/config.json"),
        )
        self.assertEqual(
            config_path({}, platform="posix"),
            Path.home() / ".config" / "rule34-downloader" / "config.json",
        )

    def test_config_path_windows_default(self):
        p = config_path({"APPDATA": "C:/AppData"}, platform="nt")
        self.assertEqual(p, Path("C:/AppData") / "Rule34Downloader" / "config.json")

    def test_credentials_from_env(self):
        cfg = self.d / "c.json"; _write(cfg, "other", "otherkey")
        c = load_credentials({"R34_USER_ID": FAKE_ID, "R34_API_KEY": FAKE_KEY}, cfg, self.d / "no")
        self.assertEqual((c.user_id, c.api_key), (FAKE_ID, FAKE_KEY))

    def test_credentials_from_config(self):
        cfg = self.d / "c.json"; _write(cfg)
        legacy = self.d / "l.json"; _write(legacy, "legacy", "legacykey")
        c = load_credentials({}, cfg, legacy)
        self.assertEqual(c.user_id, FAKE_ID)

    @unittest.skipIf(os.name == "nt", "legacy file is Linux only")
    def test_credentials_legacy_fallback(self):
        legacy = self.d / "l.json"; _write(legacy)
        c = load_credentials({}, self.d / "missing.json", legacy)
        self.assertEqual(c.api_key, FAKE_KEY)

    def test_missing_credentials_error_has_no_key(self):
        with self.assertRaises(MissingCredentials) as cm:
            load_credentials({"R34_USER_ID": FAKE_ID}, self.d / "a.json", self.d / "b.json")
        msg = str(cm.exception)
        self.assertNotIn(FAKE_KEY, msg)
        for word in ("environment", "config", "legacy"):
            self.assertIn(word, msg)

    @unittest.skipIf(os.name == "nt", "POSIX modes only")
    def test_save_credentials_mode_0600(self):
        p = save_credentials(FAKE_ID, FAKE_KEY, self.d / "sub" / "c.json")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)

    def test_save_roundtrip(self):
        p = self.d / "n" / "c.json"
        save_credentials(FAKE_ID, FAKE_KEY, p)
        c = load_credentials({}, p, self.d / "none")
        self.assertEqual(c, Credentials(FAKE_ID, FAKE_KEY))

    def test_redact_removes_key_and_query_values(self):
        t = f"GET /x?user_id={FAKE_ID}&api_key=zzz&limit=1 failed, key {FAKE_KEY}"
        out = redact(t, FAKE_KEY)
        self.assertNotIn(FAKE_KEY, out)
        self.assertNotIn("zzz", out)
        self.assertNotIn(FAKE_ID, out)
        self.assertIn("api_key=REDACTED", out)
        self.assertIn("limit=1", out)

    def test_credentials_repr_masks_key(self):
        r = repr(Credentials(FAKE_ID, FAKE_KEY))
        self.assertIn("api_key='***'", r)
        self.assertNotIn(FAKE_KEY, r)


if __name__ == "__main__":
    unittest.main()
