"""Luminance noise reduction (spec/luminance-noise-reduction.md): calms the
fine, random speckle of a scan while keeping edges, texture and the grain's
character. Undecimated wavelet bands of the luma, each shrunk by a local
Wiener gain against the noise measured for that band AND tone, with a
floor (Detail) so grain is never wiped. The change goes back as one equal
add to R, G and B, so colour differences are untouched.

Quality rules (CLAUDE.md): 0 returns the input; float32 inside; the gain is
C1 in the coefficient (no threshold); nothing leaves range (an equal add,
shortened to fit); unmoved pixels come back exactly; bands are weighted by
their size on the full-resolution scan, so a preview shows what the output
gets at that viewing size.
"""
from __future__ import annotations

import numpy as np
import cv2

LUMA = np.array([0.299, 0.587, 0.114], np.float32)      # Rec.601, as the chroma NR
B3 = np.array([1, 4, 6, 4, 1], np.float32) / 16.0
LEVELS = 4
DETAIL_DEFAULT = 50
K_MAX = 3.0              # Amount 100 = shrink against 3 sigma of the noise
FLOOR_MAX = 0.6          # Detail 100 = at least 60 % of every band kept
TONE_BINS = 8
STRIP = 1024             # rows per strip on big pictures
MARGIN = 64              # rows of context either side of a strip
MOVE_EPS = 1e-7
NOISE_TILES = 6          # x 6 tiles of NOISE_TILE px sample the noise on big pictures
NOISE_TILE = 256


def band_weight(full_px: float) -> float:
    """How strongly a band of this size ON THE FULL-RESOLUTION SCAN is
    treated: fully up to 4 px (film grain on a 50 MP scan sits at 2-8 px),
    then less, 0.75 at 8 px, 0.5 at 16, 0 at 64 and beyond."""
    s = max(float(full_px), 4.0)
    return float(min(1.0, max(0.0, 1.0 - 0.25 * np.log2(s / 4.0))))


def _kernel(step: int) -> np.ndarray:
    k = np.zeros(4 * step + 1, np.float32)
    k[::step] = B3
    return k


def _smooth(x: np.ndarray, step: int) -> np.ndarray:
    k = _kernel(step)
    return cv2.sepFilter2D(x, -1, k, k, borderType=cv2.BORDER_REFLECT)


def _tone_guide(y):
    return _smooth(_smooth(y, 1), 2)


# The noise is read on each band's RESIDUAL, the coefficient minus the mean of
# its neighbours (at the band's own spacing): smooth structure (a gradient, a
# slow curve) is coherent and all but vanishes there (1e-8 against 1e-5), noise
# doesn't. Over the quieter half of the residual's local energy (so texture and
# edges don't count), MAD / 0.6745 / RESID_BIAS[j] = the band's sigma. The bias
# is measured on noise alone: 0.65-0.74 for white and grain-like noise (sigma
# 0-0.7 px), 0.42 at band 1 for very coarse grain (sigma 1 px), which is then
# read low (less is taken out there, never more).
RESID_BIAS = (0.68, 0.66, 0.66, 0.66)


def _residual(d: np.ndarray, step: int) -> np.ndarray:
    k = np.zeros(2 * step + 1, np.float32)
    k[0] = k[step] = k[-1] = 1.0 / 3.0
    return d - cv2.sepFilter2D(d, -1, k, k, borderType=cv2.BORDER_REFLECT)


