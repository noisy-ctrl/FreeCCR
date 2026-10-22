"""Golden Highlights: the creamy gold of a lab scan's sunlit highlights,
added at unchanged lightness (spec/golden-highlights.md).

Display values (sRGB-encoded, 0..1) in OKLab: gold chroma is added along one
hue, weighted by tone (none below the start, full through the bright tones,
easing back to neutral at the very top) and by colour (near-neutrals take it
all, colours already near gold some, cool colours very little)."""
import numpy as np

GOLDEN_SHAPE_DEFAULT = {"hue": 72, "start": 40, "clean": 60}
GOLDEN_LIMITS = {"hue": (50, 95), "start": (20, 70), "clean": (0, 100)}
GOLD_CHROMA = 0.035      # OKLab chroma added to a neutral at 100
RISE = 0.25              # L over which the effect comes in after the start
TOP = 0.85               # L where clean whites start easing it back
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
    """Chroma to add (before the amount) for each OKLab colour: tone x colour."""
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    hg = np.radians(shape["hue"])
    w = _smooth((L - shape["start"] / 100.0) / RISE)
    w = w * (1.0 - shape["clean"] / 100.0 * _smooth((L - TOP) / (1.0 - TOP)))
    C = np.hypot(a, b)
    near = 0.5 + 0.5 * (a * np.cos(hg) + b * np.sin(hg)) / np.maximum(C, 1e-6)
    C0 = 0.015 + 0.085 * np.maximum(near, 0.0) ** 1.5
    c = 1.0 / (1.0 + (C / C0) ** 2)
    return (GOLD_CHROMA * w * c).astype(np.float32)


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
