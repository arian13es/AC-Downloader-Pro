from pathlib import Path
from typing import Callable, Optional
import subprocess
try:
    import pymupdf as fitz  # PyMuPDF, used to rasterize PDF slides. Preferred modern name.
except ImportError:
    try:
        import fitz  # legacy alias
    except ImportError:  # Keeps the module importable on pure-stdlib installs; PyInstaller still detects the nested import
        fitz = None
from core.config import AppConfig, DEFAULT_CONFIG
from core.timeline_parser import RecordingTimeline, SegmentType, StreamSegment
from utils.ffmpeg_utils import FFmpegUtils
from utils.logger import logger

class VideoComposer:
    def __init__(self, config: AppConfig = DEFAULT_CONFIG):
        self.config = config
        self.ffmpeg_bin = FFmpegUtils.find_executable(config.ffmpeg_path)

    def convert_recording(
        self,
        timeline: RecordingTimeline,
        output_file: Path,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> bool:
        """Composites audio and video tracks from timeline into a unified MP4 or MP3 file."""
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        if timeline.total_duration_sec <= 0:
            logger.error("Cannot convert timeline with zero duration.")
            return False
            
        if self.config.layout_mode == "audio_only" or output_file.suffix.lower() in [".mp3", ".m4a", ".aac"]:
            return self._compose_audio_only(timeline, output_file, progress_callback)
        else:
            return self._compose_video(timeline, output_file, progress_callback)

    def _compose_audio_only(
        self,
        timeline: RecordingTimeline,
        output_file: Path,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> bool:
        """Synthesizes all audio tracks into a single high-quality audio file."""
        logger.info(f"Compositing audio-only track to {output_file.name}")
        
        if not timeline.audio_segments:
            logger.error("No audio segments found to convert.")
            return False

        cmd = [self.ffmpeg_bin]
        filter_complex = []
        audio_labels = []
        
        for idx, seg in enumerate(timeline.audio_segments):
            cmd.extend(["-i", str(seg.file_path)])
            delay_ms = seg.start_time_ms
            if delay_ms > 0:
                filter_complex.append(f"[{idx}:a]adelay={delay_ms}|{delay_ms}[a{idx}]")
                audio_labels.append(f"[a{idx}]")
            else:
                audio_labels.append(f"[{idx}:a]")
                
        if len(audio_labels) > 1:
            filter_complex.append(f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:duration=longest:dropout_transition=0:normalize=0[aout]")
            final_audio = "[aout]"
        else:
            final_audio = audio_labels[0]
            
        if filter_complex:
            cmd.extend(["-filter_complex", ";".join(filter_complex), "-map", final_audio])
        else:
            cmd.extend(["-map", "0:a"])
            
        if output_file.suffix.lower() == ".mp3":
            cmd.extend(["-c:a", "libmp3lame", "-b:a", self.config.audio_bitrate, str(output_file)])
        else:
            cmd.extend(["-c:a", "aac", "-b:a", self.config.audio_bitrate, str(output_file)])

        return FFmpegUtils.run_ffmpeg_with_progress(cmd, timeline.total_duration_sec, progress_callback)

    def _render_slide_to_png(self, doc_file: Path, png_path: Path) -> bool:
        """Renders the first page of a slide asset (SWF or PDF) to a valid PNG.

        swfrender is invoked EXACTLY as: swfrender {file} -o {output}
        Resolution flags (-X/-Y) crash certain swftools builds and are deliberately omitted.
        """
        png_path.unlink(missing_ok=True)
        suffix = doc_file.suffix.lower()
        try:
            if suffix == ".swf":
                swf_bin = self.config.swfrender_path
                res = subprocess.run(
                    [swf_bin, str(doc_file), "-o", str(png_path)],
                    capture_output=True, text=True, check=False
                )
                if res.returncode != 0:
                    tail = (res.stderr or res.stdout or "").strip()[-300:]
                    logger.warning(f"swfrender failed for {doc_file.name} "
                                   f"(exit code {res.returncode}): {tail}")
                    return False
            elif suffix == ".pdf":
                if fitz is None:
                    logger.warning("PyMuPDF not installed — skipping PDF slide rendering (pip install pymupdf)")
                    return False
                doc = fitz.open(doc_file)
                page = doc.load_page(0)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                pix.save(str(png_path))
            else:
                return False
        except FileNotFoundError:
            logger.error(f"Renderer binary not found for {suffix.upper()} slide: {doc_file.name}")
            return False
        except Exception as e:
            logger.warning(f"Failed to render slide ({doc_file.name}): {e}")
            return False

        # Post-render validation: swfrender can exit 0 while writing nothing/garbage.
        if not self._is_valid_png(png_path):
            logger.warning(f"Slide renderer produced no valid PNG for {doc_file.name}")
            return False
        return True

    @staticmethod
    def _is_valid_png(path: Path) -> bool:
        """Validates PNG magic bytes rather than trusting file size alone."""
        try:
            if not path.exists() or path.stat().st_size < 64:
                return False
            with open(path, "rb") as f:
                return f.read(8) == b"\x89PNG\r\n\x1a\n"
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Audio mixing shared by every video composition path
    # ------------------------------------------------------------------

    def _mix_audio(self, cmd: list, filter_parts: list, timeline) -> str:
        """Appends audio inputs and the mix filter; returns the audio map label."""
        audio_outputs = []
        for idx, seg in enumerate(timeline.audio_segments):
            cmd.extend(["-i", str(seg.file_path)])
            if seg.start_time_ms > 0:
                lbl = f"a_in_{idx}"
                filter_parts.append(f"[{idx}:a]adelay={seg.start_time_ms}|{seg.start_time_ms}[{lbl}]")
                audio_outputs.append(f"[{lbl}]")
            else:
                audio_outputs.append(f"[{idx}:a]")

        if len(audio_outputs) > 1:
            filter_parts.append(
                f"{''.join(audio_outputs)}amix=inputs={len(audio_outputs)}:"
                f"duration=longest:dropout_transition=0:normalize=0[aout]"
            )
            return "[aout]"
        if len(audio_outputs) == 1:
            return audio_outputs[0]
        filter_parts.append(f"aevalsrc=0:d={timeline.total_duration_sec}:s=44100[aout]")
        return "[aout]"

    # ------------------------------------------------------------------
    # Slide-show reconstruction (true page-by-page playback)
    # ------------------------------------------------------------------

    def _resolve_slide_file(self, timeline, doc_index: int) -> Optional[Path]:
        """Maps a 1-based descriptor index to its downloaded slide file (slide_XXX*)."""
        prefix = f"slide_{doc_index:03d}"
        for seg in timeline.document_segments:
            if seg.file_path.name.startswith(prefix):
                return seg.file_path
        return None

    def _render_pdf_page_png(self, doc_file: Path, page_num: int, png_path: Path) -> bool:
        """Rasterizes one PDF page at high resolution."""
        if fitz is None:
            return False
        try:
            doc = fitz.open(doc_file)
            if page_num >= doc.page_count:
                page_num = doc.page_count - 1
            if page_num < 0:
                return False
            page = doc.load_page(page_num)
            width = page.rect.width or 612.0
            zoom = min(4.0, max(1.5, 1920.0 / width))
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            pix.save(str(png_path))
            return self._is_valid_png(png_path)
        except Exception as e:
            logger.warning(f"PDF page render failed ({doc_file.name} p{page_num}): {e}")
            return False

    def _make_blank_png(self, path: Path, w: int, h: int) -> bool:
        """Generates the dark canvas frame used when the Share pod was empty."""
        try:
            subprocess.run(
                [self.ffmpeg_bin, "-v", "error", "-y",
                 "-f", "lavfi", "-i", f"color=c=#181820:s={w}x{h}",
                 "-frames:v", "1", str(path)],
                capture_output=True, check=False,
            )
        except Exception as e:
            logger.warning(f"Blank frame generation failed: {e}")
        return self._is_valid_png(path)

    def _build_slideshow_segments(self, timeline, output_file: Path, out_w: int, out_h: int,
                                  progress_callback=None) -> list[tuple[float, float, Path]]:
        """Converts slide_events into concrete [(start, end, png)] beats.

        Returns [] when no schedule is available (caller falls back to a static
        backdrop). Pages are rendered once and cached per (file, page).
        """
        events = timeline.slide_events
        if not events or not timeline.document_segments:
            return []

        total = timeline.total_duration_sec
        render_cache: dict[tuple, Optional[Path]] = {}
        try:
            self.config.temp_dir.mkdir(parents=True, exist_ok=True)
            blank_png = self.config.temp_dir / "blank_canvas.png"
        except Exception:
            blank_png = output_file.parent / f"{output_file.stem}_blank.png"
        have_blank = self._make_blank_png(blank_png, out_w, out_h)

        raw: list[list] = []
        for i, ev in enumerate(events):
            start = ev.time_ms / 1000.0
            end = min(events[i + 1].time_ms / 1000.0 if i + 1 < len(events) else total, total)
            if end <= start:
                continue
            key = ("__blank__", -1)
            if ev.doc_index > 0 and ev.page_num >= 0:
                doc_file = self._resolve_slide_file(timeline, ev.doc_index)
                if doc_file is not None:
                    key = (str(doc_file), ev.page_num)
            raw.append([start, end, key])

        if not raw:
            return []

        # Coalesce identical consecutive pages only. Brief page flickers are
        # real navigation (fast scrolling) and must survive into the output;
        # the encoder clamps their duration to a visible minimum.
        merged: list[list] = []
        for seg in raw:
            if merged and seg[2] == merged[-1][2]:
                merged[-1][1] = seg[1]
            else:
                merged.append(seg)

        MAX_SEGMENTS = 240
        while len(merged) > MAX_SEGMENTS:
            # Drop the shortest beat until the graph fits FFmpeg's practical limits.
            shortest = min(range(len(merged)), key=lambda k: merged[k][1] - merged[k][0])
            dropped = merged.pop(shortest)
            if 0 < shortest < len(merged):
                merged[shortest - 1][1] = merged[shortest][0]

        segments: list[tuple[float, float, Path]] = []
        for start, end, key in merged:
            if key[0] == "__blank__":
                if not have_blank:
                    continue
                segments.append((start, end, blank_png))
                continue

            if key not in render_cache:
                doc_file = Path(key[0])
                png_path = doc_file.with_name(f"{doc_file.stem}_p{key[1]:03d}.png")
                ok = False
                if doc_file.suffix.lower() == ".pdf":
                    ok = self._render_pdf_page_png(doc_file, key[1], png_path)
                else:
                    ok = self._render_slide_to_png(doc_file, png_path)
                render_cache[key] = png_path if ok else None

            png = render_cache[key]
            if png is None:
                png = blank_png if have_blank else None
                if png is None:
                    continue
            segments.append((start, end, png))

        if progress_callback:
            progress_callback(0.0, f"Slide schedule ready: {len(segments)} visual beats")
        return segments

    def _encode_slideshow(self, timeline, output_file: Path,
                          segments: list[tuple[float, float, Path]],
                          progress_callback=None) -> bool:
        """Encodes audio + a concatenated page-exact slideshow into the output MP4."""
        if "x" in self.config.resolution:
            out_w, out_h = [int(x) for x in self.config.resolution.split("x")]
        else:
            out_w, out_h = (1920, 1080)

        cmd = [self.ffmpeg_bin]
        filter_parts: list[str] = []
        audio_map = self._mix_audio(cmd, filter_parts, timeline)

        concat_labels = []
        for i, (start, end, png) in enumerate(segments):
            dur = max(0.2, end - start)
            cmd.extend(["-loop", "1", "-framerate", "5", "-t", f"{dur:.3f}", "-i", str(png)])
            filter_parts.append(
                f"[{len(timeline.audio_segments) + i}:v]"
                f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=#181820,setsar=1[s{i}]"
            )
            concat_labels.append(f"[s{i}]")

        filter_parts.append(f"{''.join(concat_labels)}concat=n={len(segments)}:v=1:a=0[slideshow]")

        filter_script_path = output_file.with_name(f"{output_file.stem}_filter.txt")
        with open(filter_script_path, "w", encoding="utf-8") as f:
            f.write(";\n".join(filter_parts))

        cmd.extend([
            "-filter_complex_script", str(filter_script_path),
            "-map", "[slideshow]",
            "-map", audio_map,
            "-c:v", "libx264",
            "-tune", "stillimage",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            "-r", str(min(self.config.fps, 10)),
            "-c:a", self.config.audio_codec,
            "-b:a", self.config.audio_bitrate,
            "-t", str(timeline.total_duration_sec),
            str(output_file),
        ])

        success = FFmpegUtils.run_ffmpeg_with_progress(cmd, timeline.total_duration_sec, progress_callback)
        try:
            if filter_script_path.exists():
                filter_script_path.unlink()
        except Exception:
            pass
        return success

    def _compose_video(
        self,
        timeline: RecordingTimeline,
        output_file: Path,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> bool:
        """Constructs complex FFmpeg pipeline to composite synchronized video and audio."""
        logger.info(f"Starting video synthesis -> {output_file.name}")

        # Placeholder video tracks (e.g. 160x120 black dummy inside cameraVoip FLVs)
        # must never be scaled onto the canvas: they would obscure slide backdrops.
        real_cam_segments = [s for s in timeline.camera_segments if not s.info.is_dummy_video]
        if len(real_cam_segments) != len(timeline.camera_segments):
            logger.info(f"Ignoring {len(timeline.camera_segments) - len(real_cam_segments)} "
                        f"dummy/placeholder video track(s) detected via ffprobe.")

        has_video_tracks = bool(timeline.screenshare_segments or real_cam_segments)

        if not has_video_tracks:
            # Voice-only / whiteboard classroom: Generate lightweight still-frame video fast
            logger.info("No screen share / webcam video streams found. Synthesizing lightweight audio-visual MP4...")
            return self._compose_audio_with_backdrop(timeline, output_file, progress_callback)
            
        cmd = [self.ffmpeg_bin]
        inputs: list[StreamSegment] = []
        
        def get_input_idx(seg: StreamSegment) -> int:
            for i, existing in enumerate(inputs):
                if existing.file_path == seg.file_path:
                    return i
            inputs.append(seg)
            return len(inputs) - 1

        for s in timeline.audio_segments:
            get_input_idx(s)
        for s in timeline.screenshare_segments:
            get_input_idx(s)
        for s in real_cam_segments:
            get_input_idx(s)

        for inp in inputs:
            cmd.extend(["-i", str(inp.file_path)])

        if "x" in self.config.resolution:
            out_w, out_h = [int(x) for x in self.config.resolution.split("x")]
        else:
            out_w, out_h = (1920, 1080)

        filter_parts = []

        # 1. AUDIO MIXING
        audio_outputs = []
        for seg in timeline.audio_segments:
            idx = get_input_idx(seg)
            lbl = f"a_in_{idx}"
            if seg.start_time_ms > 0:
                filter_parts.append(f"[{idx}:a]adelay={seg.start_time_ms}|{seg.start_time_ms}[{lbl}]")
                audio_outputs.append(f"[{lbl}]")
            else:
                audio_outputs.append(f"[{idx}:a]")

        if len(audio_outputs) > 1:
            filter_parts.append(f"{''.join(audio_outputs)}amix=inputs={len(audio_outputs)}:duration=longest:dropout_transition=0:normalize=0[aout]")
            audio_map = "[aout]"
        elif len(audio_outputs) == 1:
            audio_map = audio_outputs[0]
        else:
            filter_parts.append(f"aevalsrc=0:d={timeline.total_duration_sec}:s=44100[aout]")
            audio_map = "[aout]"

        # 2. VIDEO COMPOSITION
        # Determine base canvas
        backdrop_image = None
        if not timeline.screenshare_segments:
            for doc_seg in timeline.document_segments:
                doc_file = doc_seg.file_path
                png_path = doc_file.with_suffix(".png")
                if progress_callback:
                    progress_callback(0.0, f"Rendering slide ({doc_file.name}) for canvas...")
                if self._render_slide_to_png(doc_file, png_path):
                    backdrop_image = png_path
                    break

        if backdrop_image:
            cmd.extend(["-loop", "1", "-framerate", str(self.config.fps), "-i", str(backdrop_image)])
            bg_idx = len(inputs) # Image is the last input added
            filter_parts.append(f"[{bg_idx}:v]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=#1e1e24[canvas]")
        else:
            if not timeline.screenshare_segments:
                if progress_callback:
                    progress_callback(0.0, "Slide backdrop unavailable — output will be audio over a blank canvas")
                logger.warning("No slide backdrop could be rendered. Falling back to blank canvas.")
            filter_parts.append(f"color=c=#1e1e24:s={out_w}x{out_h}:d={timeline.total_duration_sec}:r={self.config.fps}[canvas]")
            
        current_base = "[canvas]"

        if timeline.screenshare_segments:
            for s_idx, seg in enumerate(timeline.screenshare_segments):
                idx = get_input_idx(seg)
                scaled_lbl = f"v_scr_scaled_{s_idx}"
                overlay_lbl = f"v_scr_base_{s_idx}"
                
                filter_parts.append(
                    f"[{idx}:v]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                    f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black[{scaled_lbl}]"
                )
                start_s = seg.start_time_sec
                end_s = start_s + seg.duration_sec
                filter_parts.append(
                    f"{current_base}[{scaled_lbl}]overlay=0:0:enable='between(t,{start_s:.2f},{end_s:.2f})'[{overlay_lbl}]"
                )
                current_base = f"[{overlay_lbl}]"

        if real_cam_segments:
            for c_idx, seg in enumerate(real_cam_segments):
                idx = get_input_idx(seg)
                if timeline.screenshare_segments or timeline.document_segments:
                    pip_w = int(out_w * 0.22)
                    pip_h = int(out_h * 0.22)
                    cam_lbl = f"v_cam_pip_{c_idx}"
                    cam_over_lbl = f"v_cam_base_{c_idx}"
                    filter_parts.append(f"[{idx}:v]scale={pip_w}:{pip_h}:force_original_aspect_ratio=decrease[{cam_lbl}]")
                    start_s = seg.start_time_sec
                    end_s = start_s + seg.duration_sec
                    filter_parts.append(
                        f"{current_base}[{cam_lbl}]overlay=main_w-overlay_w-20:20:enable='between(t,{start_s:.2f},{end_s:.2f})'[{cam_over_lbl}]"
                    )
                    current_base = f"[{cam_over_lbl}]"
                else:
                    cam_lbl = f"v_cam_full_{c_idx}"
                    cam_over_lbl = f"v_cam_base_{c_idx}"
                    filter_parts.append(
                        f"[{idx}:v]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                        f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black[{cam_lbl}]"
                    )
                    start_s = seg.start_time_sec
                    end_s = start_s + seg.duration_sec
                    filter_parts.append(
                        f"{current_base}[{cam_lbl}]overlay=0:0:enable='between(t,{start_s:.2f},{end_s:.2f})'[{cam_over_lbl}]"
                    )
                    current_base = f"[{cam_over_lbl}]"

        video_map = current_base
        encoder = FFmpegUtils.detect_best_video_encoder(self.config.hwaccel)
        
        # Write complex filter to script to bypass Windows 8191 char limit
        filter_script_path = output_file.with_name(f"{output_file.stem}_filter.txt")
        with open(filter_script_path, "w", encoding="utf-8") as f:
            f.write(";\n".join(filter_parts))
            
        cmd.extend([
            "-filter_complex_script", str(filter_script_path),
            "-map", video_map,
            "-map", audio_map,
            "-c:v", encoder,
            "-pix_fmt", "yuv420p",
            "-c:a", self.config.audio_codec,
            "-b:a", self.config.audio_bitrate,
            "-t", str(timeline.total_duration_sec)
        ])

        if encoder == "libx264":
            cmd.extend(["-preset", self.config.preset, "-crf", str(self.config.crf)])

        cmd.append(str(output_file))
        success = FFmpegUtils.run_ffmpeg_with_progress(cmd, timeline.total_duration_sec, progress_callback)
        
        # Cleanup filter script
        try:
            if filter_script_path.exists():
                filter_script_path.unlink()
        except Exception:
            pass
            
        return success

    def _compose_audio_with_backdrop(
        self,
        timeline: RecordingTimeline,
        output_file: Path,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> bool:
        """Synthesis for voice-only classrooms.

        When a slide schedule was reconstructed from the Share-pod events, this
        builds a true page-exact slideshow; otherwise it falls back to a single
        static backdrop for the whole duration.
        """
        if "x" in self.config.resolution:
            out_w, out_h = [int(x) for x in self.config.resolution.split("x")]
        else:
            out_w, out_h = (1920, 1080)

        segments = self._build_slideshow_segments(timeline, output_file, out_w, out_h, progress_callback)
        if segments:
            logger.info(f"Compositing page-exact slideshow ({len(segments)} beats) -> {output_file.name}")
            return self._encode_slideshow(timeline, output_file, segments, progress_callback)

        logger.info(f"No slide schedule; compositing static backdrop -> {output_file.name}")

        cmd = [self.ffmpeg_bin]
        filter_parts = []
        audio_map = self._mix_audio(cmd, filter_parts, timeline)

        backdrop_image = None
        for doc_seg in timeline.document_segments:
            doc_file = doc_seg.file_path
            png_path = doc_file.with_suffix(".png")
            if progress_callback:
                progress_callback(0.0, f"Rendering slide ({doc_file.name})...")
            if self._render_slide_to_png(doc_file, png_path):
                backdrop_image = png_path
                break
        
        if backdrop_image:
            cmd.extend(["-loop", "1", "-framerate", "2", "-i", str(backdrop_image)])
            bg_idx = len(timeline.audio_segments)
            filter_parts.append(f"[{bg_idx}:v]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=#181820[vout]")
        else:
            if progress_callback:
                progress_callback(0.0, "Slide backdrop unavailable — output will be audio over a blank canvas")
            logger.warning("No slide backdrop could be rendered. Falling back to blank canvas.")
            # Generate a minimal 2fps static video canvas
            filter_parts.append(f"color=c=#181820:s={out_w}x{out_h}:r=2:d={timeline.total_duration_sec}[vout]")

        filter_script_path = output_file.with_name(f"{output_file.stem}_filter.txt")
        with open(filter_script_path, "w", encoding="utf-8") as f:
            f.write(";\n".join(filter_parts))

        cmd.extend([
            "-filter_complex_script", str(filter_script_path),
            "-map", "[vout]",
            "-map", audio_map,
            "-c:v", "libx264",
            "-tune", "stillimage",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            "-c:a", self.config.audio_codec,
            "-b:a", self.config.audio_bitrate,
            "-t", str(timeline.total_duration_sec),
            str(output_file)
        ])

        success = FFmpegUtils.run_ffmpeg_with_progress(cmd, timeline.total_duration_sec, progress_callback)
        
        try:
            if filter_script_path.exists():
                filter_script_path.unlink()
        except Exception:
            pass
            
        return success
