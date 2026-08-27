import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch
from core.timeline_parser import TimelineParser, SegmentType
from utils.ffmpeg_utils import MediaStreamInfo

class TestTimelineParser(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())

    def test_xml_parsing(self):
        # Create a mock mainstream.xml using the REAL Adobe Connect playEvent vocabulary
        xml_content = '''<?xml version="1.0" encoding="utf-8"?>
<root>
    <Message time="0" type="data">
        <Method><![CDATA[playEvent]]></Method>
        <Array>
            <Object>
                <startTime><![CDATA[0]]></startTime>
                <streamId><![CDATA[cameraVoip_0]]></streamId>
                <streamName><![CDATA[/cameraVoip_0_0]]></streamName>
                <streamType><![CDATA[cameraVoip]]></streamType>
            </Object>
        </Array>
    </Message>
    <Message time="12000" type="data">
        <Method><![CDATA[playEvent]]></Method>
        <Array>
            <Object>
                <startTime><![CDATA[12000]]></startTime>
                <streamId><![CDATA[screenshare_1]]></streamId>
                <streamName><![CDATA[/screenshare_1_0]]></streamName>
                <streamType><![CDATA[screenshare]]></streamType>
            </Object>
        </Array>
    </Message>
</root>'''
        (self.temp_dir / "mainstream.xml").write_text(xml_content, encoding="utf-8")
        
        # Create mock media files with FLV magic header (must be >1KB to pass validation)
        flv_header = b"FLV\x01\x05\x00\x00\x00\x09" + b"\x00" * 1024
        (self.temp_dir / "cameraVoip_0_0.flv").write_bytes(flv_header)
        (self.temp_dir / "screenshare_1_0.flv").write_bytes(flv_header)
        
        # Mock probe_media_file since test FLV stubs have no real streams
        mock_audio_info = MediaStreamInfo(has_audio=True, duration=60.0, audio_codec="nellymoser")
        mock_video_info = MediaStreamInfo(has_video=True, has_audio=False, duration=60.0,
                                          video_codec="vp6f", width=1280, height=720)
        
        def fake_probe(path):
            if "cameraVoip" in path.name:
                return mock_audio_info
            return mock_video_info

        with patch("core.timeline_parser.FFmpegUtils.probe_media_file", side_effect=fake_probe):
            timeline = TimelineParser.parse_directory(self.temp_dir)
        
        self.assertEqual(len(timeline.all_segments), 2)
        
        cam_seg = next(s for s in timeline.all_segments if s.name == "cameraVoip_0_0")
        scr_seg = next(s for s in timeline.all_segments if s.name == "screenshare_1_0")
        
        self.assertEqual(cam_seg.start_time_ms, 0)
        self.assertEqual(scr_seg.start_time_ms, 12000)
        self.assertEqual(scr_seg.segment_type, SegmentType.SCREENSHARE)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
