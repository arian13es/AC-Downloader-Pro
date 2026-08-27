import io
import json
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import mkdtemp
from threading import Thread

from core.downloader import RecordingDownloader
from core.session import HTTPSession
from core.url_parser import ParsedMeetingURL
from utils.logger import logger

logger.disabled = True

SWF_BYTES = b"FWS" + b"\x06\x00\x00\x00\x00" + b"\x00" * 2048  # >1KB: must pass slide size sanity checks
HTML_LOGIN = b"<html><head><title>Not Authorized</title></head><body>login required</body></html>"

METADATA_XML = """<?xml version="1.0" encoding="utf-8"?>
<root>
  <Message time="1000" type="data">
    <documentDescriptor>
      <id><![CDATA[pDOC1]]></id>
      <name><![CDATA[Lecture Slides]]></name>
      <contentOutputPath><![CDATA[/walled/doc1/]]></contentOutputPath>
      <playbackContentOutputPath><![CDATA[/play/doc1/]]></playbackContentOutputPath>
    </documentDescriptor>
    <documentDescriptor>
      <id>pDOC2</id>
      <name>Backup.pdf</name>
      <contentOutputPath>/walled/doc2/</contentOutputPath>
      <playbackContentOutputPath>/walled/doc2player/</playbackContentOutputPath>
    </documentDescriptor>
  </Message>
</root>"""


def build_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("10.swf", SWF_BYTES)
        zf.writestr("2.swf", SWF_BYTES)
        zf.writestr("notes.txt", "not a slide")
    return buf.getvalue()


class FakeACServerHandler(BaseHTTPRequestHandler):
    # path -> (status, content_type, body)
    routes: dict = {}

    def do_GET(self):
        path = self.path.split("?")[0]
        entry = self.routes.get(path)
        if entry is None:
            self.send_response(404)
            self.end_headers()
            return
        status, ctype, body = entry
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class TestSlideCascade(unittest.TestCase):
    server: ThreadingHTTPServer
    base_url: str
    temp_dir: Path

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeACServerHandler)
        port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{port}"

        swf = ("application/x-shockwave-flash", SWF_BYTES)
        html = ("text/html", HTML_LOGIN)
        zip_body = ("application/zip", build_zip())

        FakeACServerHandler.routes = {
            "/play/doc1/1.swf": (200,) + swf,
            "/play/doc1/2.swf": (200,) + swf,
            # /play/doc1/3.swf intentionally missing -> series stops at slide 2
            "/walled/doc1/1.swf": (200,) + html,          # permission-walled
            "/walled/doc2player/1.swf": (200,) + html,    # permission-walled
            "/walled/doc2/1.swf": (200,) + html,          # permission-walled
            "/source/pDOC2.zip": (200, "application/zip", build_zip()),
        }

        Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.temp_dir = Path(mkdtemp())

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _make_meeting(self) -> ParsedMeetingURL:
        return ParsedMeetingURL(
            original_url=f"{self.base_url}/rec123",
            scheme="http",
            host="127.0.0.1",
            port=self.server.server_address[1],
            path="rec123",
            meeting_id="rec123",
            base_url=self.base_url,
            room_url=f"{self.base_url}/rec123",
            session_token=None,
            output_zip_url="",
            mainstream_url="",
        )

    def test_descriptor_parsing_handles_cdata_and_plain_tags(self):
        dl = RecordingDownloader(HTTPSession())
        descs = dl._parse_document_descriptors(METADATA_XML)
        self.assertEqual(len(descs), 2)
        self.assertEqual(descs[0]["id"], "pDOC1")
        self.assertEqual(descs[0]["playbackContentOutputPath"], "/play/doc1/")
        self.assertEqual(descs[1]["id"], "pDOC2")
        self.assertEqual(descs[1]["contentOutputPath"], "/walled/doc2/")

    def test_cascade_tier1_playback_then_tier3_zip_fallback(self):
        dl = RecordingDownloader(HTTPSession())
        meeting = self._make_meeting()
        target = self.temp_dir / "cascade_run"
        target.mkdir(parents=True, exist_ok=True)

        saved = dl.fetch_slide_backdrops(meeting, target, METADATA_XML)
        names = sorted(p.name for p in saved)

        # Doc 1 served from public playback path, series stops at first miss (slide 3)
        self.assertIn("slide_001_n01.swf", names)
        self.assertIn("slide_001_n02.swf", names)
        self.assertNotIn("slide_001_n03.swf", names)
        # Doc 2 walled on both direct paths -> recovered via /source/{id}.zip,
        # natural-sorted so 2.swf precedes 10.swf
        self.assertEqual(names[-2:], ["slide_002_z01.swf", "slide_002_z02.swf"])

        # No probe temp files may linger (they would pollute the timeline scan)
        leftovers = [p.name for p in target.iterdir() if "_temp" in p.name]
        self.assertEqual(leftovers, [])

    def test_all_paths_walled_returns_empty_without_crash(self):
        dl = RecordingDownloader(HTTPSession())
        meeting = self._make_meeting()
        xml = METADATA_XML.replace("/play/doc1/", "/walled/doc1b/").replace("pDOC2", "pMISSING")
        target = self.temp_dir / "walled_run"
        target.mkdir(parents=True, exist_ok=True)
        saved = dl.fetch_slide_backdrops(meeting, target, xml)
        self.assertEqual(saved, [])

    def test_session_token_appended_query_safe(self):
        self.assertEqual(
            RecordingDownloader._append_session("http://h/a.zip?download=zip", "tok"),
            "http://h/a.zip?download=zip&session=tok",
        )
        self.assertEqual(
            RecordingDownloader._append_session("http://h/1.swf", "tok"),
            "http://h/1.swf?session=tok",
        )
        self.assertEqual(RecordingDownloader._append_session("http://h/1.swf", None), "http://h/1.swf")


if __name__ == "__main__":
    unittest.main()