def noise_profile(y: np.ndarray, tone: np.ndarray = None) -> np.ndarray:
    """sigma per band and tone bin: (LEVELS, TONE_BINS), from each band's
    residual (see RESID_BIAS) in each tone bin, over the quieter half.
    Sampled tiles on a big picture; every other row and column read."""
    h, w = y.shape
    if tone is None:
        tone = _tone_guide(y)
    if h * w > 4_000_000:
        boxes = []
        ys = np.linspace(MARGIN, h - MARGIN - NOISE_TILE, NOISE_TILES).astype(int)
        xs = np.linspace(MARGIN, w - MARGIN - NOISE_TILE, NOISE_TILES).astype(int)
        for y0 in ys:
            for x0 in xs:
                boxes.append((y0 - MARGIN, y0 + NOISE_TILE + MARGIN,
                              x0 - MARGIN, x0 + NOISE_TILE + MARGIN, MARGIN))
    else:
        boxes = [(0, h, 0, w, 0)]
    res = [[] for _ in range(LEVELS)]
    eng = [[] for _ in range(LEVELS)]
    tones = []
    for y0, y1, x0, x1, m in boxes:
        t = y[y0:y1, x0:x1]
        inner = (slice(m, (y1 - y0) - m, 2), slice(m, (x1 - x0) - m, 2))
        tones.append(tone[y0:y1, x0:x1][inner].ravel())
        c = t
        for j in range(LEVELS):
            step = 2 ** j
            s_ = _smooth(c, step)
            r = _residual(c - s_, step)
            e = cv2.GaussianBlur(r * r, (0, 0), sigmaX=float(step),
                                 borderType=cv2.BORDER_REFLECT)
            res[j].append(np.abs(r[inner]).ravel())
            eng[j].append(e[inner].ravel())
            c = s_
    tone_s = np.concatenate(tones)
    b = np.clip((tone_s * TONE_BINS).astype(np.int32), 0, TONE_BINS - 1)
    order = np.argsort(b, kind="stable")
    bounds = np.searchsorted(b[order], np.arange(TONE_BINS + 1))
    prof = np.full((LEVELS, TONE_BINS), np.nan, np.float64)
    for j in range(LEVELS):
        a_ = np.concatenate(res[j])[order]
        e_ = np.concatenate(eng[j])[order]
        for k in range(TONE_BINS):
            lo, hi = bounds[k], bounds[k + 1]
            if hi - lo < 300:
                continue
            ek = e_[lo:hi]
            quiet = a_[lo:hi][ek <= np.median(ek)]
            prof[j, k] = np.median(quiet) / 0.6745 / RESID_BIAS[j]
    for j in range(LEVELS):                      # empty bins: the nearest measured
        row = prof[j]
        ok = np.isfinite(row)
        if not ok.any():
            prof[j] = 0.0
            continue
        idx = np.arange(TONE_BINS)
        prof[j] = np.interp(idx, idx[ok], row[ok])
        prof[j] = np.convolve(np.pad(prof[j], 1, mode="edge"), [0.25, 0.5, 0.25], "valid")
    return prof.astype(np.float32)


def _sigma_at(prof_row: np.ndarray, tone: np.ndarray) -> np.ndarray:
    centres = (np.arange(TONE_BINS, dtype=np.float32) + 0.5) / TONE_BINS
    return np.interp(tone, centres, prof_row).astype(np.float32)


def delta_luma(y: np.ndarray, amount: float, detail: float, scale: float,
               prof: np.ndarray, tone: np.ndarray = None) -> np.ndarray:
    """The change to the luma for a whole picture (or a strip with context)."""
    k_all = K_MAX * min(max(amount, 0.0), 100.0) / 100.0
    floor = FLOOR_MAX * min(max(detail, 0.0), 100.0) / 100.0
    if tone is None:
        tone = _tone_guide(y)
    dy = np.zeros_like(y)
    c = y
    for j in range(LEVELS):
        step = 2 ** j
        s = _smooth(c, step)
        wgt = band_weight(step / max(scale, 1e-6))
        if wgt > 0.0 and k_all > 0.0:
            d = c - s
            sig = _sigma_at(prof[j], tone) * np.float32(k_all * wgt)
            e = cv2.GaussianBlur(d * d, (0, 0), sigmaX=float(step), borderType=cv2.BORDER_REFLECT)
            g = e / (e + sig * sig + np.float32(1e-12))
            g = np.float32(floor) + np.float32(1.0 - floor) * g
            dy += (g - 1.0) * d
        c = s
    return dy


def apply_luma_nr(img16: np.ndarray, amount: float, detail: float = DETAIL_DEFAULT,
                  scale: float = 1.0) -> np.ndarray:
    """Luminance NR on a uint16 RGB picture. ``scale`` = this buffer's long
    side / the full-resolution scan's (<= 1). Returns uint16 (the input object
    itself at Amount 0)."""
    amount = float(amount or 0.0)
    if amount <= 0.0 or img16 is None or img16.ndim != 3 or img16.shape[2] < 3:
        return img16
    h, w = img16.shape[:2]
    rgb = img16[..., :3].astype(np.float32) / np.float32(65535.0)
    y = rgb @ LUMA
    tone = _tone_guide(y)
    prof = noise_profile(y, tone)
    if h <= STRIP + 2 * MARGIN:
        dy = delta_luma(y, amount, detail, scale, prof, tone)
    else:
        dy = np.empty_like(y)
        for r0 in range(0, h, STRIP):
            r1 = min(h, r0 + STRIP)
            a, b = max(0, r0 - MARGIN), min(h, r1 + MARGIN)
            part = delta_luma(y[a:b], amount, detail, scale, prof, tone[a:b])
            dy[r0:r1] = part[r0 - a:r0 - a + (r1 - r0)]
    del y, tone
    # an equal add, shortened so no channel leaves 0..1
    lo = -rgb.min(axis=2)
    hi = 1.0 - rgb.max(axis=2)
    np.clip(dy, lo, hi, out=dy)
    moved = np.abs(dy) >= MOVE_EPS
    out = img16.copy()
    if moved.any():
        new = rgb + dy[..., None]
        q = np.rint(np.clip(new, 0.0, 1.0) * 65535.0).astype(np.uint16)
        out[..., :3][moved] = q[moved]
    return out
