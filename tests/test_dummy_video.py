import unittest
from pathlib import Path
from tempfile import mkdtemp
from unittest.mock import patch

from core.composer import VideoComposer
from core.config import AppConfig
from core.timeline_parser import RecordingTimeline, SegmentType, StreamSegment
from utils.ffmpeg_utils import FFmpegUtils, MediaStreamInfo
from utils.logger import logger

logger.disabled = True


def _audio_seg(name="cameraVoip_0_0", dummy_video=False):
    info = MediaStreamInfo(
        has_audio=True,
        has_video=dummy_video,
        width=160 if dummy_video else 0,
        height=120 if dummy_video else 0,
        is_dummy_video=dummy_video,
        duration=30.0,
    )
    return StreamSegment(
        name=name,
        file_path=Path("mock.flv"),
        xml_path=None,
        segment_type=SegmentType.AUDIO_VOIP,
        info=info,
        duration_ms=30000,
    )


class TestDummyVideoRouting(unittest.TestCase):
    """A 160x120 placeholder video track must never be composited as a visible layer."""

    def setUp(self):
        self.composer = VideoComposer(AppConfig())
        self.out = Path(mkdtemp()) / "out.mp4"

    def test_dummy_only_camera_routes_to_backdrop_path(self):
        tl = RecordingTimeline(total_duration_sec=30.0)
        seg = _audio_seg(dummy_video=True)
        tl.audio_segments.append(seg)
        # Simulate a parser that (wrongly) also listed it as a camera track
        tl.camera_segments.append(seg)

        with patch.object(VideoComposer, "_compose_audio_with_backdrop", return_value=True) as m_backdrop:
            ok = self.composer.convert_recording(tl, self.out)

        self.assertTrue(ok)
        m_backdrop.assert_called_once()

    def test_real_camera_stays_in_video_pipeline(self):
        tl = RecordingTimeline(total_duration_sec=30.0)
        tl.audio_segments.append(_audio_seg())
        real_cam = StreamSegment(
            name="ftvideo_1",
            file_path=Path("cam.flv"),
            xml_path=None,
            segment_type=SegmentType.CAMERA_VIDEO,
            info=MediaStreamInfo(has_video=True, has_audio=False, width=1280, height=720, duration=30.0),
            duration_ms=30000,
        )
        tl.camera_segments.append(real_cam)

        with patch.object(VideoComposer, "_compose_audio_with_backdrop", return_value=True) as m_backdrop, \
             patch.object(FFmpegUtils, "run_ffmpeg_with_progress", return_value=True), \
             patch.object(FFmpegUtils, "detect_best_video_encoder", return_value="libx264"):
            self.composer.convert_recording(tl, self.out)

        m_backdrop.assert_not_called()


class TestProbeDummyDetection(unittest.TestCase):
    """ffprobe-based heuristic: <=320x240 video-only tracks are flagged as dummies."""

    temp_dir: Path

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = Path(mkdtemp())
        ffmpeg = FFmpegUtils.find_executable("ffmpeg")
        for name, size in (("dummy.mp4", "160x120"), ("real.mp4", "1280x720")):
            FFmpegUtils.run_ffmpeg_with_progress(
                [ffmpeg, "-f", "lavfi", "-i", f"color=c=black:s={size}:d=0.5", "-an", str(cls.temp_dir / name)],
                1.0,
                None,
            )

    def test_tiny_track_flagged_dummy(self):
        info = FFmpegUtils.probe_media_file(self.temp_dir / "dummy.mp4")
        self.assertTrue(info.has_video)
        self.assertTrue(info.is_dummy_video)

    def test_normal_resolution_not_dummy(self):
        info = FFmpegUtils.probe_media_file(self.temp_dir / "real.mp4")
        self.assertTrue(info.has_video)
        self.assertFalse(info.is_dummy_video)


if __name__ == "__main__":
    unittest.main()
