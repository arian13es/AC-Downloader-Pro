"""AC-Downloader Pro — Aurora Console.

A native Windows desktop app (WebView2 shell via pywebview, browser fallback)
hosting a local single-page UI. The engine runs in a background worker thread;
the frontend polls /api/status for live progress. Features: page-exact slide
sync, 4-tier slide fetch cascade, cooperative cancellation, download history,
theme palettes and developer credits.
"""
import os
import platform
import sys
import json
import socket
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core.config import AppConfig
from core.engine import ACDownloadEngine, JobCancelled
from utils.logger import logger, setup_logger

APP_VERSION = "1.0.0"
DEVELOPER = {"name": "Arian", "handle": "arian13es", "github": "https://github.com/arian13es"}
STACK = ["FFmpeg", "swftools", "PyMuPDF", "WebView2", "PyInstaller"]

BASE_DIR = Path(__file__).resolve().parent.parent


def _setup_file_logging():
    """Persistent run log — essential for post-mortem on failed downloads."""
    try:
        cfg = AppConfig()
        cfg.ensure_directories()
        setup_logger(log_file=cfg.temp_dir / "acdownloader.log")
    except Exception:
        pass


def _icon_bytes() -> bytes:
    """App icon for the favicon (packaged exe uses its embedded icon anyway)."""
    candidates = [
        BASE_DIR / "installer" / "app.ico",
        Path(getattr(sys, "_MEIPASS", "")) / "app.ico",
    ]
    for p in candidates:
        try:
            if p.exists():
                return p.read_bytes()
        except OSError:
            pass
    return b""


HTML_CONTENT = r"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AC-Downloader Pro</title>
<link rel="icon" href="/favicon.ico">
<link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/@fontsource/ibm-plex-mono@5.0.13/index.min.css" rel="stylesheet">
<style>
:root{
  --paper:#F2EEE6; --paper2:#E9E4D7; --paper3:#E0DAC9;
  --ink:#16140F; --ink2:#6E695C; --line:#16140F;
  --hair:rgba(22,20,15,.22);
  --sig:#1F7A50; --sig-deep:#14532D;
  --err:#B3261E;
  --mono:'IBM Plex Mono','Consolas',monospace;
}
*{box-sizing:border-box;margin:0;padding:0;font-family:'Vazirmatn',system-ui,sans-serif}
::selection{background:var(--sig);color:var(--paper)}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-track{background:var(--paper)}
::-webkit-scrollbar-thumb{background:var(--ink);border:3px solid var(--paper)}
html,body{height:100%}
body{background:var(--paper);color:var(--ink);display:flex;flex-direction:column}

/* ---------- document frame ---------- */
.doc{width:100%;flex:1;display:flex;flex-direction:column;min-height:0;background:var(--paper)}
.masthead{display:flex;align-items:baseline;justify-content:space-between;
  padding:14px 26px 10px;border-bottom:2px solid var(--line)}
.brand{display:flex;align-items:baseline;gap:10px}
.brand .mark{font-family:var(--mono);font-weight:700;font-size:1.02rem;letter-spacing:.06em}
.brand .fa{font-size:.78rem;color:var(--ink2)}
.ver{font-family:var(--mono);font-size:.72rem;color:var(--ink2);border:1px solid var(--hair);padding:1px 8px}

.tabs{display:flex;border-bottom:1px solid var(--line);background:var(--paper2)}
.tab{flex:1;text-align:center;padding:9px 4px;font-size:.8rem;font-weight:700;color:var(--ink2);
  cursor:pointer;border:none;background:transparent;border-bottom:2px solid transparent;transition:all .15s;font-family:inherit}
.tab:hover{color:var(--ink)}
.tab.on{color:var(--ink);border-bottom-color:var(--sig);background:var(--paper)}
.tab.on .tno{color:var(--sig)}
.tab .tno{font-family:var(--mono);font-size:.66rem;margin-inline-end:6px}

main{flex:1;overflow-y:auto;min-height:0}
.view{display:none;padding:18px 26px 30px;max-width:780px;margin:0 auto;width:100%;position:relative}
.view.on{display:block;animation:vin .22s ease both}
.view>*{position:relative;z-index:1}
@keyframes vin{from{opacity:0;transform:translateY(6px)}to{opacity:1}}

.sec{display:flex;align-items:baseline;gap:10px;padding:8px 0 10px;margin-bottom:14px;border-bottom:1px solid var(--hair)}
.sec .no{font-family:var(--mono);font-size:.7rem;color:var(--sig);font-weight:700}
.sec .t{font-size:.92rem;font-weight:800}
.sec .en{font-family:var(--mono);font-size:.62rem;color:var(--ink2);margin-inline-start:auto;letter-spacing:.12em;position:relative;z-index:3}

/* ---------- form ---------- */
.field{margin-bottom:16px}
.field label{display:flex;justify-content:space-between;align-items:baseline;gap:10px;
  font-size:.8rem;font-weight:700;color:var(--ink);margin-bottom:6px}
.field label .lbl-en{font-family:var(--mono);font-size:.6rem;letter-spacing:.12em;color:var(--ink2)}
.inrow{display:flex;gap:10px;align-items:center}
input[type=text],select{
  width:100%;padding:10px 2px;background:transparent;border:none;border-bottom:2px solid var(--ink);
  color:var(--ink);font-size:.95rem;outline:none;border-radius:0;transition:border-color .15s;font-family:inherit;
}
input::placeholder{color:rgba(22,20,15,.35)}
input:focus,select:focus{border-bottom-color:var(--sig)}
select{cursor:pointer;appearance:none;-webkit-appearance:none;
  padding:12px 2px 12px 40px;line-height:1.4;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='14' height='9' viewBox='0 0 14 9'><path d='M1.5 1.5L7 7l5.5-5.5' fill='none' stroke='%2316140F' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/></svg>");
  background-repeat:no-repeat;background-position:left 6px center;}
