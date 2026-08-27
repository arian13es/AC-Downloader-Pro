from dataclasses import dataclass
from enum import Enum
from core.session import HTTPSession
from core.url_parser import ParsedMeetingURL
from utils.logger import logger

class RecordingType(Enum):
    ZIP_ARCHIVE = "zip_archive"
    COMPONENT_DIR = "component_dir"
    DIRECT_MP4 = "direct_mp4"
    AUTH_REQUIRED = "auth_required"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"

@dataclass
class ProbeResult:
    recording_type: RecordingType
    download_url: str | None = None
    direct_mp4_url: str | None = None
    content_length: int | None = None
    title: str = ""
    error_message: str | None = None

class RecordingProbe:
    @staticmethod
    def probe(session: HTTPSession, meeting: ParsedMeetingURL) -> ProbeResult:
        """Probes the Adobe Connect recording server to identify format and accessibility."""
        # 1. First probe the standard ZIP download endpoint
        try:
            r_zip = session.get(meeting.output_zip_url, headers={"Range": "bytes=0-1024"}, timeout=15)
            ctype = r_zip.headers.get("content-type", "").lower()
            
            if "zip" in ctype or "octet-stream" in ctype or "application/x-zip-compressed" in ctype:
                clen_hdr = r_zip.headers.get("content-length")
                clen = int(clen_hdr) if clen_hdr and clen_hdr.isdigit() else None
                logger.info(f"Target identified: Full ZIP package available ({meeting.output_zip_url})")
                r_zip.raw.close()  # Close stream to prevent downloading the file body
                return ProbeResult(
                    recording_type=RecordingType.ZIP_ARCHIVE,
                    download_url=meeting.output_zip_url,
                    content_length=clen,
                    title=meeting.meeting_id
                )
            
            text_preview = r_zip.raw.read(1024).decode('utf-8', errors='ignore').lower()
            r_zip.raw.close()
            
            if "not authorized" in text_preview:
                return ProbeResult(
                    recording_type=RecordingType.AUTH_REQUIRED,
                    error_message="Authentication failed or session token has expired on the server."
                )
        except Exception as e:
            logger.debug(f"Zip probe failed: {e}")

        # 2. Check if mainstream.xml is directly accessible
        try:
            r_xml = session.get(meeting.mainstream_url, timeout=15)
            if r_xml.status_code == 200 and ("<Message" in r_xml.text or "<root" in r_xml.text or "<stream" in r_xml.text):
                logger.info("Target identified: Individual Component directory")
                return ProbeResult(
                    recording_type=RecordingType.COMPONENT_DIR,
                    download_url=f"{meeting.room_url}/output/",
                    title=meeting.meeting_id
                )
            elif "not authorized" in r_xml.text.lower():
                return ProbeResult(
                    recording_type=RecordingType.AUTH_REQUIRED,
                    error_message="Session expired or not authorized."
                )
        except Exception as e:
            logger.debug(f"Mainstream XML probe failed: {e}")

        # 3. Check room page for direct MP4 stream or Enhanced AV
        try:
            r_room = session.get(meeting.room_url, timeout=15)
            if "not authorized" in r_room.text.lower() or "denied" in r_room.text.lower():
                return ProbeResult(
                    recording_type=RecordingType.AUTH_REQUIRED,
                    error_message="Session ticket invalid or expired. Refresh your LMS session."
                )
            if "<title>Not Found</title>" in r_room.text:
                return ProbeResult(
                    recording_type=RecordingType.NOT_FOUND,
                    error_message="Recording room was not found on this server."
                )
        except Exception as e:
            return ProbeResult(recording_type=RecordingType.UNKNOWN, error_message=str(e))

        return ProbeResult(
            recording_type=RecordingType.UNKNOWN,
            error_message="Unable to detect recording format. Check URL and session validity."
        )
