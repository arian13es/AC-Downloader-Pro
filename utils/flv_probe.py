"""Pure-Python FLV inspector — no external processes required.

Parses the FLV container directly (tag walk) to detect audio/video streams,
their codecs and duration. Immune to child-process spawn failures
(0xc0000142 / Smart App Control) that affect ffprobe.exe.
"""
import struct
from pathlib import Path

from utils.ffmpeg_utils import MediaStreamInfo

_AUDIO_CODECS = {2: "mp3", 4: "nellymoser16", 5: "nellymoser8", 6: "nellymoser",
                 10: "aac", 11: "speex", 14: "mp3", 15: "device"}
_VIDEO_CODECS = {2: "flv1", 3: "screen1", 4: "vp6", 5: "screen2", 7: "h264"}


def probe_flv(path: Path) -> MediaStreamInfo:
    """Walks FLV tags and returns stream info. Partial/robust: never raises.

    Some Adobe Connect muxers write payload sizes that are off by a byte from
    the real layout (PreviousTagSize is the ground truth). After each tag we
    verify the next header and resync by scanning forward when misaligned.
    """
    info = MediaStreamInfo()
    try:
        with open(path, "rb") as f:
            head = f.read(9)
            if len(head) < 9 or head[:3] != b"FLV":
                return info
            f.seek(0, 2)
            fsize = f.tell()

            _AUDIO_FMTS = {0, 1, 2, 3, 4, 5, 6, 10, 11, 13, 14, 15}
            _VIDEO_FMTS = {1, 2, 3, 4, 5, 6, 7}
            _TYPES = (8, 9, 18)

            def plausible(pos: int, depth: int = 0) -> bool:
                """Strict check that a valid FLV tag header starts at pos."""
                if pos + 15 > fsize or depth > 1:
                    return depth > 0 or pos + 15 <= fsize
                f.seek(pos)
                h = f.read(15)
                if h[0] not in _TYPES:
                    return False
                dsize = int.from_bytes(h[1:4], "big")
                if dsize > fsize - pos or int.from_bytes(h[8:11], "big") != 0:
                    return False
                if int.from_bytes(h[4:7], "big") | (h[7] << 24) > 86400000 * 30:
                    return False
                # payload first byte must decode to a known codec
                pb = h[11]
                if h[0] == 8 and (pb >> 4) not in _AUDIO_FMTS:
                    return False
                if h[0] == 9 and ((pb & 0x0F) not in _VIDEO_FMTS or (pb >> 4) > 5):
                    return False
                # look-ahead: the NEXT tag must also be plausible (or EOF)
                nxt = pos + 11 + dsize + 4
                if nxt < fsize - 15:
                    return plausible(nxt, depth + 1)
                return True

            max_ts = 0
            pos = head[8] + 4  # DataOffset + PreviousTagSize0
            while pos + 11 <= fsize:
                f.seek(pos)
                hdr = f.read(11)
                ttype = hdr[0]
                dsize = int.from_bytes(hdr[1:4], "big")
                ts = int.from_bytes(hdr[4:7], "big") | (hdr[7] << 24)
                if ts > max_ts:
                    max_ts = ts
                data_pos = pos + 11
                if data_pos + dsize > fsize:
                    break  # truncated tail

                if ttype == 8 and not info.has_audio:
                    f.seek(data_pos)
                    b0 = f.read(1)
                    if b0 and (b0[0] >> 4) in _AUDIO_FMTS:
                        info.audio_codec = _AUDIO_CODECS.get(b0[0] >> 4, "")
                        info.has_audio = True
                        info.sample_rate = 44100
                        info.channels = 2
                elif ttype == 9 and not info.has_video:
                    f.seek(data_pos)
                    b0 = f.read(1)
                    if b0 and (b0[0] & 0x0F) in _VIDEO_FMTS:
                        info.video_codec = _VIDEO_CODECS.get(b0[0] & 0x0F, "")
                        info.has_video = True
                elif ttype == 18 and info.duration <= 0:
                    f.seek(data_pos)
                    data = f.read(min(dsize, 8192))
                    i = data.find(b"duration\x00")
                    if i >= 0 and i + 17 <= len(data) and data[i + 9:i + 10] == b"\x00":
                        try:
                            info.duration = struct.unpack(">d", data[i + 10:i + 18])[0]
                        except struct.error:
                            pass

                # ---- advance to the next tag (with resync) ----
                nxt = data_pos + dsize + 4  # spec: payload + PreviousTagSize
                if nxt + 11 <= fsize and plausible(nxt):
                    pos = nxt
                else:
                    # Muxer quirk: sizes may be off. Scan forward for the next
                    # plausible tag header (limited window).
                    f.seek(data_pos + dsize)
                    window = f.read(min(65536, fsize - data_pos - dsize))
                    found = -1
                    for o in range(0, max(0, len(window) - 11)):
                        if window[o] in (8, 9, 18) and plausible(data_pos + dsize + o):
                            found = data_pos + dsize + o
                            break
                    if found < 0:
                        break
                    pos = found

            if info.duration <= 0 and max_ts > 0:
                info.duration = max_ts / 1000.0
    except OSError:
        pass
    return info
