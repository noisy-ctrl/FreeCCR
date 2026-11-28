"""Make a synthetic colour negative to measure what a converter does with it.

The file is a 16-bit linear RGB DNG laid out exactly like FreeCCR's merged
trichrome DNGs (the header and tags are copied from one of them, so a
converter treats it as one of the user's own frames), holding a chart drawn
as a negative:

  * a 17 x 17 x 17 colour cube (a HALD-style grid in density), the 24
    colour-checker colours, black and white anchors, over-range patches;
  * a 64-step grey wedge, a smooth ramp and one wedge per channel;
  * slanted edges, a Siemens star, a zone plate, line pairs, 1-3 px lines and
    dots, step edges and colour edges (sharpening, resolution, halos);
  * flat patches with known luminance and colour noise (noise reduction);
  * a large soft blob and sinusoids of several sizes (local contrast);
  * a skin-tone ramp and a hue ring (colour rendering);
  * corner marks for lining the converter's output up again;
  * a clear-film rebate round the picture and an opaque holder outside it.

Everything is written in DENSITY FRACTION x per channel (0 = the film base,
which prints black; 1 = the densest picture content, which prints white),
turned into the negative's light as T = base * 10^(-span * x). The chart's
black and white anchors cover well over 0.2 % of the picture, so a converter
that levels each channel from the picture's histogram lands on them exactly.

Usage:  python tools/make_test_negative.py SOURCE.dng OUTDIR
SOURCE.dng is one of the merged DNGs (its 668-byte header is reused).
Writes OUTDIR/test_negative.dng, OUTDIR/test_negative_key.json and
OUTDIR/test_negative_preview.jpg.
"""

import json
import os
import sys

import cv2
import numpy as np

W, H = 8088, 6206
HEADER = 668                                   # the merged DNGs' header size
BASE = np.array([28105.0, 23918.0, 25942.0])   # clear film, as the merges read it
HOLDER = 15.0                                  # the opaque holder outside the film
SPAN = np.array([1.27, 1.30, 1.42])            # density of x = 1 per channel (median
                                               # of five real frames' picture ranges)
FILM = (150, 120, 7938, 6086)                  # film edge (rebate starts here)
PICTURE = (260, 230, 7828, 5976)               # picture inside a 110 px rebate
BG = 0.5                                       # picture background (neutral)
SS = 4                                         # supersampling for drawn shapes

key = {"what": "synthetic colour negative, density fraction x per channel",
       "width": W, "height": H, "base_rgb": BASE.tolist(), "holder": HOLDER,
       "span_density_rgb": SPAN.tolist(),
       "x_to_value": "value_c = base_c * 10^(-span_c * x_c)",
       "film": FILM, "picture": PICTURE, "background_x": BG, "items": []}


def note(kind, rect, **info):
    key["items"].append(dict(kind=kind, rect=[int(v) for v in rect], **info))


def to_T(x):
    """Density fraction (..., 3) -> the negative's 16-bit light (float)."""
    return BASE * 10.0 ** (-SPAN * np.asarray(x, np.float64))


def put_x(T, x0, y0, xs):
    """Write an (h, w, 3) block of density fractions at (x0, y0)."""
    h, w = xs.shape[:2]
    T[y0:y0 + h, x0:x0 + w] = to_T(xs)


def put_drawn(T, x0, y0, w, h, fx):
    """A shape drawn at SS x and averaged in LIGHT (as a lens would), so
    edges are anti-aliased: fx(xx, yy) -> (hh, ww, 3) density fractions on a
    supersampled grid in pixel units relative to (x0, y0)."""
    yy, xx = np.mgrid[0:h * SS, 0:w * SS].astype(np.float64)
    xx = (xx + 0.5) / SS
    yy = (yy + 0.5) / SS
    t = to_T(fx(xx, yy))
    t = t.reshape(h, SS, w, SS, 3).mean(axis=(1, 3))
    T[y0:y0 + h, x0:x0 + w] = t


def grey(v):
    return np.array([v, v, v], np.float64)


