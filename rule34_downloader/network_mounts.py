"""Network shares through the gio command (list mounted, mount with a typed password).

Pure helper: standard library only, no Qt. The password is only ever written
to the child's terminal; it never reaches argv, the environment, a message
or a file.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from typing import List, Optional, Tuple

_MOUNT_LINE = re.compile(r"^Mount\(\d+\): (.+) -> (\S+)\s*$")
_SERVER_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")
_SHARE_RE = re.compile(r"^[A-Za-z0-9 _.$+-]+$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")

_SERVER_MSG = "Enter the server name or IPv4 address."
_SHARE_MSG = "The share name has characters that are not allowed."

_UNREACHABLE = (
    "connection refused", "no route", "unreachable", "host not found",
    "could not resolve", "name or service not known", "timed out",
)
_BAD_CREDENTIALS = (
    "permission denied", "logon failure", "access denied",
    "invalid credentials", "authentication", "password",
)


@dataclass(frozen=True)
class Mount:
    name: str
    uri: str
    path: str


@dataclass(frozen=True)
class MountResult:
    ok: bool
    kind: str
    message: str


def _gio_env() -> dict:
    """Environment for every gio child: only what gio needs, no secrets."""
    env = {k: os.environ[k]
           for k in ("PATH", "HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
           if k in os.environ}
    env["LC_ALL"] = "C.UTF-8"
    return env


def find_gio() -> Optional[str]:
    return shutil.which("gio")


def parse_mount_list(text: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for line in text.splitlines():
        m = _MOUNT_LINE.match(line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def _usable(gio: Optional[str]) -> bool:
    return bool(gio) and os.path.isfile(gio) and os.access(gio, os.X_OK)


def _run(argv: List[str], timeout: float) -> Optional[str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, env=_gio_env())
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def list_mounts(gio: Optional[str] = None, timeout: float = 5.0) -> List[Mount]:
    if gio is None:
        gio = find_gio()
    if gio is None or not _usable(gio):
        return []
    listing = _run([gio, "mount", "-l"], timeout)
    if listing is None:
        return []
    mounts: List[Mount] = []
    for name, uri in parse_mount_list(listing):
        info = _run([gio, "info", "-a", "standard::display-name", uri], timeout)
        if info is None:
            continue
        path = ""
        for line in info.splitlines():
            if line.startswith("local path: "):
                path = line[len("local path: "):].strip()
                break
        if path and os.path.isdir(path):
            mounts.append(Mount(name, uri, path))
    return mounts


def validate_server(text: str) -> Optional[str]:
    t = text.strip(" ")
    if not t:
        return "Enter the server name or address."
    if len(t) > 253 or ".." in t or not _SERVER_RE.match(t):
        return _SERVER_MSG
    return None


def validate_share(text: str) -> Optional[str]:
    t = text.strip(" ")
    if not t:
        return "Enter the share name."
    if len(t) > 80 or not _SHARE_RE.match(t) or not t.strip("."):
        return _SHARE_MSG
    return None


def build_uri(server: str, share: str) -> str:
    return ("smb://" + server.strip() + "/"
            + urllib.parse.quote(share.strip(), safe="") + "/")


def _sanitize(text: str, password: str) -> str:
    if password:
        text = text.replace(password, "***")
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = _CONTROL_RE.sub("", text)
    if password:
        text = text.replace(password, "***")
    return text.strip()[:200]


def _norm_uri(uri: str) -> str:
    return urllib.parse.unquote(uri).rstrip("/")


def _bad_field(text: str) -> bool:
    return len(text) > 64 or bool(_CONTROL_RE.search(text))


def _last_line(pending: str) -> str:
    return pending.rsplit("\n", 1)[-1].lstrip("\r ")


def _classify(text: str) -> str:
    low = text.lower()
    if any(w in low for w in _UNREACHABLE):
        return "unreachable"
    if any(w in low for w in _BAD_CREDENTIALS):
        return "bad_credentials"
    return "error"


def mount_share(server: str, share: str, user: str, password: str,
                domain: str = "", gio: Optional[str] = None,
                timeout: float = 30.0) -> MountResult:
    if not password:
        return MountResult(False, "invalid", "Enter the password.")
    err = validate_server(server) or validate_share(share)
    if err:
        return MountResult(False, "invalid", err)
    if _bad_field(user) or _bad_field(domain):
        return MountResult(False, "invalid", "User or domain is not valid.")
    if gio is None:
        gio = find_gio()
    if gio is None or not _usable(gio):
        return MountResult(False, "no_gio", "The gio command is not installed.")

    import pty
    import select
    import signal
    import termios

    uri = build_uri(server, share)
    try:
        pid, fd = pty.fork()
    except OSError:
        return MountResult(False, "error", "Could not connect.")
    if pid == 0:  # child: never fall back into the caller
        try:
            os.execvpe(gio, [gio, "mount", uri], _gio_env())
        except Exception:
            pass
        os._exit(127)

    reaped = False
    status = 0

    def kill_and_reap() -> None:
        nonlocal reaped, status
        if reaped:
            return
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            _, status = os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        reaped = True

    def reap_after_eof() -> None:
        nonlocal reaped, status
        end = time.monotonic() + 2.0
        while time.monotonic() < end:
            try:
                got, st = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                reaped = True
                return
            if got:
                status, reaped = st, True
                return
            time.sleep(0.05)
        kill_and_reap()

    try:
        try:
            attrs = termios.tcgetattr(fd)
            attrs[3] &= ~termios.ECHO
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except termios.error:
            pass

        transcript = ""
        pending = ""
        answered = set()
        password_sent = False
        deadline = time.monotonic() + timeout
        eof = False
        while True:
            if time.monotonic() >= deadline:
                kill_and_reap()
                return MountResult(False, "timeout", "The server did not answer in time.")
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    data = b""
                if not data:
                    eof = True
                else:
                    chunk = data.decode("utf-8", errors="replace")
                    transcript += chunk
                    pending += chunk
            if password_sent and transcript.count("Authentication Required") >= 2:
                kill_and_reap()
                return MountResult(False, "bad_credentials",
                                   "The user name or password was not accepted.")
            if eof:
                break
            if pending and not pending.endswith("\n"):
                last = _last_line(pending)
                for word, reply in (("User", user), ("Domain", domain),
                                    ("Password", password)):
                    if last.startswith(word) and word not in answered:
                        answered.add(word)
                        if word == "Password":
                            time.sleep(0.2)
                            password_sent = True
                        try:
                            os.write(fd, (reply + "\n").encode("utf-8"))
                        except OSError:
                            pass
                        pending = ""
                        break
        reap_after_eof()
        if password_sent and transcript.count("Authentication Required") >= 2:
            return MountResult(False, "bad_credentials",
                               "The user name or password was not accepted.")
        code = os.waitstatus_to_exitcode(status) if reaped else -1
        if code == 0:
            want = _norm_uri(uri)
            for attempt in range(3):
                if any(_norm_uri(m.uri) == want for m in list_mounts(gio)):
                    return MountResult(True, "ok", "Connected.")
                if attempt < 2:
                    time.sleep(0.5)
            return MountResult(False, "error",
                               "The share did not appear after connecting.")
        text = _sanitize(transcript, password)
        return MountResult(False, _classify(text), text or "Could not connect.")
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        kill_and_reap()
