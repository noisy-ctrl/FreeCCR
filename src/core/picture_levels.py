"""Levels from the picture: the default conversion (spec/picture-levels-conversion.md).

Convert sets each channel's black from the thinnest 0.1 % of the picture and
its white from the densest 0.2 %, as a two-point DENSITY conversion (so
Channel Levels and Printer Lights still grade in density), then the render
turns that density back into transmission levels and through one measured
tone curve F (the print curve) in the Cineon / Film Look slot.

measure(raw, rect)       -> Levels | None    (on the unconverted preview scan)
print_curve(ci)          -> PrintCurve | None (the render's decode for a frame)
F(u)                     -> display value     (the curve, extended linearly)
"""

from dataclasses import dataclass

import numpy as np

from core.print_curve_data import KNOTS

BLACK_PCT = 99.9          # thinnest 0.1 % of the picture -> black
WHITE_PCT = 0.2           # densest 0.2 % -> white
INSET = 0.03              # inside a found picture area, each side
BUFFER = 0.07             # with no picture area: the middle of the scan
CLIP = 65280              # a channel at or over this is clipped: left out
HOLDER_DENSITY = 2.7      # denser than this above black: holder, not picture
MIN_RANGE = 0.3           # a channel's density range is at least this
MIN_PIXELS = 1000
MIN_BLACK = 64.0          # a black under this: no light
STEP = 2                  # every 2nd pixel each way

LEVELS_KEY = "levels"     # conversion_inputs key


# --------------------------------------------------------------------------- #
# The curve
# --------------------------------------------------------------------------- #

_Y = np.asarray(KNOTS, np.float64)
_N = len(_Y)
_H = 1.0 / (_N - 1)


def _fc_slopes(y, h):
    """Fritsch-Carlson slopes for knots spaced h apart (monotone cubic)."""
    d = np.diff(y) / h
    m = np.empty_like(y)
    m[0], m[-1] = d[0], d[-1]
    for k in range(1, len(y) - 1):
        if d[k - 1] * d[k] <= 0:
            m[k] = 0.0
        else:                       # equal spacing: the weighted harmonic mean
            m[k] = 2.0 / (1.0 / d[k - 1] + 1.0 / d[k])
    return m


_M = _fc_slopes(_Y, _H)
SLOPE_BLACK = float(_M[0])
SLOPE_WHITE = float(_M[-1])
_Y32 = _Y.astype(np.float32)
_HM32 = (_M * _H).astype(np.float32)


def F(u):
    """The print curve: transmission levels u (0 = the picture's black, 1 =
    its white) -> display value. A monotone cubic through KNOTS, carried on
    as a straight line at its end slopes outside [0, 1]. float32 in, float32
    out (float64 in gives float64)."""
    u = np.asarray(u)
    dt = np.float64 if u.dtype == np.float64 else np.float32
    u = u.astype(dt, copy=False)
    y, hm = (_Y, _M * _H) if dt == np.float64 else (_Y32, _HM32)
    s = np.clip(u, 0.0, 1.0) * dt(_N - 1)
    i = np.minimum(s.astype(np.int32), _N - 2)
    t = s - i
    t2 = t * t
    t3 = t2 * t
    out = ((2 * t3 - 3 * t2 + 1) * y[i] + (t3 - 2 * t2 + t) * hm[i]
           + (3 * t2 - 2 * t3) * y[i + 1] + (t3 - t2) * hm[i + 1])
    lo = u < 0
    if np.any(lo):
        out = np.where(lo, y[0] + dt(SLOPE_BLACK) * u, out)
    hi = u > 1
    if np.any(hi):
        out = np.where(hi, y[-1] + dt(SLOPE_WHITE) * (u - 1), out)
    return out.astype(dt, copy=False)


# --------------------------------------------------------------------------- #
# The render's decode
# --------------------------------------------------------------------------- #

_CHUNK = 1 << 20


class PrintCurve:
    """The decode of a picture-levels frame, in the Film Look slot (it rides
    the look_lut argument; spec §7). `ranges` are the channels' density
    ranges D_c (array order). decode(x): x is the density fraction after
    Channel Levels (0 = black, 1 = white), per channel
    u = (1 - 10^(-D x)) / (1 - 10^(-D)), then F(u)."""

    colour_only = False
    # Temperature / Tint on these frames are the anchored gammas
    # (spec/anchored-white-balance.md), read by the working-space recovery.
    anchored_wb = True

    def __init__(self, ranges):
        self.ranges = np.asarray(ranges, np.float64).reshape(3)
        D = np.maximum(self.ranges, 1e-6)
        # 10^(-D x) = 2^(-D log2(10) x)
        self._k = (-D * np.log2(10.0)).astype(np.float32)
        self._inv = (1.0 / (1.0 - 10.0 ** (-D))).astype(np.float32)

    def __eq__(self, other):
        return isinstance(other, PrintCurve) and np.array_equal(self.ranges, other.ranges)

    def __hash__(self):
        return hash(tuple(self.ranges))

    def levels_u(self, x):
        """Transmission levels of density fractions x (..., 3), float32."""
        x = np.asarray(x, np.float32)
        u = np.exp2(x * self._k)
        np.subtract(np.float32(1.0), u, out=u)
        u *= self._inv
        return u

    def decode(self, d):
        d = np.asarray(d, np.float32)
        shape = d.shape
        flat = d.reshape(-1, 3)
        out = np.empty_like(flat)
        for s in range(0, flat.shape[0], _CHUNK):
            out[s:s + _CHUNK] = F(self.levels_u(flat[s:s + _CHUNK]))
        return out.reshape(shape)


