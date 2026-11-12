"""Auto Radius for the Details sharpening (spec/auto-sharpen-radius.md,
revision 2026-11-12 b: sized to the GRAIN, so it comes out crisp as in a lab
scan; edges no longer decide it).

Measures, on a full-resolution scan, how large the grain is (σg) and turns it
into an unsharp-mask radius in full-resolution pixels. numpy + OpenCV only;
Qt-free.
"""
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

REGION_MAX = 3000          # px: the analysed region's side at most
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

# The unsharp mask's sigma (radius / 2) is set to the grain's own sigma: the
# high-pass then lifts detail at the grain's scale, so grain comes out crisp,
# and adds little halo to larger edges.
RADIUS_PER_GRAIN = 2.0
RADIUS_MIN_PX = 0.5
RADIUS_MAX_PX = 4.0         # the Radius slider's own maximum (100)
RADIUS_DIV = 25.0          # = ccr_processor.SHARPEN_RADIUS_DIV (tested)


@dataclass
class RadiusResult:
    radius_px: Optional[float]
    slider: Optional[int]
    sigma_grain: Optional[float] = None
    tiles: int = 0
    limited_by: str = ""        # "grain" | "fine" (sub-pixel) | "cap" | "floor"
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
    """(σg px or None, the grain's std, tiles used) from the flattest tiles'
    high-pass autocorrelation."""
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
        return None, float(hp.std()), 0
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
        return None, noise, len(halves)
    raw_sigma = float(np.median(halves)) / HALF_TO_SIGMA
    return GRAIN_GAIN * raw_sigma + GRAIN_OFFSET, noise, len(halves)


def measure(raw, crop_rect=None, full_long=None) -> RadiusResult:
    """The Auto Radius for the scan `raw` (full resolution): the grain's own
    size. `full_long` is the frame's full-resolution long side when `raw` was
    decoded at another size: the radius is rescaled to full-resolution
    pixels. Grain finer than GRAIN_MIN_SIGMA gets the smallest useful radius;
    no smooth area to measure in changes nothing (slider None)."""
    lum = luma_region(raw, crop_rect)
    sg, _noise, tiles = grain_sigma(lum)
    if sg is None:
        return RadiusResult(None, None, None, tiles, "",
                            "Not enough smooth areas to measure the grain in.")
    scale = 1.0
    long_side = max(np.asarray(raw).shape[:2])
    if full_long and long_side and full_long != long_side:
        scale = float(full_long) / float(long_side)
    sg_full = sg * scale
    if sg < GRAIN_MIN_SIGMA:
        r, limited = RADIUS_MIN_PX * scale, "fine"
    else:
        r, limited = RADIUS_PER_GRAIN * sg_full, "grain"
    if r > RADIUS_MAX_PX:
        r, limited = RADIUS_MAX_PX, "cap"
    elif r < RADIUS_MIN_PX:
        r, limited = RADIUS_MIN_PX, "floor"
    slider = int(round(r * RADIUS_DIV))
    why = {"grain": f"sized to the grain ({sg_full:.2f} px)",
           "fine": "grain finer than a pixel at this scan resolution: the "
                   "smallest useful radius",
           "cap": f"capped: the grain is very coarse ({sg_full:.2f} px)",
           "floor": f"sized to the grain ({sg_full:.2f} px), at the smallest "
                    f"useful radius"}[limited]
    return RadiusResult(r, slider, sg_full, tiles, limited,
                        f"Auto Radius {slider} ({slider / RADIUS_DIV:.1f} px): {why}.")


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
