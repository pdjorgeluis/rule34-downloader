"""API client for the rule34.xxx dapi (single network seam: ``_get``)."""
from __future__ import annotations

import json
import os
import re
import threading

import requests

from .config import Credentials, redact
from .errors import (  # noqa: F401  (ApiError re-exported)
    ApiError, TemporaryError, check_reply, classify_exception, strip_bom,
)

DEFAULT_API_URL = "https://api.rule34.xxx/index.php"
USER_AGENT = "Rule34Downloader/0.1 (personal archiver)"
TIMEOUT = (10, 60)


def api_url() -> str:
    """Read at call time so tests and users can override with R34_API_URL."""
    return os.environ.get("R34_API_URL") or DEFAULT_API_URL


class Reply(tuple):
    """``(status, body)`` that also carries the response headers."""

    def __new__(cls, status: int, body: bytes, headers=None):
        self = super().__new__(cls, (status, body))
        self.headers = dict(headers) if headers else {}
        return self

    @property
    def status(self) -> int:
        return self[0]

    @property
    def body(self) -> bytes:
        return self[1]


class ApiClient:
    def __init__(self, credentials: Credentials, session=None):
        self.credentials = credentials
        self.session = session if session is not None else requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.requests_made = 0
        self._lock = threading.Lock()

    def _safe(self, text) -> str:
        return redact(str(text), self.credentials.api_key)

    def _params(self, query: str, limit: int, pid: int, json_mode: bool) -> dict:
        params = {"page": "dapi", "s": "post", "q": "index"}
        if json_mode:
            params["json"] = "1"
        params.update(
            {
                "limit": str(limit),
                "pid": str(pid),
                "tags": query,
                "user_id": self.credentials.user_id,
                "api_key": self.credentials.api_key,
            }
        )
        return params

    def _get(self, params: dict):
        """The only network call: returns a Reply (status, body, headers)."""
        with self._lock:
            self.requests_made += 1
        try:
            resp = self.session.get(api_url(), params=params, timeout=TIMEOUT)
            return Reply(resp.status_code, resp.content, dict(resp.headers))
        except requests.RequestException as exc:
            message = f"request failed: {self._safe(exc)}"
            temp = classify_exception(exc, self.credentials.api_key)
            if temp is not None:
                raise TemporaryError(message, temp.status, temp.retry_after, temp.kind) from None
            raise ApiError(message) from None

    def _checked(self, params: dict) -> bytes:
        """GET, classify the reply, return the body without a leading BOM."""
        reply = self._get(params)
        headers = getattr(reply, "headers", None)
        status, body = reply[0], reply[1]
        check_reply(status, body, headers, self.credentials.api_key)
        return strip_bom(body)

    def fetch_posts(self, query: str, page_size: int, pid: int) -> list:
        limit = max(1, min(1000, int(page_size)))
        body = self._checked(self._params(query, limit, pid, True))
        if not body.strip():
            return []
        try:
            data = json.loads(body)
        except ValueError:
            raise ApiError(
                "non-JSON reply: " + self._safe(body[:200].decode("utf-8", "replace"))
            ) from None
        if isinstance(data, list):
            return data
        if isinstance(data, str):
            raise ApiError(self._safe(data))
        raise ApiError("unexpected JSON reply of type " + type(data).__name__)

    def total_count(self, query: str) -> int:
        body = self._checked(self._params(query, 0, 0, False))
        m = re.search(r'count="(\d+)"', body.decode("utf-8", "replace"))
        if not m:
            raise ApiError("no count in reply: " + self._safe(body[:200].decode("utf-8", "replace")))
        return int(m.group(1))
