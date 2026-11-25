"""Colour Organics v2: the labs' hue moves and saturation, BY TONE
(spec/colour-organics-v2.md).

Two sliders, each 0..100 with 50 = the measured labs' amount:

- Organics v2 (hue): turns each colour by the labs' measured move for its
  OKLab hue AND lightness (core.organics_v2_data.HUE_MOVE). Dark tones are
  included: dark browns turn further towards ochre than mid oranges, dark
  blues further towards teal.
- Organic Saturation: scales chroma by the labs' measured ratio for its hue
  and lightness, on top of Clean Lights (CHROMA_LOG, never above 0): mid
  tones keep their colour, shadows and light tones ease off, by hue.

On display values (sRGB-encoded 0..1) in OKLab, float32 throughout, by the
finishing tools' quality rules:

- HOW MUCH is decided on the denoised decision view (core.oklab.decision_lab):
  the move and the chroma factor are read at the decision view's hue and
  lightness, faded in from grey by a C1 chroma gate, then smoothed along
  the picture's edges with a guided filter on the decision lightness;
- APPLIED per pixel: the pixel's own (a, b) turned by that angle and scaled
  by that factor (<= 1) at its own L, so lightness never changes and colour
  grain is kept or reduced;
- a pixel the move would take out of 0..1 gets the largest fraction that
  fits (core.oklab.fit_move); pixels it doesn't move are returned EXACTLY.

The tables are evaluated as the fit was: a normalised Gaussian kernel over
the knots (12 deg in hue, periodic; 0.08 in L), tabulated once on a fine
(hue, L) grid and read bilinearly."""
import functools

import numpy as np

from core import organics_v2_data as data
from core.oklab import (decision_lab, fit_move, guided_filter, matte_radius,
                        smooth, GUIDE_EPS)

LAB_AMOUNT = 50.0          # slider value that gives the labs' measured amount
AMOUNT_MAX = 100.0
SIGMA_H = 12.0             # kernel width in hue (deg), as fitted
SIGMA_L = 0.08             # kernel width in OKLab L, as fitted
GATE_LO = 0.005            # chroma gate: 0 at or below this decision chroma ...
GATE_SPAN = 0.03           # ... full from GATE_LO + GATE_SPAN (C1 smoothstep)
SMOOTHING = 30             # edge-aware smoothing of the fields (finishing default)
MIN_SLOPE = 0.10           # the hue map never folds: 1 + d move / d hue >= this
MOVE_EPS = 1e-3            # degrees: a smaller turn leaves the pixel exactly
LOG_EPS = 1e-5             # a smaller chroma change leaves the pixel exactly
GRID_H = 0.5               # tabulation step in hue (deg)
GRID_L = 0.005             # tabulation step in L
CHUNK = 1 << 20

_HK = np.asarray(data.HUE_KNOTS, np.float64)
_LK = np.asarray(data.L_KNOTS, np.float64)
_A = np.asarray(data.HUE_MOVE, np.float64)
_B = np.minimum(np.asarray(data.CHROMA_LOG, np.float64), 0.0)


def table_fields(L, h):
    """(move deg, chroma log) at the labs' amount for lightness L and hue h
    (degrees), by the fit's own kernel evaluation. float64 arrays."""
    L = np.asarray(L, np.float64).reshape(-1)
    h = np.asarray(h, np.float64).reshape(-1)
    dx = ((h[:, None] - _HK[None, :] + 180.0) % 360.0) - 180.0
    kh = np.exp(-0.5 * (dx / SIGMA_H) ** 2)
    kl = np.exp(-0.5 * ((L[:, None] - _LK[None, :]) / SIGMA_L) ** 2)
    den = kh.sum(1) * kl.sum(1)
    move = ((kh @ _A) * kl).sum(1) / den
    clog = ((kh @ _B) * kl).sum(1) / den
    return move, clog


@functools.lru_cache(maxsize=1)
def _grids():
    hs = np.arange(0.0, 360.0 + GRID_H, GRID_H)
    ls = np.arange(0.0, 1.0 + GRID_L, GRID_L)
    H, L = np.meshgrid(hs, ls, indexing="ij")
    move, clog = table_fields(L.ravel(), H.ravel())
    return (move.reshape(H.shape).astype(np.float32),
            np.minimum(clog, 0.0).reshape(H.shape).astype(np.float32))


@functools.lru_cache(maxsize=1)
def fold_limit():
    """The largest hue amount (slider units) whose map never folds: at every
    lightness 1 + (amount/50) . d move/d hue >= MIN_SLOPE."""
    move, _ = _grids()
    d = np.diff(move.astype(np.float64), axis=0) / GRID_H     # per degree
    worst = float(d.min())
    if worst >= 0:
        return AMOUNT_MAX
    return min(AMOUNT_MAX, LAB_AMOUNT * (1.0 - MIN_SLOPE) / -worst)


