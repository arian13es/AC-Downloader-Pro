import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from utils.ffmpeg_utils import FFmpegUtils, MediaStreamInfo
from utils.logger import logger

class SegmentType(Enum):
    AUDIO_VOIP = "audio_voip"
    SCREENSHARE = "screenshare"
    CAMERA_VIDEO = "camera_video"
    MAINSTREAM = "mainstream"
    DOCUMENT = "document"
    GENERIC = "generic"

@dataclass
class StreamSegment:
    name: str
    file_path: Path
    xml_path: Path | None
    segment_type: SegmentType
    start_time_ms: int = 0
    duration_ms: int = 0
    info: MediaStreamInfo = field(default_factory=MediaStreamInfo)

    @property
    def start_time_sec(self) -> float:
        return self.start_time_ms / 1000.0

    @property
    def duration_sec(self) -> float:
        return self.duration_ms / 1000.0 if self.duration_ms > 0 else self.info.duration

@dataclass
class RecordingTimeline:
    total_duration_sec: float = 0.0
    audio_segments: list[StreamSegment] = field(default_factory=list)
    screenshare_segments: list[StreamSegment] = field(default_factory=list)
    camera_segments: list[StreamSegment] = field(default_factory=list)
    document_segments: list[StreamSegment] = field(default_factory=list)
    all_segments: list[StreamSegment] = field(default_factory=list)
    # Chronological Share-pod beats: (time, document, page) reconstructed from
    # ftcontent*.xml + ContentManager registry. Empty when unavailable.
    slide_events: list = field(default_factory=list)

