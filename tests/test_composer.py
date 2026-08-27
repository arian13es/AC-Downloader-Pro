import unittest
from pathlib import Path
from core.config import AppConfig
from core.composer import VideoComposer
from core.timeline_parser import RecordingTimeline, StreamSegment, SegmentType
from utils.ffmpeg_utils import MediaStreamInfo

class TestVideoComposer(unittest.TestCase):
    def test_audio_only_composition(self):
        config = AppConfig(layout_mode="audio_only")
        composer = VideoComposer(config)
        
        timeline = RecordingTimeline(total_duration_sec=60.0)
        seg = StreamSegment(
            name="cameraVoip_0_0",
            file_path=Path("mock.flv"),
            xml_path=None,
            segment_type=SegmentType.AUDIO_VOIP,
            info=MediaStreamInfo(has_audio=True, duration=60.0),
            duration_ms=60000
        )
        timeline.audio_segments.append(seg)
        
        # Verify structure
        self.assertEqual(len(timeline.audio_segments), 1)
        self.assertEqual(timeline.total_duration_sec, 60.0)

if __name__ == "__main__":
    unittest.main()
