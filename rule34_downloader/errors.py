"""Error classes and pure classifiers for API replies and exceptions (offline)."""
from __future__ import annotations

import json
import math
import re
from typing import Mapping, Optional

from .config import redact


class ApiError(Exception):
    pass


class TemporaryError(ApiError):
    def __init__(self, message: str, status: Optional[int] = None,
                 retry_after: Optional[float] = None, kind: str = "http"):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.kind = kind


class PermanentError(ApiError):
    def __init__(self, message: str, status: Optional[int] = None, kind: str = "client"):
        super().__init__(message)
        self.status = status
        self.kind = kind


class ServerUnavailable(TemporaryError):
    """Raised by the caller after the give-up window has expired."""


_RETRY_RE = re.compile(r"^\s*\d+(\.\d+)?\s*$")
_MAX_RETRY_AFTER = 3600.0
_TIMEOUT_NAMES = {"Timeout", "ReadTimeout", "ConnectTimeout"}
_CONN_NAMES = {"ConnectionError", "ChunkedEncodingError"}


def parse_retry_after(value) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if math.isfinite(value) and value >= 0:
            return min(float(value), _MAX_RETRY_AFTER)
        return None
    if isinstance(value, str) and _RETRY_RE.match(value):
        return min(float(value), _MAX_RETRY_AFTER)
    return None


def _msg(text: str, api_key) -> str:
    return redact(text, api_key)[:200]


def _header(headers, name: str):
    if not headers:
        return None
    for k, v in headers.items():
        if str(k).lower() == name.lower():
            return v
    return None


_BOM = b"\xef\xbb\xbf"


def strip_bom(body: bytes) -> bytes:
    """Remove exactly one leading UTF-8 BOM and nothing else."""
    return body[len(_BOM):] if body.startswith(_BOM) else body


def check_reply(status: int, body: bytes, headers: Optional[Mapping] = None,
                api_key: Optional[str] = None) -> None:
    text = strip_bom(body or b"").decode("utf-8", errors="replace")
    low = text.lower()
    if status == 429:
        raise TemporaryError(_msg(f"HTTP 429: {text}", api_key), status,
                             parse_retry_after(_header(headers, "Retry-After")), "http")
    if status >= 500:
        raise TemporaryError(_msg(f"HTTP {status}: {text}", api_key), status, kind="http")
    if status in (401, 403):
        kind = "captcha" if "captcha" in low else "auth"
        raise PermanentError(_msg(f"HTTP {status}: {text}", api_key), status, kind)
    if status >= 400:
        raise PermanentError(_msg(f"HTTP {status}: {text}", api_key), status, "client")
    stripped = text.strip()
    if not stripped:
        return None
    head = stripped.lower()
    if head.startswith("<?xml") or head.startswith("<posts"):
        return None
    try:
        data = json.loads(stripped)
        parsed = True
    except ValueError:
        data, parsed = None, False
    if parsed and isinstance(data, list):
        return None
    if "captcha" in low:
        raise PermanentError(_msg(f"captcha page: {text}", api_key), status, "captcha")
    if parsed and isinstance(data, str) and "authentication" in data.lower():
        raise PermanentError(_msg(data, api_key), status, "auth")
    if parsed and isinstance(data, dict) and "success" in data:
        s = data["success"]
        failed = (
            (s is False)
            or (not isinstance(s, bool) and isinstance(s, (int, float)) and s == 0)
            or (isinstance(s, str) and s.replace(" ", "").lower() == "false")
        )
        if failed:
            m = data.get("message")
            raise TemporaryError(_msg(m if isinstance(m, str) else "search down", api_key),
                                 status, kind="search_down")
    raise TemporaryError(_msg(f"malformed reply: {text}", api_key), status, kind="malformed")


def classify_exception(exc: BaseException, api_key: Optional[str] = None) -> Optional[TemporaryError]:
    names = {c.__name__ for c in type(exc).__mro__}
    message = _msg(str(exc), api_key)
    if isinstance(exc, TimeoutError) or names & _TIMEOUT_NAMES:
        return TemporaryError(message, kind="timeout")
    if isinstance(exc, ConnectionError) or names & _CONN_NAMES:
        return TemporaryError(message, kind="connection")
    return None
