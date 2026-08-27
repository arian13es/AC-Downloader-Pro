import json
import re
import shutil
import subprocess
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from utils.logger import logger

@dataclass
class MediaStreamInfo:
    has_video: bool = False
    has_audio: bool = False
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_codec: str = ""
    audio_codec: str = ""
    sample_rate: int = 44100
    channels: int = 2
    # Adobe Connect cameraVoip streams often carry a placeholder video track
    # (typically 160x120 solid black). Such tracks must never be used as a
    # visible layer; they exist only to keep the FLV container well-formed.
    is_dummy_video: bool = False

class FFmpegUtils:
    @staticmethod
    def find_executable(name: str) -> str:
        """Finds the absolute path to ffmpeg or ffprobe binary."""
        path = shutil.which(name)
        if path:
            return path
        candidates = [
            Path(r"C:\ffmpeg\bin") / f"{name}.exe",
            Path(r"C:\Program Files\ffmpeg\bin") / f"{name}.exe",
        ]
        for c in candidates:
            if c.is_file():
                return str(c)
        return name

    @staticmethod
    def detect_best_video_encoder(requested: str = "auto") -> str:
        """Detects available hardware video encoders (NVENC, QSV, AMF) or falls back to libx264."""
        if requested not in ["auto", "none"]:
            return f"h264_{requested}"
        if requested == "none":
            return "libx264"
            
        ffmpeg_bin = FFmpegUtils.find_executable("ffmpeg")
        try:
            res = subprocess.run(
                [ffmpeg_bin, "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                check=False
            )
            encoders = res.stdout.lower()
            if "h264_nvenc" in encoders:
                test = subprocess.run(
                    [ffmpeg_bin, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-"],
                    capture_output=True,
                    check=False
                )
                if test.returncode == 0:
                    return "h264_nvenc"
            if "h264_qsv" in encoders:
                test = subprocess.run(
                    [ffmpeg_bin, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-c:v", "h264_qsv", "-f", "null", "-"],
                    capture_output=True,
                    check=False
                )
                if test.returncode == 0:
                    return "h264_qsv"
            if "h264_amf" in encoders:
                test = subprocess.run(
                    [ffmpeg_bin, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-c:v", "h264_amf", "-f", "null", "-"],
                    capture_output=True,
                    check=False
                )
                if test.returncode == 0:
                    return "h264_amf"
        except Exception as e:
            logger.debug(f"Encoder detection error: {e}")
            
        return "libx264"

    @staticmethod
    def probe_media_file(file_path: Path) -> MediaStreamInfo:
        """Extracts technical stream information.

        FLV files are parsed with the pure-Python inspector first (immune to
        child-process spawn failures like 0xc0000142 / Smart App Control);
        ffprobe is only the fallback for exotic containers.
        """
        info = MediaStreamInfo()
        if not file_path.exists() or file_path.stat().st_size == 0:
            return info

        # Adobe Connect domain knowledge: these streams are pure AMF metadata
        # (chat / slide events / layout / index / transcript). They never carry
        # audio or video, and probing them only produces false positives.
        stem = file_path.stem.lower()
        if stem.startswith(("ftchat", "ftcontent", "ftstage", "ftvideo",
                            "indexstream", "mainstream", "transcriptstream")):
            return info

        if file_path.suffix.lower() == ".flv":
            try:
                from utils.flv_probe import probe_flv
                info = probe_flv(file_path)
                if info.has_audio or info.has_video or info.duration > 0:
                    if not info.has_audio and not info.has_video:
                        logger.warning(f"flv-probe: no media streams in {file_path.name} "
                                       f"({file_path.stat().st_size} bytes) — data-only stream")
                    return info
            except Exception as e:
                logger.warning(f"flv-probe failed for {file_path.name}: {e} — falling back to ffprobe")

        ffprobe_bin = FFmpegUtils.find_executable("ffprobe")
        cmd = [
            ffprobe_bin,
            "-v", "error",
            "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
            "-of", "json",
            str(file_path)
        ]
        
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(res.stdout)
            
            if "format" in data and "duration" in data["format"]:
                try:
                    info.duration = float(data["format"]["duration"])
                except ValueError:
                    pass
                    
            for s in data.get("streams", []):
                ctype = s.get("codec_type")
                if ctype == "video":
                    w = int(s.get("width", 0))
                    h = int(s.get("height", 0))
                    if w > 0 and h > 0:
                        info.has_video = True
                        info.video_codec = s.get("codec_name", "")
                        info.width = w
                        info.height = h
                        r_fps = s.get("r_frame_rate", "30/1")
                        if "/" in r_fps:
                            num, den = r_fps.split("/")
                            if float(den) > 0:
                                info.fps = float(num) / float(den)
                        else:
                            info.fps = float(r_fps)
                elif ctype == "audio":
                    info.has_audio = True
                    info.audio_codec = s.get("codec_name", "")
                    info.sample_rate = int(s.get("sample_rate", 44100))
                    info.channels = int(s.get("channels", 2))

            # Heuristic dummy-track detection: real webcams/screenshares are never
            # this small; 160x120 is the canonical Adobe Connect placeholder.
            if info.has_video and 0 < info.width <= 320 and 0 < info.height <= 240:
                info.is_dummy_video = True

            if not info.has_audio and not info.has_video:
                logger.warning(f"ffprobe: no streams detected in {file_path.name} "
                               f"({file_path.stat().st_size} bytes)")

        except subprocess.CalledProcessError as e:
            stderr_tail = (e.stderr or "").strip()[-200:]
            logger.warning(f"ffprobe failed for {file_path.name} "
                           f"(exit {e.returncode}): {stderr_tail}")
        except Exception as e:
            logger.warning(f"ffprobe failed for {file_path.name}: {e}")

        return info

    @staticmethod
    def run_ffmpeg_with_progress(
        cmd: list[str],
        total_duration_sec: float,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> bool:
        """Executes FFmpeg safely, parses stderr stream metrics live, and avoids pipe deadlocks."""
        if not cmd:
            return False

        logger.debug(f"Executing FFmpeg: {' '.join(cmd)}")

        full_cmd = [cmd[0], "-y", "-nostdin"] + cmd[1:]

        try:
            process = subprocess.Popen(
                full_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
                bufsize=1
            )

            time_re = re.compile(r"time=(-?\d+):(\d+):(\d+(?:\.\d+)?)")
            speed_re = re.compile(r"speed=\s*([\d\.]+)x")
            err_tail: deque = deque(maxlen=30)

            if process.stderr:
                for line in iter(process.stderr.readline, ""):
                    err_tail.append(line.rstrip())
                    t_match = time_re.search(line)
                    s_match = speed_re.search(line)
                    if t_match and total_duration_sec > 0:
                        h, m, s = [float(x) for x in t_match.groups()]
                        cur_sec = h * 3600.0 + m * 60.0 + s
                        pct = min(99.0, max(0.0, (cur_sec / total_duration_sec) * 100.0))
                        speed_str = s_match.group(1) if s_match else "1.0"
                        if progress_callback:
                            progress_callback(pct, f"Synthesizing: {pct:.1f}% ({speed_str}x speed)")

            process.wait()

            if process.returncode != 0:
                logger.error(f"FFmpeg exited with code {process.returncode}")
                for line in list(err_tail)[-12:]:
                    logger.error(f"  ffmpeg| {line}")
                return False
                
            if progress_callback:
                progress_callback(100.0, "Conversion complete (100%)")
            return True
            
        except Exception as e:
            logger.error(f"FFmpeg execution error: {e}")
            return False