class TimelineParser:
    @staticmethod
    def _is_valid_media_file(file_path: Path) -> bool:
        """Validates that a file is a real media file, not an HTML error page or empty stub."""
        if not file_path.exists():
            return False
        size = file_path.stat().st_size
        if size < 1024:  # Files under 1KB cannot be real media
            logger.warning(f"Skipping too-small file ({size} bytes): {file_path.name}")
            return False
        try:
            with open(file_path, "rb") as f:
                header = f.read(256)
            text = header.decode("utf-8", errors="ignore").lower()
            if "<html" in text or "<!doctype" in text or "<head>" in text or "not found" in text:
                logger.warning(f"Skipping HTML error page masquerading as media: {file_path.name}")
                return False
        except Exception:
            return False
        return True

    @staticmethod
    def parse_directory(unpacked_dir: Path) -> RecordingTimeline:
        """Deeply inspects an unpacked Adobe Connect recording folder and builds an accurate timeline."""
        timeline = RecordingTimeline()
        
        all_media = list(unpacked_dir.glob("*.flv")) + list(unpacked_dir.glob("*.mp4"))
        # Filter out invalid files (HTML error pages, empty stubs, etc.)
        flv_files = [f for f in all_media if TimelineParser._is_valid_media_file(f)]
        
        if not flv_files:
            logger.warning(f"No valid FLV/MP4 media files found in {unpacked_dir} "
                          f"({len(all_media)} files checked, all invalid)")
            return timeline
            
        stream_map: dict[str, StreamSegment] = {}
        
        for mf in flv_files:
            stem = mf.stem
            xml_sidecar = unpacked_dir / f"{stem}.xml"
            
            seg_type = SegmentType.GENERIC
            if stem.startswith("cameraVoip"):
                seg_type = SegmentType.AUDIO_VOIP
            elif stem.startswith("screenshare"):
                seg_type = SegmentType.SCREENSHARE
            elif stem.startswith("ftvideo") or (stem.startswith("camera_") and not stem.startswith("cameraVoip")):
                seg_type = SegmentType.CAMERA_VIDEO
            elif stem.startswith("mainstream"):
                seg_type = SegmentType.MAINSTREAM
                
            info = FFmpegUtils.probe_media_file(mf)
            
            # Skip segments where ffprobe couldn't detect any streams (corrupt file)
            if not info.has_audio and not info.has_video and info.duration <= 0:
                logger.warning(f"Skipping unreadable media file (no streams detected): {mf.name}")
                continue
                
            seg = StreamSegment(
                name=stem,
                file_path=mf,
                xml_path=xml_sidecar if xml_sidecar.exists() else None,
                segment_type=seg_type,
                info=info,
                duration_ms=int(info.duration * 1000)
            )
            stream_map[stem] = seg

        # Scan for PDF/SWF documents (Slides)
        document_files = list(unpacked_dir.rglob("slide_*.pdf")) + list(unpacked_dir.rglob("slide_*.swf"))
        # Exclude temporary source files in src dirs if a main slide exists
        document_files = sorted([f for f in document_files if f.stat().st_size > 1024 and "_temp" not in f.name])
        
        for doc in document_files:
            stem = doc.stem
            # Usually we don't have FFprobe info for PDFs/SWFs, so we create a basic segment
            seg = StreamSegment(
                name=stem,
                file_path=doc,
                xml_path=None,
                segment_type=SegmentType.DOCUMENT,
                duration_ms=0
            )
            stream_map[stem] = seg

        mainstream_xml = unpacked_dir / "mainstream.xml"
        if mainstream_xml.exists():
            TimelineParser._parse_mainstream_xml(mainstream_xml, stream_map)

        # A segment is usable only if ffprobe detected real streams in it, or if it
        # is a document asset (PDF/SWF slide). This prevents dummy/placeholder
        # tracks from being mapped into FFmpeg filter graphs as audio inputs.
        all_segs = [
            s for s in stream_map.values()
            if s.info.has_audio or s.info.has_video or s.segment_type == SegmentType.DOCUMENT
        ]
        all_segs.sort(key=lambda s: s.start_time_ms)
        timeline.all_segments = all_segs

        for s in all_segs:
            # Membership requires an actually detected audio stream. cameraVoip files
            # that carry ONLY a placeholder video track must not become audio inputs,
            # or ffmpeg will fail with "Stream map matches no streams".
            if s.info.has_audio:
                timeline.audio_segments.append(s)
            if s.segment_type == SegmentType.SCREENSHARE:
                timeline.screenshare_segments.append(s)
            elif s.segment_type == SegmentType.CAMERA_VIDEO:
                timeline.camera_segments.append(s)
            elif s.segment_type == SegmentType.DOCUMENT:
                timeline.document_segments.append(s)

        max_end_time = 0.0
        for s in all_segs:
            end_sec = s.start_time_sec + s.duration_sec
            if end_sec > max_end_time:
                max_end_time = end_sec
                
        for meta_name in ["indexstream.xml", "mainstream.xml"]:
            meta_p = unpacked_dir / meta_name
            if meta_p.exists():
                try:
                    tree = ET.parse(str(meta_p))
                    for num in tree.getroot().iter("Number"):
                        if num.text:
                            try:
                                val = float(num.text)
                                if 60.0 < val < 86400.0 and val > max_end_time:
                                    max_end_time = val
                                    break
                            except ValueError:
                                pass
                except Exception:
                    pass

        timeline.total_duration_sec = max_end_time

        # Reconstruct the Share-pod slide schedule (which PDF page was visible when).
        try:
            from core.slide_timeline import parse_slide_schedule
            timeline.slide_events = parse_slide_schedule(unpacked_dir)
        except Exception as e:
            logger.warning(f"Slide schedule reconstruction failed: {e}")

        logger.info(
            f"Timeline parsed: Total duration = {max_end_time:.2f}s | "
            f"Audio tracks: {len(timeline.audio_segments)}, "
            f"Screenshares: {len(timeline.screenshare_segments)}, "
            f"Cameras: {len(timeline.camera_segments)}"
        )
        return timeline

    @staticmethod
    def _parse_mainstream_xml(xml_path: Path, stream_map: dict[str, StreamSegment]) -> None:
        """Extracts precise event timestamps from mainstream.xml."""
        try:
            tree = ET.parse(str(xml_path))
            root = tree.getroot()
            
            stream_starts = {}
            stream_stops = {}
            
            for msg in root.iter("Message"):
                t_msg = int(msg.attrib.get("time", 0))
                xml_str = ET.tostring(msg, encoding="utf-8").decode("utf-8", errors="ignore")

                # Check stream names
                for name in stream_map:
                    if f">{name}<" in xml_str or f"CDATA[/{name}]" in xml_str or f"CDATA[{name}]" in xml_str or f">{name}_" in xml_str or f"/{name}<" in xml_str:
                        time_obj = msg.find(".//Object/time")
                        inner_time = int(time_obj.text) if time_obj is not None and time_obj.text and time_obj.text.isdigit() else t_msg

                        if "streamCreated" in xml_str and name not in stream_starts:
                            stream_starts[name] = inner_time
                        elif "streamRemoved" in xml_str:
                            stream_stops[name] = inner_time

            # Real Connect servers report each stream's start clock inside playEvent objects:
            # <Object><startTime>3128642</startTime><streamName>/cameraVoip_0_4</streamName>...</Object>
            # (the streamCreated/streamRemoved vocabulary above does not exist on these recordings)
            for obj in root.iter("Object"):
                sn = obj.find("streamName")
                if sn is None or not sn.text:
                    continue
                name = sn.text.strip().lstrip("/")
                if name not in stream_map:
                    continue
                st = obj.find("startTime")
                if st is not None and st.text and st.text.strip().isdigit():
                    t = int(st.text.strip())
                    if name not in stream_starts or t < stream_starts[name]:
                        stream_starts[name] = t
                            
            for name, seg in stream_map.items():
                if name in stream_starts:
                    seg.start_time_ms = stream_starts[name]
                if name in stream_stops and stream_stops[name] > seg.start_time_ms:
                    # Precise absolute duration based on server events!
                    actual_duration = stream_stops[name] - seg.start_time_ms
                    # Bound it by physical file duration just in case of weird server bugs
                    seg.duration_ms = min(actual_duration, int(seg.info.duration * 1000))
                    
        except Exception as e:
            logger.warning(f"Error parsing mainstream.xml timestamps: {e}")
