import os
import urllib.request
import zipfile
from pathlib import Path

def download_ffprobe():
    tools_dir = Path("tools")
    ffprobe_path = tools_dir / "ffprobe.exe"
    
    if ffprobe_path.exists():
        return
        
    print("[INFO] Python is downloading ffprobe.exe for your students...")
    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    zip_path = "ffmpeg.zip"
    
    try:
        urllib.request.urlretrieve(url, zip_path)
        print("[INFO] Extracting ffprobe.exe...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for file_info in zip_ref.infolist():
                if file_info.filename.endswith('ffprobe.exe'):
                    # Extract just this file
                    extracted_path = zip_ref.extract(file_info, path=".")
                    # Move to tools dir
                    os.rename(extracted_path, ffprobe_path)
                    break
                    
        # Cleanup
        os.remove(zip_path)
        import shutil
        for p in Path(".").glob("ffmpeg-*"):
            if p.is_dir():
                shutil.rmtree(p)
                
        print("[SUCCESS] ffprobe.exe downloaded successfully!")
    except Exception as e:
        print(f"[ERROR] Failed to download ffprobe via Python: {e}")
        
if __name__ == "__main__":
    download_ffprobe()
