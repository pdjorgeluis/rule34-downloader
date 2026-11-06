"""Credentials and config (pure stdlib, never prints keys)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Credentials:
    user_id: str
    api_key: str = field(repr=False)

    def __repr__(self) -> str:
        return f"Credentials(user_id={self.user_id!r}, api_key='***')"


class MissingCredentials(Exception):
    pass


def config_path(env=None, platform=None) -> Path:
    env = os.environ if env is None else env
    if env.get("R34_CONFIG"):
        return Path(env["R34_CONFIG"])
    if (platform or os.name) == "nt":
        appdata = env.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "Rule34Downloader" / "config.json"
    xdg = env.get("XDG_CONFIG_HOME")
    root = Path(xdg) if xdg else Path.home() / ".config"
    return root / "rule34-downloader" / "config.json"


def _read(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        uid, key = str(data.get("user_id", "")), str(data.get("api_key", ""))
    except (OSError, ValueError, AttributeError):
        return None
    return Credentials(uid, key) if uid and key else None


def load_credentials(env=None, path=None, legacy_path=None) -> Credentials:
    env = os.environ if env is None else env
    if env.get("R34_USER_ID") and env.get("R34_API_KEY"):
        return Credentials(env["R34_USER_ID"], env["R34_API_KEY"])
    cfg = _read(path if path is not None else config_path(env))
    if cfg:
        return cfg
    if legacy_path is None and os.name != "nt":
        legacy_path = Path.home() / ".config" / "r34dl.json"
    if legacy_path is not None and os.name != "nt":
        cfg = _read(legacy_path)
        if cfg:
            return cfg
    raise MissingCredentials(
        "No credentials found. Looked in: environment (R34_USER_ID + R34_API_KEY), "
        "the config file, and the legacy r34dl.json file."
    )


def save_credentials(user_id: str, api_key: str, path=None) -> Path:
    p = Path(path) if path is not None else config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"user_id": user_id, "api_key": api_key}), encoding="utf-8")
    if os.name != "nt":
        os.chmod(p, 0o600)
    return p


def redact(text: str, api_key=None) -> str:
    if api_key:
        text = text.replace(api_key, "REDACTED")
    return re.sub(r"\b(api_key|user_id)=[^&\s\"']*", r"\1=REDACTED", text)
