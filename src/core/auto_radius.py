"""Auto Radius for the Details sharpening (spec/auto-sharpen-radius.md).

Measures, on a full-resolution scan, how soft the sharpest edges are (σe) and
how large the grain is (σg), and turns them into an unsharp-mask radius in
full-resolution pixels. numpy + OpenCV only; Qt-free.
"""
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

REGION_MAX = 3000          # px: the analysed region's side at most
SMOOTH_SIGMA = 1.0         # px: smoothing before the gradient (removed after)
EDGE_TOP = 0.003           # strongest share of pixels taken as edge candidates
EDGE_MAX = 4000            # candidates examined at most
PROFILE_HALF = 8.0         # px either side of the edge
PROFILE_STEP = 0.5
PLATEAU = (5.0, 8.0)       # |t| range of the plateaus
PLATEAU_FLAT = 0.12        # plateau std <= this share of the step
STEP_OVER_GRAIN = 8.0      # step >= this x the grain's std
MONOTONE_TOL = 0.05        # share of the step a profile may fall back
EDGE_LOW_PCT = 20.0        # the sharp group: edges within EDGE_GROUP x the
EDGE_GROUP = 1.5           # 20th percentile; their median is sigma_e
EDGES_MIN = 25
RISE_TO_SIGMA = 2.563      # 10-90 % rise of a Gaussian-blurred step / sigma

TILE = 64
HIGHPASS_SIGMA = 6.0
FLAT_SHARE = 0.15
TILES_MIN = 12
HALF_TO_SIGMA = 1.665      # half-height radius of exp(-r^2/4s^2) / s
GRAIN_MIN_SIGMA = 0.45     # px: below this the noise is (sub-)pixel
# The high-pass and the window narrow the measured autocorrelation; fitted on
# Gaussian-correlated grain of sigma 0.5-2 px (tests/test_auto_radius.py):
# true = GRAIN_GAIN * measured + GRAIN_OFFSET, within 5 %.
GRAIN_GAIN = 1.21
GRAIN_OFFSET = 0.07

MASK_SIGMA_PER_GRAIN = 1.5  # mask sigma beyond this x grain carries clumps
RADIUS_MIN_PX = 0.5
RADIUS_MAX_PX = 2.5
RADIUS_DIV = 25.0          # = ccr_processor.SHARPEN_RADIUS_DIV (tested)


@dataclass
class RadiusResult:
    radius_px: Optional[float]
    slider: Optional[int]
    sigma_edge: Optional[float] = None
    sigma_grain: Optional[float] = None
    edges: int = 0
    limited_by: str = ""        # "edges" | "grain" | "cap" | "floor"
    reason: str = ""


