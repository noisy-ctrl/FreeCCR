"""Developer edge effect: the border a developer leaves where a dense area meets
a thin one (Mackie lines, the Eberhard effect), added on the negative's density.

Fresh developer diffuses from thin areas into dense ones beside them and
exhausted developer the other way, so a dense area develops a little denser at
its border and a thin one a little thinner, over a fraction of a millimetre.
Here: the band of detail between the grain's size and the developer's reach is
added back, soft-limited (a developer's effect saturates), and only where that
band stands above the grain's own share of it, decided on its energy smoothed
over the reach, so grain can't steer it and plain areas are left alone.

The reach is in mm on the film, so every render (the fitted view, a zoom, an
export) and every format agree. See spec/developer-edge-effect.md.
"""

import cv2
import numpy as np

# Options: Reach in hundredths of a mm (0.05-0.50 mm), each dye layer on its own.
EDGE_SHAPE_DEFAULT = {"reach": 10, "layers": 0}
EDGE_LIMITS = {"reach": (5, 50), "layers": (0, 1)}

GRAIN_MM = 0.02      # the inner blur: detail finer than this (grain) is left out
LIMIT = 0.04         # the effect saturates here (window units of density)
CORE = 3.0           # x the grain's own band energy: no stronger = left alone
FLOOR_Q = 10         # percentile of the band's energy taken as the grain's floor
DEFAULT_LONG_MM = 36.0
MIN_SIGMA_PX = 8     # wide blurs run on a copy reduced so sigma stays >= this


def normalize_edge_shape(shape):
    """A clean options dict, or None when absent / not a dict / all default."""
    if not isinstance(shape, dict):
        return None
    out = {}
    for k, dflt in EDGE_SHAPE_DEFAULT.items():
        lo, hi = EDGE_LIMITS[k]
        try:
            out[k] = int(round(float(np.clip(float(shape.get(k, dflt)), lo, hi))))
        except (TypeError, ValueError):
            out[k] = dflt
    return None if out == EDGE_SHAPE_DEFAULT else out


def shape_of(shape):
    return normalize_edge_shape(shape) or dict(EDGE_SHAPE_DEFAULT)


def frame_long_mm(img, shape=None):
    """The frame's long side on the film in mm: its format (Crop panel, then
    Capture's guide, then what auto crop found, then the crop's proportions),
    else 35 mm's 36."""
    from core import film_formats
    fmt = film_formats.film_key(getattr(img, "film_format", None))
    guide = getattr(img, "capture_guide", None)
    if not fmt and isinstance(guide, dict):
        fmt = film_formats.film_key(guide.get("format"))
    if not fmt:
        last = getattr(img, "auto_crop_last", None)
        fmt = film_formats.film_key(getattr(last, "fmt", "") if last is not None else "")
    rect = getattr(img, "crop_rect", None)
    if not fmt and rect is not None and shape is not None:
        h, w = shape[:2]
        try:
            fmt = film_formats.from_ratio(abs(rect[2] - rect[0]) * w,
                                          abs(rect[3] - rect[1]) * h)
        except Exception:
            fmt = None
    return film_formats.long_mm(fmt) or DEFAULT_LONG_MM


def px_per_mm(shape, long_mm, crop=None):
    """Pixels per mm on the film for a buffer of `shape`: the crop's long side
    (the frame) over the film's, else the whole buffer's."""
    h, w = shape[:2]
    if crop is not None:
        x1, y1, x2, y2 = (float(v) for v in crop)
        side = max(abs(x2 - x1) * w, abs(y2 - y1) * h)
        if side >= 16:
            return side / float(long_mm)
    return max(h, w) / float(long_mm)


def _floor_area(shape, crop):
    """Where the grain's floor is measured: the crop's box, else the middle."""
    h, w = shape[:2]
    if crop is not None:
        x1, y1, x2, y2 = (float(v) for v in crop)
        c0, c1 = int(min(x1, x2) * w), int(np.ceil(max(x1, x2) * w))
        r0, r1 = int(min(y1, y2) * h), int(np.ceil(max(y1, y2) * h))
        if c1 - c0 >= 16 and r1 - r0 >= 16:
            return r0, r1, c0, c1
    return h // 8, 7 * h // 8, w // 8, 7 * w // 8


