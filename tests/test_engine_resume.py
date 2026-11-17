"""Slice b: Range resume of .part files (local http.server only, no network)."""
import http.server
import json
import re
import socketserver
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from rule34_downloader.engine import Job, JobConfig
from rule34_downloader.errors import TemporaryError
from rule34_downloader.resilience import check_file_reply

KIB = 1024
SOURCE = bytes((k * 7 + k // 251) % 251 for k in range(1024 * KIB))


class _Cred:
    api_key = "k"
    user_id = "u"


class _Client:
    credentials = _Cred()
    requests_made = 0

    def __init__(self, port):
        self.port = port

    def fetch_posts(self, q, n, pid):
        if pid:
            return []
        return [{"id": 1, "file_url": f"http://127.0.0.1:{self.port}/1.mp4"}]

    def total_count(self, q):
        return 1


class _Server:
    """Serves SOURCE.  ignore_range: always 200.  etag: validator sent.
    lie_total: claim a bigger total in Content-Range."""

    def __init__(self, ignore_range=False, etag='"v1"', lie_total=None, body=SOURCE):
        self.ignore_range, self.etag, self.lie_total = ignore_range, etag, lie_total
        self.body = body
        self.seen = []          # (Range, If-Range) per request
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                rng = self.headers.get("Range")
                ifr = self.headers.get("If-Range")
                outer.seen.append((rng, ifr))
                body = outer.body
                start = None
                m = re.fullmatch(r"bytes=(\d+)-", rng or "")
                if m and not outer.ignore_range:
                    start = int(m.group(1))
                    if ifr is not None and ifr != outer.etag:
                        start = None          # validator mismatch: full 200
                if start is not None and start >= len(body):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(body)}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if start is None:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    chunk = body
                else:
                    chunk = body[start:]
                    self.send_response(206)
                    total = outer.lie_total or len(body)
                    self.send_header("Content-Range",
                                     f"bytes {start}-{len(body) - 1}/{total}")
                    self.send_header("Content-Length", str(len(chunk)))
                if outer.etag:
                    self.send_header("ETag", outer.etag)
                self.end_headers()
                self.wfile.write(chunk)

            def log_message(self, *a):
                pass

        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class ResumeBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.events = []

    def server(self, **kw):
        s = _Server(**kw)
        self.addCleanup(s.close)
        return s

    def job(self, srv, retries=False):
        cfg = JobConfig(include=["t"], dest=self.dest, types={"videos"},
                        workers=1, count=1, delay=0)
        return Job(cfg, _Client(srv.port),
                   on_event=lambda k, d: self.events.append((k, d)), retries=retries)

    def final(self):
        return self.dest / "t" / "videos" / "1.mp4"

    def part(self):
        p = self.final()
        return p.with_name(p.name + ".part")

    def meta(self):
        p = self.part()
        return p.with_name(p.name + ".meta")

    def make_part(self, data, validator='"v1"', total=len(SOURCE)):
        self.final().parent.mkdir(parents=True, exist_ok=True)
        self.part().write_bytes(data)
        if validator is not None or total is not None:
            self.meta().write_text(json.dumps({"validator": validator, "total": total}))

    def run_ok(self, srv):
        s = self.job(srv).run()
        self.assertEqual(s.downloaded, 1, self.events)
        self.assertEqual(self.final().read_bytes(), SOURCE)
        self.assertFalse(self.part().exists())
        self.assertFalse(self.meta().exists())
        return s


class ResumeTests(ResumeBase):
    def test_resume_sends_range_with_part_size_and_result_is_byte_identical(self):
        srv = self.server()
        self.make_part(SOURCE[:300 * KIB])
        self.run_ok(srv)
        self.assertEqual(srv.seen, [(f"bytes={300 * KIB - 64 * KIB}-", '"v1"')])

    def test_server_answers_200_restarts_from_zero(self):
        srv = self.server(ignore_range=True)
        self.make_part(SOURCE[:300 * KIB])
        self.run_ok(srv)
        self.assertEqual(len(srv.seen), 1)
        self.assertIsNotNone(srv.seen[0][0])

    def test_416_discards_part_and_restarts(self):
        srv = self.server()
        # stale validator absent, junk longer than the server file
        self.make_part(b"x" * (len(SOURCE) + 100 * KIB), validator=None)
        self.run_ok(srv)
        self.assertEqual(srv.seen[0][0], f"bytes={len(SOURCE) + 100 * KIB - 64 * KIB}-")
        # after the 416 the file was requested again from zero (no Range)
        self.assertEqual(srv.seen[-1][0], None)

    def test_part_larger_than_server_file_restarts(self):
        srv = self.server()
        self.make_part(b"y" * (len(SOURCE) + 200 * KIB), validator=None)
        self.run_ok(srv)
        self.assertEqual(srv.seen[-1][0], None)

    def test_changed_validator_restarts(self):
        srv = self.server(etag='"v2"')
        self.make_part(b"z" * (300 * KIB), validator='"v1"')   # stale content
        self.run_ok(srv)
        self.assertEqual(srv.seen, [(f"bytes={236 * KIB}-", '"v1"')])

    def test_changed_server_file_without_validator_restarts_and_result_is_new_content(self):
        new = bytes((k * 11 + 3) % 253 for k in range(700 * KIB))   # different length
        srv = self.server(etag=None, body=new)
        self.make_part(SOURCE[:300 * KIB], validator=None, total=len(SOURCE))
        s = self.job(srv).run()
        self.assertEqual(s.downloaded, 1, self.events)
        self.assertEqual(self.final().read_bytes(), new)   # not old-prefix + new-tail
        self.assertFalse(self.part().exists())
        self.assertFalse(self.meta().exists())
        self.assertEqual(srv.seen[-1][0], None)            # restarted without Range

    def test_zero_byte_part_restarts(self):
        srv = self.server()
        self.make_part(b"", validator='"stale"')
        self.run_ok(srv)
        self.assertEqual(srv.seen, [(None, None)])

    def test_final_size_mismatch_is_temporary_error_and_leaves_no_final_file(self):
        srv = self.server(lie_total=2 * len(SOURCE))
        self.make_part(SOURCE[:300 * KIB], total=None)
        with self.assertRaises(TemporaryError):
            self.job(srv)._download(f"http://127.0.0.1:{srv.port}/1.mp4", self.final())
        self.assertFalse(self.final().exists())

    def test_crash_leftover_part_is_resumed_by_a_new_job(self):
        srv = self.server()
        job1 = self.job(srv)
        calls = {"n": 0}

        def crash(epoch):
            calls["n"] += 1
            if calls["n"] == 5:           # inside the chunk loop (3rd chunk of 4 x 256 KiB)
                raise ConnectionError("simulated crash")

        with mock.patch.object(job1, "_check_interrupt", crash):
            s1 = job1.run()
        self.assertEqual(s1.downloaded, 0)
        self.assertFalse(self.final().exists())
        self.assertTrue(self.part().exists())
        left = self.part().stat().st_size
        self.assertGreater(left, 0)
        srv.seen.clear()
        self.run_ok(srv)                  # a new job, same tags and dest
        self.assertEqual(len(srv.seen), 1)
        self.assertEqual(srv.seen[0][0], f"bytes={max(0, left - 64 * KIB)}-"
                         if left > 64 * KIB else None)

    def test_second_job_does_not_append_to_a_part_the_first_is_writing(self):
        srv = self.server()
        self.make_part(SOURCE[:300 * KIB])
        job1, job2 = self.job(srv), self.job(srv)
        url = f"http://127.0.0.1:{srv.port}/1.mp4"
        gate, inside = threading.Event(), threading.Event()
        orig = job1._check_interrupt

        def slow(epoch):
            inside.set()
            gate.wait(10)
            return orig(epoch)

        out = {}
        with mock.patch.object(job1, "_check_interrupt", slow):
            t = threading.Thread(
                target=lambda: out.setdefault("n", job1._download(url, self.final())))
            t.start()
            self.assertTrue(inside.wait(10))
            with self.assertRaises(TemporaryError):
                job2._download(url, self.final())
            gate.set()
            t.join(20)
        self.assertEqual(self.final().read_bytes(), SOURCE)


class Reply206Tests(unittest.TestCase):
    def test_check_file_reply_accepts_206(self):
        self.assertIsNone(check_file_reply(206, {"Content-Range": "bytes 5-9/10"}))


if __name__ == "__main__":
    unittest.main()
