"""Backlog fix 1: keep-alive reuse and a safe _abort_response (local server only)."""
import http.server
import socketserver
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from rule34_downloader.engine import Job, JobConfig, _abort_response

BODY = b"x" * 5000


class _Cred:
    api_key = "k"
    user_id = "u"


class _Client:
    credentials = _Cred()
    requests_made = 0

    def __init__(self, port, n):
        self.port, self.n = port, n

    def fetch_posts(self, q, n, pid):
        if pid:
            return []
        return [{"id": i, "file_url": f"http://127.0.0.1:{self.port}/{i}.jpg"}
                for i in range(1, self.n + 1)]

    def total_count(self, q):
        return self.n


class _KeepAliveServer:
    def __init__(self):
        self.connections = 0
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                outer.connections += 1
                super().setup()

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(BODY)))
                self.end_headers()
                self.wfile.write(BODY)

            def log_message(self, *a):
                pass

        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class KeepAliveTests(unittest.TestCase):
    def test_sequential_downloads_reuse_one_connection(self):
        srv = _KeepAliveServer()
        self.addCleanup(srv.close)
        with tempfile.TemporaryDirectory() as d:
            cfg = JobConfig(include=["t"], dest=Path(d), workers=1, delay=0,
                            count=4)
            s = Job(cfg, _Client(srv.port, 4)).run()
            self.assertEqual(s.downloaded, 4)
        self.assertEqual(srv.connections, 1)

    def test_abort_does_not_shut_down_a_released_connection(self):
        sock = mock.Mock()
        resp = mock.Mock()
        resp.raw._connection = None          # already handed back to the pool
        resp.raw._fp.fp.raw._sock = sock
        _abort_response(resp)
        sock.shutdown.assert_not_called()
        _abort_response(resp)                # closing twice stays harmless

    def test_abort_still_shuts_down_a_response_that_holds_its_connection(self):
        sock = mock.Mock()
        resp = mock.Mock()
        resp.raw._connection = object()      # mid-read: connection still held
        resp.raw._fp.fp.raw._sock = sock
        _abort_response(resp)
        sock.shutdown.assert_called_once()


class StaleLockTests(unittest.TestCase):
    def test_part_lock_leftover_does_not_block_a_new_download(self):
        srv = _KeepAliveServer()
        self.addCleanup(srv.close)
        with tempfile.TemporaryDirectory() as d:
            pics = Path(d) / "t" / "pictures"
            pics.mkdir(parents=True)
            (pics / "1.jpg.part.lock").write_bytes(b"")   # crash leftover, no holder
            cfg = JobConfig(include=["t"], dest=Path(d), workers=1, delay=0,
                            count=1)
            s = Job(cfg, _Client(srv.port, 1)).run()
            self.assertEqual(s.downloaded, 1)
            self.assertEqual((pics / "1.jpg").read_bytes(), BODY)


if __name__ == "__main__":
    unittest.main()
