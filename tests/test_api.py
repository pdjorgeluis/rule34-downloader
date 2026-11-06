import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mock_api import API_KEY, USER_ID, MockServer  # noqa: E402
from rule34_downloader.api import ApiClient, ApiError  # noqa: E402
from rule34_downloader.config import Credentials  # noqa: E402


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.srv = MockServer(total=25).start()
        self.addCleanup(self.srv.stop)
        env = mock.patch.dict(os.environ, {"R34_API_URL": self.srv.api_url})
        env.start()
        self.addCleanup(env.stop)
        self.client = ApiClient(Credentials(USER_ID, API_KEY))

    def test_fetch_posts_returns_list(self):
        posts = self.client.fetch_posts("cat", 10, 0)
        self.assertEqual([p["id"] for p in posts], list(range(25, 15, -1)))
        self.assertIn("file_url", posts[0])

    def test_fetch_posts_empty_body_is_empty_list(self):
        self.assertEqual(self.client.fetch_posts("zzqxnothing", 10, 0), [])

    def test_fetch_posts_brackets_is_empty_list(self):
        self.assertEqual(self.client.fetch_posts("cat", 10, 99), [])

    def test_fetch_posts_sends_credentials_and_params(self):
        self.client.fetch_posts("cat -dog", 7, 3)
        q = self.srv.api_requests()[0]
        self.assertEqual(q["page"], "dapi")
        self.assertEqual(q["s"], "post")
        self.assertEqual(q["q"], "index")
        self.assertEqual(q["json"], "1")
        self.assertEqual(q["limit"], "7")
        self.assertEqual(q["pid"], "3")
        self.assertEqual(q["tags"], "cat -dog")
        self.assertEqual(q["user_id"], USER_ID)
        self.assertEqual(q["api_key"], API_KEY)

    def test_fetch_posts_clamps_page_size_to_1000(self):
        self.client.fetch_posts("cat", 5000, 0)
        self.client.fetch_posts("cat", 0, 0)
        self.assertEqual([q["limit"] for q in self.srv.api_requests()], ["1000", "1"])

    def test_missing_authentication_string_raises_apierror(self):
        self.srv.mode = "auth_string"
        with self.assertRaises(ApiError) as cm:
            self.client.fetch_posts("cat", 10, 0)
        self.assertIn("Missing authentication", str(cm.exception))

    def test_http_500_raises_apierror(self):
        self.srv.mode = "http500"
        with self.assertRaises(ApiError) as cm:
            self.client.fetch_posts("cat", 10, 0)
        self.assertIn("500", str(cm.exception))

    def test_apierror_message_has_no_key(self):
        import requests

        class Boom:
            headers = {}

            def get(self, url, **kw):
                raise requests.ConnectionError(
                    "HTTPConnectionPool: Max retries exceeded with url: "
                    "/index.php?page=dapi&api_key=ABC123&user_id=9"
                )

        client = ApiClient(Credentials("9", "ABC123"), session=Boom())
        with self.assertRaises(ApiError) as cm:
            client.fetch_posts("cat", 10, 0)
        self.assertNotIn("ABC123", str(cm.exception))
        self.assertNotIn("ABC123", repr(cm.exception))

    def test_total_count_reads_count_attribute(self):
        self.assertEqual(self.client.total_count("cat"), 25)
        self.assertEqual(self.client.total_count("zzqxnothing"), 0)

    def test_total_count_uses_limit_zero_without_json(self):
        self.client.total_count("cat")
        q = self.srv.api_requests()[0]
        self.assertEqual(q["limit"], "0")
        self.assertNotIn("json", q)

    def test_requests_made_counts_every_call(self):
        self.assertEqual(self.client.requests_made, 0)
        self.client.fetch_posts("cat", 5, 0)
        self.client.total_count("cat")
        self.srv.mode = "http500"
        with self.assertRaises(ApiError):
            self.client.fetch_posts("cat", 5, 0)
        self.assertEqual(self.client.requests_made, 3)
        self.assertEqual(len(self.srv.api_requests()), 3)


if __name__ == "__main__":
    unittest.main()