def is_picture_levels(conversion_inputs) -> bool:
    ci = conversion_inputs or {}
    rec = ci.get(LEVELS_KEY)
    return bool(isinstance(rec, dict) and rec.get("from") == "picture")


def print_curve(conversion_inputs):
    """The frame's PrintCurve, or None: only a frame converted with levels
    from the picture (a "bw" density conversion with both points known)."""
    ci = conversion_inputs or {}
    if not is_picture_levels(ci) or ci.get("mode") != "bw" or not ci.get("density"):
        return None
    black, white = tuple(ci.get("bw") or (None, None))
    if black is None or white is None:
        return None
    b = np.maximum(np.asarray(black, np.float64), 1.0)
    w = np.maximum(np.asarray(white, np.float64), 1.0)
    return PrintCurve(np.maximum(np.log10(b / np.minimum(w, b)), 1e-6))


def frame_print_curve(img, settings=None):
    """The print curve `img` renders with: its picture levels' curve unless
    Cineon or a Film Look takes the decode (as CCRImage.apply_adjustments
    decides), else None."""
    s = (getattr(img, "adjustment_settings", None) if settings is None else settings) or {}
    if s.get("cineon_log"):
        return None
    if s.get("look_lut"):
        from core.look_lut import resolve_look
        if resolve_look(s.get("look_lut")) is not None:
            return None
    return print_curve(getattr(img, "conversion_inputs", None))


# --------------------------------------------------------------------------- #
# Measuring
# --------------------------------------------------------------------------- #

@dataclass
class Levels:
    black: tuple          # per channel, array order (the clear end)
    white: tuple          # per channel (the dense end)
    record: dict          # conversion_inputs["levels"]


def measured_rect(rect=None):
    """The area measured, as fractions (x1, y1, x2, y2): the picture rect
    inset by INSET, else the middle of the scan less BUFFER each side."""
    if rect is not None:
        x1, y1, x2, y2 = (float(v) for v in rect)
        ix, iy = (x2 - x1) * INSET, (y2 - y1) * INSET
        return [max(0.0, x1 + ix), max(0.0, y1 + iy), min(1.0, x2 - ix), min(1.0, y2 - iy)]
    return [BUFFER, BUFFER, 1.0 - BUFFER, 1.0 - BUFFER]


def measure_area(shape, rect=None):
    """(r0, r1, c0, c1) of the area measured: the picture rect inset by INSET,
    else the middle of the scan less BUFFER each side."""
    h, w = shape[:2]
    x1, y1, x2, y2 = measured_rect(rect)
    r0, r1 = int(round(y1 * h)), int(round(y2 * h))
    c0, c1 = int(round(x1 * w)), int(round(x2 * w))
    return r0, max(r1, r0 + 1), c0, max(c1, c0 + 1)


def measure(raw, rect=None):
    """Levels from the picture of `raw` (the unconverted scan, linear, any
    channel order: the result is in the same order), or None when there is
    too little to measure. `rect` = the picture area (fractions), or None."""
    if raw is None or raw.ndim != 3 or raw.shape[2] < 3:
        return None
    r0, r1, c0, c1 = measure_area(raw.shape, rect)
    px = raw[r0:r1:STEP, c0:c1:STEP, :3].reshape(-1, 3).astype(np.float64)
    px = px[(px < CLIP).all(axis=1)]
    if px.shape[0] < MIN_PIXELS:
        return None
    black, white, ranges = [], [], []
    for c in range(3):
        v = px[:, c]
        b = float(np.percentile(v, BLACK_PCT))
        if b < MIN_BLACK:
            return None
        keep = v >= b * 10.0 ** (-HOLDER_DENSITY)
        if int(keep.sum()) < MIN_PIXELS:
            return None
        wv = max(float(np.percentile(v[keep], WHITE_PCT)), 1.0)
        d = float(np.log10(b / wv)) if b > wv else 0.0
        if d < MIN_RANGE:
            d = MIN_RANGE
            wv = b / 10.0 ** MIN_RANGE
        black.append(b)
        white.append(wv)
        ranges.append(round(d, 5))
    record = {"from": "picture",
              "rect": [float(v) for v in rect] if rect is not None else None,
              "black_pct": BLACK_PCT, "white_pct": WHITE_PCT, "range": ranges}
    return Levels(tuple(black), tuple(white), record)
