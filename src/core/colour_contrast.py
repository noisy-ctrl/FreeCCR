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

from core.oklab import (GUIDE_EPS, decision_lab, finish_matte, fit_move, guided_filter,
                        matte_radius, scaled_radius, smooth)

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


def _surround(ab, sigma):
    h, w = ab.shape[:2]
    f = max(1, int(sigma // 8))
    small = cv2.resize(ab, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / f, borderType=cv2.BORDER_REFLECT)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def push_field(img01, amount, protect_skin=True):
    """(push a, push b, OKLab) for an HxWx3 float image in 0..1."""
    a = np.asarray(img01, np.float32)
    h, w = a.shape[:2]
    dec, lab = decision_lab(a)          # decided on the denoised view: grain can't steer it
    L = np.ascontiguousarray(dec[..., 0])
    r = scaled_radius(AREA_R, a.shape)
    area = np.dstack([guided_filter(np.ascontiguousarray(dec[..., c]), L, r, AREA_EPS)
                      for c in (1, 2)]).astype(np.float32)
    sur = _surround(area, SURROUND * max(h, w))
    k = min(max(float(amount or 0.0), 0.0), AMOUNT_MAX) / 100.0 * K_FULL
    C = np.hypot(area[..., 0], area[..., 1])
    gate = smooth((C - GATE_LO) / GATE_W)
    vx = k * gate * (area[..., 0] - sur[..., 0])
    vy = k * gate * (area[..., 1] - sur[..., 1])
    mag = np.hypot(vx, vy)
    s = LIMIT * np.tanh(mag / LIMIT) / np.maximum(mag, 1e-12)
    vx *= s
    vy *= s
    # towards grey: keep at least KEEP of the area's chroma (no hue flip)
    ux, uy = area[..., 0] / np.maximum(C, 1e-9), area[..., 1] / np.maximum(C, 1e-9)
    along = vx * ux + vy * uy
    floor = -(1.0 - KEEP) * C
    fix = np.where(along < floor, floor - along, 0.0)
    vx += fix * ux
    vy += fix * uy
    # the push smoothed along the picture's edges, as every finishing tool's
    # matte is (Smoothing 30), so what little grain the area colour kept
    # can't ride on it
    rm = matte_radius(SMOOTHING, a.shape)
    vx = guided_filter(np.ascontiguousarray(vx, np.float32), L, rm, GUIDE_EPS)
    vy = guided_filter(np.ascontiguousarray(vy, np.float32), L, rm, GUIDE_EPS)
    if protect_skin:
        # skin stays natural: where the decision view's own colour has skin's
        # hue the push is taken out, after the smoothing, so a neighbour's
        # push can't spill onto a face; a matte, smoothed like the push
        hue = np.degrees(np.arctan2(dec[..., 2], dec[..., 1]))
        dh = np.abs((hue - SKIN_HUE + 180.0) % 360.0 - 180.0)
        t = np.clip((dh - SKIN_FLAT) / (SKIN_HALF - SKIN_FLAT), 0.0, 1.0)
        skin = 0.5 + 0.5 * np.cos(np.pi * t)        # 1 within SKIN_FLAT, 0 past SKIN_HALF
        skin *= smooth((np.hypot(dec[..., 1], dec[..., 2]) - 0.01) / 0.02)
        skin = finish_matte(skin.astype(np.float32), L, SMOOTHING)
        keep = 1.0 - SKIN_KEEP * skin
        vx *= keep
        vy *= keep
    return vx.astype(np.float32), vy.astype(np.float32), lab


def apply_colour_contrast(img01, amount, protect_skin=True):
    """Colour Contrast on an HxWx3 float image in 0..1 (display values). The
    input itself at 0, else a new float32 array."""
    if not amount or amount <= 0:
        return img01
    a = np.asarray(img01, np.float32)
    vx, vy, lab = push_field(a, amount, protect_skin)
    moved = np.hypot(vx, vy) > MOVE_EPS
    out = a.reshape(-1, 3).copy()
    if moved.any():
        m = moved.reshape(-1)
        lm = lab.reshape(-1, 3)[m]
        d = np.zeros_like(lm)
        d[:, 1] = vx.reshape(-1)[m]
        d[:, 2] = vy.reshape(-1)[m]
        out[m] = np.clip(fit_move(lm, d), 0.0, 1.0)
    return out.reshape(a.shape)