.mini{flex:none;background:var(--paper);border:1px solid var(--ink);color:var(--ink);font-family:var(--mono);
  font-size:.66rem;padding:11px 16px;cursor:pointer;transition:all .15s;letter-spacing:.05em}
.mini:hover{background:var(--ink);color:var(--paper)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}

/* ---------- hero ---------- */
.hero{margin-bottom:18px}
.hero-txt h1{font-size:1.5rem;font-weight:900;line-height:1.5}
.hero-txt h1 b{color:var(--sig-deep)}
.hero-txt p{color:var(--ink2);font-size:.8rem;line-height:1.9;margin-top:8px;max-width:560px}

/* ---------- custom dropdown ---------- */
.dd{position:relative}
.dd-btn{width:100%;display:flex;justify-content:space-between;align-items:center;gap:10px;
  padding:11px 2px;background:transparent;border:none;border-bottom:2px solid var(--ink);
  color:var(--ink);font-family:inherit;font-size:.95rem;cursor:pointer;text-align:right;transition:border-color .15s}
.dd-btn .chev{font-family:var(--mono);font-size:.7rem;color:var(--ink2);transition:transform .18s}
.dd.open .dd-btn{border-bottom-color:var(--sig)}
.dd.open .chev{transform:rotate(180deg);color:var(--sig)}
.dd.disabled{opacity:.4;pointer-events:none}
.dd-list{position:absolute;top:calc(100% + 8px);right:0;left:0;z-index:40;display:none;
  background:var(--paper);border:1.5px solid var(--ink);box-shadow:8px 8px 0 rgba(22,20,15,.14)}
.dd.open .dd-list{display:block;animation:vin .16s ease both}
.dd-item{width:100%;text-align:right;padding:11px 14px;background:transparent;border:none;
  border-bottom:1px solid var(--hair);color:var(--ink);font-family:inherit;font-size:.86rem;cursor:pointer;
  display:flex;justify-content:space-between;align-items:center;transition:all .12s}
.dd-item:last-child{border-bottom:none}
.dd-item .tag{font-family:var(--mono);font-size:.58rem;color:var(--ink2)}
.dd-item:hover{background:var(--ink);color:var(--paper)}
.dd-item:hover .tag{color:var(--paper2)}
.dd-item.on{color:var(--sig-deep);font-weight:800}
.dd-item.on::after{content:'●';font-size:.5rem;color:var(--sig)}

/* ---------- spec strip ---------- */
.specline{display:flex;border:1px solid var(--ink);margin-top:14px}
.specline div{flex:1;text-align:center;padding:9px 4px;font-size:.72rem;color:var(--ink2);
  border-inline-start:1px solid var(--hair)}
.specline div:first-child{border-inline-start:none}
.specline b{color:var(--ink);font-weight:800}

/* ---------- page watermark ---------- */
.seg{display:flex;border:1px solid var(--ink)}
.seg button{flex:1;padding:8px 4px;border:none;background:transparent;color:var(--ink2);cursor:pointer;
  font-family:inherit;font-size:.82rem;font-weight:700;transition:all .15s}
.seg button+button{border-inline-start:1px solid var(--ink)}
.seg button.on{background:var(--ink);color:var(--paper)}
.seg button.on .sig-dot{background:var(--sig)}
.sig-dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:transparent;margin-inline-end:6px;vertical-align:1px}
details.adv{margin:2px 0 14px}
details.adv summary{cursor:pointer;font-size:.78rem;color:var(--ink2);list-style:none;width:fit-content;transition:color .15s}
details.adv summary:hover{color:var(--sig)}
details.adv[open] summary{color:var(--sig)}
details.adv summary .m{font-family:var(--mono);font-size:.68rem;letter-spacing:.06em;direction:ltr;unicode-bidi:embed}

