import socket
import ssl
import unittest

import requests

from rule34_downloader.errors import (
    ApiError, PermanentError, ServerUnavailable, TemporaryError,
    check_reply, classify_exception, parse_retry_after,
)

KEY = "k" * 40 + "SECRETKEYVALUE"


def raised(*args, **kw):
    try:
        check_reply(*args, **kw)
    except ApiError as e:
        return e
    return None


class ErrorsTest(unittest.TestCase):
    def test_http_429_is_temporary_with_retry_after(self):
        e = raised(429, b"", {"retry-after": " 7 "})
        self.assertIsInstance(e, TemporaryError)
        self.assertEqual((e.retry_after, e.kind, e.status), (7.0, "http", 429))
        self.assertIsNone(raised(429, b"", {}).retry_after)
        self.assertEqual(raised(429, b"", {"RETRY-AFTER": "99999"}).retry_after, 3600.0)

    def test_http_5xx_are_temporary(self):
        for s in (500, 502, 503, 599):
            e = raised(s, b"")
            self.assertIsInstance(e, TemporaryError)
            self.assertEqual(e.kind, "http")

    def test_timeout_and_connection_error_are_temporary(self):
        e = classify_exception(requests.exceptions.ReadTimeout("slow"))
        self.assertEqual((type(e), e.kind), (TemporaryError, "timeout"))
        e = classify_exception(requests.exceptions.ConnectionError("down"))
        self.assertEqual(e.kind, "connection")
        self.assertEqual(classify_exception(ConnectionResetError("x")).kind, "connection")
        self.assertEqual(classify_exception(requests.exceptions.ChunkedEncodingError("x")).kind, "connection")

    def test_success_false_reply_is_temporary(self):
        for s in ("false", "False", " false ", "0", "0.0"):
            body = ('{"success":%s,"message":"search down"}' % (s if s[0] in "0" else '"%s"' % s)).encode()
            e = raised(200, body)
            self.assertEqual((type(e), e.kind), (TemporaryError, "search_down"), s)
        e = raised(200, b'{"success":false}')
        self.assertEqual((e.kind, str(e)), ("search_down", "search down"))
        self.assertEqual(str(raised(200, b'{"success":false,"message":"boom"}')), "boom")
        for body in (b'{"success":true}', b'{}', b'{"a":1}', b"3", b"null"):
            self.assertEqual(raised(200, body).kind, "malformed", body)

    def test_missing_authentication_string_is_permanent_auth(self):
        e = raised(200, b'"Missing authentication. Go to api.rule34.xxx"')
        self.assertEqual((type(e), e.kind), (PermanentError, "auth"))

    def test_http_401_403_are_permanent(self):
        for s in (401, 403):
            e = raised(s, b"")
            self.assertEqual((type(e), e.kind), (PermanentError, "auth"))
        self.assertEqual(raised(403, b"<title>Rule34.xxx CAPTCHA</title>").kind, "captcha")

    def test_captcha_html_is_permanent_captcha(self):
        e = raised(200, b"<html><title>Rule34.xxx CAPTCHA</title></html>")
        self.assertEqual((type(e), e.kind), (PermanentError, "captcha"))

    def test_non_json_200_is_temporary_malformed(self):
        e = raised(200, b"garbage")
        self.assertEqual((type(e), e.kind), (TemporaryError, "malformed"))

    def test_zero_byte_and_brackets_and_xml_are_not_errors(self):
        for b in (b"", b"  \n", b"[]", b'[{"id":1}]',
                  b'<?xml version="1.0"?><posts count="0"></posts>', b"  <POSTS count='0'/>"):
            self.assertIsNone(check_reply(200, b), b)

    def test_other_4xx_permanent_client(self):
        for s in (400, 404, 410, 418):
            e = raised(s, b"")
            self.assertEqual((type(e), e.kind), (PermanentError, "client"))

    def test_error_messages_redact_key(self):
        body = ('{"success":false,"message":"bad api_key=%s&user_id=123 and %s"}' % (KEY, KEY)).encode()
        for e in (raised(200, body, api_key=KEY), raised(500, body, api_key=KEY),
                  raised(404, body, api_key=KEY), raised(200, b"x " + KEY.encode(), api_key=KEY),
                  classify_exception(ConnectionError("fail " + KEY), KEY)):
            self.assertNotIn(KEY, str(e))
        self.assertNotIn("user_id=123", str(raised(500, b"user_id=123&x", api_key=None)))

    def test_classified_errors_are_apierror_subclasses(self):
        self.assertTrue(issubclass(TemporaryError, ApiError))
        self.assertTrue(issubclass(PermanentError, ApiError))
        self.assertTrue(issubclass(ServerUnavailable, TemporaryError))
        from rule34_downloader.api import ApiError as A2
        self.assertIs(A2, ApiError)
        s = ServerUnavailable("gone")
        self.assertEqual((s.kind, s.retry_after, s.status, str(s)), ("http", None, None, "gone"))
        self.assertEqual(PermanentError("x").kind, "client")
        self.assertFalse(hasattr(PermanentError("x"), "retry_after"))

    def test_parse_retry_after_cases(self):
        self.assertEqual(parse_retry_after(" 7 "), 7.0)
        self.assertEqual(parse_retry_after("1.5"), 1.5)
        self.assertEqual(parse_retry_after("99999"), 3600.0)
        self.assertEqual(parse_retry_after(4), 4.0)
        self.assertEqual(parse_retry_after(2.5), 2.5)
        for bad in ("-3", "1e3", "inf", "nan", "+5", ".5", "5.", "1_0",
                    "Wed, 21 Oct 2026 07:28:00 GMT", "", True, None, -1,
                    float("inf"), float("nan"), [], b"5"):
            self.assertIsNone(parse_retry_after(bad), bad)

    def test_captcha_text_inside_post_list_is_not_an_error(self):
        self.assertIsNone(check_reply(200, b'[{"id":1,"tags":"captcha foo"}]'))

    def test_redaction_happens_before_truncation(self):
        text = "x" * 185 + KEY  # prefix "HTTP 500: " puts the key straddling char 200
        e = raised(500, text.encode(), api_key=KEY)
        self.assertLessEqual(len(str(e)), 200)
        self.assertNotIn(KEY[:5], str(e))
        self.assertTrue(str(e).endswith("REDAC"))  # mask cut, not key

    def test_timeout_wins_over_connection(self):
        e = classify_exception(requests.exceptions.ConnectTimeout("t"))
        self.assertEqual(e.kind, "timeout")
        self.assertEqual(classify_exception(TimeoutError("t")).kind, "timeout")

    def test_classify_exception_returns_none_for_other_exceptions(self):
        for exc in (socket.gaierror("dns"), ssl.SSLError("tls"), ApiError("a"),
                    TemporaryError("t"), ValueError("v"), KeyError("k")):
            self.assertIsNone(classify_exception(exc), exc)

    def test_check_reply_ignores_leading_bom(self):
        self.assertIsNone(check_reply(200, b"\xef\xbb\xbf"))
        self.assertIsNone(check_reply(200, b"\xef\xbb\xbf[]"))
        self.assertIsNone(check_reply(200, b"\xef\xbb\xbf<posts count=\"0\"/>"))

    def test_strip_bom_removes_one_leading_bom_only(self):
        from rule34_downloader.errors import strip_bom
        self.assertEqual(strip_bom(b"\xef\xbb\xbf[]"), b"[]")
        self.assertEqual(strip_bom(b"\xef\xbb\xbf\xef\xbb\xbf[]"), b"\xef\xbb\xbf[]")
        self.assertEqual(strip_bom(b"[]\xef\xbb\xbf"), b"[]\xef\xbb\xbf")
        self.assertEqual(strip_bom(b""), b"")


if __name__ == "__main__":
    unittest.main()
