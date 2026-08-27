from dataclasses import dataclass, field
import os
import sys
from pathlib import Path
from typing import Literal


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _default_downloads_dir() -> Path:
    if _is_frozen():
        # Installed builds write to the user's Documents — visible & portable.
        return Path.home() / "Documents" / "AC-Downloader"
    return Path(__file__).resolve().parent.parent / "downloads"


def _default_temp_dir() -> Path:
    if _is_frozen():
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "AC-Downloader" / "temp"
    return Path(__file__).resolve().parent.parent / "temp"


@dataclass
class AppConfig:
    # Directories
    base_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    downloads_dir: Path = field(default_factory=_default_downloads_dir)
    temp_dir: Path = field(default_factory=_default_temp_dir)

    @property
    def tools_dir(self) -> Path:
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            return Path(sys._MEIPASS) / "tools"
        return self.base_dir / "tools"
    
    # Network & Download Settings
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    request_timeout: int = 30
    max_retries: int = 4
    chunk_size: int = 1024 * 256  # 256 KB
    concurrent_workers: int = 4
    verify_ssl: bool = False  # Many university subdomains have self-signed/expired certs
    
    # Video & Conversion Settings
    @property
    def ffmpeg_path(self) -> str:
        local_bin = self.tools_dir / "ffmpeg.exe"
        return str(local_bin) if local_bin.exists() else "ffmpeg"
        
    @property
    def ffprobe_path(self) -> str:
        local_bin = self.tools_dir / "ffprobe.exe"
        return str(local_bin) if local_bin.exists() else "ffprobe"

    @property
    def swfrender_path(self) -> str:
        local_bin = self.tools_dir / "swfrender.exe"
        return str(local_bin) if local_bin.exists() else "swfrender"

    video_codec: str = "libx264"
    audio_codec: str = "aac"
    audio_bitrate: str = "128k"
    crf: int = 23
    preset: str = "fast"
    resolution: str = "1920x1080"  # Options: original, 1920x1080, 1280x720, 854x480
    fps: int = 30
    layout_mode: Literal["smart_pip", "side_by_side", "screenshare_only", "camera_only", "audio_only"] = "smart_pip"
    hwaccel: Literal["auto", "nvenc", "qsv", "amf", "none"] = "auto"
    keep_raw_files: bool = True

    def ensure_directories(self) -> None:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = AppConfig()