def luma_region(raw, crop_rect=None, region_max=REGION_MAX):
    """Float32 luma (mean of the channels, scaled to its 99.5th percentile)
    of the picture area: inside `crop_rect` (normalised x1, y1, x2, y2 in the
    unrotated scan, its angle ignored - only a region is needed) or the middle
    80 %, then at most a centred `region_max` square."""
    a = np.asarray(raw)
    h, w = a.shape[:2]
    if crop_rect:
        x1, y1, x2, y2 = crop_rect
        x1, x2 = sorted((int(round(x1 * w)), int(round(x2 * w))))
        y1, y2 = sorted((int(round(y1 * h)), int(round(y2 * h))))
    else:
        x1, y1, x2, y2 = int(0.1 * w), int(0.1 * h), int(0.9 * w), int(0.9 * h)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    rw, rh = min(x2 - x1, region_max), min(y2 - y1, region_max)
    sub = a[cy - rh // 2:cy - rh // 2 + rh, cx - rw // 2:cx - rw // 2 + rw]
    sub = sub.astype(np.float32)
    lum = sub.mean(axis=2) if sub.ndim == 3 else sub
    top = float(np.percentile(lum[::4, ::4], 99.5)) or 1.0
    return lum / top


def grain_sigma(lum):
    """(σg px or None, the grain's std) from the flattest tiles' high-pass
    autocorrelation."""
    hp = lum - cv2.GaussianBlur(lum, (0, 0), HIGHPASS_SIGMA)
    low = cv2.GaussianBlur(lum, (0, 0), 4.0)
    gx = cv2.Sobel(low, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(low, cv2.CV_32F, 0, 1)
    energy = gx * gx + gy * gy
    h, w = lum.shape
    tiles = []
    for y in range(0, h - TILE + 1, TILE):
        for x in range(0, w - TILE + 1, TILE):
            m = float(low[y:y + TILE, x:x + TILE].mean())
            if 0.03 < m < 0.95:
                tiles.append((float(energy[y:y + TILE, x:x + TILE].mean()), y, x))
    if len(tiles) < TILES_MIN:
        return None, float(hp.std())
    tiles.sort()
    keep = tiles[:max(TILES_MIN, int(len(tiles) * FLAT_SHARE))]
    win = np.outer(np.hanning(TILE), np.hanning(TILE)).astype(np.float32)
    yy, xx = np.mgrid[0:TILE, 0:TILE]
    rr = np.hypot(np.minimum(yy, TILE - yy), np.minimum(xx, TILE - xx))
    rb = np.round(rr).astype(int)
    halves, stds = [], []
    for _, y, x in keep:
        t = hp[y:y + TILE, x:x + TILE]
        t = (t - t.mean()) * win
        stds.append(float(hp[y:y + TILE, x:x + TILE].std()))
        f = np.fft.fft2(t)
        ac = np.real(np.fft.ifft2(f * np.conj(f)))
        if ac[0, 0] <= 0:
            continue
        ac /= ac[0, 0]
        prof = np.bincount(rb.ravel(), ac.ravel()) / np.maximum(
            np.bincount(rb.ravel()), 1)
        below = np.flatnonzero(prof[:12] < 0.5)
        if not below.size:
            continue
        k = int(below[0])
        if k == 0:
            continue
        a0, a1 = prof[k - 1], prof[k]
        halves.append(k - 1 + (a0 - 0.5) / max(a0 - a1, 1e-6))
    noise = float(np.median(stds)) if stds else float(hp.std())
    if len(halves) < TILES_MIN // 2:
        return None, noise
    raw_sigma = float(np.median(halves)) / HALF_TO_SIGMA
    return GRAIN_GAIN * raw_sigma + GRAIN_OFFSET, noise


def edge_sigmas(lum, noise):
    """σ (px) of the accepted edges, each from its 10-90 % rise."""
    sm = cv2.GaussianBlur(lum, (0, 0), SMOOTH_SIGMA)
    gy, gx = np.gradient(sm)
    mag = np.hypot(gx, gy)
    b = int(PROFILE_HALF) + 2
    mag[:b], mag[-b:], mag[:, :b], mag[:, -b:] = 0, 0, 0, 0
    thr = float(np.quantile(mag, 1.0 - EDGE_TOP))
    if thr <= 0:
        return np.array([])
    peak = cv2.dilate(mag, np.ones((5, 5), np.uint8))
    ys, xs = np.nonzero((mag >= thr) & (mag >= peak))
    if not ys.size:
        return np.array([])
    order = np.argsort(-mag[ys, xs])[:EDGE_MAX]
    ys, xs = ys[order], xs[order]
    nx = gx[ys, xs] / mag[ys, xs]
    ny = gy[ys, xs] / mag[ys, xs]
    t = np.arange(-PROFILE_HALF, PROFILE_HALF + 1e-6, PROFILE_STEP, dtype=np.float32)
    mx = (xs[:, None] + nx[:, None] * t[None, :]).astype(np.float32)
    my = (ys[:, None] + ny[:, None] * t[None, :]).astype(np.float32)
    prof = cv2.remap(sm, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    lo_m = (t <= -PLATEAU[0]) & (t >= -PLATEAU[1])
    hi_m = (t >= PLATEAU[0]) & (t <= PLATEAU[1])
    lo, hi = prof[:, lo_m], prof[:, hi_m]
    a, c = lo.mean(axis=1), hi.mean(axis=1)
    step = c - a
    ok = (step > STEP_OVER_GRAIN * max(noise, 1e-6))
    ok &= (lo.std(axis=1) <= PLATEAU_FLAT * step) & (hi.std(axis=1) <= PLATEAU_FLAT * step)
    mid = prof[:, (t > -PLATEAU[0]) & (t < PLATEAU[0])]
    run_max = np.maximum.accumulate(mid, axis=1)
    ok &= ((run_max - mid).max(axis=1) <= MONOTONE_TOL * step)
    if not ok.any():
        return np.array([])
    p = (prof[ok] - a[ok, None]) / step[ok, None]
    sig = []
    for row in p:
        i10 = np.flatnonzero(row >= 0.1)
        i90 = np.flatnonzero(row >= 0.9)
        if not i10.size or not i90.size or i10[0] == 0 or i90[0] == 0:
            continue
        def cross(i, lvl):
            r0, r1 = row[i - 1], row[i]
            return t[i - 1] + (lvl - r0) / max(r1 - r0, 1e-6) * PROFILE_STEP
        d = cross(i90[0], 0.9) - cross(i10[0], 0.1)
        s = d / RISE_TO_SIGMA
        sig.append(np.sqrt(max(s * s - SMOOTH_SIGMA ** 2, 0.09)))
    return np.asarray(sig, dtype=np.float64)


def measure(raw, crop_rect=None, full_long=None) -> RadiusResult:
    """The Auto Radius for the scan `raw` (full resolution). `full_long` is
    the frame's full-resolution long side when `raw` was decoded at another
    size: the radius is rescaled to full-resolution pixels."""
    lum = luma_region(raw, crop_rect)
    sg, noise = grain_sigma(lum)
    if sg is not None and sg < GRAIN_MIN_SIGMA:
        sg = None
    sig = edge_sigmas(lum, noise)
    if sig.size < EDGES_MIN:
        return RadiusResult(None, None, None, sg, int(sig.size), "",
                            f"Not enough clean edges to measure ({sig.size}).")
    # The sharp group: one blur level spreads about its true value (single
    # edges are noisy), so its MEDIAN is the estimate; wider, out-of-focus
    # edges are left out by the cut at EDGE_GROUP x the 20th percentile.
    low = float(np.percentile(sig, EDGE_LOW_PCT))
    se = float(np.median(sig[sig <= EDGE_GROUP * low]))
    r, limited = 2.0 * se, "edges"
    if sg is not None and 2.0 * MASK_SIGMA_PER_GRAIN * sg < r:
        r, limited = 2.0 * MASK_SIGMA_PER_GRAIN * sg, "grain"
    if r > RADIUS_MAX_PX:
        r, limited = RADIUS_MAX_PX, "cap"
    elif r < RADIUS_MIN_PX:
        r, limited = RADIUS_MIN_PX, "floor"
    scale = 1.0
    long_side = max(np.asarray(raw).shape[:2])
    if full_long and long_side and full_long != long_side:
        scale = float(full_long) / float(long_side)
    r *= scale
    slider = int(round(min(max(r, RADIUS_MIN_PX), RADIUS_MAX_PX) * RADIUS_DIV))
    grain = f"grain {sg * scale:.2f} px" if sg is not None else "grain finer than a pixel"
    why = {"edges": "", "grain": " (held back by the grain)",
           "cap": " (capped: the edges are very soft)", "floor": ""}[limited]
    return RadiusResult(r, slider, se * scale, None if sg is None else sg * scale,
                        int(sig.size), limited,
                        f"Auto Radius {slider} ({slider / RADIUS_DIV:.1f} px): "
                        f"edges {se * scale:.2f} px, {grain}{why}.")


def measure_image(img) -> RadiusResult:
    """Auto Radius for a loaded CCRImage: its scan decoded at full resolution
    on a SHALLOW COPY (read_image writes decode state onto the image; the
    image itself is never touched), measured inside its crop. Safe on a
    worker thread."""
    import copy
    proxy = copy.copy(img)
    raw = proxy.read_image(img.file_path, preview=False)
    if raw is None:
        return RadiusResult(None, None, reason="Couldn't read the scan.")
    full = getattr(img, "original_full_size", None)
    return measure(raw, getattr(img, "crop_rect", None),
                   full_long=max(full) if full else None)
