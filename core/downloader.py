import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Callable, Optional
from core.config import AppConfig, DEFAULT_CONFIG
from core.session import HTTPSession
from core.url_parser import ParsedMeetingURL
from utils.ffmpeg_utils import FFmpegUtils
from utils.logger import logger

# Magic bytes for common media container formats
_MEDIA_MAGIC = {
    b"FLV": "FLV",
    b"\x1a\x45\xdf\xa3": "MKV/WebM",
    b"\x00\x00\x00": "MP4/MOV",   # ftyp box (first 3 bytes usually 0x00)
}

_MAX_SLIDES_PER_DOC = 60          # Hard cap on sequential slide probing per shared document
_MAX_SOURCE_ZIP_CANDIDATES = 4    # Hard cap on ZIP-fallback URLs tried per descriptor


def _natural_key(name: str) -> list:
    """Sorts slide filenames numerically (2.swf < 10.swf)."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]

def _is_html_error_page(file_path: Path) -> bool:
    """Detects whether a downloaded file is an HTML error page instead of a real media file."""
    try:
        with open(file_path, "rb") as f:
            header = f.read(256)
        if not header:
            return True
        text = header.decode("utf-8", errors="ignore").lower()
        if "<html" in text or "<!doctype" in text or "<head>" in text:
            return True
        return False
    except Exception:
        return True


class RecordingDownloader:
    def __init__(self, session: HTTPSession, config: AppConfig = DEFAULT_CONFIG):
        self.session = session
        self.config = config

    def download_file(
        self,
        url: str,
        dest_path: Path,
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
        max_retries: Optional[int] = None
    ) -> bool:
        """Downloads a single file over HTTP with streaming, resume support, and retry logic."""
        if max_retries is None:
            max_retries = self.config.max_retries
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        temp_dest = dest_path.with_suffix(dest_path.suffix + ".part")

        # Some AC servers (e.g. online3.tabrizu.ac.ir) require the session token
        # as a URL parameter rather than (or in addition to) the BREEZESESSION
        # cookie. Build an alternate URL with the token appended.
        alt_url = None
        if self.session and hasattr(self.session, "get_cookie"):
            token = self.session.get_cookie("BREEZESESSION")
            if token and "session=" not in url:
                sep = "&" if "?" in url else "?"
                alt_url = f"{url}{sep}session={token}"

        use_url = url
        alt_tried = False

        for attempt in range(max_retries + 1):
            headers = {}
            downloaded = 0
            if temp_dest.exists():
                downloaded = temp_dest.stat().st_size
                headers["Range"] = f"bytes={downloaded}-"

            try:
                r = self.session.get(use_url, headers=headers, stream=True, timeout=self.config.request_timeout)

                # Detect HTTP error responses (excluding 416 which we handle below)
                if r.status_code >= 400 and r.status_code != 416:
                    logger.warning(f"HTTP {r.status_code} for {url}")
                    if attempt < max_retries:
                        wait = 2 ** attempt
                        logger.info(f"Retrying in {wait}s (attempt {attempt + 1}/{max_retries})...")
                        time.sleep(wait)
                        continue
                    return False

                # Handle server without range support
                if r.status_code == 416 or r.status_code == 200:
                    downloaded = 0
                    mode = "wb"
                    clen_hdr = r.headers.get("Content-Length", "0")
                    total_size = int(clen_hdr) if clen_hdr.isdigit() else 0
                elif r.status_code == 206:
                    mode = "ab"
                    content_range = r.headers.get("Content-Range", "")
                    if "/" in content_range:
                        total_size = int(content_range.split("/")[-1])
                    else:
                        clen_hdr = r.headers.get("Content-Length", "0")
                        total_size = downloaded + (int(clen_hdr) if clen_hdr.isdigit() else 0)
                else:
                    mode = "wb"
                    clen_hdr = r.headers.get("Content-Length", "0")
                    total_size = int(clen_hdr) if clen_hdr.isdigit() else 0

                # Check Content-Type for obvious HTML error pages
                content_type = r.headers.get("Content-Type", "").lower()
                if "text/html" in content_type and not url.endswith(".xml"):
                    logger.warning(f"Server returned HTML (likely error page) for: {url}")
                    return False

                start_time = time.time()
                last_time = start_time
                last_bytes = downloaded
                speed = 0.0

                with open(temp_dest, mode) as f:
                    for chunk in r.iter_content(chunk_size=self.config.chunk_size):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)

                            now = time.time()
                            if now - last_time >= 0.5:
                                speed = (downloaded - last_bytes) / (now - last_time)
                                last_time = now
                                last_bytes = downloaded
                                if progress_callback:
                                    progress_callback(downloaded, total_size, speed)

                if progress_callback:
                    progress_callback(downloaded, total_size, speed)

                if temp_dest.exists():
                    if dest_path.exists():
                        dest_path.unlink()
                    temp_dest.replace(dest_path)

                # Post-download validation: check if we got an HTML error page
                if dest_path.exists() and _is_html_error_page(dest_path):
                    logger.warning(f"Downloaded file is an HTML error page, removing: {dest_path.name}")
                    dest_path.unlink()
                    # If we have an alternate URL (session-in-URL) and haven't
                    # tried it yet, clean up and retry with it.
                    if alt_url and not alt_tried:
                        alt_tried = True
                        use_url = alt_url
                        temp_dest.unlink(missing_ok=True)
                        logger.info(f"Retrying with session-in-URL: {use_url[:80]}...")
                        continue
                    return False

                return True

            except Exception as e:
                logger.error(f"Download failed for {url}: {e}")
                if attempt < max_retries:
                    wait = 2 ** attempt
                    logger.info(f"Retrying in {wait}s (attempt {attempt + 1}/{max_retries})...")
                    time.sleep(wait)
                    continue
                return False

        return False

    def extract_zip(self, zip_path: Path, extract_dir: Path) -> bool:
        """Safely extracts a zip archive with integrity validation and path traversal prevention."""
        extract_dir.mkdir(parents=True, exist_ok=True)

        # Pre-validate ZIP integrity
        if not zipfile.is_zipfile(zip_path):
            logger.error(f"File is not a valid ZIP archive: {zip_path.name} ({zip_path.stat().st_size} bytes)")
            return False

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                # Test CRC integrity of all members
                bad_file = zf.testzip()
                if bad_file is not None:
                    logger.error(f"ZIP archive is corrupted: first bad file = {bad_file}")
                    return False

                for member in zf.infolist():
                    target_path = extract_dir / member.filename
                    if not str(target_path.resolve()).startswith(str(extract_dir.resolve())):
                        logger.warning(f"Skipping potentially malicious zip member: {member.filename}")
                        continue
                    zf.extract(member, extract_dir)
            logger.info(f"Successfully extracted zip package into: {extract_dir}")
            return True
        except zipfile.BadZipFile as e:
            logger.error(f"ZIP extraction failed (corrupt/truncated archive): {e}")
            return False
        except Exception as e:
            logger.error(f"Zip extraction failed: {e}")
            return False

    def download_and_extract_package(
        self,
        zip_url: str,
        meeting_id: str,
        progress_callback: Optional[Callable[[int, int, float], None]] = None
    ) -> Path | None:
        """Downloads the full raw zip recording package and unpacks it into a working directory."""
        target_dir = self.config.temp_dir / f"rec_{meeting_id}_{int(time.time())}"
        target_dir.mkdir(parents=True, exist_ok=True)
        zip_file = target_dir / f"{meeting_id}.zip"

        logger.info(f"Downloading recording package from: {zip_url}")
        success = self.download_file(zip_url, zip_file, progress_callback)
        if not success or not zip_file.exists() or zip_file.stat().st_size == 0:
            logger.error("Download package could not be fetched or is empty.")
            return None

        unpacked_dir = target_dir / "unpacked"
        extracted = self.extract_zip(zip_file, unpacked_dir)
        if not extracted:
            return None

        # Post-extraction validation: ensure we have at least one real media file
        media_files = list(unpacked_dir.glob("*.flv")) + list(unpacked_dir.glob("*.mp4"))
        valid_media = [f for f in media_files if not _is_html_error_page(f) and f.stat().st_size > 1024]

        if not valid_media:
            logger.error("ZIP extraction produced no valid media files (all empty or HTML error pages).")
            return None

        logger.info(f"Extracted {len(valid_media)} valid media files from ZIP.")
        return unpacked_dir

    def _sniff(self, path: Path) -> str:
        """Sniff file magic bytes to determine real file type."""
        if not path.exists() or path.stat().st_size < 16:
            return "unknown"
        try:
            with open(path, "rb") as f:
                header = f.read(16)
            if header.startswith(b"FWS") or header.startswith(b"CWS") or header.startswith(b"ZWS"):
                return "swf"
            if header.startswith(b"%PDF"):
                return "pdf"
            if header.startswith(b"PK\x03\x04"):
                return "zip"
            if b"<html" in header.lower() or b"<!doctype html" in header.lower():
                return "html"
        except Exception:
            pass
        return "unknown"

    def _parse_document_descriptors(self, xml_text: str) -> list[dict]:
        """Extract all <documentDescriptor> blocks from recording metadata.

        Delegates to core.slide_timeline so the descriptor ordering used for
        slide filenames is identical everywhere.
        """
        from core.slide_timeline import parse_document_descriptors
        return parse_document_descriptors(xml_text)

    @staticmethod
    def _append_session(url: str, token: Optional[str]) -> str:
        """Appends the session token to a URL, respecting existing query strings."""
        if not token:
            return url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}session={token}"

    def _fetch_swf_series(
        self,
        meeting: ParsedMeetingURL,
        base_path: str,
        target_dir: Path,
        prefix: str,
    ) -> list[Path]:
        """Fetches consecutive numbered slides (1.swf, 2.swf, ...) from a content path.

        Stops at the first missing/invalid slide. Uses max_retries=0 so that
        permission-walled paths fail fast instead of triggering retry storms.
        """
        clean_path = "/" + str(base_path).strip().lstrip("/")
        if not clean_path.endswith("/"):
            clean_path += "/"

        saved: list[Path] = []
        for n in range(1, _MAX_SLIDES_PER_DOC + 1):
            url = self._append_session(f"{meeting.base_url}{clean_path}{n}.swf", meeting.session_token)
            tmp_file = target_dir / f"{prefix}_n{n}_temp.swf"
            if not self.download_file(url, tmp_file, max_retries=0):
                tmp_file.unlink(missing_ok=True)
                break
            if self._sniff(tmp_file) != "swf":
                logger.debug(f"{prefix}: slide {n} from {clean_path} is not a valid SWF; stopping series.")
                tmp_file.unlink(missing_ok=True)
                break
            final_path = target_dir / f"{prefix}_n{n:02d}.swf"
            tmp_file.replace(final_path)
            saved.append(final_path)
        return saved

    def _resolve_sco_url_path(self, meeting: ParsedMeetingURL, sco_id: str) -> str | None:
        """Resolves a sco-id to its LIVE url-path via the XML API.

        mainstream.xml descriptors frequently carry stale paths (legacy /_a7/
        mounts that no longer map to files); sco-info always returns the
        current location. Results are cached per downloader instance.
        """
        if not sco_id or not sco_id.isdigit():
            return None
        cache = getattr(self, "_sco_url_cache", None)
        if cache is None:
            cache = self._sco_url_cache = {}
        if sco_id in cache:
            return cache[sco_id]

        url_path = None
        try:
            api_url = f"{meeting.base_url}/api/xml?action=sco-info&sco-id={sco_id}"
            r = self.session.get(api_url, timeout=self.config.request_timeout)
            m = re.search(r"<url-path>([^<]+)</url-path>", r.text)
            if m:
                url_path = m.group(1).strip()
                logger.debug(f"sco-info {sco_id} -> {url_path}")
        except Exception as e:
            logger.debug(f"sco-info lookup failed for {sco_id}: {e}")

        cache[sco_id] = url_path
        return url_path

    def _original_asset_name(self, desc: dict) -> Optional[str]:
        """Recovers the original upload filename (e.g. 'Lecture.pdf') from a descriptor."""
        name = desc.get("name")
        if name and name.lower().endswith(".pdf"):
            return re.sub(r'[\\/*?:"<>|]', "", name).strip()
        dl = desc.get("downloadUrl")
        if dl and "name=" in dl:
            raw = dl.split("name=", 1)[1].split("&", 1)[0]
            try:
                import urllib.parse
                decoded = urllib.parse.unquote_plus(raw)
            except Exception:
                decoded = raw
            decoded = re.sub(r'[\\/*?:"<>|]', "", decoded).strip()
            if decoded:
                return decoded
        return None

    def _fetch_slides_via_sco_api(
        self,
        meeting: ParsedMeetingURL,
        desc: dict,
        idx: int,
        target_dir: Path,
    ) -> list[Path]:
        """Tier-3 fallback: resolve the document's live url-path via the XML API and
        download the server-converted source PDF at {url-path}default/connect.pdf.

        This is the route the Adobe Connect viewer itself uses for shared PDFs;
        it works even when every path embedded in mainstream.xml is stale or walled.
        """
        saved: list[Path] = []
        for sco_id in (desc.get("originatingSco"), desc.get("scoID"), desc.get("id")):
            url_path = self._resolve_sco_url_path(meeting, sco_id or "")
            if not url_path:
                continue
            pdf_url = f"{meeting.base_url}{url_path.rstrip('/')}/default/connect.pdf"
            tmp_file = target_dir / f"slide_{idx:03d}_api_temp.pdf"
            logger.info(f"Trying SCO-API slide fetch ({sco_id}): {pdf_url}")
            if self.download_file(pdf_url, tmp_file, max_retries=0) and self._sniff(tmp_file) == "pdf":
                final_path = target_dir / f"slide_{idx:03d}.pdf"
                tmp_file.replace(final_path)
                saved.append(final_path)

                # Preserve the original-named copy for the assets export step.
                original_name = self._original_asset_name(desc)
                if original_name:
                    try:
                        assets_dir = self.config.downloads_dir / f"{meeting.meeting_id}_assets"
                        assets_dir.mkdir(parents=True, exist_ok=True)
                        asset_copy = assets_dir / original_name
                        if not asset_copy.exists():
                            shutil.copy2(final_path, asset_copy)
                            logger.info(f"Saved presentation source as: {assets_dir.name}/{original_name}")
                    except Exception as e:
                        logger.warning(f"Could not save asset copy of {final_path.name}: {e}")
                break
            tmp_file.unlink(missing_ok=True)
        return saved

    def _fetch_slides_from_source_zip(
        self,
        meeting: ParsedMeetingURL,
        desc: dict,
        idx: int,
        target_dir: Path,
    ) -> list[Path]:
        """Tier-3 fallback: downloads the source/output ZIP bundle and extracts slides from it.

        Mirrors the HosseinShams00/AdobeConnectDownloader strategy:
        /source/{docId}.zip?download=zip -> /output/{docId}.zip -> /source/{recId}.zip (once per run).
        """
        candidates: list[str] = []
        doc_id = desc.get("id")
        if doc_id:
            candidates.append(f"{meeting.base_url}/source/{doc_id}.zip?download=zip")
            candidates.append(f"{meeting.base_url}/output/{doc_id}.zip?download=zip")
        # Last resort only, and at most once per run: recording-level bundles can be huge.
        if not getattr(self, "_rec_zip_attempted", False):
            self._rec_zip_attempted = True
            candidates.append(f"{meeting.base_url}/source/{meeting.meeting_id}.zip?download=zip")

        for c_idx, zip_url in enumerate(candidates[:_MAX_SOURCE_ZIP_CANDIDATES]):
            zip_url = self._append_session(zip_url, meeting.session_token)
            zip_file = target_dir / f"_srcslide_{idx}_{c_idx}_temp.zip"
            logger.info(f"Trying slide ZIP fallback: {zip_url}")
            if not self.download_file(zip_url, zip_file, max_retries=0):
                zip_file.unlink(missing_ok=True)
                continue
            if self._sniff(zip_file) != "zip":
                logger.warning(f"ZIP fallback returned non-zip payload ({self._sniff(zip_file)}): {zip_url}")
                zip_file.unlink(missing_ok=True)
                continue

            extract_dir = target_dir / f"_srcslide_{idx}_{c_idx}_extracted"
            if not self.extract_zip(zip_file, extract_dir):
                zip_file.unlink(missing_ok=True)
                continue

            slide_files = [
                p for p in list(extract_dir.rglob("*.swf")) + list(extract_dir.rglob("*.pdf"))
                if p.stat().st_size > 1024 and "_temp" not in p.name
            ]
            slide_files.sort(key=lambda p: _natural_key(p.name))

            saved: list[Path] = []
            for n, src in enumerate(slide_files, start=1):
                final_path = target_dir / f"slide_{idx:03d}_z{n:02d}{src.suffix.lower()}"
                shutil.copy2(src, final_path)
                saved.append(final_path)

            shutil.rmtree(extract_dir, ignore_errors=True)
            zip_file.unlink(missing_ok=True)

            if saved:
                logger.info(f"Extracted {len(saved)} slide file(s) from ZIP bundle.")
                return saved
        return []

    def fetch_slide_backdrops(self, meeting: ParsedMeetingURL, target_dir: Path, xml_text: str, progress_callback=None) -> list[Path]:
        """Cascade-fetch presentation slides for every document shared in the meeting.

        Tiers per descriptor:
          1. {playbackContentOutputPath}{n}.swf  (public player mirror)
          2. {contentOutputPath}{n}.swf          (permission-walled on hardened servers)
          3. sco-info API -> {url-path}default/connect.pdf   (live server route)
          4. /source/{id}.zip?download=zip       (original asset bundle, unzipped locally)
        """
        saved_paths: list[Path] = []
        descriptors = self._parse_document_descriptors(xml_text)

        if not descriptors:
            logger.info("No documentDescriptors found in recording metadata; no slides to fetch.")
            return saved_paths

        logger.info(f"Found {len(descriptors)} shared document(s); fetching slide backdrops...")

        for idx, desc in enumerate(descriptors, start=1):
            prefix = f"slide_{idx:03d}"
            fetched: list[Path] = []

            for path_key in ("playbackContentOutputPath", "contentOutputPath"):
                path_val = desc.get(path_key)
                if not path_val:
                    continue
                fetched = self._fetch_swf_series(meeting, path_val, target_dir, prefix)
                if fetched:
                    logger.info(f"{prefix}: fetched {len(fetched)} SWF slide(s) via {path_key}")
                    break
                logger.debug(f"{prefix}: direct SWF fetch failed via {path_key}")

            if not fetched:
                fetched = self._fetch_slides_via_sco_api(meeting, desc, idx, target_dir)

            if not fetched:
                fetched = self._fetch_slides_from_source_zip(meeting, desc, idx, target_dir)

            saved_paths.extend(fetched)

        logger.info(f"Slide backdrop fetching complete: {len(saved_paths)} file(s) saved.")
        return saved_paths

    def download_component_directory(
        self,
        meeting: ParsedMeetingURL,
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
        target_dir: Optional[Path] = None
    ) -> Path | None:
        """Downloads all individual recording streams via output HTTP directory.

        When target_dir is given (recovery mode), streams merge into an existing
        unpacked folder instead of creating a fresh one.
        """
        if target_dir is None:
            target_dir = self.config.temp_dir / f"rec_{meeting.meeting_id}_{int(time.time())}" / "unpacked"
        target_dir.mkdir(parents=True, exist_ok=True)

        mainstream_file = target_dir / "mainstream.xml"
        if not self.download_file(meeting.mainstream_url, mainstream_file):
            logger.error("Failed to download mainstream.xml")
            return None

        try:
            with open(mainstream_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # Extract stream names from <streamName> tags
            stream_names = set()
            raw_streams = re.findall(r'<streamName>(.*?)</streamName>', content, re.IGNORECASE | re.DOTALL)
            for inner in raw_streams:
                clean = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', inner)
                clean = clean.strip().lstrip("/")
                if clean:
                    stream_names.add(clean)

            fallback_matches = re.findall(
                r'((?:cameraVoip|screenshare|ftvideo|ftcontent|ftchat|ftstage|whiteboard)(?:_\d+)+)',
                content,
            )
            for m in fallback_matches:
                stream_names.add(m)

            logger.info(f"Found {len(stream_names)} media streams in mainstream.xml")

            if not stream_names:
                logger.error("Could not find any media stream references in mainstream.xml")
                return None

            # Slide backdrops are fetched once by the engine (step 3.5) for both download routes
            total_items = len(stream_names) * 2
            completed = 0
            valid_downloads = 0

            for sname in sorted(stream_names):
                for ext in [".flv", ".xml"]:
                    fname = f"{sname}{ext}"
                    furl = f"{meeting.room_url}/output/{fname}"
                    fdest = target_dir / fname
                    success = self.download_file(furl, fdest)
                    if success and fdest.exists():
                        stem = fdest.stem.lower()
                        metadata_stream = stem.startswith(
                            ("ftchat", "ftcontent", "ftstage", "ftvideo",
                             "indexstream", "mainstream", "transcriptstream"))
                        if ext == ".flv" and not metadata_stream and not _is_html_error_page(fdest):
                            # Truncation guard: flaky connections can close the
                            # stream early while urllib treats EOF as success.
                            # Validate with ffprobe; on failure retry once clean.
                            info = FFmpegUtils.probe_media_file(fdest)
                            if not (info.has_audio or info.has_video):
                                logger.warning(f"{fname}: incomplete/corrupt — retrying fresh download")
                                fdest.unlink(missing_ok=True)
                                Path(str(fdest) + ".part").unlink(missing_ok=True)
                                if self.download_file(furl, fdest):
                                    info = FFmpegUtils.probe_media_file(fdest)
                                    if not (info.has_audio or info.has_video):
                                        fdest.unlink(missing_ok=True)
                                        completed += 1
                                        if progress_callback:
                                            progress_callback(completed, total_items, 0.0)
                                        continue
                            valid_downloads += 1
                    completed += 1
                    if progress_callback:
                        progress_callback(completed, total_items, 0.0)

            if valid_downloads == 0:
                logger.error("All component downloads failed or returned error pages.")
                return None

            logger.info(f"Successfully downloaded {valid_downloads} valid FLV stream files.")
            return target_dir
        except Exception as e:
            logger.error(f"Component download failed: {e}")
            return None

    def download_assets(self, meeting: ParsedMeetingURL, unpacked_dir: Path) -> list[Path]:
        """Scans mainstream.xml for embedded assets (like uploaded PDFs) and downloads them."""
        import urllib.parse
        downloaded_assets = []
        
        mainstream_file = unpacked_dir / "mainstream.xml"
        if not mainstream_file.exists():
            return downloaded_assets
            
        try:
            with open(mainstream_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                
            # Find download URLs. Pattern might be wrapped in CDATA or just plain text.
            raw_assets = re.findall(r'(/system/download\?download-url=[^\]<]+)', content)
            asset_urls = set()
            for r in raw_assets:
                asset_urls.add(r.strip())
            
            if not asset_urls:
                return downloaded_assets
                
            logger.info(f"Found {len(asset_urls)} embedded assets in presentation.")
            
            assets_dir = self.config.downloads_dir / f"{meeting.meeting_id}_assets"
            assets_dir.mkdir(parents=True, exist_ok=True)
            
            for url_path in asset_urls:
                # url_path might be: /system/download?download-url=/_a7/pb7ccpzkpmpv/source/&name=Lecture.pdf
                full_url = f"{meeting.base_url}{url_path}"
                
                # Extract filename from URL
                qs = urllib.parse.urlparse(full_url).query
                params = urllib.parse.parse_qs(qs)
                
                name = params.get("name", ["unknown_asset.pdf"])[0]
                # Clean filename
                name = re.sub(r'[\\/*?:"<>|]', "", name)

                dest_path = assets_dir / name

                # The SCO-API slide tier may have already saved this source PDF
                if dest_path.exists() and dest_path.stat().st_size > 1024:
                    downloaded_assets.append(dest_path)
                    logger.info(f"Asset already present: {name}")
                    continue

                logger.info(f"Downloading asset: {name}")
                if self.download_file(full_url, dest_path):
                    # The server wraps source folders in a ZIP even when the URL name says .pdf
                    if self._sniff(dest_path) == "zip":
                        fixed_path = dest_path.with_suffix(".zip")
                        dest_path.rename(fixed_path)
                        dest_path = fixed_path
                        logger.info(f"Asset was a ZIP archive; saved as {fixed_path.name}")
                    downloaded_assets.append(dest_path)
                    
            return downloaded_assets
        except Exception as e:
            logger.error(f"Failed to download assets: {e}")
            return downloaded_assets