.cta{
  display:inline-flex;align-items:center;gap:10px;padding:12px 30px;cursor:pointer;
  background:var(--ink);color:var(--paper);border:1px solid var(--ink);
  font-family:inherit;font-size:.95rem;font-weight:800;transition:background .15s,transform .1s;
}
.cta:hover:not(:disabled){background:var(--sig);border-color:var(--sig)}
.cta:active:not(:disabled){transform:scale(.975)}
.cta:disabled{opacity:.45;cursor:not-allowed}
.cta .arr{font-family:var(--mono)}
.spinner{width:15px;height:15px;border:2px solid rgba(242,238,230,.35);border-top-color:var(--paper);border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* ---------- progress instrument ---------- */
#runCard{display:none}
.instr{display:flex;align-items:flex-end;gap:22px;margin-bottom:6px}
.bignum{font-family:var(--mono);font-size:3.4rem;font-weight:700;line-height:1;letter-spacing:-.03em;min-width:150px}
.bignum small{font-size:1.3rem;color:var(--ink2)}
.bignum.err{color:var(--err)}
.tape{flex:1;padding-bottom:6px}
.ticks{height:26px;position:relative;background:
  repeating-linear-gradient(to left,var(--hair) 0 1px,transparent 1px 5%);border-bottom:2px solid var(--ink)}
.ticks .fill{position:absolute;inset:0;width:0%;background:var(--sig);
  clip-path:polygon(0 0,100% 0,100% 100%,0 100%);
  mask:repeating-linear-gradient(to left,#000 0 calc(5% - 2px),transparent calc(5% - 2px) 5%);
  transition:width .3s cubic-bezier(.2,.8,.3,1)}
.ticks .head{position:absolute;top:-4px;bottom:-2px;width:2px;background:var(--sig);left:0;transition:left .3s cubic-bezier(.2,.8,.3,1)}
.readout{display:flex;gap:18px;flex-wrap:wrap;font-family:var(--mono);font-size:.72rem;color:var(--ink2);margin:8px 0 14px;direction:ltr}
.readout b{color:var(--ink);font-weight:700}
.readout .live{color:var(--sig)}

.steps{display:flex;flex-wrap:wrap;gap:4px 14px;padding:9px 0;border-top:1px solid var(--hair);border-bottom:1px solid var(--hair);margin-bottom:12px}
.st{font-size:.72rem;font-weight:600;color:var(--ink2);display:flex;align-items:center;gap:6px}
.st .n{font-family:var(--mono);font-size:.64rem}
.st i{width:9px;height:9px;border:1.5px solid var(--ink2);display:inline-block;transition:all .2s}
.st.done{color:var(--ink)}
.st.done i{background:var(--ink);border-color:var(--ink)}
.st.act{color:var(--sig-deep);font-weight:800}
.st.act i{border-color:var(--sig);background:var(--sig);animation:blink 1s steps(2) infinite}
@keyframes blink{50%{opacity:.25}}

.statusline{font-size:.86rem;min-height:1.35em;margin-bottom:9px}
.statusline small{display:block;color:var(--ink2);font-size:.72rem;margin-top:2px}
.dlstats{margin-bottom:10px}
.dltext{display:flex;justify-content:space-between;font-family:var(--mono);font-size:.7rem;color:var(--ink2);direction:ltr;margin-bottom:3px}
.dltext b{color:var(--sig-deep)}
.console{background:var(--paper2);border:1px solid var(--ink);height:150px;overflow-y:auto;
  padding:8px 11px;font-family:var(--mono);font-size:.7rem;direction:ltr;text-align:left;line-height:1.65}
.console div{white-space:pre-wrap;word-break:break-all}
.c-info{color:#4A463C}.c-ok{color:var(--sig-deep);font-weight:700}.c-err{color:var(--err)}.c-warn{color:#8A5A00}

/* ---------- results / lists ---------- */
#resultCard{display:none;margin-top:18px}
.badge{font-size:.74rem;font-weight:800;padding:3px 12px;border:1px solid var(--ink);letter-spacing:.02em}
.badge.ok{background:var(--ink);color:var(--paper)}
.badge.err{background:var(--err);border-color:var(--err);color:var(--paper)}
.files{border-top:none}
.frow{display:flex;align-items:center;gap:12px;padding:9px 2px;border-bottom:1px solid var(--hair);transition:background .15s;animation:vin .25s both}
.frow:hover{background:var(--paper2)}
.ftag{font-family:var(--mono);font-size:.58rem;letter-spacing:.08em;border:1px solid var(--ink);padding:2px 7px;flex:none}
.fmeta{flex:1;min-width:0}
.fname{font-size:.85rem;font-weight:600;direction:ltr;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fsize{font-family:var(--mono);font-size:.66rem;color:var(--ink2);direction:ltr;text-align:right}
.actions{display:flex;gap:8px;margin-top:14px}
.btn2{padding:8px 16px;background:transparent;border:1px solid var(--ink);color:var(--ink);cursor:pointer;
  font-family:inherit;font-size:.8rem;font-weight:700;transition:all .15s}
.btn2:hover{background:var(--ink);color:var(--paper)}

/* ---------- history / about ---------- */
.empty{font-size:.8rem;color:var(--ink2);padding:26px 0;text-align:center}
.about{padding-top:6px}
.about .big{font-size:2.6rem;font-weight:900;line-height:1.15;letter-spacing:-.02em}
.about .desc{color:var(--ink2);font-size:.88rem;line-height:2;max-width:520px;margin:14px 0 20px}
.contacts{display:flex;border:1.5px solid var(--ink);margin-bottom:4px}
.contact{flex:1;display:flex;flex-direction:column;align-items:center;gap:5px;padding:14px 8px;
  text-decoration:none;color:var(--ink2);font-family:var(--mono);font-size:.58rem;letter-spacing:.16em;
  border-inline-start:1px solid var(--hair);transition:all .18s}
.contact:first-child{border-inline-start:none}
.contact b{font-size:.85rem;color:var(--ink);letter-spacing:.02em;direction:ltr}
.contact:hover{background:var(--ink);color:var(--paper2)}
.contact:hover b{color:var(--paper)}
.rule{height:1px;background:var(--line);margin:18px 0}
.kv{display:flex;justify-content:space-between;font-family:var(--mono);font-size:.72rem;padding:7px 0;border-bottom:1px solid var(--hair);direction:ltr}
.kv span:first-child{color:var(--ink2)}

/* ---------- footer / drop / toast ---------- */
.foot{border-top:2px solid var(--line);padding:9px 26px;display:flex;justify-content:space-between;align-items:center;
  font-size:.72rem;color:var(--ink2);background:var(--paper2)}
.foot .m{font-family:var(--mono);font-size:.64rem;letter-spacing:.08em}
.foot a{color:var(--ink);text-decoration:none;border-bottom:1px solid var(--sig)}
.foot a:hover{color:var(--sig)}
#toasts{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);z-index:60;display:flex;flex-direction:column;gap:6px;align-items:center}
.toast{background:var(--ink);color:var(--paper);padding:.55rem 1.2rem;font-size:.82rem;animation:vin .25s both;border-inline-start:3px solid var(--sig)}
.toast.err{border-inline-start-color:var(--err)}
.dropzone{position:fixed;inset:12px;z-index:70;border:2px dashed var(--sig);background:rgba(242,238,230,.92);
  display:none;place-items:center;font-weight:800;font-size:1.05rem;color:var(--ink)}
@media(max-width:640px){.grid2{grid-template-columns:1fr}.instr{flex-direction:column;align-items:stretch}}
</style>
</head>
<body>
<div class="doc">
  <div class="masthead">
    <div class="brand">
      <span class="mark">AC-DOWNLOADER</span>
      <span class="fa">سند عملیات استخراج کلاس</span>
    </div>
    <span class="ver" id="verBadge">v—</span>
  </div>
  <div class="tabs stepper">
    <button class="tab on" data-view="home"><span class="tno">01</span>عملیات</button>
    <button class="tab" data-view="history"><span class="tno">02</span>بایگانی</button>
    <button class="tab" data-view="about"><span class="tno">03</span>سازنده</button>
  </div>

  <main>
    <!-- HOME -->
    <section class="view on" id="view-home">
      <div class="hero">
        <div class="hero-txt">
          <h1>دانلودر کلاس‌های سامانه tulms <b>دانشگاه تبریز</b></h1>
          <p>دانلود و بازسازی کامل کلاس‌های Adobe Connect دانشگاه تبریز. صدا، تصویر و تک‌تک اسلایدها — سینک‌شده تا سطح هر صفحه. تمام پردازش کاملاً آفلاین و روی سیستم شما انجام می‌شود.</p>
        </div>
      </div>
      <div class="sec"><span class="no">01</span><span class="t">ورودی</span><span class="en">INPUT</span></div>
      <div class="field">
        <label><span>لینک کلاس ضبط‌شده</span><span class="lbl-en">RECORDING URL</span></label>
        <div class="inrow">
          <input type="text" id="url" dir="ltr" placeholder="https://ac.example.ac.ir/xxxxx/?session=..." spellcheck="false">
          <button class="mini" onclick="pasteUrl()">PASTE</button>
        </div>
      </div>
      <div class="grid2">
        <div class="field">
          <label><span>فرمت خروجی</span><span class="lbl-en">FORMAT</span></label>
          <div class="seg" id="fmtSeg">
            <button data-v="mp4" class="on"><span class="sig-dot"></span>MP4 ویدیو</button>
            <button data-v="mp3"><span class="sig-dot"></span>MP3 صدا</button>
          </div>
        </div>
        <div class="field">
          <label><span>کیفیت تصویر</span><span class="lbl-en">QUALITY</span></label>
          <div class="dd" id="ddRes">
            <button type="button" class="dd-btn" onclick="toggleDD(event)">
              <span id="ddLabel">1080p — Full HD</span><span class="chev">▼</span>
            </button>
            <div class="dd-list">
              <button type="button" class="dd-item on" data-v="1920x1080" data-label="1080p — Full HD">1080p — Full HD<span class="tag">1920×1080</span></button>
              <button type="button" class="dd-item" data-v="1280x720" data-label="720p — HD">720p — HD<span class="tag">1280×720</span></button>
            </div>
          </div>
          <input type="hidden" id="res" value="1920x1080">
        </div>
      </div>
      <details class="adv">
        <summary>توکن دسترسی <span class="m" dir="ltr">BREEZESESSION</span> — اختیاری؛ خودکار از لینک استخراج می‌شود</summary>
        <div class="field" style="margin-top:8px">
          <input type="text" id="cookie" dir="ltr" placeholder="breez..." spellcheck="false">
        </div>
      </details>
      <button class="cta" id="go" onclick="toggleRun()">
        <span class="arr">▸</span><span id="goText">شروع عملیات استخراج</span>
      </button>
      <div class="specline">
        <div>موتور <b>FFmpeg</b></div>
        <div>خروجی <b>MP4 · MP3</b></div>
        <div>اسلاید <b>صفحه‌به‌صفحه</b></div>
        <div>پردازش <b>۱۰۰٪ محلی</b></div>
      </div>

      <div id="runCard" style="margin-top:20px">
        <div class="sec"><span class="no">02</span><span class="t">پیشرفت</span><span class="en">PROGRESS</span></div>
        <div class="instr">
          <div class="bignum"><span id="pct" dir="ltr">0</span><small>%</small></div>
          <div class="tape">
            <div class="ticks"><div class="fill" id="barFill"></div><div class="head" id="barHead"></div></div>
            <div class="readout">
              <span id="roPhase">PHASE: <b>—</b></span>
              <span id="dlStats" style="display:none">FILE: <b id="dlText"></b> <span class="live" id="dlPct"></span></span>
            </div>
          </div>
        </div>
        <div class="steps" id="steps"></div>
        <div class="statusline"><span id="statusText">آماده…</span><small>Ctrl+Enter شروع · Esc لغو</small></div>
        <div class="console" id="con"></div>
      </div>

      <div id="resultCard">
        <div class="sec" style="margin-top:20px"><span class="no">03</span><span class="t">خروجی</span><span class="en">OUTPUT</span></div>
        <div style="margin-bottom:10px" id="resHead"></div>
        <div class="files" id="files"></div>
        <div class="actions" id="resActions"></div>
      </div>
    </section>

    <!-- HISTORY -->
    <section class="view" id="view-history">
      <div class="sec"><span class="no">02</span><span class="t">بایگانی خروجی‌ها</span><span class="en">ARCHIVE</span></div>
      <div class="files" id="histList"><div class="empty">در حال بارگذاری…</div></div>
      <div class="actions">
        <button class="btn2" onclick="fetch('/api/open-folder',{method:'POST'})">📁 باز کردن پوشه خروجی</button>
        <button class="btn2" onclick="loadHistory()">↺ بروزرسانی</button>
      </div>
    </section>

    <!-- ABOUT -->
    <section class="view" id="view-about">
      <div class="sec"><span class="no">03</span><span class="t">سازنده</span><span class="en">COLOPHON</span></div>
      <div class="about">
        <div class="big">آرین</div>
        <p class="desc">
          این برنامه به‌طور ویژه برای دانلود ویدیوهای آموزشی از سامانه tulms <b>دانشگاه تبریز</b> توسعه یافته است. ابزاری برای استخراج آفلاین کلاس‌های Adobe Connect که صدای استاد، تصویر و تمام اسلایدها را استخراج کرده و یک فایل ویدیویی بی‌نقص به شما تحویل می‌دهد. تمام پردازش‌ها روی سیستم شما و بدون نیاز به اینترنت (پس از اتمام دانلود) انجام می‌شود.
        </p>
        <div class="contacts">
          <a class="contact" href="https://github.com/arian13es" target="_blank" rel="noopener">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M12 .5C5.7.5.5 5.7.5 12.1c0 5.1 3.3 9.4 7.9 10.9.6.1.8-.2.8-.6v-2c-3.2.7-3.9-1.5-3.9-1.5-.5-1.3-1.3-1.7-1.3-1.7-1-.7.1-.7.1-.7 1.2.1 1.8 1.2 1.8 1.2 1 1.8 2.7 1.3 3.4 1 .1-.8.4-1.3.7-1.6-2.6-.3-5.3-1.3-5.3-5.8 0-1.3.5-2.3 1.2-3.2-.1-.3-.5-1.5.1-3.1 0 0 1-.3 3.2 1.2a11 11 0 0 1 5.8 0C17.3 4.6 18.3 5 18.3 5c.6 1.6.2 2.8.1 3.1.8.9 1.2 1.9 1.2 3.2 0 4.5-2.7 5.5-5.3 5.8.4.4.8 1.1.8 2.2v3.3c0 .3.2.7.8.6 4.6-1.5 7.9-5.8 7.9-10.9C23.5 5.7 18.3.5 12 .5z"/></svg>
            GITHUB<b>arian13es</b>
          </a>
          <a class="contact" href="https://t.me/arian13es" target="_blank" rel="noopener">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M23.9 3.1L20.3 20c-.3 1.2-1 1.5-2 .9l-5.5-4-2.6 2.5c-.3.3-.5.5-1.1.5l.4-5.6L19.6 4.9c.4-.4-.1-.6-.7-.2L6.4 12.7l-5.4-1.7c-1.2-.4-1.2-1.2.2-1.7L22.3 1.6c1-.4 1.9.2 1.6 1.5z"/></svg>
            TELEGRAM<b>arian13es</b>
          </a>
        </div>
        <div class="rule"></div>
        <div class="kv"><span>VERSION</span><span id="verText">—</span></div>
        <div class="kv"><span>LICENSE</span><span>MIT</span></div>
      </div>
    </section>
  </main>

  <div class="foot">
    <span class="m">DESIGN NO. AC-01 — TECHNICAL PRINT</span>
    <span>ساخته‌شده توسط <a href="https://github.com/arian13es" target="_blank" rel="noopener">arian13es</a></span>
  </div>
</div>

<div class="dropzone" id="dropzone">لینک کلاس را همین‌جا رها کنید…</div>
<div id="toasts"></div>

<script>
const PHASES=[["INIT","شروع"],["AUTH","احراز"],["PROBE","بررسی"],["DOWNLOAD","دانلود"],["PARSE","تحلیل"],["CONVERT","تبدیل"],["EXTRACT","پیوست"]];
let fmt='mp4', running=false, logIdx=0;

document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>showView(b.dataset.view));
function showView(v){
  document.querySelectorAll('.view').forEach(s=>s.classList.remove('on'));
  document.getElementById('view-'+v).classList.add('on');
  document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('on',b.dataset.view===v));
  if(v==='history') loadHistory();
  if(v==='about') loadAbout();
}
document.querySelectorAll('#fmtSeg button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('#fmtSeg button').forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); fmt=b.dataset.v;
  document.getElementById('ddRes').classList.toggle('disabled',fmt==='mp3');
});

/* custom quality dropdown */
function toggleDD(e){e.stopPropagation();document.getElementById('ddRes').classList.toggle('open');}
document.querySelectorAll('#ddRes .dd-item').forEach(it=>it.onclick=()=>{
  document.querySelectorAll('#ddRes .dd-item').forEach(x=>x.classList.remove('on'));
  it.classList.add('on');
  document.getElementById('res').value=it.dataset.v;
  document.getElementById('ddLabel').textContent=it.dataset.label;
  document.getElementById('ddRes').classList.remove('open');
});
document.addEventListener('click',e=>{
  const dd=document.getElementById('ddRes');
  if(dd.classList.contains('open')&&!dd.contains(e.target))dd.classList.remove('open');
});

document.getElementById('url').addEventListener('change',autoToken);
function autoToken(){
  const u=document.getElementById('url').value;
  const m=u.match(/[?&](?:session|ticket|breeze)=([^&]+)/i);
  if(m&&!document.getElementById('cookie').value)document.getElementById('cookie').value=m[1];
}
async function pasteUrl(){try{const t=await navigator.clipboard.readText();document.getElementById('url').value=t.trim();autoToken();}catch(e){toast('دسترسی به کلیپ‌بورد ممکن نیست','err');}}
function toast(msg,type=''){const d=document.createElement('div');d.className='toast '+type;d.textContent=msg;
  document.getElementById('toasts').appendChild(d);setTimeout(()=>d.remove(),3800);}

function buildSteps(){const el=document.getElementById('steps');el.innerHTML='';
  PHASES.forEach(([k,l],i)=>{const s=document.createElement('span');s.className='st';s.id='st-'+k;
    s.innerHTML=`<i></i><span class="n">${String(i+1).padStart(2,'0')}</span>${l}`;el.appendChild(s);});}
function setPhase(phase){
  const idx=PHASES.findIndex(p=>p[0]===phase);
  PHASES.forEach(([k],i)=>{const el=document.getElementById('st-'+k);if(!el)return;
    el.className='st '+(idx<0?'':(i<idx?'done':(i===idx?(phase==='DONE'?'done':'act'):'')));});
  document.getElementById('roPhase').innerHTML='PHASE: <b>'+(idx>=0?PHASES[idx][1]:phase)+'</b>';
}

async function toggleRun(){running?doCancel():start();}
async function start(){
  const url=document.getElementById('url').value.trim();
  if(!url){toast('لینک کلاس را وارد کنید','err');return;}
  running=true;logIdx=0;buildSteps();
  document.getElementById('runCard').style.display='block';
  document.getElementById('runCard').scrollIntoView({behavior:'smooth',block:'nearest'});
  document.getElementById('resultCard').style.display='none';
  setBtn(true);
  try{
    const r=await fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url,cookie:document.getElementById('cookie').value.trim(),format:fmt,resolution:document.getElementById('res').value})});
    const j=await r.json();
    if(!j.success){toast(j.message||'خطا','err');resetBtn();}
  }catch(e){toast('خطای شبکه: '+e,'err');resetBtn();}
  pollLoop();
}
async function doCancel(){setBtn(true,true);await fetch('/api/cancel',{method:'POST'});toast('درخواست لغو ارسال شد…');}
function setBtn(busy,cancel){
  const b=document.getElementById('go');
  b.disabled=false;
  b.innerHTML=busy?'<span class="spinner"></span><span>'+(cancel?'در حال لغو…':'لغو عملیات')+'</span>'
    :'<span class="arr">▸</span><span id="goText">شروع عملیات استخراج</span>';
  if(busy&&!cancel)b.classList.add('running');else b.classList.remove('running');
}
function resetBtn(){running=false;setBtn(false);}
function setProgress(p){
  const v=Math.max(0,Math.min(100,p));
  document.getElementById('barFill').style.width=v+'%';
  document.getElementById('barHead').style.left='calc('+v+'% - 1px)';
  document.getElementById('pct').textContent=Math.round(v);
  document.getElementById('pct').classList.toggle('err',false);
}

