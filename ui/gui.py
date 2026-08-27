import sys
import threading
import subprocess
from pathlib import Path

# Auto-install customtkinter if missing
try:
    import customtkinter as ctk
except ImportError:
    try:
        subprocess.run(["uv", "pip", "install", "customtkinter", "darkdetect"], check=True, capture_output=True)
    except Exception:
        subprocess.run([sys.executable, "-m", "pip", "install", "customtkinter", "darkdetect"], check=True, capture_output=True)
    import customtkinter as ctk

from tkinter import filedialog, messagebox
from core.config import AppConfig
from core.engine import ACDownloadEngine
from utils.logger import logger

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

class ModernGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("AC-Downloader Pro")
        self.geometry("750x650")
        self.minsize(750, 650)
        
        self.config = AppConfig()
        self.engine = ACDownloadEngine(self.config)
        self.is_processing = False

        self._build_ui()

    def _build_ui(self):
        # Grid layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header Frame
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")
        
        title = ctk.CTkLabel(header_frame, text="AC-Downloader Pro", font=ctk.CTkFont(size=24, weight="bold"))
        title.pack(anchor="w")
        
        subtitle = ctk.CTkLabel(header_frame, text="Lossless Adobe Connect Media Extractor", text_color="gray")
        subtitle.pack(anchor="w")

        # Main Card Frame
        card = ctk.CTkFrame(self, corner_radius=15)
        card.grid(row=1, column=0, padx=20, pady=10, sticky="ew")
        card.grid_columnconfigure(1, weight=1)

        # URL Input
        ctk.CTkLabel(card, text="Recording URL:", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=20, pady=(20, 5), sticky="w")
        self.url_var = ctk.StringVar()
        self.url_entry = ctk.CTkEntry(card, textvariable=self.url_var, placeholder_text="https://domain.com/p12345/...", width=400)
        self.url_entry.grid(row=0, column=1, padx=(0, 20), pady=(20, 5), sticky="ew")

        # Cookie Input
        ctk.CTkLabel(card, text="BREEZESESSION:", font=ctk.CTkFont(weight="bold")).grid(row=1, column=0, padx=20, pady=5, sticky="w")
        self.cookie_var = ctk.StringVar()
        self.cookie_entry = ctk.CTkEntry(card, textvariable=self.cookie_var, placeholder_text="(Optional) Only for private LMS classes")
        self.cookie_entry.grid(row=1, column=1, padx=(0, 20), pady=5, sticky="ew")

        # Output Format & Res
        options_frame = ctk.CTkFrame(card, fg_color="transparent")
        options_frame.grid(row=2, column=0, columnspan=2, padx=20, pady=10, sticky="ew")
        options_frame.grid_columnconfigure((0, 1, 2), weight=1)

        self.fmt_var = ctk.StringVar(value="MP4 Video")
        fmt_menu = ctk.CTkOptionMenu(options_frame, variable=self.fmt_var, values=["MP4 Video", "MP3 Audio"])
        fmt_menu.grid(row=0, column=0, padx=(0, 10), sticky="ew")

        self.res_var = ctk.StringVar(value="1080p")
        res_menu = ctk.CTkOptionMenu(options_frame, variable=self.res_var, values=["1080p", "720p", "480p"])
        res_menu.grid(row=0, column=1, padx=10, sticky="ew")

        # Save Dir
        self.dir_var = ctk.StringVar(value=str(self.config.downloads_dir))
        dir_btn = ctk.CTkButton(options_frame, text="Select Output Folder", command=self._choose_folder, fg_color="#334155", hover_color="#475569")
        dir_btn.grid(row=0, column=2, padx=(10, 0), sticky="ew")
        
        self.dir_lbl = ctk.CTkLabel(options_frame, textvariable=self.dir_var, text_color="gray", font=ctk.CTkFont(size=11))
        self.dir_lbl.grid(row=1, column=0, columnspan=3, pady=(5, 0), sticky="w")

        # Start Button
        self.start_btn = ctk.CTkButton(card, text="Download & Process", command=self._start_download, font=ctk.CTkFont(size=14, weight="bold"), height=40)
        self.start_btn.grid(row=3, column=0, columnspan=2, padx=20, pady=(15, 20), sticky="ew")

        # Log Frame
        log_frame = ctk.CTkFrame(self, corner_radius=15)
        log_frame.grid(row=2, column=0, padx=20, pady=(0, 20), sticky="nsew")
        log_frame.grid_rowconfigure(1, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        self.status_var = ctk.StringVar(value="Ready.")
        status_lbl = ctk.CTkLabel(log_frame, textvariable=self.status_var, font=ctk.CTkFont(weight="bold"))
        status_lbl.grid(row=0, column=0, padx=15, pady=(10, 5), sticky="w")

        self.log_text = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Consolas", size=12), text_color="#a5b4fc", fg_color="#020617")
        self.log_text.grid(row=1, column=0, padx=15, pady=(0, 15), sticky="nsew")

    def _choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.config.downloads_dir)
        if folder:
            self.config.downloads_dir = Path(folder)
            self.dir_var.set(folder)

    def _append_log(self, text: str):
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")

    def _start_download(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Warning", "Please enter a valid Adobe Connect URL.")
            return

        if self.is_processing:
            return

        self.is_processing = True
        self.start_btn.configure(state="disabled", text="Processing...")
        self.log_text.delete("0.0", "end")

        res_map = {"1080p": "1920x1080", "720p": "1280x720", "480p": "854x480"}
        self.config.resolution = res_map.get(self.res_var.get(), "1920x1080")
        fmt = "mp3" if "MP3" in self.fmt_var.get() else "mp4"

        threading.Thread(
            target=self._worker,
            args=(url, self.cookie_var.get().strip(), fmt),
            daemon=True
        ).start()

    def _worker(self, url: str, cookie: str, fmt: str):
        def progress_cb(phase: str, pct: float, msg: str):
            self.after(0, lambda p=phase, pc=pct, m=msg: self._update_ui(p, pc, m))

        try:
            res = self.engine.process_recording(
                url=url,
                cookie=cookie if cookie else None,
                output_format=fmt,
                progress_callback=progress_cb
            )
            if res:
                self.after(0, lambda r=res: messagebox.showinfo("Success", f"Video saved successfully!\n{r.name}"))
            else:
                self.after(0, lambda: messagebox.showerror("Error", "Download failed. Check the activity log."))
        except Exception as e:
            self.after(0, lambda err=str(e): self._append_log(f"[EXCEPTION] {err}"))
        finally:
            self.is_processing = False
            self.after(0, lambda: self.start_btn.configure(state="normal", text="Download & Process"))

    def _update_ui(self, phase: str, pct: float, msg: str):
        self.status_var.set(f"[{phase}] {msg}")
        self._append_log(f"[{phase}] {msg}")

def launch_gui():
    app = ModernGUI()
    app.mainloop()

if __name__ == "__main__":
    launch_gui()
