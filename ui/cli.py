import argparse
import sys
import time
from pathlib import Path
from core.config import AppConfig, DEFAULT_CONFIG
from core.engine import ACDownloadEngine
from utils.logger import logger

def render_banner():
    banner = (
        "\033[36m"
        "========================================================================\n"
        "  AC-Downloader Pro - Enterprise Adobe Connect Video Downloader\n"
        "  Supports Tabriz University, Sharif, Tehran, Payame Noor & All LMS Portals\n"
        "========================================================================\n"
        "\033[0m"
    )
    print(banner)

def print_progress_bar(phase: str, pct: float, msg: str):
    bar_len = 35
    filled_len = int(bar_len * pct / 100.0)
    bar = "=" * filled_len + "-" * (bar_len - filled_len)
    sys.stdout.write(f"\r\033[36m[{phase:<8}]\033[0m [{bar}] \033[32m{pct:5.1f}%\033[0m | {msg:<45}")
    sys.stdout.flush()
    if pct >= 100.0 or phase in ["DONE", "ERROR"]:
        sys.stdout.write("\n")
        sys.stdout.flush()

def run_cli():
    parser = argparse.ArgumentParser(
        description="AC-Downloader Pro: Download and Convert Adobe Connect recorded classes to MP4/MP3."
    )
    parser.add_argument("url", nargs="?", help="Adobe Connect recording URL (e.g., https://tuvc2.tabrizu.ac.ir/p12345/...)")
    parser.add_argument("-c", "--cookie", help="BREEZESESSION cookie or full Cookie header")
    parser.add_argument("-u", "--username", help="Adobe Connect or LMS username")
    parser.add_argument("-p", "--password", help="Adobe Connect or LMS password")
    parser.add_argument("-b", "--batch", help="Path to text file containing list of recording URLs")
    parser.add_argument("-f", "--format", choices=["mp4", "mp3"], default="mp4", help="Output format (default: mp4)")
    parser.add_argument("-r", "--resolution", choices=["1920x1080", "1280x720", "854x480"], default="1920x1080", help="Output resolution")
    parser.add_argument("-l", "--layout", choices=["smart_pip", "screenshare_only", "camera_only", "audio_only"], default="smart_pip", help="Video composition layout")
    parser.add_argument("-o", "--output-dir", help="Destination folder for downloaded videos")
    parser.add_argument("--keep-raw", action="store_true", help="Keep raw extracted XML/FLV files")
    parser.add_argument("--gui", action="store_true", help="Launch Graphical User Interface")
    
    args = parser.parse_args()
    
    if args.gui:
        from ui.gui import launch_gui
        launch_gui()
        return

    render_banner()
    
    config = AppConfig()
    if args.output_dir:
        config.downloads_dir = Path(args.output_dir)
    config.resolution = args.resolution
    config.layout_mode = args.layout
    config.keep_raw_files = args.keep_raw

    engine = ACDownloadEngine(config)

    # Collect URLs to process
    urls = []
    if args.batch:
        batch_path = Path(args.batch)
        if batch_path.is_file():
            with open(batch_path, "r", encoding="utf-8") as f:
                urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
            print(f"\033[32mLoaded {len(urls)} URLs from batch file: {args.batch}\033[0m")
        else:
            print(f"\033[31mBatch file not found: {args.batch}\033[0m")
            sys.exit(1)
    elif args.url:
        urls = [args.url]
    else:
        # Interactive prompt
        print("\033[33mEnter Adobe Connect Recording URL:\033[0m")
        u = input("URL: ").strip()
        if not u:
            print("\033[31mNo URL provided. Exiting.\033[0m")
            return
        urls = [u]
        
        cookie_prompt = input("Cookie / BREEZESESSION (Press Enter to skip if guest or token in URL): ").strip()
        if cookie_prompt:
            args.cookie = cookie_prompt

    # Process all URLs
    for idx, u in enumerate(urls, 1):
        print(f"\n\033[1;36m>>> Processing [{idx}/{len(urls)}] {u}\033[0m")
        
        def progress_callback(phase: str, pct: float, msg: str):
            print_progress_bar(phase, pct, msg)

        result = engine.process_recording(
            url=u,
            cookie=args.cookie,
            username=args.username,
            password=args.password,
            output_format=args.format,
            progress_callback=progress_callback
        )
        
        if result:
            print(f"\n\033[1;32m[OK] Successfully saved: {result}\033[0m\n")
        else:
            print(f"\n\033[1;31m[FAILED] Could not process recording: {u}\033[0m\n")
