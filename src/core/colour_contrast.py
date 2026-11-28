"""Colour Contrast (spec/colour-contrast.md): each area's colour pushed away
from the colour around it, at unchanged lightness.

Groups of colour read as separate when neighbouring areas differ more in
colour than they do inside themselves. This raises the first and leaves the
second alone:

- AREA colour: the decision view's OKLab a, b (core.oklab.decision_lab, the
  denoised view every finishing tool decides on) smoothed inside areas (a
  guided filter on its lightness, at the render's own resolution), so grain
  and small texture don't take part;
- SURROUND colour: the area colour blurred wide (a Gaussian a few per cent of
  the picture across), what the area sits among;
- the push is k (area - surround), faded out for near-grey areas (a grey wall
  beside a red door stays grey: no complementary tint) and, with Protect
  skin on, taken out wherever the colour itself has skin's hue (a face isn't
  pushed redder against foliage or yellower against sky),
  softly limited, and never taking an area more than half of the way to grey;
- it is ADDED to every pixel's own a, b at unchanged lightness, so grain and
  detail pass through untouched and a linear colour ramp (whose area and
  surround colours agree) doesn't move.

The surround is blurred on a reduced copy (sigma >= 8 px there) and scaled
back up: it is far smoother than its sampling, like Glow's spread, so this is
not a decision taken small and stretched. A move that would leave 0..1 is
shortened (core.oklab.fit_move); a pixel the push leaves alone comes back
exactly."""
import cv2
import numpy as np

from core.oklab import (DENOISE_EPS, DENOISE_R, GUIDE_EPS, _box, fit_move, guided_smooth,
                        matte_radius, scaled_radius, smooth, to_oklab)

AREA_R = 6                # area colour: guided-filter radius, px at the preview's 1080
AREA_EPS = 0.02 ** 2      # lightness steps over ~0.02 are area edges
SURROUND = 0.04           # the surround's Gaussian sigma, share of the long side
K_FULL = 1.2              # the push at 100, times (area - surround)
LIMIT = 0.035             # soft limit of the push (OKLab chroma)
GATE_LO, GATE_W = 0.008, 0.030   # area chroma below ~0.01 isn't pushed, full by ~0.04
KEEP = 0.5                # a push towards grey keeps at least this share of the area's chroma
MOVE_EPS = 1e-5           # pushes smaller than this leave the pixel exactly as it was
SMOOTHING = 30            # the push's edge-aware smoothing (core.oklab.matte_radius)
SKIN_HUE = 62.0           # OKLab hue of skin (his faces: 62-68 deg, spread 35-85)
SKIN_FLAT, SKIN_HALF = 16.0, 32.0  # full guard within this, none past this (deg)
SKIN_KEEP = 1.0           # skin-hued colour takes none of the push (Protect skin on)
AMOUNT_MAX = 100.0

# The options (right-click the slider's label): Protect skin, on by default.
# Stored as adjustment_settings["colour_contrast_shape"] = {"skin": False} only
# when it is off; None = the default.
CC_SHAPE_DEFAULT = {"skin": True}


def normalize_cc_shape(shape):
    """The stored form of Colour Contrast's options: {"skin": False} when
    Protect skin is off, None when everything is the default (or the value is
    unreadable)."""
    if isinstance(shape, dict) and "skin" in shape and not bool(shape["skin"]):
        return {"skin": False}
    return None


def protects_skin(shape):
    """Whether Protect skin is on for these (stored) options."""
    return normalize_cc_shape(shape) is None


def describe_cc(shape):
    """One line for a paste row."""
    return "Protect skin " + ("on" if protects_skin(shape) else "off")


