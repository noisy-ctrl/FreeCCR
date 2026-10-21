"""False colour: the classic classic exposure scale over a greyscale picture, and
the HDR glow drawn as dots. Display tools only; nothing here ever reaches an
export. See spec/false-colour.md."""
from __future__ import annotations

import numpy as np

# (low, high, RGB, label, meaning): signal level as a fraction of white, on
# the finished picture's video luma (Rec.709 weights on the display values),
# the way a monitor's false colour reads the signal. classic camera's classic
# bands; everything outside them is shown as greyscale.
BANDS = (
    (0.000, 0.025, (148, 50, 210), "0–2.5 %", "black clipping"),
    (0.025, 0.040, (40, 110, 240), "2.5–4 %", "just above black clipping"),
    (0.380, 0.420, (40, 200, 70), "38–42 %", "mid grey"),
    (0.520, 0.560, (255, 125, 200), "52–56 %", "one stop over grey (skin)"),
    (0.970, 0.990, (255, 230, 40), "97–99 %", "just below white clipping"),
    (0.990, 1.001, (240, 30, 30), "99–100 %", "white clipping"),
)
_LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


def signal_level(rgb8: np.ndarray) -> np.ndarray:
    """Video luma of an 8-bit display picture, 0..1."""
    return (rgb8[..., :3].astype(np.float32) @ _LUMA) / np.float32(255.0)


def false_colour(rgb8: np.ndarray) -> np.ndarray:
    """The false-colour view of an HxWx3 uint8 display picture: greyscale by
    signal level, with each band painted in its colour. A new array."""
    y = signal_level(rgb8)
    grey = np.clip(np.rint(y * 255.0), 0, 255).astype(np.uint8)
    out = np.repeat(grey[..., None], 3, axis=2)
    for lo, hi, colour, _label, _meaning in BANDS:
        out[(y >= lo) & (y < hi)] = colour
    return out


# --- HDR glow as dots ----------------------------------------------------------
# A halftone of small two-tone dots (white with a black partner, so they read
# on any background, the false-colour bands included). The stronger the glow,
# the more of the grid's dots are shown, in a 4 x 4 ordered-dither order, so
# any glow at all shows the sparsest layer.
DOT_SPACING = 6          # px at preview resolution, per dot cell
DOT_SIZE = 2             # px square
_BAYER4 = np.array([[0, 8, 2, 10],
                    [12, 4, 14, 6],
                    [3, 11, 1, 9],
                    [15, 7, 13, 5]], np.float32) / 16.0


def glow_dots_rgba(strength: np.ndarray) -> np.ndarray:
    """RGBA uint8 (HxWx4) dot overlay for a glow strength map (0..1, HxW,
    0 = none). Dots are fully opaque; everything else is transparent."""
    s = np.asarray(strength, np.float32)
    h, w = s.shape[:2]
    out = np.zeros((h, w, 4), np.uint8)
    if h == 0 or w == 0 or not np.any(s > 0):
        return out
    d, k = DOT_SPACING, DOT_SIZE
    half = d // 2
    ys, xs = np.mgrid[0:h, 0:w]
    cy, cx = ys // d, xs // d                     # which cell
    oy, ox = ys - cy * d, xs - cx * d             # where in the cell
    threshold = _BAYER4[cy % 4, cx % 4]
    shown = (s > 0) & (s >= threshold)
    white = shown & (oy < k) & (ox < k)
    black = shown & (oy >= half) & (oy < half + k) & (ox >= half) & (ox < half + k)
    out[white] = (255, 255, 255, 255)
    out[black] = (0, 0, 0, 255)
    return out