def _blur(x, sigma):
    """Gaussian blur (BORDER_REFLECT). A wide one runs on a copy reduced by
    INTER_AREA so sigma stays >= MIN_SIGMA_PX (the box's own variance taken
    off) and is scaled back up bilinearly: within 1.3e-4 of a full-size blur
    at sigma 22 px (measured on a noisy test frame), ~20x quicker. The field
    is far smoother than its sampling, so this is no decision on a small map."""
    sigma = float(sigma)
    H, W = x.shape[:2]
    f = max(1, int(sigma // MIN_SIGMA_PX))
    if f == 1:
        return cv2.GaussianBlur(x, (0, 0), sigma, borderType=cv2.BORDER_REFLECT)
    small = cv2.resize(x, (max(1, round(W / f)), max(1, round(H / f))),
                       interpolation=cv2.INTER_AREA)
    k = small.shape[1] / float(W)
    s = np.sqrt(max(sigma * sigma - (f * f - 1) / 12.0, 1.0)) * k
    g = cv2.GaussianBlur(small, (0, 0), s, borderType=cv2.BORDER_REFLECT)
    return cv2.resize(g, (W, H), interpolation=cv2.INTER_LINEAR)


def edge_field(v, px_mm, shape=None, crop=None):
    """The cored band before the amount and the limit: (H, W, 1) float32 when
    the layers act together, (H, W, 3) each on its own. `v` is (H, W, 3)
    float32, linear in density. The slow part, so a caller may keep it."""
    sh = shape_of(shape)
    sd = max(sh["reach"] / 100.0 * px_mm, 0.5)
    sg = GRAIN_MM * px_mm
    if sh["layers"]:
        src = [v[..., c] for c in range(3)]
    else:
        src = [(v[..., 0] + v[..., 1] + v[..., 2]) * np.float32(1.0 / 3.0)]
    r0, r1, c0, c1 = _floor_area(v.shape, crop)
    step = max(1, int(max(r1 - r0, c1 - c0) // 512))
    out = np.empty(v.shape[:2] + (len(src),), np.float32)
    for c, x in enumerate(src):
        x = np.ascontiguousarray(x, dtype=np.float32)
        fine = x if sg < 0.3 else _blur(x, sg)
        band = fine - _blur(x, sd)
        E = _blur(band * band, sd)
        n2 = float(np.percentile(E[r0:r1:step, c0:c1:step], FLOOR_Q)) + 1e-12
        r = E * np.float32(1.0 / (CORE * CORE * n2))
        r *= r
        out[..., c] = band * (r / (np.float32(1.0) + r))
    return out


def edge_delta(field, amount):
    """The change to add: amount/100 x LIMIT x tanh(field / LIMIT), float32."""
    a = float(np.clip(amount, 0.0, 100.0)) / 100.0
    lim = np.float32(LIMIT)
    return (np.float32(a) * lim) * np.tanh(np.asarray(field, np.float32) / lim)


def apply_edge_effect(base, amount, px_mm, shape=None, ws=None, crop=None, field=None):
    """The edge effect on a uint16 base. `ws` = (WS_B, WS_W) for a windowed
    base (its values are de-windowed first), None for a plain 0..65535 one.
    `field` = a kept edge_field of this base. Amount 0 returns `base` itself."""
    if not amount or amount <= 0 or base is None or base.ndim != 3:
        return base
    lo, hi = (float(ws[0]), float(ws[1])) if ws is not None else (0.0, 65535.0)
    span = np.float32(hi - lo)
    v = (base[..., :3].astype(np.float32) - np.float32(lo)) / span
    if field is None:
        # kept as float16 (as a caller keeps it), so a kept field and a fresh
        # one give the same picture; far finer than the window's codes
        field = edge_field(v, px_mm, shape, crop).astype(np.float16)
    v += edge_delta(field, amount)
    out = base.copy()
    out[..., :3] = np.clip(np.rint(v * span + np.float32(lo)), 0, 65535).astype(base.dtype)
    return out
