"""AC-Downloader Pro icon — green pinwheel mark (user-provided design).

White rounded tile, dark-green rounded frame broken at the bottom where a
download arrow + baseline sit, and a geometric green pinwheel inside.
Analytic-AA SDF renderer at 1024x1024, packed into a multi-size .ico.
"""
import math
import struct
import subprocess
import zlib
from pathlib import Path

S = 1024
CX = CY = S / 2

# palette
WHITE = (252, 252, 250)
DARK = (28, 70, 52)      # #1C4634
DARK2 = (35, 88, 67)     # #235843
LIGHT = (78, 158, 111)   # #4E9E6F


def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def sd_round_rect(px, py, cx, cy, hw, hh, r):
    """Rounded rectangle SDF (r = corner radius)."""
    qx = abs(px - cx) - (hw - r)
    qy = abs(py - cy) - (hh - r)
    ox, oy = max(qx, 0.0), max(qy, 0.0)
    return math.hypot(ox, oy) + min(max(qx, qy), 0.0) - r


def sd_capsule(px, py, ax, ay, bx, by, r):
    pax, pay = px - ax, py - ay
    bax, bay = bx - ax, by - ay
    t = clamp((pax * bax + pay * bay) / (bax * bax + bay * bay + 1e-9))
    dx, dy = pax - bax * t, pay - bay * t
    return math.hypot(dx, dy) - r


def sd_poly(px, py, pts, r=0.0):
    """Convex polygon SDF with optional corner rounding (shrinks by r, rounds by r)."""
    n = len(pts)
    d = 1e18
    inside = True
    sign_sum = 0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        ex, ey = x2 - x1, y2 - y1
        wx, wy = px - x1, py - y1
        cross = ex * wy - ey * wx
        sign_sum += 1 if cross > 0 else (-1 if cross < 0 else 0)
        t = clamp((wx * ex + wy * ey) / (ex * ex + ey * ey + 1e-9))
        bx, by = x1 + ex * t - px, y1 + ey * t - py
        d = min(d, math.hypot(bx, by))
    if sign_sum < 0:
        inside = sign_sum == -n
    else:
        inside = sign_sum == n
    d = d - r if r > 0 else d
    return -d if inside else d


def mix(c1, c2, t):
    return (c1[0] + (c2[0] - c1[0]) * t,
            c1[1] + (c2[1] - c1[1]) * t,
            c1[2] + (c2[2] - c1[2]) * t)


def render():
    rows = []
    tile_r = 0.24 * S          # generous curve on tile corners
    fr_c = 0.185 * S           # frame corner radius
    fr_t = 0.058 * S           # frame thickness
    fr_m = 0.075 * S           # frame inset margin
    gap_hw = 0.155 * S         # half-width of bottom gap

    for y in range(S):
        row = bytearray()
        fy = y + 0.5
        for x in range(S):
            fx = x + 0.5

            # ---- tile ----
            d_tile = sd_round_rect(fx, fy, CX, CY, S / 2, S / 2, tile_r)
            cov_tile = clamp(0.5 - d_tile)
            col = WHITE

            if cov_tile > 0:
                # ---- pinwheel (sharp corners, small gaps — like the reference) ----
                d_tl = sd_round_rect(fx, fy, 388, 384, 88, 88, 20)
                d_bl = sd_round_rect(fx, fy, 388, 638, 88, 88, 20)
                d_tri = sd_poly(fx, fy, [(472, 515), (735, 300), (735, 515)])
                d_par = sd_poly(fx, fy, [(728, 548), (728, 722), (556, 722), (664, 548)])

                for dd, cc in ((d_tl, DARK), (d_bl, DARK2), (d_par, DARK), (d_tri, LIGHT)):
                    a = clamp(0.5 - dd)
                    if a > 0:
                        col = mix(col, cc, a)

                # ---- frame (broken at bottom) ----
                d_fo = sd_round_rect(fx, fy, CX, CY, S / 2 - fr_m, S / 2 - fr_m, fr_c)
                d_frame = abs(d_fo) - fr_t / 2
                a_fr = clamp(0.5 - d_frame)
                in_gap = (fy > CY) and (abs(fx - CX) < gap_hw)
                if in_gap:
                    a_fr = 0.0
                if a_fr > 0:
                    col = mix(col, DARK, a_fr)

                # ---- download arrow in the gap ----
                d_shaft = sd_capsule(fx, fy, CX, 640, CX, 812, 27)
                d_chev1 = sd_capsule(fx, fy, CX, 892, CX - 62, 826, 27)
                d_chev2 = sd_capsule(fx, fy, CX, 892, CX + 62, 826, 27)
                d_base = sd_capsule(fx, fy, CX - 108, 952, CX + 108, 952, 21)
                d_arr = min(d_shaft, d_chev1, d_chev2, d_base)
                a_arr = clamp(0.5 - d_arr)
                if a_arr > 0:
                    col = mix(col, DARK, a_arr)

            r, g, b = int(col[0]), int(col[1]), int(col[2])
            row += bytes((r, g, b, int(cov_tile * 255)))
        rows.append(b"\x00" + bytes(row))

    raw = b"".join(rows)

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", S, S, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    Path("installer/app256.png").write_bytes(png)
    print("pinwheel icon written:", len(png), "bytes")


if __name__ == "__main__":
    render()
