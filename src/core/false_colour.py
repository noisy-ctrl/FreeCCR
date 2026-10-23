"""False colour: a classic exposure scale over a greyscale picture (or a
scale of stop zones), and the HDR glow drawn as dots. Display tools only; nothing here ever reaches an
export. See spec/false-colour.md."""
from __future__ import annotations

import numpy as np

# (low, high, RGB, label, meaning): signal level as a fraction of white, on
# the finished picture's video luma (Rec.709 weights on the display values),
# the way a monitor's false colour reads the signal; the classic cinema
# camera bands; everything outside them is shown as greyscale.
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


# --- Stop zones -----------------------------------------------------------------
# The alternative scale (right-click False colour, or Settings -> General ->
# Preview): 15 zones in stops from mid grey, whole stops out to +/-6 with
# half-stop zones either side of grey, each its own colour. Read either from
# the NEGATIVE (its density above the film base: the film recorded the scene
# in log, which is what such a scale needs; the grade doesn't change it) or
# from the finished picture (its display light: white is only ~+2.5 stops
# over grey, so the zones above that never appear). spec/false-colour.md.
ZONES = (
    # (label, low, high, RGB): stops from mid grey, [low, high)
    ("\u22126", -np.inf, -5.5, (0, 0, 0)),
    ("\u22125", -5.5, -4.5, (112, 48, 160)),
    ("\u22124", -4.5, -3.5, (24, 96, 176)),
    ("\u22123", -3.5, -2.5, (0, 170, 235)),
    ("\u22122", -2.5, -1.5, (24, 140, 40)),
    ("\u22121", -1.5, -0.75, (60, 200, 70)),
    ("\u2212\u00bd", -0.75, -0.25, (40, 245, 40)),
    ("0", -0.25, 0.25, (128, 128, 128)),
    ("+\u00bd", 0.25, 0.75, (245, 240, 20)),
    ("+1", 0.75, 1.5, (250, 245, 130)),
    ("+2", 1.5, 2.5, (245, 135, 25)),
    ("+3", 2.5, 3.5, (250, 170, 50)),
    ("+4", 3.5, 4.5, (235, 25, 25)),
    ("+5", 4.5, 5.5, (245, 85, 85)),
    ("+6", 5.5, np.inf, (255, 255, 255)),
)
_ZONE_EDGES = np.array([z[2] for z in ZONES[:-1]], np.float32)
_ZONE_RGB = np.array([z[3] for z in ZONES], np.uint8)
ZONE_SCALES = ("bands", "zones")
ZONE_SOURCES = ("negative", "picture")

# The negative: a normally exposed mid grey sits about 0.70 density above the
# film base, and the film's contrast (gamma ~0.6) makes one stop of exposure
# 0.6 x log10(2) = 0.18 density (the Cineon / lab-aim convention). The film
# base itself is then about -3.9 stops: below that a negative holds nothing.
GREY_DENSITY = 0.70
FILM_GAMMA = 0.60
STOP_DENSITY = FILM_GAMMA * float(np.log10(2.0))
MID_GREY = 0.18            # the picture: 18 % of white, in linear light
_DENSITY_FLOOR = 1.0       # raw units (0..65535): avoids log(0)


def zone_index(stops: np.ndarray) -> np.ndarray:
    """Zone number 0..14 (ZONES order) for each value in stops."""
    return np.searchsorted(_ZONE_EDGES, np.asarray(stops, np.float32),
                           side="right").astype(np.uint8)


def zones_rgb(index: np.ndarray) -> np.ndarray:
    """The zone colours for a zone-index map (uint8 HxWx3)."""
    return _ZONE_RGB[np.asarray(index, np.uint8)]


def film_base(raw: np.ndarray) -> tuple:
    """A film-base estimate when no black point is known: each channel's
    99.9th percentile (the clearest film in the frame), in raw units."""
    a = np.asarray(raw)[::4, ::4].reshape(-1, 3).astype(np.float32)
    return tuple(float(np.percentile(a[:, c], 99.9)) for c in range(3))


def negative_stops(raw: np.ndarray, base) -> np.ndarray:
    """Stops from mid grey of an unconverted negative scan (HxWx3, linear,
    BGR or RGB - green is read, the middle channel either way): its density
    above the film base `base` (that channel order too), against a normally
    exposed grey at GREY_DENSITY."""
    g = np.maximum(np.asarray(raw)[..., 1].astype(np.float32), _DENSITY_FLOOR)
    b = max(float(base[1]), _DENSITY_FLOOR)
    d = np.log10(np.float32(b) / g)
    return (d - np.float32(GREY_DENSITY)) / np.float32(STOP_DENSITY)


def _srgb_to_linear(v):
    v = np.asarray(v, np.float32)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def picture_stops(rgb8: np.ndarray) -> np.ndarray:
    """Stops from mid grey of an 8-bit display picture: its luminance in
    linear light (sRGB decoded, Rec.709 weights) against 18 % of white."""
    lin = _srgb_to_linear(rgb8[..., :3].astype(np.float32) / np.float32(255.0))
    y = lin @ _LUMA
    return np.log2(np.maximum(y, np.float32(1e-6)) / np.float32(MID_GREY))


def stop_zones(rgb8: np.ndarray) -> np.ndarray:
    """The stop-zone view of a finished picture (the 'picture' reading)."""
    return zones_rgb(zone_index(picture_stops(rgb8)))


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
