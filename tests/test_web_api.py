"""Smoke tests for the modern Web UI backend (in-process server)."""
import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui import web_ui


class TestWebAPI(unittest.TestCase):
    httpd = None
    base = ""

    @classmethod
    def setUpClass(cls):
        web_ui.STATE.done = True  # idle
        cls.httpd = web_ui.ThreadingHTTPServer(("127.0.0.1", 0), web_ui.UIRequestHandler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        web_ui.STATE.cancel_event.set()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return r.status, r.read()

    def _post(self, path, payload=None):
        data = json.dumps(payload or {}).encode()
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())

    def _wait_done(self, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            _, raw = self._get("/api/status")
            snap = json.loads(raw)
            if snap["done"]:
                return snap
            time.sleep(0.2)
        raise AssertionError("job did not finish in time")

    def test_index_page_contains_app_shell(self):
        status, body = self._get("/")
        html = body.decode("utf-8")
        for needle in ["AC-Downloader", "/api/start", "/api/status", "stepper", "باز کردن پوشه"]:
            self.assertIn(needle, html)

    def test_status_snapshot_shape(self):
        _, raw = self._get("/api/status?after=0")
        snap = json.loads(raw)
        for key in ("logs", "logEnd", "phase", "pct", "message", "done", "success", "files"):
            self.assertIn(key, snap)

    def test_invalid_url_job_completes_as_failure_with_reason(self):
        status, j = self._post("/api/start", {"url": "not-a-valid-url"})
        self.assertTrue(j["success"])
        snap = self._wait_done()
        self.assertFalse(snap["success"])
        self.assertNotEqual(snap["message"].strip(), "")

    def test_about_endpoint_has_developer(self):
        with urllib.request.urlopen(self.base + "/api/about", timeout=10) as r:
            about = json.loads(r.read())
        self.assertEqual(about["developer"]["handle"], "arian13es")
        self.assertEqual(about["developer"]["github"], "https://github.com/arian13es")
        self.assertTrue(about["version"])
        self.assertIn("FFmpeg", about["stack"])

    def test_history_endpoint_returns_list(self):
        with urllib.request.urlopen(self.base + "/api/history", timeout=10) as r:
            hist = json.loads(r.read())
        self.assertIn("files", hist)
        self.assertIsInstance(hist["files"], list)

    def test_favicon_served_or_empty(self):
        try:
            with urllib.request.urlopen(self.base + "/favicon.ico", timeout=10) as r:
                self.assertIn(r.status, (200, 204))
                if r.status == 200:
                    self.assertGreater(len(r.read()), 100)
        except urllib.error.HTTPError as e:
            self.fail(f"favicon endpoint failed: {e.code}")

    def test_cancel_endpoint(self):
        status, j = self._post("/api/cancel")
        self.assertEqual(status, 200)
        self.assertTrue(j["success"])

    def test_concurrent_start_rejected_while_running(self):
        self._post("/api/start", {"url": "https://example.com/jobA/?session=x"})
        time.sleep(0.3)  # let worker start; example.com job fails fast but may linger briefly
        if not web_ui.STATE.done:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._post("/api/start", {"url": "https://example.com/jobB/"})
            self.assertEqual(cm.exception.code, 409)
            web_ui.STATE.cancel_event.set()
            self._wait_done()


if __name__ == "__main__":
    unittest.main()
