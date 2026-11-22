"""Pure-numpy scope math for the canvas Scopes panel (RGB parade + vectorscope).

No Qt imports — everything here is unit-testable headless. The Qt side
(widgets/scopes_panel.py) turns these count arrays into images; the capture of
the displayed image lives in widgets/image_preview.py. See
spec/color-scopes-panel.md.
"""

import numpy as np

# BT.601 full-range chroma weights (the standard vectorscope plane). For 8-bit
# RGB input Cb/Cr land in ~[-127.5, 127.5]; both weight rows sum to exactly 0,
# so every neutral gray maps to the scope center.
_CB_W = np.array([-0.168736, -0.331264, 0.5], dtype=np.float32)
_CR_W = np.array([0.5, -0.418688, -0.081312], dtype=np.float32)

VECTOR_SIZE = 128           # vectorscope histogram bins per side

# Parade display axis (DaVinci-style 10-bit code values). The data stays
# 8-bit/256 bins — 8-bit 255 ≡ 10-bit 1023, so only labels/graticule change.
PARADE_MAX_CODE = 1023
PARADE_GRID_STEP = 128      # a graticule line every 128 codes (+ the 1023 top)
CINEON_REF_CODES = (95, 685)  # Cineon printing-density refs: Dmin / 90% white


def rgb_to_cbcr(rgb):
    """RGB (..., 3) → (cb, cr) float32 arrays in ~[-128, 128]."""
    a = np.asarray(rgb, dtype=np.float32)
    return a @ _CB_W, a @ _CR_W


def compute_parade(rgb, mask):
    """Per-column per-value counts for the RGB parade.

    rgb (H, W, 3) uint8, mask (H, W) bool → counts (3, 256, W) float32 where
    ``counts[c, v, x]`` = number of masked pixels in column x with value v.
    """
    h, w = np.asarray(mask).shape
    counts = np.zeros((3, 256, w), dtype=np.float32)
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return counts
    base = xs.astype(np.intp) * 256
    for c in range(3):
        flat = base + rgb[ys, xs, c].astype(np.intp)
        counts[c] = np.bincount(flat, minlength=w * 256).reshape(w, 256).T
    return counts


def compute_vectorscope(rgb, mask, size=VECTOR_SIZE, zoom=1.0):
    """2D chroma histogram on the CbCr plane.

    Returns (size, size) float32 counts; x = Cb (left→right), y = Cr with
    +Cr UP (row 0 = strongest red direction) — broadcast-vectorscope
    orientation, matching :func:`cbcr_to_plot`. `zoom` > 1 spreads the
    middle out (spec/scope-outline-and-zoom.md): the plot then spans
    ±128/zoom, and what lies beyond it is left out rather than piled on
    the edge.
    """
    px = np.asarray(rgb)[np.asarray(mask, dtype=bool)]
    if px.size == 0:
        return np.zeros((size, size), dtype=np.float32)
    cb, cr = rgb_to_cbcr(px)
    scale = size / 256.0
    if zoom != 1.0:
        cb, cr = cb * np.float32(zoom), cr * np.float32(zoom)
        inside = (np.abs(cb) < 128.0) & (np.abs(cr) < 128.0)
        cb, cr = cb[inside], cr[inside]
    ix = np.clip(((cb + 128.0) * scale).astype(np.intp), 0, size - 1)
    iy = np.clip(((128.0 - cr) * scale).astype(np.intp), 0, size - 1)
    counts = np.bincount(iy * size + ix, minlength=size * size)
    counts = counts.reshape(size, size).astype(np.float32)
    if zoom > 1.5:
        # the display values are 8-bit, so zoomed in their chroma steps show as
        # a grid of empty bins; spread each count over its step
        import cv2
        counts = cv2.GaussianBlur(counts, (0, 0), 0.3 * zoom * size / 256.0 * 2.0)
    return counts


def cbcr_to_plot(cb, cr, size):
    """Map chroma coordinates to (x, y) on a size×size plot (same convention
    as compute_vectorscope: +Cb right, +Cr up)."""
    scale = size / 256.0
    return (cb + 128.0) * scale, (128.0 - cr) * scale


def scale_counts(counts, pctl=98.0, gamma=0.55):
    """Normalise raw counts to [0, 1] display intensity.

    The reference is a high percentile of the NONZERO counts — a dominant
    flat-area pile must clip at full brightness instead of defining the scale
    and crushing sparse single-pixel traces to invisibility (same philosophy
    as the histogram widget's vertical scale). The sub-linear gamma then
    lifts what remains of the faint traces into the visible range.
    """
    c = np.asarray(counts, dtype=np.float32)
    nz = c[c > 0]
    if nz.size == 0:
        return np.zeros_like(c)
    ref = max(float(np.percentile(nz, pctl)), 1.0)
    return np.clip(c / ref, 0.0, 1.0) ** np.float32(gamma)


_VECTOR_LUT_CACHE = {}


def vector_color_lut(size=VECTOR_SIZE, luma=140.0):
    """(size, size, 3) float32 in [0, 1]: the RGB hue of each CbCr bin at a
    fixed luma. Multiplied by the trace intensity it tints the vectorscope
    trace with its own hue. Static per size — cached."""
    key = (size, luma)
    lut = _VECTOR_LUT_CACHE.get(key)
    if lut is None:
        axis = (np.arange(size, dtype=np.float32) + 0.5) * (256.0 / size) - 128.0
        cb, cr = np.meshgrid(axis, -axis)   # row 0 = +Cr (up), like compute_vectorscope
        r = luma + 1.402 * cr
        g = luma - 0.344136 * cb - 0.714136 * cr
        b = luma + 1.772 * cb
        lut = np.clip(np.stack([r, g, b], axis=-1) / 255.0, 0.0, 1.0)
        _VECTOR_LUT_CACHE[key] = lut
    return lut


def skin_tone_direction():
    """Unit (cb, cr) direction of the vectorscope skin-tone line: the +I axis
    of YIQ, mapped through the same RGB→CbCr conversion as the trace. The RGB
    direction of pure +I (Y=0, Q=0) comes from the NTSC YIQ inverse matrix;
    in our plane it lands at ≈132° — between the R and Yl targets, closer to
    R, the classic 10:30–11 o'clock skin line."""
    cb, cr = rgb_to_cbcr(np.array([0.9563, -0.2721, -1.1070], dtype=np.float32))
    norm = float(np.hypot(cb, cr))
    return float(cb) / norm, float(cr) / norm


def vector_targets(frac=0.75):
    """The six standard vectorscope targets at ``frac`` intensity, derived
    from the same RGB→CbCr conversion the trace uses (so the boxes always
    agree with plotted data). Returns [(label, cb, cr)] in R Mg B Cy G Yl
    order."""
    prim = (("R", (255, 0, 0)), ("Mg", (255, 0, 255)), ("B", (0, 0, 255)),
            ("Cy", (0, 255, 255)), ("G", (0, 255, 0)), ("Yl", (255, 255, 0)))
    out = []
    for name, c in prim:
        cb, cr = rgb_to_cbcr(np.array(c, dtype=np.float32) * frac)
        out.append((name, float(cb), float(cr)))
    return out
