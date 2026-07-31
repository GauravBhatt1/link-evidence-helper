import tempfile
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch

from library_backend import LibraryService
from runtime_support import BoundedCache
from web_app import BoundedThreadingHTTPServer


class RuntimeSafetyTests(unittest.TestCase):
    def test_bounded_cache_evicts_least_recently_used_entry(self):
        cache = BoundedCache(2)
        cache["first"] = 1
        cache["second"] = 2
        self.assertEqual(cache["first"], 1)  # first becomes most recently used
        cache["third"] = 3
        self.assertIn("first", cache)
        self.assertNotIn("second", cache)
        self.assertEqual(cache["third"], 3)

    def test_failed_scan_creation_releases_the_scan_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            service = LibraryService("", str(Path(directory) / "library.db"))
            with patch.object(service, "_connection", side_effect=OSError("database unavailable")):
                with self.assertRaises(OSError):
                    service.start_scan()
            self.assertTrue(service._scan_lock.acquire(blocking=False))
            service._scan_lock.release()

    def test_get_job_returns_a_snapshot_not_the_shared_state(self):
        with tempfile.TemporaryDirectory() as directory:
            service = LibraryService("", str(Path(directory) / "library.db"))
            with service._jobs_lock:
                service._jobs["job"] = {"id": "job", "progress": {"percentage": 10}}
            snapshot = service.get_job("job")
            snapshot["progress"]["percentage"] = 99
            self.assertEqual(service.get_job("job")["progress"]["percentage"], 10)

    def test_http_server_rejects_requests_after_worker_limit(self):
        class SlowHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                time.sleep(.25)
                self.send_response(200); self.end_headers()
            def log_message(self, *_args):
                pass

        server = BoundedThreadingHTTPServer(("127.0.0.1", 0), SlowHandler, max_workers=1)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            first = socket.create_connection(server.server_address)
            first.sendall(b"GET / HTTP/1.1\r\nHost: test\r\n\r\n")
            time.sleep(.03)
            with socket.create_connection(server.server_address) as second:
                second.sendall(b"GET / HTTP/1.1\r\nHost: test\r\n\r\n")
                self.assertIn(b"503 Service Unavailable", second.recv(256))
            first.close()
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
