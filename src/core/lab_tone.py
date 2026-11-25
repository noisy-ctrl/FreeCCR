"""Lab Tone: the labs' tone curve on lightness (spec/lab-tone.md).

One slider, 0..100, 50 = the measured labs' curve: a lifted, soft toe
(black at OKLab L 0.113, about 6/255), lower mids a touch darker, steeper
mids and a soft top (white at L 0.979, about 250/255), on top of FreeCCR's
own conversion-time shoulder (Auto tone). Applied to each pixel's own OKLab L with its a and b kept, so hue
and chroma don't change; a move that would leave 0..1 is shortened
(core.oklab.fit_move) and a pixel the curve doesn't move comes back exactly.

The curve is a monotone cubic (Fritsch-Carlson) through the knots, so C1 and
never turning back. Other amounts keep it monotone by working on its SLOPE:
slope^(amount/50) between ends moved linearly (black = k . black_lab, white =
1 + k (white_lab - 1)), so 0 is the identity, 50 the labs', 100 a stronger
version whose slope never reaches 0. A tone curve on the pixel's own
lightness has nothing for grain to steer; grain is scaled by the curve's
slope like any tone control's."""
import functools

import numpy as np

from core import lab_tone_data as data
from core.oklab import fit_move, to_oklab

LAB_AMOUNT = 50.0
AMOUNT_MAX = 100.0
GRID = np.linspace(0.0, 1.0, 4097)
MOVE_EPS = 1e-6
CHUNK = 1 << 20

_X = np.asarray(data.KNOTS, np.float64)
_Y = np.asarray(data.VALUES, np.float64)


def _pchip(x, y, xs):
    """Fritsch-Carlson monotone cubic through (x, y), evaluated at xs."""
    h = np.diff(x)
    d = np.diff(y) / h
    m = np.zeros_like(y)
    for i in range(1, len(y) - 1):
        if d[i - 1] * d[i] > 0:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
    m[0], m[-1] = d[0], d[-1]
    i = np.clip(np.searchsorted(x, xs, side="right") - 1, 0, len(h) - 1)
    t = (xs - x[i]) / h[i]
    return ((2 * t ** 3 - 3 * t ** 2 + 1) * y[i] + (t ** 3 - 2 * t ** 2 + t) * h[i] * m[i]
            + (-2 * t ** 3 + 3 * t ** 2) * y[i + 1] + (t ** 3 - t ** 2) * h[i] * m[i + 1])


@functools.lru_cache(maxsize=16)
def curve(amount):
    """The tone curve on GRID for a slider amount (rounded to 0.5)."""
    k = min(max(float(amount), 0.0), AMOUNT_MAX) / LAB_AMOUNT
    f = _pchip(_X, _Y, GRID)
    if abs(k - 1.0) < 1e-9:
        return f
    if k <= 0:
        return GRID.copy()
    s = np.maximum(np.gradient(f, GRID), 1e-4) ** k
    cum = np.concatenate([[0.0], np.cumsum((s[1:] + s[:-1]) * 0.5 * np.diff(GRID))])
    lo, hi = k * _Y[0], 1.0 + k * (_Y[-1] - 1.0)
    return lo + (hi - lo) * cum / cum[-1]


def apply_lab_tone(img01, amount):
    """Lab Tone on an HxWx3 float image in 0..1 (display values). The input
    itself at 0, else a new float32 array."""
    amount = min(max(float(amount or 0.0), 0.0), AMOUNT_MAX)
    if amount <= 0:
        return img01
    t = curve(round(amount * 2) / 2).astype(np.float32)
    a = np.asarray(img01, dtype=np.float32)
    flat = a.reshape(-1, 3)
    out = flat.copy()
    for s in range(0, flat.shape[0], CHUNK):
        x = flat[s:s + CHUNK]
        lab = to_oklab(x)
        L2 = np.interp(np.clip(lab[:, 0], 0.0, 1.0), GRID, t).astype(np.float32)
        moved = np.abs(L2 - lab[:, 0]) > MOVE_EPS
        if not moved.any():
            continue
        lm = lab[moved]
        new = lm.copy()
        new[:, 0] = L2[moved]
        res = x.copy()
        res[moved] = np.clip(fit_move(lm, new - lm), 0.0, 1.0)
        out[s:s + CHUNK] = res
    return out.reshape(a.shape)