def build():
    T = np.empty((H, W, 3), np.float64)
    T[:] = HOLDER
    fx0, fy0, fx1, fy1 = FILM
    T[fy0:fy1, fx0:fx1] = BASE                                # clear rebate
    px0, py0, px1, py1 = PICTURE
    put_x(T, px0, py0, np.broadcast_to(grey(BG), (py1 - py0, px1 - px0, 3)))
    note("background", PICTURE, x=[BG] * 3)

    # 1. colour cube: 17 levels per channel, blocks by blue, rows green, cols red
    n, cell = 17, 24
    lv = np.linspace(0.0, 1.0, n)
    for b in range(n):
        bx = 600 + (b % 6) * n * cell
        by = 520 + (b // 6) * n * cell
        blk = np.empty((n * cell, n * cell, 3))
        for g in range(n):
            for r in range(n):
                blk[g * cell:(g + 1) * cell, r * cell:(r + 1) * cell] = (lv[r], lv[g], lv[b])
        put_x(T, bx, by, blk)
    note("cube", (600, 520, 600 + 6 * n * cell, 520 + 3 * n * cell), levels=n, cell=cell,
         layout="block index = blue level (6 blocks a row, from top-left); inside a block row = green, column = red; "
                "read the middle 12 x 12 px of each 24 px cell")

    # 2. colour checker (sRGB 8-bit values, scene-linear, then 8 stops of log exposure)
    cc = [(115, 82, 68), (194, 150, 130), (98, 122, 157), (87, 108, 67), (133, 128, 177), (103, 189, 170),
          (214, 126, 44), (80, 91, 166), (193, 90, 99), (94, 60, 108), (157, 188, 64), (224, 163, 46),
          (56, 61, 150), (70, 148, 73), (175, 54, 60), (231, 199, 31), (187, 86, 149), (8, 133, 161),
          (243, 243, 242), (200, 200, 200), (160, 160, 160), (122, 122, 121), (85, 85, 85), (52, 52, 52)]
    for i, rgb in enumerate(cc):
        v = np.array(rgb) / 255.0
        lin = np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
        x = np.clip((np.log2(np.maximum(lin, 1e-4) / 0.18) + 5.0) / 8.0, 0.0, 1.0)
        cx, cy = 3200 + (i % 6) * 200 + 15, 520 + (i // 6) * 200 + 15
        put_x(T, cx - 15, cy - 15, np.broadcast_to(grey(0.15), (200, 200, 3)))   # dark cell, so no
        put_x(T, cx, cy, np.broadcast_to(x, (170, 170, 3)))                      # patch hides in the background
        note("checker", (cx, cy, cx + 170, cy + 170), index=i + 1, srgb=list(rgb), x=x.round(5).tolist())
    key["checker_mapping"] = "x = clip((log2(scene_linear / 0.18) + 5) / 8, 0, 1); each patch 170 px on a 200 px cell of x 0.15"

    # 3. black and white anchors, over-range patches
    put_x(T, 4560, 520, np.broadcast_to(grey(0.0), (800, 800, 3)))
    note("anchor_black", (4560, 520, 5360, 1320), x=[0.0] * 3)
    put_x(T, 5460, 520, np.broadcast_to(grey(1.0), (800, 800, 3)))
    note("anchor_white", (5460, 520, 6260, 1320), x=[1.0] * 3)
    for i, v in enumerate((-0.05, 1.05, 1.10, 1.20)):
        ox, oy = 6360 + i * 100, 520
        put_x(T, ox, oy, np.broadcast_to(grey(v), (80, 80, 3)))
        note("over_range", (ox, oy, ox + 80, oy + 80), x=[v] * 3)
    for c in range(3):                                   # one channel at 0 / 1, others mid
        for j, v in enumerate((0.0, 1.0)):
            x = grey(0.5); x[c] = v
            ox, oy = 6360 + (c * 2 + j) * 100, 640
            put_x(T, ox, oy, np.broadcast_to(x, (80, 80, 3)))
            note("channel_extreme", (ox, oy, ox + 80, oy + 80), x=x.tolist())

    # 4. wedges
    for k in range(64):
        v = k / 63.0
        put_x(T, 600 + k * 100, 1880, np.broadcast_to(grey(v), (120, 100, 3)))
        note("wedge64", (600 + k * 100, 1880, 700 + k * 100, 2000), step=k, x=[v] * 3)
    ramp = np.linspace(0.0, 1.0, 6400)
    put_x(T, 600, 2020, np.broadcast_to(ramp[None, :, None], (120, 6400, 3)))
    note("ramp", (600, 2020, 7000, 2140), x="0 at left to 1 at right, linear in x, all channels")
    for c in range(3):
        for k in range(32):
            x = grey(0.5); x[c] = k / 31.0
            oy = 2170 + c * 90
            put_x(T, 600 + k * 200, oy, np.broadcast_to(x, (80, 200, 3)))
            note("channel_wedge", (600 + k * 200, oy, 800 + k * 200, oy + 80), channel="RGB"[c], step=k, x=x.tolist())

    # 5. sharpness and resolution (y 2500..3100)
    def slanted(lo, hi, side=300, deg=5.0):
        a = np.radians(deg)
        def f(xx, yy):
            u = (xx - 250) * np.cos(a) + (yy - 250) * np.sin(a)
            v = -(xx - 250) * np.sin(a) + (yy - 250) * np.cos(a)
            inside = (np.abs(u) < side / 2) & (np.abs(v) < side / 2)
            return np.where(inside[..., None], grey(lo), grey(hi))
        return f
    put_drawn(T, 600, 2500, 500, 500, slanted(0.35, 0.75))
    note("slanted_edge", (600, 2500, 1100, 3000), inside=0.35, outside=0.75, degrees=5.0, side=300)
    put_drawn(T, 1150, 2500, 500, 500, slanted(0.50, 0.60))
    note("slanted_edge", (1150, 2500, 1650, 3000), inside=0.50, outside=0.60, degrees=5.0, side=300)

    def star(xx, yy):
        dx, dy = xx - 300, yy - 300
        r = np.hypot(dx, dy); th = np.arctan2(dy, dx)
        on = (np.cos(36 * th) > 0) & (r < 280)
        return np.where(on[..., None], grey(0.35), np.where((r < 280)[..., None], grey(0.75), grey(BG)))
    put_drawn(T, 1700, 2500, 600, 600, star)
    note("siemens_star", (1700, 2500, 2300, 3100), spokes=72, radius=280, dark=0.35, light=0.75)

    R = 280.0
    def zone(xx, yy):
        r2 = (xx - 300) ** 2 + (yy - 300) ** 2
        v = 0.55 + 0.2 * np.cos(np.pi * r2 / (2 * R))     # 0.5 cycles/px at r = R
        v = np.where(r2 < R * R, v, BG)
        return np.repeat(v[..., None], 3, axis=2)
    put_drawn(T, 2350, 2500, 600, 600, zone)
    note("zone_plate", (2350, 2500, 2950, 3100), radius=R, centre_x=0.55, amplitude=0.2,
         frequency="0.5 * r / radius cycles per px")

    x0 = 3000
    for i, p in enumerate((2, 3, 4, 6, 8, 12, 16)):
        bars = (np.arange(120) % p) < p / 2.0            # dark for the first half of each period
        col = np.where(bars[None, :, None], grey(0.35), grey(0.75))
        put_x(T, x0 + i * 130, 2500, np.broadcast_to(col, (260, 120, 3)))
        note("line_pairs_vertical", (x0 + i * 130, 2500, x0 + i * 130 + 120, 2760), period_px=p, dark=0.35, light=0.75)
        rows = (np.arange(120) % p) < p / 2.0
        row = np.where(rows[:, None, None], grey(0.35), grey(0.75))
        put_x(T, x0 + i * 130, 2800, np.broadcast_to(row, (120, 120, 3)))
        note("line_pairs_horizontal", (x0 + i * 130, 2800, x0 + i * 130 + 120, 2920), period_px=p, dark=0.35, light=0.75)

    # single-pixel lines and dots on the background
    for j, wpx in enumerate((1, 2, 3)):
        for k, v in enumerate((0.30, 0.70)):
            lx = 3950 + (j * 2 + k) * 60
            put_x(T, lx, 2500, np.broadcast_to(grey(v), (260, wpx, 3)))
            note("line", (lx, 2500, lx + wpx, 2760), width_px=wpx, x=[v] * 3)
            dy = 2800 + (j * 2 + k) * 50
            put_x(T, 3950, dy, np.broadcast_to(grey(v), (wpx, wpx, 3)))
            note("dot", (3950, dy, 3950 + wpx, dy + wpx), size_px=wpx, x=[v] * 3)

    # step edges
    blk = np.broadcast_to(grey(0.30), (280, 600, 3)).copy(); blk[:, 300:] = grey(0.70)
    put_x(T, 4400, 2500, blk); note("step_vertical", (4400, 2500, 5000, 2780), left=0.30, right=0.70, edge_x=4700)
    blk = np.broadcast_to(grey(0.30), (280, 600, 3)).copy(); blk[140:] = grey(0.70)
    put_x(T, 4400, 2800, blk); note("step_horizontal", (4400, 2800, 5000, 3080), top=0.30, bottom=0.70, edge_y=2940)
    blk = np.broadcast_to(grey(0.45), (280, 600, 3)).copy(); blk[:, 300:] = grey(0.55)
    put_x(T, 5050, 2500, blk); note("step_low_contrast", (5050, 2500, 5650, 2780), left=0.45, right=0.55, edge_x=5350)

    # colour edges: squares on the background
    cols = {"red": (0.75, 0.40, 0.40), "green": (0.40, 0.75, 0.40), "blue": (0.40, 0.40, 0.75),
            "cyan": (0.30, 0.65, 0.65), "magenta": (0.65, 0.30, 0.65), "yellow": (0.65, 0.65, 0.30)}
    for i, (name, x) in enumerate(cols.items()):
        ox, oy = 5750 + (i % 3) * 300, 2500 + (i // 3) * 300
        put_x(T, ox, oy, np.broadcast_to(np.array(x), (200, 200, 3)))
        note("colour_edge_square", (ox, oy, ox + 200, oy + 200), colour=name, x=list(x))

    # 6. noise patches: 5 tones x 5 kinds, 300 px, seeded
    rng = np.random.default_rng(20261128)
    kinds = [("none", 0.0, False, 0.0), ("luminance_0.010", 0.010, False, 0.0), ("luminance_0.025", 0.025, False, 0.0),
             ("luminance_grain_0.025_sigma1.5px", 0.025, False, 1.5), ("colour_0.020", 0.020, True, 0.0)]
    for ti, tone in enumerate((0.2, 0.35, 0.5, 0.65, 0.8)):
        for ki, (name, amp, colour, blur) in enumerate(kinds):
            ox, oy = 600 + ti * 320, 3250 + ki * 320
            if colour:
                nz = rng.normal(0, 1, (300, 300, 3))
            else:
                nz = np.repeat(rng.normal(0, 1, (300, 300))[..., None], 3, axis=2)
            if blur > 0:
                nz = cv2.GaussianBlur(nz, (0, 0), blur)
                nz /= nz.std()
            put_x(T, ox, oy, grey(tone) + amp * nz)
            note("noise", (ox, oy, ox + 300, oy + 300), tone=tone, noise=name, sigma_x=amp, per_channel=colour,
                 grain_blur_px=blur, seed=20261128)

    # 7. local contrast: a large soft blob and sinusoids of several sizes
    yy, xx = np.mgrid[0:1600, 0:1000].astype(np.float64)
    blob = BG + 0.12 * np.exp(-((xx - 500) ** 2 + (yy - 800) ** 2) / (2 * 300.0 ** 2))
    put_x(T, 2300, 3250, np.repeat(blob[..., None], 3, axis=2))
    note("soft_blob", (2300, 3250, 3300, 4850), background=BG, amplitude=0.12, sigma_px=300, centre=[2800, 4050])
    for i, per in enumerate((8, 16, 32, 64, 128, 256)):
        ox, oy = 3400 + (i % 2) * 520, 3250 + (i // 2) * 520
        s = BG + 0.05 * np.sin(2 * np.pi * np.arange(500) / per)
        put_x(T, ox, oy, np.broadcast_to(s[None, :, None], (500, 500, 3)))
        note("sinusoid", (ox, oy, ox + 500, oy + 500), period_px=per, amplitude=0.05, background=BG)

    # 8. skin ramp (between the checker's light and dark skin, in log exposure)
    def cc_x(rgb):
        v = np.array(rgb) / 255.0
        lin = np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
        return np.clip((np.log2(np.maximum(lin, 1e-4) / 0.18) + 5.0) / 8.0, 0, 1)
    light, dark = cc_x((194, 150, 130)), cc_x((115, 82, 68))
    for k in range(10):
        x = dark + (light - dark) * k / 9 + 0.03 * (k - 4.5) / 4.5
        ox = 4500 + k * 280
        put_x(T, ox, 3250, np.broadcast_to(np.clip(x, 0, 1), (260, 260, 3)))
        note("skin_ramp", (ox, 3250, ox + 260, 3510), step=k, x=np.clip(x, 0, 1).round(5).tolist())

    # 9. hue ring: 24 hues x 3 saturations around x = 0.55
    for si, sat in enumerate((0.33, 0.66, 1.0)):
        for h in range(24):
            ang = 2 * np.pi * h / 24
            x = 0.55 + sat * 0.25 * np.cos(ang - np.array([0.0, 2 * np.pi / 3, 4 * np.pi / 3]))
            ox, oy = 4500 + h * 110, 3600 + si * 110
            put_x(T, ox, oy, np.broadcast_to(x, (100, 100, 3)))
            note("hue_ring", (ox, oy, ox + 100, oy + 100), hue_step=h, saturation=sat, x=x.round(5).tolist())

    # 10. corner marks (white cross on a black square), for lining outputs up
    for cx, cy in ((620, 470), (7400, 470), (620, 5660), (7400, 5660)):
        sq = np.broadcast_to(grey(0.0), (60, 60, 3)).copy()
        sq[28:32, :] = grey(1.0); sq[:, 28:32] = grey(1.0)
        put_x(T, cx, cy, sq)
        note("corner_mark", (cx, cy, cx + 60, cy + 60), centre=[cx + 30, cy + 30])
    return T


def main():
    src, outdir = sys.argv[1], sys.argv[2]
    with open(src, "rb") as f:
        head = f.read(HEADER)
    assert head[:2] == b"II", "expected a little-endian DNG"
    size = os.path.getsize(src)
    assert size == HEADER + W * H * 3 * 2, "not the merged-DNG layout this tool copies"
    T = build()
    data = np.clip(np.round(T), 0, 65535).astype("<u2")
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "test_negative.dng"), "wb") as f:
        f.write(head)
        f.write(data.tobytes())
    with open(os.path.join(outdir, "test_negative_key.json"), "w") as f:
        json.dump(key, f, indent=1)
    # a preview: the intended print, x as brightness (gamma for the screen)
    X = -np.log10(np.maximum(T, 1) / BASE) / SPAN
    prev = np.clip(X, 0, 1) ** (1 / 1.6)
    prev = cv2.resize((prev * 255).astype(np.uint8)[..., ::-1], (W // 4, H // 4), interpolation=cv2.INTER_AREA)
    cv2.imwrite(os.path.join(outdir, "test_negative_preview.jpg"), prev, [cv2.IMWRITE_JPEG_QUALITY, 90])
    print("items", len(key["items"]))


if __name__ == "__main__":
    main()