def _lookup(grid, h, L):
    """Bilinear read of a (hue, L) grid at hue h (deg) and lightness L."""
    fh = np.mod(h, 360.0) / GRID_H
    fl = np.clip(L, 0.0, 1.0) / GRID_L
    i0 = np.minimum(fh.astype(np.int64), grid.shape[0] - 2)
    j0 = np.minimum(fl.astype(np.int64), grid.shape[1] - 2)
    th = (fh - i0).astype(np.float32)
    tl = (fl - j0).astype(np.float32)
    g00, g10 = grid[i0, j0], grid[i0 + 1, j0]
    g01, g11 = grid[i0, j0 + 1], grid[i0 + 1, j0 + 1]
    return ((g00 * (1 - th) + g10 * th) * (1 - tl)
            + (g01 * (1 - th) + g11 * th) * tl).astype(np.float32)


def amounts(hue_amount, sat_amount):
    """Slider values -> (hue factor, saturation factor), 1 = the labs'.
    The hue amount is held under the fold limit."""
    hue = min(max(float(hue_amount or 0.0), 0.0), AMOUNT_MAX, fold_limit())
    sat = min(max(float(sat_amount or 0.0), 0.0), AMOUNT_MAX)
    return hue / LAB_AMOUNT, sat / LAB_AMOUNT


def field_maps(img01, hue_amount, sat_amount, with_full=False):
    """The turn (deg) and chroma log maps decided on the decision view, at
    the given amounts, smoothed along the picture's edges."""
    kh, ks = amounts(hue_amount, sat_amount)
    smooth_lab, lab = decision_lab(img01)
    Ls = smooth_lab[..., 0]
    Cs = np.hypot(smooth_lab[..., 1], smooth_lab[..., 2])
    hs = np.degrees(np.arctan2(smooth_lab[..., 2], smooth_lab[..., 1]))
    gate = smooth((Cs - GATE_LO) / GATE_SPAN).astype(np.float32)
    move_g, clog_g = _grids()
    flat_h, flat_l = hs.reshape(-1), Ls.reshape(-1)
    move = np.zeros(flat_h.shape, np.float32)
    clog = np.zeros(flat_h.shape, np.float32)
    for s in range(0, flat_h.size, CHUNK):
        sl = slice(s, s + CHUNK)
        if kh > 0:
            move[sl] = _lookup(move_g, flat_h[sl], flat_l[sl])
        if ks > 0:
            clog[sl] = _lookup(clog_g, flat_h[sl], flat_l[sl])
    move = move.reshape(Ls.shape) * gate * np.float32(kh)
    clog = clog.reshape(Ls.shape) * gate * np.float32(ks)
    r = matte_radius(SMOOTHING, Ls.shape)
    guide = np.ascontiguousarray(Ls, np.float32)
    if kh > 0 and np.abs(move).max() > 0:
        move = guided_filter(np.ascontiguousarray(move), guide, r, GUIDE_EPS)
    if ks > 0 and clog.min() < 0:
        clog = np.minimum(guided_filter(np.ascontiguousarray(clog), guide, r,
                                        GUIDE_EPS), 0.0)
    move = move.astype(np.float32)
    clog = clog.astype(np.float32)
    return (move, clog, lab) if with_full else (move, clog)


def _chunk(x, move, clog, lab):
    out = x.copy()
    moved = (np.abs(move) > MOVE_EPS) | (clog < -LOG_EPS)
    if not moved.any():
        return out
    lm = lab[moved]
    ang = np.radians(move[moved]).astype(np.float32)
    k = np.exp(clog[moved]).astype(np.float32)
    c, s = np.cos(ang), np.sin(ang)
    new = lm.copy()
    new[:, 1] = (lm[:, 1] * c - lm[:, 2] * s) * k
    new[:, 2] = (lm[:, 1] * s + lm[:, 2] * c) * k
    out[moved] = np.clip(fit_move(lm, new - lm), 0.0, 1.0)
    return out


def apply_organics_v2(img01, hue_amount=0.0, sat_amount=0.0):
    """Organics v2 (hue) and Organic Saturation on an HxWx3 float image in
    0..1. A new float32 array; the input itself when both are 0."""
    kh, ks = amounts(hue_amount, sat_amount)
    if kh <= 0 and ks <= 0:
        return img01
    a = np.asarray(img01, dtype=np.float32)
    move, clog, full = field_maps(a, hue_amount, sat_amount, with_full=True)
    flat = a.reshape(-1, 3)
    move = move.reshape(-1)
    clog = clog.reshape(-1)
    full = full.reshape(-1, 3)
    out = flat.copy()
    for s in range(0, flat.shape[0], CHUNK):
        sl = slice(s, s + CHUNK)
        out[sl] = _chunk(flat[sl], move[sl], clog[sl], full[sl])
    return out.reshape(a.shape)
