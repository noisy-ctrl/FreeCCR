"""Golden Highlights: more of a lab scan's gold in SUNLIT highlights, added at
unchanged lightness (spec/golden-highlights.md, revision 2).

Display values (sRGB-encoded, 0..1) in OKLab. Only bright tones that are
already a little warm and low in colour - sunlit whites, creams, pale stone,
lit fabric - take it: skin (more colour, more orange), sky (blue), foliage and
every strongly coloured area are left alone, and pure greys only take a small
share (Neutral whites). Gold chroma is then added along one hue, easing back to
a clean cream at the very top."""
import numpy as np

GOLDEN_SHAPE_DEFAULT = {"hue": 72, "start": 55, "clean": 60, "neutral": 10}
GOLDEN_LIMITS = {"hue": (50, 95), "start": (30, 85), "clean": (0, 100),
                 "neutral": (0, 100)}
GOLD_CHROMA = 0.06       # OKLab chroma added at 100 to a fully sunlit tone
RISE = 0.20              # L over which the effect comes in after the start
TOP = 0.85               # L where clean whites start easing it back
# What counts as sunlit: warm and low in colour. Hue window (OKLab degrees)
# around sunlit cream / pale stone / lit fabric (measured 85-90); skin sits
# lower (~45-62) and has more colour, so it falls outside.
SUNLIT_HUE = 88.0
SUNLIT_WIDTH = 30.0      # half-width: weight 0.5 at +-15, 0 at +-30
SUNLIT_CHROMA = 0.035    # colour gate exp(-(C/0.035)^2): skin and sky drop out
NEUTRAL_BELOW = 0.004    # chroma under this counts as a pure grey...
NEUTRAL_ABOVE = 0.012    # ...and over this as having a hue of its own
CHUNK = 1 << 20

_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]], np.float32)
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]], np.float32)
_M1i = np.linalg.inv(_M1.astype(np.float64)).astype(np.float32)
_M2i = np.linalg.inv(_M2.astype(np.float64)).astype(np.float32)


def normalize_golden_shape(shape):
    """A clean options dict, or None when absent / not a dict / all default."""
    if not isinstance(shape, dict):
        return None
    out = {}
    for k, dflt in GOLDEN_SHAPE_DEFAULT.items():
        lo, hi = GOLDEN_LIMITS[k]
        try:
            out[k] = int(round(float(np.clip(float(shape.get(k, dflt)), lo, hi))))
        except (TypeError, ValueError):
            out[k] = dflt
    return None if out == GOLDEN_SHAPE_DEFAULT else out


def _lin(c):
    c = np.maximum(c, 0.0)
    return np.where(c <= 0.04045, c / np.float32(12.92),
                    ((c + np.float32(0.055)) / np.float32(1.055)) ** np.float32(2.4))


def _enc(l):
    l = np.maximum(l, 0.0)
    return np.where(l <= 0.0031308, l * np.float32(12.92),
                    np.float32(1.055) * l ** np.float32(1 / 2.4) - np.float32(0.055))


def to_oklab(rgb):
    return np.cbrt(_lin(rgb) @ _M1.T) @ _M2.T


def from_oklab(lab):
    lms = lab @ _M2i.T
    return _enc((lms * lms * lms) @ _M1i.T)


def _smooth(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def gold_weight(lab, shape):
    """Chroma to add (before the amount) for each OKLab colour: tone x sunlit."""
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    w = _smooth((L - shape["start"] / 100.0) / RISE)
    w = w * (1.0 - shape["clean"] / 100.0 * _smooth((L - TOP) / (1.0 - TOP)))
    C = np.hypot(a, b)
    h = np.degrees(np.arctan2(b, a))
    d = np.abs((h - SUNLIT_HUE + 180.0) % 360.0 - 180.0)
    g_hue = np.where(d < SUNLIT_WIDTH, 0.5 + 0.5 * np.cos(np.pi * d / SUNLIT_WIDTH), 0.0)
    g_chroma = np.exp(-(C / SUNLIT_CHROMA) ** 2)
    # a grey has no reliable hue: blend from the Neutral-whites share to the
    # sunlit gate as the colour becomes measurable (smooth, so no speckle)
    t = _smooth((C - NEUTRAL_BELOW) / (NEUTRAL_ABOVE - NEUTRAL_BELOW))
    col = (1.0 - t) * (shape["neutral"] / 100.0) + t * g_hue * g_chroma
    return (GOLD_CHROMA * w * col).astype(np.float32)


def weight_map(rgb01, shape=None):
    """0..1 map of where the tool acts on a display image (for the canvas)."""
    shape = normalize_golden_shape(shape) or dict(GOLDEN_SHAPE_DEFAULT)
    a = np.asarray(rgb01, np.float32)
    w = gold_weight(to_oklab(a.reshape(-1, 3)), shape) / np.float32(GOLD_CHROMA)
    return w.reshape(a.shape[:2])


def _outside(rgb):
    return (rgb < -1e-4).any(axis=1) | (rgb > 1.0 + 1e-4).any(axis=1)


def _chunk(x, amount, shape):
    out = x.copy()
    lab_all = to_oklab(x)
    k_all = gold_weight(lab_all, shape) * np.float32(amount / 100.0)
    sel = k_all > 0
    if not sel.any():
        return out            # nothing to add: exactly the input (no round trip)
    lab, k = lab_all[sel], k_all[sel]
    hg = np.radians(shape["hue"])
    d = np.stack([np.zeros_like(k), k * np.float32(np.cos(hg)), k * np.float32(np.sin(hg))], 1)
    res = from_oklab(lab + d)
    out[sel] = _fit(res, lab, d)
    return np.clip(out, 0.0, 1.0)


def _fit(out, lab, d):
    bad = _outside(out)
    if bad.any():
        # the largest fraction of the shift that keeps the colour in range
        lb, db = lab[bad], d[bad]
        lo = np.zeros(len(lb), np.float32)
        hi = np.ones(len(lb), np.float32)
        for _ in range(8):
            mid = 0.5 * (lo + hi)
            ok = ~_outside(from_oklab(lb + mid[:, None] * db))
            lo = np.where(ok, mid, lo)
            hi = np.where(ok, hi, mid)
        out[bad] = from_oklab(lb + lo[:, None] * db)
    return out


def apply_golden(img01, amount, shape=None):
    """Golden Highlights on an HxWx3 float image in 0..1. A new float32 array;
    the input itself at 0."""
    if not amount or amount <= 0:
        return img01
    shape = normalize_golden_shape(shape) or dict(GOLDEN_SHAPE_DEFAULT)
    a = np.asarray(img01, dtype=np.float32)
    flat = a.reshape(-1, 3)
    out = np.empty_like(flat)
    for s in range(0, flat.shape[0], CHUNK):
        out[s:s + CHUNK] = _chunk(flat[s:s + CHUNK], float(amount), shape)
    return out.reshape(a.shape)
