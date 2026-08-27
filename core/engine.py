import time
import threading
from pathlib import Path
from typing import Callable, Optional
from core.config import AppConfig, DEFAULT_CONFIG
from core.url_parser import URLParser, ParsedMeetingURL
from core.session import SessionManager
from core.auth import Authenticator, AuthResult
from core.probe import RecordingProbe, RecordingType, ProbeResult
from core.downloader import RecordingDownloader
from core.timeline_parser import TimelineParser
from core.composer import VideoComposer
from utils.cleaner import WorkspaceCleaner
from utils.logger import logger


class JobCancelled(Exception):
    """Raised when the user requests cancellation at a phase boundary."""


class ACDownloadEngine:
    def __init__(self, config: AppConfig = DEFAULT_CONFIG):
        self.config = config
        self.config.ensure_directories()

    def process_recording(
        self,
        url: str,
        cookie: str | None = None,
        username: str | None = None,
        password: str | None = None,
        output_format: str = "mp4",
        progress_callback: Optional[Callable[[str, float, str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Path | None:
        """Complete end-to-end pipeline: Parse -> Auth -> Probe -> Download -> Synchronize -> Convert.

        Args:
            cancel_event: when set, the pipeline aborts cleanly at the next phase boundary.
        """

        def notify(phase: str, pct: float, msg: str):
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelled("Cancellation requested by user.")
            if progress_callback:
                progress_callback(phase, pct, msg)
                
        notify("INIT", 0.0, "Parsing meeting URL...")
        meeting = URLParser.parse(url)
        session = SessionManager.create_session(self.config)
        
        # 1. Authentication & Session Setup
        notify("AUTH", 10.0, "Configuring authentication...")
        if cookie:
            auth_res = Authenticator.authenticate_with_cookie(session, cookie, domain=meeting.host)
            if not auth_res.success:
                logger.warning(f"Cookie parsing issue: {auth_res.error_message}")
        elif username and password:
            auth_res = Authenticator.login_adobe_connect(session, meeting.base_url, username, password)
            if not auth_res.success:
                logger.warning(f"Adobe Connect API login failed: {auth_res.error_message}")
        elif meeting.session_token:
            Authenticator.authenticate_with_cookie(session, f"BREEZESESSION={meeting.session_token}", domain=meeting.host)
        else:
            Authenticator.probe_guest_access(session, meeting)
            
        # 2. Probing Recording Endpoint
        notify("PROBE", 20.0, "Probing recording format and permissions on server...")
        probe = RecordingProbe.probe(session, meeting)
        
        if probe.recording_type == RecordingType.AUTH_REQUIRED:
            error_msg = (
                "Server returned 'Not Authorized'. Your session token or cookie is missing/expired. "
                "Please copy a fresh link or active BREEZESESSION cookie from your LMS."
            )
            logger.error(error_msg)
            notify("ERROR", 0.0, error_msg)
            return None
            
        if probe.recording_type == RecordingType.NOT_FOUND:
            error_msg = f"Recording '{meeting.meeting_id}' was not found on {meeting.base_url}."
            logger.error(error_msg)
            notify("ERROR", 0.0, error_msg)
            return None
            
        downloader = RecordingDownloader(session, self.config)
        unpacked_dir: Path | None = None
        
        # 3. Downloading Raw Data Package
        try:
            if probe.recording_type == RecordingType.ZIP_ARCHIVE or probe.recording_type == RecordingType.UNKNOWN:
                notify("DOWNLOAD", 30.0, "Downloading raw recording package (ZIP)...")
                
                def dl_cb(curr, tot, speed):
                    mb_curr = curr / (1024 * 1024)
                    mb_tot = tot / (1024 * 1024)
                    sp_mb = speed / (1024 * 1024)
                    if tot > 0:
                        pct = 30.0 + ((curr / tot) * 30.0)
                        notify("DOWNLOAD", pct,
                               f"Downloading: {mb_curr:.1f}/{mb_tot:.1f} MB ({sp_mb:.2f} MB/s)")
                    else:
                        # Server omitted Content-Length: keep the global band
                        # moving gently so the ring never appears stuck at 0.
                        pct = 30.0 + min(29.0, mb_curr * 0.15)
                        notify("DOWNLOAD", pct,
                               f"Downloading: {mb_curr:.1f} MB ({sp_mb:.2f} MB/s)")
                    
                unpacked_dir = downloader.download_and_extract_package(meeting.output_zip_url, meeting.meeting_id, dl_cb)

            if not unpacked_dir:
                notify("DOWNLOAD", 30.0, "Trying direct component stream directory...")
                def comp_cb(curr, tot, speed):
                    pct = 30.0 + ((curr / tot) * 30.0) if tot > 0 else 45.0
                    notify("DOWNLOAD", pct, f"Downloading stream files ({curr}/{tot})...")
                unpacked_dir = downloader.download_component_directory(meeting, comp_cb)

            if not unpacked_dir:
                notify("ERROR", 0.0, "Failed to download recording data from server.")
                return None

            # 3.5 Fetch slides for both download routes (cascade: playback path -> content path -> source ZIP)
            xml_text = ""
            for meta_name in ("mainstream.xml", "document-metadata.xml"):
                meta_p = unpacked_dir / meta_name
                if meta_p.exists():
                    try:
                        xml_text += meta_p.read_text(encoding="utf-8", errors="ignore") + "\n"
                    except Exception as e:
                        logger.warning(f"Could not read {meta_name}: {e}")
            if xml_text:
                try:
                    downloader.fetch_slide_backdrops(meeting, unpacked_dir, xml_text, None)
                except Exception as e:
                    logger.warning(f"Engine failed to fetch slides: {e}")
            if xml_text:
                try:
                    downloader.fetch_slide_backdrops(meeting, unpacked_dir, xml_text, None)
                except Exception as e:
                    logger.warning(f"Engine failed to fetch slides: {e}")

            # 4. Parsing Timeline and Event Sync
            notify("PARSE", 65.0, "Parsing mainstream.xml and calculating millisecond offsets...")
            timeline = TimelineParser.parse_directory(unpacked_dir)
            if timeline.total_duration_sec <= 0:
                notify("ERROR", 0.0, "Timeline has zero duration. Recording files may be empty or corrupt.")
                return None

            # If the package carried no usable A/V (Adobe Connect sometimes ships
            # metadata-only ZIPs), try recovering the streams from the public
            # per-file output directory before deciding anything.
            has_av = bool(timeline.audio_segments or timeline.screenshare_segments or timeline.camera_segments)
            if not has_av:
                notify("PARSE", 63.5, "استریم رسانه‌ای در بسته نبود؛ تلاش برای بازیابی از مسیر جایگزین…")
                recovered = downloader.download_component_directory(
                    meeting, lambda c, t, s: None, target_dir=unpacked_dir
                )
                if recovered:
                    timeline = TimelineParser.parse_directory(unpacked_dir)
                    has_av = bool(timeline.audio_segments or timeline.screenshare_segments
                                  or timeline.camera_segments)
                    if has_av:
                        notify("PARSE", 64.0, "استریم‌ها بازیابی شدند ✓")

            if not has_av:
                # Root-cause diagnostics: probe every downloaded FLV and tell the
                # user exactly which failure mode happened (via UI console + log).
                hint = ""
                try:
                    from utils.ffmpeg_utils import FFmpegUtils
                    flvs = sorted(unpacked_dir.glob("*.flv"))
                    has_slides = bool(timeline.document_segments or timeline.slide_events)
                    if not flvs:
                        hint = ("هیچ فایل رسانه‌ای (.flv) دانلود نشد — سرور درخواست‌های رسانه را رد کرد. "
                                "اتصال را بررسی کنید و دوباره تلاش کنید.")
                    else:
                        html_n = broken_n = ok_n = meta_n = 0
                        for f in flvs:
                            try:
                                with open(f, "rb") as fh:
                                    head = fh.read(32)
                                size_kb = f.stat().st_size // 1024
                                if b"<html" in head.lower() or b"<!doctype" in head.lower():
                                    html_n += 1
                                    notify("PARSE", 64.0, f"[DIAG] {f.name}: صفحه لاگین/خطا ({size_kb} KB)")
                                    continue
                                info = FFmpegUtils.probe_media_file(f)
                                if info.has_audio or info.has_video:
                                    ok_n += 1
                                    notify("PARSE", 64.0, f"[DIAG] {f.name}: سالم ({size_kb} KB)")
                                elif size_kb >= 1:
                                    meta_n += 1  # data-only stream (chat/index/transcript) — normal
                                    broken_n += 1 if f.name.startswith(("ft", "index", "main", "transcript")) else 0
                                    notify("PARSE", 64.0, f"[DIAG] {f.name}: استریم متادیتا ({size_kb} KB)")
                                else:
                                    broken_n += 1
                                    notify("PARSE", 64.0, f"[DIAG] {f.name}: خالی ({size_kb} KB)")
                            except OSError:
                                continue
                        if html_n and html_n == len(flvs):
                            hint = ("سرور برای همه‌ی فایل‌های رسانه، صفحه‌ی لاگین/خطا برگردانده — "
                                    "توکن نشست منقضی شده. لینک/توکن تازه بگیرید و دوباره تلاش کنید.")
                        elif not has_slides:
                            hint = ("هیچ استریم رسانه‌ای و هیچ اسلایدی در این بسته موجود نیست — "
                                    "احتمالاً سرور هنوز پردازش ضبط را کامل نکرده. بعداً دوباره تلاش کنید.")
                        else:
                            hint = ""
                    if not has_av and not has_slides:
                        notify("ERROR", 0.0, "هیچ استریم صوتی/تصویری معتبری یافت نشد. " + hint)
                        return None
                    if not has_av:
                        notify("PARSE", 65.0,
                               "NOTE: صدای این کلاس در بسته‌ی ضبط موجود نبود؛ خروجی به‌صورت "
                               "اسلایدشوی بی‌صدا ساخته می‌شود.")
                except Exception:
                    pass

            # Transparency: an audio-only session (empty Share pod, no screen share)
            # is a property of the RECORDING, not a download failure — say so loudly.
            has_visuals = bool(timeline.slide_events) or bool(timeline.document_segments)
            has_visuals = has_visuals or any(
                s.info.has_video and not s.info.is_dummy_video for s in timeline.all_segments
            )
            if not has_visuals:
                notify("PARSE", 65.0,
                       "NOTE: This session contains NO visual content — the Share pod was never "
                       "used and there is no screen share. The output will be audio over a dark "
                       "canvas; this is faithful to what attendees saw.")

            # 5. FFmpeg Synthesis & Conversion
            notify("CONVERT", 70.0, "Synthesizing media with FFmpeg...")
            composer = VideoComposer(self.config)
            
            ext = ".mp3" if output_format.lower() == "mp3" else ".mp4"
            timestamp_str = time.strftime("%Y%m%d_%H%M%S")
            output_file = self.config.downloads_dir / f"{meeting.meeting_id}_{timestamp_str}{ext}"
            
            def conv_cb(*args):
                if len(args) == 2:
                    pct_ffmpeg, msg = args
                elif len(args) == 3:
                    _, pct_ffmpeg, msg = args
                elif len(args) == 1:
                    pct_ffmpeg, msg = args[0], ""
                else:
                    pct_ffmpeg, msg = 0.0, ""
                global_pct = 70.0 + (pct_ffmpeg * 0.28)
                notify("CONVERT", global_pct, msg)
                
            success = composer.convert_recording(timeline, output_file, conv_cb)
            
            # 5.5 Extract Uploaded Assets (PDFs/Slides)
            assets = []
            if success:
                notify("EXTRACT", 90.0, "Searching for uploaded PDF/Slide assets...")
                assets = downloader.download_assets(meeting, unpacked_dir)
                if assets:
                    asset_msg = f"Extracted {len(assets)} presentation files (PDF/Slides)."
                    notify("EXTRACT", 95.0, asset_msg)
                    logger.info(asset_msg)

            if success and output_file.exists() and output_file.stat().st_size > 0:
                msg = f"Saved: {output_file.name} ({output_file.stat().st_size / (1024*1024):.2f} MB)"
                if assets:
                    msg += f"\n+ {len(assets)} PDFs downloaded to {assets[0].parent.name}"
                notify("DONE", 100.0, msg)
                logger.info(f"Video created successfully at: {output_file.resolve()}")
                return output_file
            else:
                notify("ERROR", 0.0, "Conversion failed during FFmpeg encoding.")
                return None

        except JobCancelled:
            # notify() would re-raise while the cancel flag is still set.
            if progress_callback:
                progress_callback("CANCELLED", 0.0, "Operation cancelled by user.")
            logger.info("Pipeline cancelled by user.")
            return None
        except Exception as e:
            notify("ERROR", 0.0, f"Critical error during processing: {e}")
            logger.error(f"Engine exception: {e}")
            return None
        finally:
            # 6. Cleanup
            if unpacked_dir and not self.config.keep_raw_files:
                if progress_callback:
                    progress_callback("CLEANUP", 99.0, "Cleaning temporary extracted files...")
                WorkspaceCleaner.remove_path(unpacked_dir.parent)