async function pollLoop(){
  while(running){
    try{
      const r=await fetch('/api/status?after='+logIdx);
      const j=await r.json();
      j.logs.forEach(l=>addLog(l.text,l.type));
      logIdx=j.logEnd;
      if(j.phase!==undefined&&j.phase!==''){
        setPhase(j.phase);
        setProgress(j.pct||0);
        document.getElementById('statusText').textContent=j.message||'';
        updateDl(j.phase,j.message||'');
      }
      if(j.done){
        running=false;resetBtn();
        setProgress(j.success?100:j.pct||0);
        document.getElementById('dlStats').style.display='none';
        if(j.success){showResult(j.files,'DONE — انجام شد','ok');stamp();}
        else{document.getElementById('pct').classList.add('err');showResult([],j.message||'ناموفق','err');}
        loadHistory();
        break;
      }
    }catch(e){}
    await new Promise(r=>setTimeout(r,850));
  }
}
function addLog(txt,type){
  const c=document.getElementById('con');
  const d=document.createElement('div');d.className='c-'+(type||'info');d.textContent=txt;
  c.appendChild(d);while(c.children.length>220)c.firstChild.remove();
  c.scrollTop=c.scrollHeight;
}
function updateDl(phase,msg){
  const row=document.getElementById('dlStats');
  if(phase!=='DOWNLOAD'){row.style.display='none';return;}
  row.style.display='inline';
  const m=msg.match(/([\d.]+)\s*\/\s*([\d.]+)\s*MB/);
  const sp=(msg.match(/([\d.]+)\s*MB\/s/)||[])[1];
  if(m){
    const cur=parseFloat(m[1]),tot=parseFloat(m[2]);
    const p=tot>0?Math.min(100,cur/tot*100):0;
    document.getElementById('dlText').textContent=cur.toFixed(1)+'/'+tot.toFixed(1)+'MB'+(sp?' @'+sp+'MB/s':'');
    document.getElementById('dlPct').textContent=p.toFixed(0)+'% of file';
  }else{
    const cur=(msg.match(/([\d.]+)\s*MB(?!\s*\/s)/)||[])[1];
    document.getElementById('dlText').textContent=(cur?cur+'MB':'…')+(sp?' @'+sp+'MB/s':' · size unknown');
    document.getElementById('dlPct').textContent='—';
  }
}
function fmtSize(n){if(n==null)return'';if(n>=1048576)return(n/1048576).toFixed(1)+' MB';return Math.max(1,Math.round(n/1024))+' KB'}
function showResult(files,title,cls){
  const card=document.getElementById('resultCard');card.style.display='block';
  document.getElementById('resHead').innerHTML=`<span class="badge ${cls}">${title}</span>`;
  const fl=document.getElementById('files');fl.innerHTML='';
  files.forEach(f=>{
    const d=document.createElement('div');d.className='frow';
    const tag=f.kind==='pdf'?'PDF':(f.kind==='audio'?'MP3':'MP4');
    d.innerHTML=`<span class="ftag">${tag}</span>
      <div class="fmeta"><div class="fname">${esc(f.name)}</div></div>
      <span class="fsize">${fmtSize(f.size)}${f.folder?' · '+esc(f.folder):''}</span>`;
    fl.appendChild(d);
  });
  const ac=document.getElementById('resActions');ac.innerHTML='';
  if(files.length){
    const ob=document.createElement('button');ob.className='btn2';
    ob.textContent='📁 باز کردن پوشه خروجی';
    ob.onclick=()=>fetch('/api/open-folder',{method:'POST'});
    ac.appendChild(ob);
  }
  const ag=document.createElement('button');ag.className='btn2';ag.textContent='↺ عملیات جدید';
  ag.onclick=()=>{card.style.display='none';};ac.appendChild(ag);
  card.scrollIntoView({behavior:'smooth',block:'nearest'});
  toast(title,cls==='ok'?'':'err');
}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

