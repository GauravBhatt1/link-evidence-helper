"""Local, body-free HTTP fixture server for workflow regression tests."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _send(self, status=200, content_type="text/html; charset=utf-8", body=b"", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_POST(self):
        if self.path == "/ajax":
            self._send(200, "application/json", b'{"url":"/attachment"}')
            return
        self._send(404, body=b"missing")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/movie":
            return self._send(body=b'<a href="/redirect">1080p Download</a>')
        if path == "/movie-post":
            return self._send(body=b'<a href="/post-form">1080p Download</a>')
        if path == "/redirect":
            return self._send(302, headers={"Location": "/landing"})
        if path == "/landing":
            return self._send(body=b'<a href="/attachment">Direct Download</a>')
        if path == "/branches":
            return self._send(body=b'<a href="/captcha">1080p Download mirror A</a><a href="/attachment">1080p Download mirror B</a>')
        if path == "/attachment":
            return self._send(200, "application/octet-stream", b"", {"Content-Disposition": 'attachment; filename="fixture.1080p.mkv"'})
        if path == "/interstitial/movie.mkv":
            return self._send(body=b"<html>not a file</html>")
        if path == "/expired":
            return self._send(403, "application/json", b'{"error":"signed URL expired"}')
        if path == "/captcha":
            return self._send(body=b'<div class="cf-turnstile"></div>Verify that you are human')
        if path == "/login":
            return self._send(body=b"<p>Login required</p>")
        if path == "/inactive-captcha":
            return self._send(body=b'<!-- captcha --><script>window.turnstile={}</script><div hidden class="cf-turnstile"></div><a href="/attachment">1080p Download</a>')
        if path == "/ajax":
            return self._send(body=b'<form method="post" action="/ajax"><button>Generate download</button></form>')
        if path == "/post-form":
            return self._send(body=b'<form method="post" action="/ajax"><button>Generate download</button></form>')
        if path == "/storage":
            return self._send(body=b'<script>localStorage.setItem("fixture", "ok"); sessionStorage.setItem("session", "ok")</script><a href="/attachment">1080p Download</a>')
        if path == "/delayed":
            return self._send(body=b'<script>setTimeout(()=>document.body.innerHTML=\'<a href="/attachment">Direct Download</a>\', 50)</script>')
        if path == "/popup":
            return self._send(body=b'<script>window.open("/attachment")</script>')
        if path == "/timeout":
            time.sleep(1.2)
            return self._send(body=b"late")
        if path == "/duplicate":
            return self._send(body=b'<a href="/attachment" data-action="first">1080p Download</a><a href="/attachment" data-action="second">1080p Download</a>')
        self._send(404, body=b"missing")


@contextmanager
def mock_workflow_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
