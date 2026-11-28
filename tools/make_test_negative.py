"""Make synthetic colour negatives to measure what a converter does with them.

Each file is a 16-bit linear RGB DNG laid out exactly like FreeCCR's merged
trichrome DNGs (the header and tags are copied from one of them, so a
converter treats it as one of the user's own frames). Two charts:

test_negative_colour.dng - colour rendering:
  * a 33 x 33 x 33 colour cube (35,937 colours, 24 px cells), HALD-style, in
    density; the 24 colour-checker colours; black and white anchors;
    over-range patches; wedges; a skin ramp and a hue ring.
  Export it with sharpening and noise reduction off and read the middle of
  each cell.

test_negative_detail.dng - detail, grain and noise:
  * slanted edges, a Siemens star, a zone plate, line pairs, 1-3 px lines and
    dots, step edges and colour edges (sharpening, resolution, halos);
  * a noise grid: 8 kinds of grain / noise (clean, luminance noise,
    luminance grain, colour film grain at three sizes, colour-only noise,
    colour-only blotches), each laid over the same 8 backgrounds (dark, mid
    and light flats, a luminance ramp, a hue sweep, a saturated colour, fine
    colour-only stripes, an edge). The same grain field is used along a row,
    so a tile minus the clean tile in its column is what the converter left
    of the grain;
  * grain amplitude and grain size series on a flat and on an edge;
  * a large soft blob and sinusoids (local contrast); the checker, anchors,
    wedges, skin ramp and hue ring.

Both carry black and white anchors on the same background, so a converter
that levels each channel from the picture levels both alike.

Everything is written in DENSITY FRACTION x per channel (0 = the film base,
which prints black; 1 = the densest picture content, which prints white),
turned into the negative's light as T = base * 10^(-span * x). Grain and
noise are added in x, as film grain lives in the negative's density.

Usage:  python tools/make_test_negative.py SOURCE.dng OUTDIR [colour] [detail]
SOURCE.dng is one of the merged DNGs (its 668-byte header is reused). Writes
test_negative_<kind>.dng, _key.json and _preview.jpg for each kind.
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
SEED = 20261128
# Rec.709 weights: offsets along these axes leave a print's luminance about
# where it was (the same tone curve on every channel), so they are colour only.
_WY = np.array([0.2126, 0.7152, 0.0722])
AXIS_MG = np.array([1.0, -(_WY[0] + _WY[2]) / _WY[1], 1.0])          # magenta / green
AXIS_BY = np.array([-_WY[2] / (_WY[0] + _WY[1])] * 2 + [1.0])        # blue / yellow
AXIS_MG /= np.sqrt(np.mean(AXIS_MG ** 2))
AXIS_BY /= np.sqrt(np.mean(AXIS_BY ** 2))


class Chart:
    def __init__(self, kind):
        self.kind = kind
        self.T = np.empty((H, W, 3), np.float64)
        self.key = {"what": f"synthetic colour negative ({kind}), density fraction x per channel",
                    "width": W, "height": H, "base_rgb": BASE.tolist(), "holder": HOLDER,
                    "span_density_rgb": SPAN.tolist(),
                    "x_to_value": "value_c = base_c * 10^(-span_c * x_c)",
                    "film": FILM, "picture": PICTURE, "background_x": BG, "items": []}

    def note(self, kind, rect, **info):
        self.key["items"].append(dict(kind=kind, rect=[int(v) for v in rect], **info))

    def put_x(self, x0, y0, xs):
        """Write an (h, w, 3) block of density fractions at (x0, y0)."""
        xs = np.asarray(xs, np.float64)
        h, w = xs.shape[:2]
        self.T[y0:y0 + h, x0:x0 + w] = to_T(xs)

    def put_drawn(self, x0, y0, w, h, fx):
        self.put_x(x0, y0, drawn_x(w, h, fx))


def to_T(x):
    """Density fraction (..., 3) -> the negative's 16-bit light (float)."""
    return BASE * 10.0 ** (-SPAN * np.asarray(x, np.float64))


