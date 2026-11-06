"""In-process mock of the rule34.xxx dapi plus a file host (127.0.0.1, port 0)."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

USER_ID = "424242"
API_KEY = "FAKEKEY0123456789"
EXTS = ("jpg", "jpeg", "png", "gif", "mp4")


def file_body(post_id: int) -> bytes:
    return (b"mockdata-%d-" % post_id) * 20


class MockServer:
    """Base tag ``cat`` has ``total`` posts (ids 1..total, extension by id % 5)."""

    def __init__(self, total=25, user_id=USER_ID, api_key=API_KEY, exts=EXTS):
        self.total = total
        self.user_id = user_id
        self.api_key = api_key
        self.exts = exts
        self.mode = None            # None | "auth_string" | "http500"
        self.fail_ids = set()       # /f/<id> answers 500
        self.slow = 0.0             # seconds each /f/ request sleeps
        self.custom_posts = None    # list of ids served in this order, any sort
        self.log = []               # every API request: dict of params
        self.file_log = []          # every file request: post id
        self.api_script = []        # [(status, body_bytes, headers)] one per API request
        self.file_script = {}       # {post_id: [status, ...]} one per file request
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)

    @property
    def base(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    @property
    def api_url(self):
        return self.base + "/index.php"

    # -- data --------------------------------------------------------------
    def ext_of(self, post_id):
        return self.exts[post_id % len(self.exts)]

    def post(self, post_id):
        return {
            "id": post_id,
            "file_url": f"{self.base}/f/{post_id}.{self.ext_of(post_id)}",
            "score": post_id % 50,
            "tags": "cat mock",
        }

    def ids_for(self, tags):
        toks = tags.split()
        if any(t.startswith("zzqx") for t in toks):
            return []
        if self.custom_posts is not None:
            return list(self.custom_posts)
        ids = list(range(1, self.total + 1))
        return ids if "sort:id:asc" in toks else ids[::-1]

    def api_requests(self):
        with self._lock:
            return list(self.log)

    # -- http --------------------------------------------------------------
    def _handler(self):
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body=b"", ctype="application/json", headers=None):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                for hk, hv in (headers or {}).items():
                    self.send_header(hk, str(hv))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

            def do_GET(self):
                u = urlparse(self.path)
                if u.path.startswith("/f/"):
                    return self._file(u.path)
                q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
                with srv._lock:
                    srv.log.append(q)
                    scripted = srv.api_script.pop(0) if srv.api_script else None
                if scripted is not None:
                    st, sbody, shdr = scripted
                    return self._send(st, sbody, headers=shdr)
                if srv.mode == "http500":
                    return self._send(500, b"boom")
                if q.get("user_id") != srv.user_id or q.get("api_key") != srv.api_key:
                    return self._send(200, json.dumps("Missing authentication.").encode())
                if srv.mode == "auth_string":
                    return self._send(200, json.dumps("Missing authentication. Go to ...").encode())
                limit = int(q.get("limit", "100"))
                pid = int(q.get("pid", "0"))
                ids = srv.ids_for(q.get("tags", ""))
                if "json" not in q:
                    body = f'<?xml version="1.0"?><posts count="{len(ids)}" offset="0"></posts>'
                    return self._send(200, body.encode(), "text/xml")
                if not ids:
                    return self._send(200, b"")
                page = ids[pid * limit:(pid + 1) * limit]
                return self._send(200, json.dumps([srv.post(i) for i in page]).encode())

            def _file(self, path):
                name = path.rsplit("/", 1)[-1]
                post_id = int(name.split(".")[0])
                with srv._lock:
                    srv.file_log.append(post_id)
                    queue = srv.file_script.get(post_id)
                    scripted_status = queue.pop(0) if queue else None
                    srv.active += 1
                    srv.max_active = max(srv.max_active, srv.active)
                try:
                    if srv.slow:
                        time.sleep(srv.slow)
                    if scripted_status is not None:
                        # entry: status | (status, headers) | (status, headers, body)
                        entry = scripted_status if isinstance(scripted_status, tuple) \
                            else (scripted_status,)
                        st = entry[0]
                        hdr = entry[1] if len(entry) > 1 else None
                        sbody = entry[2] if len(entry) > 2 else b"scripted"
                        return self._send(st, sbody, "application/octet-stream", hdr)
                    if post_id in srv.fail_ids:
                        return self._send(500, b"fail")
                    return self._send(200, file_body(post_id), "application/octet-stream")
                finally:
                    with srv._lock:
                        srv.active -= 1

        return H
