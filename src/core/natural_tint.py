"""Natural tint (spec/natural-tint.md): a B&W frame given back the tint it
has in Color profile, as a curve over lightness (never per pixel), so grain
and stray colour objects don't come back with it. Qt-free."""
import cv2
import numpy as np

from core.oklab import fit_move, from_oklab, to_oklab  # noqa: F401

SAMPLE_LONG = 256       # the tint is measured on a copy this long
BINS = 32
MIN_COUNT = 20          # a bin with fewer samples is filled from its neighbours
SMOOTH_BINS = 1.5       # Gaussian over bins
DARK_FADE = 0.08        # the tint fades out to pure black below this OKLab L
LIGHT_FADE = 0.94       # ... and to pure white above this
EPS = 1e-6


def _smoothstep(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def tint_curve(colour01, area=None):
    """(L centres, a, b) of the frame's tint: per lightness bin the median
    OKLab a and b of the colour render, filled and smoothed. `area` = the
    crop's (x1, y1, x2, y2) shares, or None for the whole frame."""
    c = np.asarray(colour01, np.float32)
    H, W = c.shape[:2]
    if area is not None:
        x1, y1, x2, y2 = area
        c0, c1 = int(round(min(x1, x2) * W)), int(round(max(x1, x2) * W))
        r0, r1 = int(round(min(y1, y2) * H)), int(round(max(y1, y2) * H))
        if c1 - c0 >= 4 and r1 - r0 >= 4:
            c = c[max(r0, 0):r1, max(c0, 0):c1]
            H, W = c.shape[:2]
    s = SAMPLE_LONG / float(max(H, W))
    if s < 1.0:
        c = cv2.resize(c, (max(1, round(W * s)), max(1, round(H * s))),
                       interpolation=cv2.INTER_AREA)
    lab = to_oklab(np.clip(c, 0.0, 1.0)).reshape(-1, 3)
    edges = np.linspace(0.0, 1.0, BINS + 1)
    centres = 0.5 * (edges[:-1] + edges[1:])
    idx = np.clip(np.digitize(lab[:, 0], edges) - 1, 0, BINS - 1)
    a = np.full(BINS, np.nan, np.float64)
    b = np.full(BINS, np.nan, np.float64)
    for k in range(BINS):
        sel = idx == k
        if np.count_nonzero(sel) >= MIN_COUNT:
            a[k] = float(np.median(lab[sel, 1]))
            b[k] = float(np.median(lab[sel, 2]))
    ok = ~np.isnan(a)
    if not ok.any():
        return centres, np.zeros(BINS), np.zeros(BINS)
    a = np.interp(centres, centres[ok], a[ok])
    b = np.interp(centres, centres[ok], b[ok])
    k = cv2.getGaussianKernel(int(np.ceil(SMOOTH_BINS * 3)) * 2 + 1, SMOOTH_BINS).ravel()
    pad = len(k) // 2
    a = np.convolve(np.pad(a, pad, mode="edge"), k, mode="valid")
    b = np.convolve(np.pad(b, pad, mode="edge"), k, mode="valid")
    fade = (_smoothstep(centres / DARK_FADE)
            * _smoothstep((1.0 - centres) / (1.0 - LIGHT_FADE)))
    return centres, a * fade, b * fade


def apply(grey01, colour01, amount, area=None):
    """The B&W picture `grey01` (HxWx3, R = G = B, 0..1) with `amount`/100 of
    the tint of `colour01` (the colour render it came from) added at each
    pixel's own lightness. A new float32 array; `grey01` itself at 0."""
    if not amount or amount <= 0:
        return grey01
    g = np.asarray(grey01, np.float32)
    centres, ca, cb = tint_curve(colour01, area)
    t = float(min(amount, 100.0)) / 100.0
    lab = to_oklab(np.clip(g, 0.0, 1.0))
    L = lab[..., 0]
    da = (np.interp(L, centres, ca) * t).astype(np.float32)
    db = (np.interp(L, centres, cb) * t).astype(np.float32)
    move = (np.abs(da) + np.abs(db)) > EPS
    out = g.copy()
    if not move.any():
        return out
    d = np.zeros((int(move.sum()), 3), np.float32)
    d[:, 1], d[:, 2] = da[move], db[move]
    out[move] = np.clip(fit_move(lab[move], d), 0.0, 1.0)
    return out