function stamp(){
  const s=document.createElement('div');
  s.style.cssText='position:fixed;top:38%;left:50%;transform:translate(-50%,-50%) rotate(-8deg) scale(3);z-index:80;'+
    'font-family:var(--mono);font-weight:700;font-size:2.2rem;color:var(--sig);border:4px solid var(--sig);'+
    'padding:10px 34px;letter-spacing:.15em;opacity:0;transition:all .28s cubic-bezier(.2,1.4,.4,1);pointer-events:none';
  s.textContent='DONE ✓';
  document.body.appendChild(s);
  requestAnimationFrame(()=>{s.style.opacity='1';s.style.transform='translate(-50%,-50%) rotate(-8deg) scale(1)';});
  setTimeout(()=>{s.style.opacity='0';setTimeout(()=>s.remove(),400);},1900);
}

async function loadHistory(){
  const list=document.getElementById('histList');list.innerHTML='<div class="empty">در حال بارگذاری…</div>';
  try{const j=await(await fetch('/api/history')).json();
    if(!j.files.length){list.innerHTML='<div class="empty">— بایگانی خالی است —</div>';return;}
    list.innerHTML='';
    j.files.forEach(f=>{const d=document.createElement('div');d.className='frow';
      const tag=f.kind==='pdf'?'PDF':(f.kind==='audio'?'MP3':'MP4');
      d.innerHTML=`<span class="ftag">${tag}</span>
        <div class="fmeta"><div class="fname">${esc(f.name)}</div></div>
        <span class="fsize">${new Date(f.mtime*1000).toLocaleDateString('fa-IR')} · ${fmtSize(f.size)}</span>`;
      list.appendChild(d);});
  }catch(e){list.innerHTML='<div class="empty">خطا در دریافت بایگانی</div>';}
}
async function loadAbout(){
  try{const j=await(await fetch('/api/about')).json();
    document.getElementById('verText').textContent='v'+j.version;
    document.getElementById('verBadge').textContent='v'+j.version;
  }catch(e){}
}