def _surround(ab, sigma):
    h, w = ab.shape[:2]
    f = max(1, int(sigma // 8))
    small = cv2.resize(ab, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / f, borderType=cv2.BORDER_REFLECT)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


# Rows per band for the per-pixel steps. The maths is the same as doing the
# whole picture at once; only the memory differs (a 51 MP export ran out of
# memory at 8 GB doing everything as whole-picture arrays).
CHUNK_ROWS = 256


def _bands(h):
    for r0 in range(0, h, CHUNK_ROWS):
        yield r0, min(h, r0 + CHUNK_ROWS)


def _oklab_band(a, r0, r1):
    """The OKLab of rows r0:r1 of `a`, (r1 - r0) x W x 3 float32."""
    w = a.shape[1]
    return to_oklab(a[r0:r1].reshape(-1, 3)).reshape(r1 - r0, w, 3).astype(np.float32, copy=False)


def _decision(a):
    """oklab.decision_lab's denoised view as three planes [L, a, b]: the OKLab
    conversion a band of rows at a time, each raw plane let go once smoothed."""
    h, w = a.shape[:2]
    raw = [np.empty((h, w), np.float32) for _ in range(3)]
    for r0, r1 in _bands(h):
        band = _oklab_band(a, r0, r1)
        for c in range(3):
            raw[c][r0:r1] = band[..., c]
    r = scaled_radius(DENOISE_R, a.shape)
    dec = []
    for c in range(3):
        dec.append(guided_smooth(raw[c], r, DENOISE_EPS[c]))
        raw[c] = None
    return dec


def _guided(ps, guide, r, eps):
    """oklab.guided_filter of each plane in the list `ps` along one `guide`:
    the guide's statistics worked out once and the arithmetic done in place.
    The same operations in the same order, so the same numbers, in less
    memory. Empties `ps` as it goes (so a plane can be let go of)."""
    if r < 1 or min(guide.shape[:2]) < 2 * r + 1:
        return list(ps)
    mI = _box(guide, r)
    den = _box(guide * guide, r)
    den -= mI * mI
    den += np.float32(eps)
    out = []
    for i in range(len(ps)):
        p = ps[i]
        ps[i] = None
        mp = _box(p, r)
        a = _box(guide * p, r)
        del p
        a -= mI * mp
        a /= den
        mp -= a * mI                     # b = mp - a·mI
        q = _box(a, r)
        del a
        q *= guide
        q += _box(mp, r)
        del mp
        out.append(q)
    return out


def _skin_weight(da, db):
    """1 on skin's hue (within SKIN_FLAT of SKIN_HUE), 0 past SKIN_HALF, faded
    in by chroma: on the decision view's own colour, before smoothing."""
    out = np.empty(da.shape, np.float32)
    for r0, r1 in _bands(da.shape[0]):
        x, y = da[r0:r1], db[r0:r1]
        hue = np.degrees(np.arctan2(y, x))
        dh = np.abs((hue - SKIN_HUE + 180.0) % 360.0 - 180.0)
        t = np.clip((dh - SKIN_FLAT) / (SKIN_HALF - SKIN_FLAT), 0.0, 1.0)
        s = 0.5 + 0.5 * np.cos(np.pi * t)       # 1 within SKIN_FLAT, 0 past SKIN_HALF
        s *= smooth((np.hypot(x, y) - 0.01) / 0.02)
        out[r0:r1] = s
    return out


def _push(a, amount, protect_skin):
    """(push a, push b) planes for an HxWx3 float32 image in 0..1."""
    h, w = a.shape[:2]
    L, da, db = _decision(a)            # decided on the denoised view: grain can't steer it
    # skin stays natural: where the decision view's own colour has skin's hue
    # the push is taken out (below, after the smoothing, so a neighbour's push
    # can't spill onto a face)
    skin = _skin_weight(da, db) if protect_skin else None
    planes = [da, db]
    del da, db
    ax, ay = _guided(planes, L, scaled_radius(AREA_R, a.shape), AREA_EPS)
    sigma = SURROUND * max(h, w)
    sx, sy = _surround(ax, sigma), _surround(ay, sigma)
    k = min(max(float(amount or 0.0), 0.0), AMOUNT_MAX) / 100.0 * K_FULL
    vx = np.empty((h, w), np.float32)
    vy = np.empty((h, w), np.float32)
    for r0, r1 in _bands(h):
        x, y = ax[r0:r1], ay[r0:r1]
        C = np.hypot(x, y)
        gate = smooth((C - GATE_LO) / GATE_W)
        px = k * gate * (x - sx[r0:r1])
        py = k * gate * (y - sy[r0:r1])
        mag = np.hypot(px, py)
        s = LIMIT * np.tanh(mag / LIMIT) / np.maximum(mag, 1e-12)
        px *= s
        py *= s
        # towards grey: keep at least KEEP of the area's chroma (no hue flip)
        ux, uy = x / np.maximum(C, 1e-9), y / np.maximum(C, 1e-9)
        along = px * ux + py * uy
        floor = -(1.0 - KEEP) * C
        fix = np.where(along < floor, floor - along, 0.0)
        px += fix * ux
        py += fix * uy
        vx[r0:r1] = px
        vy[r0:r1] = py
    del sx, sy, ax, ay
    # the push smoothed along the picture's edges, as every finishing tool's
    # matte is (Smoothing 30), so what little grain the area colour kept
    # can't ride on it; the skin matte smoothed the same way (finish_matte)
    planes = [vx, vy]
    del vx, vy
    top = 0.0
    if skin is not None:
        top = float(skin.max()) if skin.size else 0.0
        if top > 0.0:
            planes.append(skin)
        skin = None
    out = _guided(planes, L, matte_radius(SMOOTHING, a.shape), GUIDE_EPS)
    vx, vy = out[0], out[1]
    if top > 0.0:
        q = out[2]
        np.clip(q, 0.0, top, out=q)
        q[q < 1e-7] = 0.0
        keep = 1.0 - SKIN_KEEP * q
        vx *= keep
        vy *= keep
    return vx.astype(np.float32, copy=False), vy.astype(np.float32, copy=False)


def push_field(img01, amount, protect_skin=True):
    """(push a, push b, OKLab) for an HxWx3 float image in 0..1."""
    a = np.asarray(img01, np.float32)
    vx, vy = _push(a, amount, protect_skin)
    lab = np.empty(a.shape, np.float32)
    for r0, r1 in _bands(a.shape[0]):
        lab[r0:r1] = _oklab_band(a, r0, r1)
    return vx, vy, lab


def apply_colour_contrast(img01, amount, shape=None, protect_skin=None, inplace=False):
    """Colour Contrast on an HxWx3 float image in 0..1 (display values). The
    input itself at 0, else a float32 array: a new one, or with `inplace` the
    input's own buffer when it is float32 (the render passes a copy it owns).
    `shape` is the stored options (normalize_cc_shape); `protect_skin`, when
    given, overrides them."""
    if not amount or amount <= 0:
        return img01
    if protect_skin is None:
        protect_skin = protects_skin(shape)
    a = np.asarray(img01, np.float32)
    vx, vy = _push(a, amount, protect_skin)
    out = a if (inplace or a is not img01) else np.array(a, np.float32, copy=True)
    for r0, r1 in _bands(a.shape[0]):
        px, py = vx[r0:r1], vy[r0:r1]
        moved = np.hypot(px, py) > MOVE_EPS
        if not moved.any():
            continue
        lm = _oklab_band(a, r0, r1)[moved]      # read before the band is written
        d = np.zeros_like(lm)
        d[:, 1] = px[moved]
        d[:, 2] = py[moved]
        band = out[r0:r1]
        band[moved] = np.clip(fit_move(lm, d), 0.0, 1.0)
    return out