def drawn_x(w, h, fx):
    """A shape drawn at SS x and averaged in LIGHT (as a lens would), so its
    edges are anti-aliased; returned as density fractions. fx(xx, yy) ->
    (hh, ww, 3) on a supersampled grid in pixel units of the block."""
    yy, xx = np.mgrid[0:h * SS, 0:w * SS].astype(np.float64)
    t = to_T(fx((xx + 0.5) / SS, (yy + 0.5) / SS))
    t = t.reshape(h, SS, w, SS, 3).mean(axis=(1, 3))
    return -np.log10(t / BASE) / SPAN


def grey(v):
    return np.array([v, v, v], np.float64)


def cc_x(rgb):
    """A colour-checker sRGB colour as the film would record it: scene linear,
    then 8 stops of log exposure across x (18 % grey at x = 0.625)."""
    v = np.array(rgb) / 255.0
    lin = np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
    return np.clip((np.log2(np.maximum(lin, 1e-4) / 0.18) + 5.0) / 8.0, 0.0, 1.0)


CHECKER = [(115, 82, 68), (194, 150, 130), (98, 122, 157), (87, 108, 67), (133, 128, 177), (103, 189, 170),
           (214, 126, 44), (80, 91, 166), (193, 90, 99), (94, 60, 108), (157, 188, 64), (224, 163, 46),
           (56, 61, 150), (70, 148, 73), (175, 54, 60), (231, 199, 31), (187, 86, 149), (8, 133, 161),
           (243, 243, 242), (200, 200, 200), (160, 160, 160), (122, 122, 121), (85, 85, 85), (52, 52, 52)]


# --------------------------------------------------------------------------- #
# Pieces both charts share
# --------------------------------------------------------------------------- #

def film(c):
    T = c.T
    T[:] = HOLDER
    fx0, fy0, fx1, fy1 = FILM
    T[fy0:fy1, fx0:fx1] = BASE                                    # clear rebate
    px0, py0, px1, py1 = PICTURE
    T[py0:py1, px0:px1] = to_T(grey(BG))
    c.note("background", PICTURE, x=[BG] * 3)
    for cx, cy in ((290, 260), (7740, 260), (290, 5880), (7740, 5880)):
        sq = np.broadcast_to(grey(0.0), (60, 60, 3)).copy()
        sq[28:32, :] = grey(1.0)
        sq[:, 28:32] = grey(1.0)
        c.put_x(cx, cy, sq)
        c.note("corner_mark", (cx, cy, cx + 60, cy + 60), centre=[cx + 30, cy + 30])


