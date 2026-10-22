"""OKLab on display values, shared by the colour finishing tools (Golden
Highlights, Shadow Neutralise). Float32 throughout.

Display values are sRGB-encoded 0..1. Neutral (R = G = B) is a = b = 0 for
any D65 working space, so a "towards neutral" move is exact whatever the
primaries; chroma magnitudes are the sRGB ones."""
import cv2
import numpy as np

_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]], np.float32)
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]], np.float32)
_M1i = np.linalg.inv(_M1.astype(np.float64)).astype(np.float32)
_M2i = np.linalg.inv(_M2.astype(np.float64)).astype(np.float32)

# Bisection steps when a move has to be shortened to stay in range: 12 steps
# resolve the fraction to 1/4096, i.e. well under one 16-bit code for any
# move these tools make, so neighbouring pixels can't step against each other.
FIT_STEPS = 12


def _lin(c):
    c = np.maximum(c, 0.0)
    return np.where(c <= 0.04045, c / np.float32(12.92),
                    ((c + np.float32(0.055)) / np.float32(1.055)) ** np.float32(2.4))


def _enc(l):
    l = np.maximum(l, 0.0)
    return np.where(l <= 0.0031308, l * np.float32(12.92),
                    np.float32(1.055) * l ** np.float32(1 / 2.4) - np.float32(0.055))


def to_oklab(rgb):
    """N x 3 (or ... x 3) display RGB -> OKLab, float32."""
    return np.cbrt(_lin(rgb) @ _M1.T) @ _M2.T


def from_oklab(lab):
    """OKLab -> display RGB, float32 (not clipped)."""
    lms = lab @ _M2i.T
    return _enc((lms * lms * lms) @ _M1i.T)


def smooth(x):
    """Smoothstep on 0..1 (C1: no kinks for a gradient to band on)."""
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def outside(rgb):
    return (rgb < -1e-4).any(axis=1) | (rgb > 1.0 + 1e-4).any(axis=1)


def fit_move(out, lab, d, steps=FIT_STEPS):
    """`out` = from_oklab(lab + d). Where that leaves 0..1, use the largest
    fraction of the move that stays inside (bisection): nothing is clipped, so
    no channel flattens and no hue twists. Returns `out`, fixed in place."""
    bad = outside(out)
    if bad.any():
        lb, db = lab[bad], d[bad]
        lo = np.zeros(len(lb), np.float32)
        hi = np.ones(len(lb), np.float32)
        for _ in range(steps):
            mid = 0.5 * (lo + hi)
            ok = ~outside(from_oklab(lb + mid[:, None] * db))
            lo = np.where(ok, mid, lo)
            hi = np.where(ok, hi, mid)
        out[bad] = from_oklab(lb + lo[:, None] * db)
    return out


# --- The decision map --------------------------------------------------------
# How much a finishing tool acts on a pixel is decided from a lightly
# denoised, edge-preserving view of the picture, never from the pixel's own
# noisy colour: a gate that reads grain turns grain into more grain (a pixel
# that happens to be a little warmer gets more gold, so the colour noise is
# stretched). With the decision spatially smooth, the change applied to each
# pixel is a smooth field and the pixel's own grain and detail pass through
# untouched - or, for a move towards neutral, only ever reduced.
#
# Made at the preview's resolution so preview, zoom and export decide alike: a
# bigger render is box-downsampled to DECISION_LONG first and the finished
# factor map is bilinearly upsampled back. The bilateral keeps real edges
# (an OKLab step well over SIGMA_COLOR) sharp, so nothing bleeds across them.
DECISION_LONG = 1080        # long side the decision is made at (the preview's)
SIGMA_SPACE = 1.5           # px at DECISION_LONG (scaled for smaller renders)
SIGMA_COLOR = 0.05          # OKLab (L1 over L, a, b): grain averages, edges stay


def decision_lab(img01, with_full=False):
    """OKLab of `img01` (HxWx3 float, 0..1) at the decision resolution,
    bilateral-smoothed: an hxwx3 float32 array (h, w <= H, W). With
    `with_full`, also the render's own per-pixel OKLab (HxWx3) when it was
    computed on the way (a render at or under the decision resolution, e.g.
    the preview), else None - so the caller needn't convert twice."""
    a = np.asarray(img01, np.float32)
    H, W = a.shape[:2]
    long_side = max(H, W)
    if long_side > DECISION_LONG:
        s = DECISION_LONG / long_side
        a = cv2.resize(a, (max(1, round(W * s)), max(1, round(H * s))),
                       interpolation=cv2.INTER_AREA)
    lab = np.ascontiguousarray(to_oklab(a.reshape(-1, 3)).reshape(a.shape),
                               dtype=np.float32)
    full = lab if long_side <= DECISION_LONG else None
    sigma = SIGMA_SPACE * min(1.0, long_side / DECISION_LONG)
    if sigma >= 0.5 and min(lab.shape[:2]) >= 3:
        d = 2 * int(np.ceil(2.0 * sigma)) + 1
        smooth_lab = cv2.bilateralFilter(lab, d, SIGMA_COLOR, sigma)
    else:
        smooth_lab = lab                            # too small to smooth
    return (smooth_lab, full) if with_full else smooth_lab


def to_render_size(m, shape):
    """A decision-resolution map back at the render's HxW (bilinear; an area
    that is exactly 0 stays exactly 0)."""
    H, W = shape[:2]
    if m.shape[:2] == (H, W):
        return m.astype(np.float32, copy=False)
    return cv2.resize(m.astype(np.float32), (W, H), interpolation=cv2.INTER_LINEAR)