let dragDepth=0;
window.addEventListener('dragenter',()=>{dragDepth++;document.getElementById('dropzone').style.display='grid';});
window.addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;document.getElementById('dropzone').style.display='none';}});
window.addEventListener('dragover',e=>e.preventDefault());
window.addEventListener('drop',e=>{
  e.preventDefault();dragDepth=0;document.getElementById('dropzone').style.display='none';
  const text=e.dataTransfer.getData('text/plain')||'';
  const m=text.match(/https?:\/\/\S+/);
  if(m){document.getElementById('url').value=m[0];autoToken();toast('لینک دریافت شد');showView('home');}
});
window.addEventListener('keydown',e=>{
  if(e.ctrlKey&&e.key==='Enter'){e.preventDefault();if(!running)start();}
  if(e.key==='Escape'&&running){doCancel();}
});

buildSteps();
autoToken();
loadAbout();
</script>
</body>
</html>
"""


class JobState:
    """Shared state of the single active job."""

    def __init__(self):
        self.lock = threading.Lock()
        self.logs: list[dict] = []
        self.phase = ""
        self.pct = 0.0
        self.message = "آماده…"
        self.done = True
        self.success = False
        self.files: list[dict] = []
        self.cancel_event = threading.Event()
        self.thread: threading.Thread | None = None

    def reset(self):
        with self.lock:
            self.logs = []
            self.phase = ""
            self.pct = 0.0
            self.message = "آماده…"
            self.done = False
            self.success = False
            self.files = []
            self.cancel_event.clear()

    def log(self, text: str, type_: str = "info"):
        with self.lock:
            self.logs.append({"text": text, "type": type_})

    def snapshot(self, after: int) -> dict:
        with self.lock:
            return {
                "logs": self.logs[after:],
                "logEnd": len(self.logs),
                "phase": self.phase,
                "pct": self.pct,
                "message": self.message,
                "done": self.done,
                "success": self.success,
                "files": list(self.files),
            }


STATE = JobState()


def _collect_output_files(config: AppConfig, output_file: Path | None) -> list[dict]:
    files: list[dict] = []
    if output_file is not None and output_file.exists():
        kind = "audio" if output_file.suffix.lower() in (".mp3", ".m4a", ".aac") else "video"
        files.append({"path": str(output_file), "name": output_file.name,
                      "folder": config.downloads_dir.name, "size": output_file.stat().st_size,
                      "kind": kind})
    for pdf in sorted(config.downloads_dir.glob("*_assets/*.pdf")):
        files.append({"path": str(pdf), "name": pdf.name,
                      "folder": f"{config.downloads_dir.name}/{pdf.parent.name}",
                      "size": pdf.stat().st_size, "kind": "pdf"})
    return files


def _scan_history(config: AppConfig) -> list[dict]:
    """Latest produced media/PDF files under the downloads directory (robust walk)."""
    entries: list[dict] = []
    root = config.downloads_dir
    if not root.exists():
        return entries

    candidates = []
    try:
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                p = Path(dirpath) / fn
                try:
                    ext = p.suffix.lower().lstrip(".")
                    if "_filter.txt" in fn or "_blank" in fn or ext not in ("mp4", "mp3", "pdf"):
                        continue
                    st = p.stat()
                    kind = {"mp4": "video", "mp3": "audio", "pdf": "pdf"}[ext]
                    candidates.append((st.st_mtime, p, kind, st.st_size))
                except OSError:
                    continue
    except OSError as e:
        logger.warning(f"history scan failed: {e}")
        return entries

    candidates.sort(reverse=True)
    for mtime, p, kind, size in candidates[:40]:
        try:
            rel = p.relative_to(root)
            folder = "" if str(rel.parent) == "." else str(rel.parent)
            entries.append({"name": p.name, "folder": folder, "size": size,
                            "mtime": int(mtime), "kind": kind})
        except (ValueError, OSError):
            continue
    return entries


def _run_job(data: dict):
    config = AppConfig()
    config.resolution = data.get("resolution", "1920x1080")
    engine = ACDownloadEngine(config)

    def cb(phase: str, pct: float, msg: str):
        if msg.startswith("NOTE:"):
            STATE.log(f"[{phase}] {msg}", "warn")
        else:
            type_map = {"ERROR": "err", "CANCELLED": "warn", "DONE": "ok"}
            STATE.log(f"[{phase}] {msg}", type_map.get(phase, "info"))
        with STATE.lock:
            STATE.phase = phase
            STATE.pct = pct
            STATE.message = msg

    output_file = None
    try:
        output_file = engine.process_recording(
            url=data.get("url", ""),
            cookie=data.get("cookie", ""),
            output_format=data.get("format", "mp4"),
            progress_callback=cb,
            cancel_event=STATE.cancel_event,
        )
        success = output_file is not None
        files = _collect_output_files(config, output_file)
    except Exception as e:
        logger.error(f"Job crashed: {e}")
        STATE.log(f"[FATAL] {e}", "err")
        success, files = False, []

    with STATE.lock:
        STATE.done = True
        STATE.success = success
        STATE.files = files
        if success:
            STATE.phase = "DONE"
            STATE.pct = 100.0
        elif STATE.logs and STATE.logs[-1]["type"] == "err":
            STATE.message = STATE.logs[-1]["text"].split("] ", 1)[-1]


class UIRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, payload: dict, status: int = 200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            body = HTML_CONTENT.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/favicon.ico":
            icon = _icon_bytes()
            if icon:
                self.send_response(200)
                self.send_header("Content-Type", "image/x-icon")
                self.send_header("Content-Length", str(len(icon)))
                self.end_headers()
                self.wfile.write(icon)
            else:
                self.send_response(204)
                self.end_headers()

        elif self.path.startswith("/api/status"):
            after = 0
            if "after=" in self.path:
                try:
                    after = int(self.path.split("after=")[1].split("&")[0])
                except ValueError:
                    pass
            self._json(STATE.snapshot(after))

        elif self.path == "/api/about":
            self._json({
                "version": APP_VERSION,
                "developer": DEVELOPER,
                "python": platform.python_version(),
                "stack": STACK,
            })


        elif self.path == "/api/history":
            self._json({"files": _scan_history(AppConfig())})

        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"

        if self.path == "/api/start":
            if not STATE.done and STATE.thread and STATE.thread.is_alive():
                self._json({"success": False, "message": "یک عملیات در حال اجراست؛ ابتدا آن را لغو کنید."}, 409)
                return
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._json({"success": False, "message": "Bad JSON"}, 400)
                return
            STATE.reset()
            STATE.thread = threading.Thread(target=_run_job, args=(data,), daemon=True)
            STATE.thread.start()
            self._json({"success": True, "message": "started"})

        elif self.path == "/api/cancel":
            STATE.cancel_event.set()
            self._json({"success": True})

        elif self.path == "/api/open-folder":
            config = AppConfig()
            config.ensure_directories()
            try:
                os.startfile(config.downloads_dir)  # Windows-only by design
                self._json({"success": True})
            except Exception as e:
                self._json({"success": False, "message": str(e)})
        else:
            self.send_error(404)


def _free_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
                s2.bind(("127.0.0.1", 0))
                return s2.getsockname()[1]


def _is_our_server(base_url: str) -> bool:
    """True when an AC-Downloader instance already serves on this URL."""
    try:
        with urllib.request.urlopen(base_url + "/api/status", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def run_server():
    """Launches the app: native desktop window (WebView2) around the local UI.

    Falls back to the default browser when the WebView2 runtime / pywebview is
    unavailable. A second launch attaches another window to the running
    instance instead of failing on the occupied port.
    """
    _setup_file_logging()
    preferred_port = 8765
    existing_url = f"http://127.0.0.1:{preferred_port}"

    if _is_our_server(existing_url):
        url = existing_url          # attach to the running instance
    else:
        port = _free_port(preferred_port)
        url = f"http://127.0.0.1:{port}"
        httpd = ThreadingHTTPServer(("127.0.0.1", port), UIRequestHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

    try:
        import webview  # pywebview — optional at runtime; browser fallback otherwise
        webview.create_window(
            "AC-Downloader Pro",
            url,
            width=1000,
            height=760,
            min_size=(860, 600),
            background_color="#04060e",
        )
        webview.start()
        logger.info("Native window closed.")
    except Exception as e:
        logger.warning(f"Native shell unavailable ({e}); falling back to default browser.")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    run_server()