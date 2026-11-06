import unittest

import requests

from rule34_downloader.api import ApiClient, Reply
from rule34_downloader.config import Credentials
from rule34_downloader.errors import ApiError, PermanentError, TemporaryError

KEY = "k" * 30 + "SECRETKEYVALUE"


class Resp:
    def __init__(self, status=200, content=b"", headers=None):
        self.status_code, self.content, self.headers = status, content, headers or {}


class FakeSession:
    def __init__(self, reply):
        self.headers = {}
        self.reply = reply

    def get(self, url, params=None, timeout=None):
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


def client(reply):
    return ApiClient(Credentials("1", KEY), session=FakeSession(reply))


class ApiErrorsTest(unittest.TestCase):
    def test_fetch_posts_429_raises_temporary_with_retry_after(self):
        c = client(Resp(429, b"slow down", {"Retry-After": "7"}))
        with self.assertRaises(TemporaryError) as cm:
            c.fetch_posts("cat", 100, 0)
        self.assertEqual((cm.exception.status, cm.exception.retry_after), (429, 7.0))
        self.assertEqual(c.requests_made, 1)

    def test_fetch_posts_captcha_raises_permanent(self):
        c = client(Resp(403, b"Please solve the CAPTCHA"))
        with self.assertRaises(PermanentError) as cm:
            c.fetch_posts("cat", 100, 0)
        self.assertEqual(cm.exception.kind, "captcha")

    def test_fetch_posts_search_down_raises_temporary(self):
        c = client(Resp(200, b'{"success": false, "message": "search down"}'))
        with self.assertRaises(TemporaryError) as cm:
            c.fetch_posts("cat", 100, 0)
        self.assertEqual(cm.exception.kind, "search_down")

    def test_fetch_posts_timeout_raises_temporary(self):
        exc = requests.ReadTimeout("timed out url=?api_key=" + KEY)
        c = client(exc)
        with self.assertRaises(TemporaryError) as cm:
            c.fetch_posts("cat", 100, 0)
        self.assertEqual(cm.exception.kind, "timeout")
        self.assertTrue(str(cm.exception).startswith("request failed: "))
        self.assertNotIn(KEY, str(cm.exception))
        self.assertIsNone(cm.exception.__cause__)
        self.assertTrue(cm.exception.__suppress_context__)

    def test_non_temporary_request_exception_stays_plain_api_error(self):
        c = client(requests.exceptions.InvalidURL("bad " + KEY))
        with self.assertRaises(ApiError) as cm:
            c.total_count("cat")
        self.assertNotIsInstance(cm.exception, TemporaryError)
        self.assertNotIn(KEY, str(cm.exception))

    def test_reply_unpacks_as_status_body_and_has_headers(self):
        r = Reply(200, b"x", {"A": "b"})
        status, body = r
        self.assertEqual((status, body, r.headers), (200, b"x", {"A": "b"}))
        self.assertEqual((len(r), r[0], r[1], r.status, r.body), (2, 200, b"x", 200, b"x"))
        self.assertEqual(r, (200, b"x"))
        self.assertEqual(Reply(204, b"").headers, {})

    def test_get_returns_reply_with_header_dict(self):
        c = client(Resp(200, b"[]", {"X": "1"}))
        r = c._get({})
        self.assertEqual((r.status, r.body, r.headers), (200, b"[]", {"X": "1"}))

    def test_bom_prefixed_empty_body_is_empty_list(self):
        self.assertEqual(client(Resp(200, b"\xef\xbb\xbf")).fetch_posts("cat", 100, 0), [])

    def test_bom_prefixed_list_is_parsed(self):
        c = client(Resp(200, b'\xef\xbb\xbf[{"id": 5}]'))
        self.assertEqual(c.fetch_posts("cat", 100, 0), [{"id": 5}])


if __name__ == "__main__":
    unittest.main()