def checker(c, x0, y0):
    for i, rgb in enumerate(CHECKER):
        x = cc_x(rgb)
        cx, cy = x0 + (i % 6) * 200 + 15, y0 + (i // 6) * 200 + 15
        c.put_x(cx - 15, cy - 15, np.broadcast_to(grey(0.15), (200, 200, 3)))   # a dark cell, so
        c.put_x(cx, cy, np.broadcast_to(x, (170, 170, 3)))                      # no patch hides
        c.note("checker", (cx, cy, cx + 170, cy + 170), index=i + 1, srgb=list(rgb),
               x=x.round(5).tolist())
    c.key["checker_mapping"] = ("x = clip((log2(scene_linear / 0.18) + 5) / 8, 0, 1); each patch "
                                "170 px on a 200 px cell of x 0.15")


def anchors(c, black, white):
    for (x0, y0, s), v, name in ((black, 0.0, "anchor_black"), (white, 1.0, "anchor_white")):
        c.put_x(x0, y0, np.broadcast_to(grey(v), (s, s, 3)))
        c.note(name, (x0, y0, x0 + s, y0 + s), x=[v] * 3)


def extremes(c, x0, y0):
    for i, v in enumerate((-0.05, 1.05, 1.10, 1.20)):
        ox = x0 + i * 100
        c.put_x(ox, y0, np.broadcast_to(grey(v), (80, 80, 3)))
        c.note("over_range", (ox, y0, ox + 80, y0 + 80), x=[v] * 3)
    for ch in range(3):                                  # one channel at 0 / 1, others mid
        for j, v in enumerate((0.0, 1.0)):
            x = grey(0.5)
            x[ch] = v
            ox, oy = x0 + (ch * 2 + j) * 100, y0 + 120
            c.put_x(ox, oy, np.broadcast_to(x, (80, 80, 3)))
            c.note("channel_extreme", (ox, oy, ox + 80, oy + 80), x=x.tolist())


def wedges(c, x0, y0, step_w, steps=64):
    for k in range(steps):
        v = k / (steps - 1)
        c.put_x(x0 + k * step_w, y0, np.broadcast_to(grey(v), (120, step_w, 3)))
        c.note("wedge", (x0 + k * step_w, y0, x0 + (k + 1) * step_w, y0 + 120), step=k, x=[v] * 3)
    n = steps * step_w
    ramp = np.linspace(0.0, 1.0, n)
    c.put_x(x0, y0 + 140, np.broadcast_to(ramp[None, :, None], (120, n, 3)))
    c.note("ramp", (x0, y0 + 140, x0 + n, y0 + 260), x="0 at left to 1 at right, linear in x, all channels")
    cw = n // 32
    for ch in range(3):
        for k in range(32):
            x = grey(0.5)
            x[ch] = k / 31.0
            oy = y0 + 290 + ch * 90
            c.put_x(x0 + k * cw, oy, np.broadcast_to(x, (80, cw, 3)))
            c.note("channel_wedge", (x0 + k * cw, oy, x0 + (k + 1) * cw, oy + 80),
                   channel="RGB"[ch], step=k, x=x.tolist())


def skin_and_hues(c, skin_xy, hue_xy):
    light, dark = cc_x((194, 150, 130)), cc_x((115, 82, 68))
    for k in range(10):
        x = np.clip(dark + (light - dark) * k / 9 + 0.03 * (k - 4.5) / 4.5, 0, 1)
        ox = skin_xy[0] + k * 280
        c.put_x(ox, skin_xy[1], np.broadcast_to(x, (260, 260, 3)))
        c.note("skin_ramp", (ox, skin_xy[1], ox + 260, skin_xy[1] + 260), step=k, x=x.round(5).tolist())
    for si, sat in enumerate((0.33, 0.66, 1.0)):
        for h in range(24):
            ang = 2 * np.pi * h / 24
            x = 0.55 + sat * 0.25 * np.cos(ang - np.array([0.0, 2 * np.pi / 3, 4 * np.pi / 3]))
            ox, oy = hue_xy[0] + h * 110, hue_xy[1] + si * 110
            c.put_x(ox, oy, np.broadcast_to(x, (100, 100, 3)))
            c.note("hue_ring", (ox, oy, ox + 100, oy + 100), hue_step=h, saturation=sat,
                   x=x.round(5).tolist())


# --------------------------------------------------------------------------- #
# The colour chart
# --------------------------------------------------------------------------- #

CUBE_N, CUBE_CELL, CUBE_COLS = 33, 24, 9
CUBE_X0, CUBE_Y0 = 480, 440


def build_colour():
    c = Chart("colour")
    film(c)
    n, cell = CUBE_N, CUBE_CELL
    side = n * cell                                    # 792 px a block
    lv = np.linspace(0.0, 1.0, n)
    rr = np.repeat(lv, cell)                           # red along a block row
    for b in range(n):
        bx = CUBE_X0 + (b % CUBE_COLS) * side
        by = CUBE_Y0 + (b // CUBE_COLS) * side
        blk = np.empty((side, side, 3))
        blk[..., 0] = rr[None, :]
        blk[..., 1] = rr[:, None]                      # green down the block
        blk[..., 2] = lv[b]
        c.put_x(bx, by, blk)
    c.note("cube", (CUBE_X0, CUBE_Y0, CUBE_X0 + CUBE_COLS * side, CUBE_Y0 + 4 * side),
           levels=n, cell=cell, block=side, blocks_per_row=CUBE_COLS,
           layout=("block b (blue level b/32) sits at column b % 9, row b // 9 from the top-left; "
                   "inside a block, cell row = green level, cell column = red level (k/32); "
                   "read the middle 12 x 12 px of each 24 px cell"))
    # the cube's three spare block slots: anchors, then extremes and grey
    a0 = CUBE_X0 + 6 * side, CUBE_Y0 + 3 * side
    anchors(c, (a0[0], a0[1], side), (a0[0] + side, a0[1], side))
    extremes(c, a0[0] + 2 * side + 96, a0[1] + 96)
    gx, gy = a0[0] + 2 * side + 96, a0[1] + 400
    c.put_x(gx - 20, gy - 20, np.broadcast_to(grey(0.15), (340, 640, 3)))
    c.put_x(gx, gy, np.broadcast_to(grey(0.5), (300, 600, 3)))
    c.note("grey_patch", (gx, gy, gx + 600, gy + 300), x=[0.5] * 3)
    checker(c, 600, 3750)
    wedges(c, 1950, 3750, 80)
    skin_and_hues(c, (4750, 4740), (1950, 4740))
    return c


# --------------------------------------------------------------------------- #
# The detail chart
# --------------------------------------------------------------------------- #

def sharpness(c, y):
    def slanted(lo, hi, side=300, deg=5.0):
        a = np.radians(deg)

        def f(xx, yy):
            u = (xx - 250) * np.cos(a) + (yy - 250) * np.sin(a)
            v = -(xx - 250) * np.sin(a) + (yy - 250) * np.cos(a)
            inside = (np.abs(u) < side / 2) & (np.abs(v) < side / 2)
            return np.where(inside[..., None], grey(lo), grey(hi))
        return f
    c.put_drawn(600, y, 500, 500, slanted(0.35, 0.75))
    c.note("slanted_edge", (600, y, 1100, y + 500), inside=0.35, outside=0.75, degrees=5.0, side=300)
    c.put_drawn(1150, y, 500, 500, slanted(0.50, 0.60))
    c.note("slanted_edge", (1150, y, 1650, y + 500), inside=0.50, outside=0.60, degrees=5.0, side=300)

    def star(xx, yy):
        dx, dy = xx - 300, yy - 300
        r = np.hypot(dx, dy)
        on = (np.cos(36 * np.arctan2(dy, dx)) > 0) & (r < 280)
        return np.where(on[..., None], grey(0.35), np.where((r < 280)[..., None], grey(0.75), grey(BG)))
    c.put_drawn(1700, y, 600, 600, star)
    c.note("siemens_star", (1700, y, 2300, y + 600), spokes=72, radius=280, dark=0.35, light=0.75)

    R = 280.0

    def zone(xx, yy):
        r2 = (xx - 300) ** 2 + (yy - 300) ** 2
        v = np.where(r2 < R * R, 0.55 + 0.2 * np.cos(np.pi * r2 / (2 * R)), BG)
        return np.repeat(v[..., None], 3, axis=2)
    c.put_drawn(2350, y, 600, 600, zone)
    c.note("zone_plate", (2350, y, 2950, y + 600), radius=R, centre_x=0.55, amplitude=0.2,
           frequency="0.5 * r / radius cycles per px")

    for i, p in enumerate((2, 3, 4, 6, 8, 12, 16)):
        ox = 3000 + i * 130
        dark = (np.arange(120) % p) < p / 2.0               # dark for the first half of each period
        c.put_x(ox, y, np.broadcast_to(np.where(dark[None, :, None], grey(0.35), grey(0.75)), (260, 120, 3)))
        c.note("line_pairs_vertical", (ox, y, ox + 120, y + 260), period_px=p, dark=0.35, light=0.75)
        c.put_x(ox, y + 300, np.broadcast_to(np.where(dark[:, None, None], grey(0.35), grey(0.75)), (120, 120, 3)))
        c.note("line_pairs_horizontal", (ox, y + 300, ox + 120, y + 420), period_px=p, dark=0.35, light=0.75)
    for j, wpx in enumerate((1, 2, 3)):
        for k, v in enumerate((0.30, 0.70)):
            lx = 3950 + (j * 2 + k) * 60
            c.put_x(lx, y, np.broadcast_to(grey(v), (260, wpx, 3)))
            c.note("line", (lx, y, lx + wpx, y + 260), width_px=wpx, x=[v] * 3)
            dy = y + 300 + (j * 2 + k) * 50
            c.put_x(3950, dy, np.broadcast_to(grey(v), (wpx, wpx, 3)))
            c.note("dot", (3950, dy, 3950 + wpx, dy + wpx), size_px=wpx, x=[v] * 3)
    for (x0, y0, a, b, vertical, name) in ((4400, y, 0.30, 0.70, True, "step_vertical"),
                                         (4400, y + 300, 0.30, 0.70, False, "step_horizontal"),
                                         (5050, y, 0.45, 0.55, True, "step_low_contrast")):
        blk = np.broadcast_to(grey(a), (280, 600, 3)).copy()
        if vertical:
            blk[:, 300:] = grey(b)
        else:
            blk[140:] = grey(b)
        c.put_x(x0, y0, blk)
        c.note(name, (x0, y0, x0 + 600, y0 + 280), first=a, second=b,
               edge=(x0 + 300) if vertical else (y0 + 140))
    cols = {"red": (0.75, 0.40, 0.40), "green": (0.40, 0.75, 0.40), "blue": (0.40, 0.40, 0.75),
            "cyan": (0.30, 0.65, 0.65), "magenta": (0.65, 0.30, 0.65), "yellow": (0.65, 0.65, 0.30)}
    for i, (name, x) in enumerate(cols.items()):
        ox, oy = 5750 + (i % 3) * 300, y + (i // 3) * 300
        c.put_x(ox, oy, np.broadcast_to(np.array(x), (200, 200, 3)))
        c.note("colour_edge_square", (ox, oy, ox + 200, oy + 200), colour=name, x=list(x))


TILE, PITCH = 300, 315


def _unit(f):
    f = f - f.mean()
    return f / max(f.std(), 1e-12)


def noise_field(kind, rng, size=TILE):
    """(size, size, 3) noise in x: luminance kinds are one field on every
    channel, colour film grain an independent field per channel (as the dye
    layers are), colour-only noise lies along the two colour axes so a
    print's luminance barely moves. Scaled to the kind's amplitude."""
    _name, amp, blur, mode = kind
    if mode == "none":
        return np.zeros((size, size, 3))

    def plane():
        p = rng.normal(0.0, 1.0, (size, size))
        if blur > 0:
            p = cv2.GaussianBlur(p, (0, 0), blur, borderType=cv2.BORDER_REFLECT)
        return _unit(p)
    if mode == "luma":
        f = np.repeat(plane()[..., None], 3, axis=2)
    elif mode == "grain":
        f = np.stack([plane(), plane(), plane()], axis=2)
    else:                                                    # colour only
        f = (plane()[..., None] * AXIS_MG + plane()[..., None] * AXIS_BY) / np.sqrt(2.0)
    return amp * f


NOISE_KINDS = [  # (name, amplitude in x, blur sigma px, mode)
    ("clean", 0.0, 0.0, "none"),
    ("luminance noise, per pixel", 0.020, 0.0, "luma"),
    ("luminance grain, 1.5 px", 0.025, 1.5, "luma"),
    ("colour film grain, fine 0.8 px", 0.025, 0.8, "grain"),
    ("colour film grain, medium 1.6 px", 0.030, 1.6, "grain"),
    ("colour film grain, coarse 3 px", 0.030, 3.0, "grain"),
    ("colour-only noise, 1 px", 0.030, 1.0, "chroma"),
    ("colour-only blotches, 6 px", 0.025, 6.0, "chroma"),
]


def backgrounds():
    """The 8 backgrounds of the noise grid, each a (TILE, TILE, 3) x tile."""
    n = TILE
    out = []
    for v in (0.25, 0.5, 0.8):
        out.append((f"flat {v}", np.broadcast_to(grey(v), (n, n, 3)).copy()))
    ramp = np.linspace(0.10, 0.90, n)
    out.append(("luminance ramp 0.1 to 0.9", np.broadcast_to(ramp[None, :, None], (n, n, 3)).copy()))
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    hue = 0.5 + 0.12 * np.cos(th[:, None] - np.array([0.0, 2 * np.pi / 3, 4 * np.pi / 3])[None, :])
    out.append(("hue sweep at x 0.5", np.broadcast_to(hue[None, :, :], (n, n, 3)).copy()))
    out.append(("saturated red", np.broadcast_to(np.array([0.70, 0.40, 0.38]), (n, n, 3)).copy()))
    stripes = np.broadcast_to(grey(0.5), (n, n, 3)).copy()
    for i, wpx in enumerate((1, 2, 4, 8)):                    # bands of 75 px rows
        sign = np.where((np.arange(n) // wpx) % 2 == 0, 1.0, -1.0)[None, :, None]
        axis = AXIS_MG if i % 2 == 0 else AXIS_BY
        stripes[i * 75:(i + 1) * 75] = 0.5 + 0.06 * sign * axis
    out.append(("colour-only stripes 1/2/4/8 px", stripes))
    a = np.radians(3.0)

    def edge(xx, yy):
        u = (xx - n / 2) * np.cos(a) + (yy - n / 2) * np.sin(a)
        return np.where((u < 0)[..., None], grey(0.35), grey(0.75))
    out.append(("edge 0.35 / 0.75 at 3 degrees", drawn_x(n, n, edge)))
    return out


def panel(c, x0, y0, cols, rows, dx=0):
    """A dark backing (x 0.15) behind a block of tiles, so a mid-grey tile
    doesn't vanish into the background; the gaps read as dark lines."""
    w, h = cols * PITCH + dx + PITCH - TILE, rows * PITCH + PITCH - TILE
    m = PITCH - TILE
    c.put_x(x0 - m, y0 - m, np.broadcast_to(grey(0.15), (h, w, 3)))


def noise_grid(c, x0, y0):
    rng = np.random.default_rng(SEED)
    bgs = backgrounds()
    panel(c, x0, y0, len(bgs), len(NOISE_KINDS))
    for r, kind in enumerate(NOISE_KINDS):
        f = noise_field(kind, rng)                         # one field along the row
        for col, (bname, bg) in enumerate(bgs):
            ox, oy = x0 + col * PITCH, y0 + r * PITCH
            c.put_x(ox, oy, bg + f)
            c.note("noise_grid", (ox, oy, ox + TILE, oy + TILE), row=r, column=col, noise=kind[0],
                   amplitude_x=kind[1], blur_px=kind[2], noise_mode=kind[3], background=bname)
    c.key["noise_grid"] = {
        "rows": [k[0] for k in NOISE_KINDS], "columns": [b[0] for b in bgs],
        "note": ("the same noise field along a row (seeded); a tile minus the clean tile at the top "
                 "of its column is the noise as the converter left it. Colour-only noise lies along "
                 "the magenta/green and blue/yellow axes weighted so luminance barely moves; colour "
                 "film grain is an independent field per channel; luminance kinds are one field on "
                 "every channel. Stripes, bands top to bottom: 1, 2, 4, 8 px wide; magenta/green, "
                 "blue/yellow, magenta/green, blue/yellow; amplitude 0.06."),
        "seed": SEED}


def grain_series(c, x0, y0):
    """Colour film grain at 1.6 px by amplitude, and at 0.03 by size, each on
    a flat and on an edge (what grain protection and its size setting see)."""
    rng = np.random.default_rng(SEED + 1)
    flat = np.broadcast_to(grey(0.5), (TILE, TILE, 3))
    edge = backgrounds()[-1][1]
    panel(c, x0, y0, 2, 4)
    panel(c, x0 + 2 * PITCH + 100, y0, 2, 4)
    for i, amp in enumerate((0.01, 0.02, 0.04, 0.06)):
        f = noise_field(("amp", amp, 1.6, "grain"), rng)
        for j, (bname, bg) in enumerate((("flat 0.5", flat), ("edge", edge))):
            ox, oy = x0 + j * PITCH, y0 + i * PITCH
            c.put_x(ox, oy, bg + f)
            c.note("grain_amplitude", (ox, oy, ox + TILE, oy + TILE), amplitude_x=amp, blur_px=1.6,
                   background=bname)
    for i, size in enumerate((0.5, 1.0, 2.0, 4.0)):
        f = noise_field(("size", 0.03, size, "grain"), rng)
        for j, (bname, bg) in enumerate((("flat 0.5", flat), ("edge", edge))):
            ox, oy = x0 + (2 + j) * PITCH + 100, y0 + i * PITCH
            c.put_x(ox, oy, bg + f)
            c.note("grain_size", (ox, oy, ox + TILE, oy + TILE), amplitude_x=0.03, blur_px=size,
                   background=bname)


def local_contrast(c, x0, y0):
    yy, xx = np.mgrid[0:1600, 0:1000].astype(np.float64)
    blob = BG + 0.12 * np.exp(-((xx - 500) ** 2 + (yy - 800) ** 2) / (2 * 300.0 ** 2))
    c.put_x(x0, y0, np.repeat(blob[..., None], 3, axis=2))
    c.note("soft_blob", (x0, y0, x0 + 1000, y0 + 1600), background=BG, amplitude=0.12, sigma_px=300,
           centre=[x0 + 500, y0 + 800])
    for i, per in enumerate((8, 16, 32, 64, 128, 256)):
        ox, oy = x0 + 1100 + (i % 2) * 520, y0 + (i // 2) * 520
        s = BG + 0.05 * np.sin(2 * np.pi * np.arange(500) / per)
        c.put_x(ox, oy, np.broadcast_to(s[None, :, None], (500, 500, 3)))
        c.note("sinusoid", (ox, oy, ox + 500, oy + 500), period_px=per, amplitude=0.05, background=BG)


def build_detail():
    c = Chart("detail")
    film(c)
    checker(c, 600, 520)
    anchors(c, (1900, 520, 800), (2800, 520, 800))
    extremes(c, 3700, 520)
    skin_and_hues(c, (4450, 520), (4450, 830))
    wedges(c, 600, 1400, 100)
    sharpness(c, 2050)
    noise_grid(c, 700, 2800)
    local_contrast(c, 3400, 2750)
    grain_series(c, 3400, 4400)
    return c


# --------------------------------------------------------------------------- #

def write(c, head, outdir):
    stem = os.path.join(outdir, f"test_negative_{c.kind}")
    with open(stem + ".dng", "wb") as f:
        f.write(head)
        for y in range(0, H, 512):                    # in bands: less memory
            f.write(np.clip(np.round(c.T[y:y + 512]), 0, 65535).astype("<u2").tobytes())
    with open(stem + "_key.json", "w") as f:
        json.dump(c.key, f, indent=1)
    small = cv2.resize(c.T, (W // 4, H // 4), interpolation=cv2.INTER_AREA)   # in light
    X = -np.log10(np.maximum(small, 1) / BASE) / SPAN                         # the intended print
    prev = (np.clip(X, 0, 1) ** (1 / 1.6) * 255).astype(np.uint8)[..., ::-1]
    cv2.imwrite(stem + "_preview.jpg", prev, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print(c.kind, "items", len(c.key["items"]))


def main():
    src, outdir = sys.argv[1], sys.argv[2]
    kinds = sys.argv[3:] or ["colour", "detail"]
    with open(src, "rb") as f:
        head = f.read(HEADER)
    assert head[:2] == b"II", "expected a little-endian DNG"
    assert os.path.getsize(src) == HEADER + W * H * 3 * 2, "not the merged-DNG layout this tool copies"
    os.makedirs(outdir, exist_ok=True)
    for kind in kinds:
        c = build_colour() if kind == "colour" else build_detail()
        write(c, head, outdir)
        del c


if __name__ == "__main__":
    main()
