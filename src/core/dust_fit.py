"""Fit a clicked dust dab to the speck under it (spec/dust-fit-spot.md).

A speck has a soft halo past its bright core. A dab that covers only the
core leaves the halo, and the heal's clone source, which must touch the
hole, lands inside it: measured on a synthetic speck (core sigma 3 px, halo
sigma 8 px), a just-covering dab left 4-16 levels of speck. This grows the
dab's radius until the speck's excess over the local background has faded
into the grain, on the picture the user is looking at. Qt-free."""
import numpy as np

GROW_MAX = 3.0          # at most three times the brush
SIGNIFICANT = 3.0       # the dab's content must stand this many grain MADs off
FADE = 0.12             # a ring belongs to the speck while its mean excess is
                        # above this share of the peak excess ...
RING_SIGMA = 2.0        # ... and above this many standard errors of the ring
MARGIN = 1.0            # extra px past the last speck ring
GRAIN_CORR = 4.0        # film grain is correlated over a few px: a ring of n
                        # px holds about n / GRAIN_CORR independent samples


def fit_radius(gray, cx, cy, r):
    """Radius (px, >= r) that covers the speck at (cx, cy) in `gray` (2-D
    float), or r when there is no distinct speck. `r` is the brush radius in
    the same pixels."""
    r = float(r)
    if r <= 0 or gray is None or gray.ndim != 2:
        return r
    h, w = gray.shape
    outer = GROW_MAX * r + 4.0 * max(1.0, r)        # background annulus beyond
    R = int(np.ceil(outer + 2))
    x0, x1 = int(max(0, np.floor(cx - R))), int(min(w, np.ceil(cx + R + 1)))
    y0, y1 = int(max(0, np.floor(cy - R))), int(min(h, np.ceil(cy + R + 1)))
    if x1 - x0 < 5 or y1 - y0 < 5:
        return r
    g = np.asarray(gray[y0:y1, x0:x1], np.float64)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    d = np.hypot(xx + 0.0 - cx, yy + 0.0 - cy)
    bg_ring = (d > GROW_MAX * r + 1.0) & (d <= outer)
    if bg_ring.sum() < 20:
        return r
    bg = float(np.median(g[bg_ring]))
    mad = float(np.median(np.abs(g[bg_ring] - bg))) * 1.4826 + 1e-6
    inside = d <= max(r, 1.0)
    excess = g[inside].mean() - bg
    if abs(excess) < SIGNIFICANT * mad / np.sqrt(max(1.0, inside.sum() / GRAIN_CORR)) or \
            abs(excess) < 0.5 * mad:
        return r                                   # nothing distinct under it
    sign = 1.0 if excess > 0 else -1.0
    peak = sign * excess
    last = 0.0
    step = 1.0 if r >= 3 else 0.5
    rad = step
    while rad <= GROW_MAX * r:
        ring = (d > rad - step) & (d <= rad)
        n = int(ring.sum())
        if n:
            m = sign * (g[ring].mean() - bg)
            if m > FADE * peak and m > RING_SIGMA * mad / np.sqrt(max(1.0, n / GRAIN_CORR)):
                last = rad
            elif rad > r:
                break
        rad += step
    return float(min(GROW_MAX * r, max(r, last + MARGIN)))
