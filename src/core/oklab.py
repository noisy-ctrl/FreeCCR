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


def to_linear(lab):
    """OKLab -> LINEAR RGB, float32, unclamped (out-of-gamut values show)."""
    lms = lab @ _M2i.T
    return (lms * lms * lms) @ _M1i.T


def from_oklab(lab):
    """OKLab -> display RGB, float32 (a negative linear value encodes as 0)."""
    return _enc(to_linear(lab))


def smooth(x):
    """Smoothstep on 0..1 (C1: no kinks for a gradient to band on)."""
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def outside(lin):
    """True where a LINEAR RGB value leaves 0..1 (by more than ~1 16-bit code
    once encoded). Tested in linear light because the display encoding
    floors negatives at 0, which would hide a channel pushed below black."""
    return (lin < -1e-6).any(axis=1) | (lin > 1.0 + 1e-5).any(axis=1)


def fit_move(lab, d, steps=FIT_STEPS):
    """Display RGB of `lab + d`, or, where that leaves 0..1, of the largest
    fraction of the move that stays inside (bisection in linear light):
    nothing is clipped, so no channel flattens and no hue twists. (A
    regula-falsi variant was tried: slower here and less exact.)"""
    lin = to_linear(lab + d)
    bad = outside(lin)
    if bad.any():
        lb, db = lab[bad], d[bad]
        lo = np.zeros(len(lb), np.float32)
        hi = np.ones(len(lb), np.float32)
        for _ in range(steps):
            mid = 0.5 * (lo + hi)
            ok = ~outside(to_linear(lb + mid[:, None] * db))
            lo = np.where(ok, mid, lo)
            hi = np.where(ok, hi, mid)
        lin[bad] = to_linear(lb + lo[:, None] * db)
    return _enc(lin)


# --- The decision view and the matte -------------------------------------------
# How much a finishing tool acts on a pixel is decided from a denoised,
# edge-preserving view of the picture, never from the pixel's own noisy colour:
# a gate that reads grain turns grain into more grain (a pixel that happens to
# be a little warmer gets more gold, so the colour noise is stretched). The
# decision is a MATTE (0..1 per pixel, like a Resolve qualifier's key); the
# change applied to each pixel is that matte times the tool's move, so the
# pixel's own grain and detail pass through untouched - or, for a move towards
# neutral, only ever reduced.
#
# Both the denoising and the matte's smoothing are GUIDED FILTERS (He, Sun,
# Tang): O(1) box filters, so they run on the render at its OWN resolution -
# no downsampling and scaling back up, so no blocks or stair-steps along edges
# at any size. Radii are set at the preview's long side (DECISION_LONG) and
# scaled with the render, so preview, zoom and export see the same picture
# area. A guided filter smooths where the window's variance is under `eps`
# (grain) and keeps what is over it (a real edge), so nothing bleeds across
# an edge.
DECISION_LONG = 1080        # radii below are px at this long side
DENOISE_R = 2               # the decision view's denoising radius (px at 1080)
DENOISE_EPS = (0.020 ** 2, 0.008 ** 2, 0.008 ** 2)   # L, a, b: grain smooths, edges stay
GUIDE_EPS = 0.03 ** 2       # matte smoothing: lightness steps over ~0.03 are edges
SMOOTH_STEP = 0.08          # Smoothing 100 -> matte radius 9 px at DECISION_LONG


def _box(x, r):
    return cv2.boxFilter(x, -1, (2 * r + 1, 2 * r + 1), normalize=True,
                         borderType=cv2.BORDER_REFLECT)


def render_scale(shape):
    """How much bigger than the preview a render is (radii scale by this)."""
    return max(shape[0], shape[1]) / float(DECISION_LONG)


def scaled_radius(r_preview, shape):
    """A radius given in px at the preview's size, for a render of `shape`."""
    return int(round(r_preview * render_scale(shape)))


def guided_smooth(x, r, eps):
    """Edge-preserving smoothing of one channel guided by itself."""
    if r < 1 or min(x.shape[:2]) < 2 * r + 1:
        return x
    m = _box(x, r)
    var = _box(x * x, r) - m * m
    a = var / (var + np.float32(eps))
    b = m - a * m
    return _box(a, r) * x + _box(b, r)


def guided_filter(p, guide, r, eps):
    """Smooth `p` (HxW) along `guide` (HxW): smooth inside areas, following
    the guide's edges instead of bleeding over them."""
    if r < 1 or min(p.shape[:2]) < 2 * r + 1:
        return p
    mI, mp = _box(guide, r), _box(p, r)
    a = (_box(guide * p, r) - mI * mp) / (_box(guide * guide, r) - mI * mI + np.float32(eps))
    b = mp - a * mI
    return _box(a, r) * guide + _box(b, r)


def decision_lab(img01, radius=DENOISE_R):
    """(denoised OKLab, OKLab) of `img01` (HxWx3 float, 0..1), both HxWx3
    float32 at the render's own size. `radius` is px at the preview's size."""
    a = np.asarray(img01, np.float32)
    lab = np.ascontiguousarray(to_oklab(a.reshape(-1, 3)).reshape(a.shape), np.float32)
    r = scaled_radius(radius, a.shape)
    smooth_lab = np.empty_like(lab)
    for c in range(3):
        smooth_lab[..., c] = guided_smooth(np.ascontiguousarray(lab[..., c]), r, DENOISE_EPS[c])
    return smooth_lab, lab


def matte_radius(smoothing, shape):
    """Matte smoothing radius for Smoothing 0..100 at a render of `shape`."""
    return scaled_radius(1.0 + float(smoothing) * SMOOTH_STEP, shape)


def finish_matte(m, guide_L, smoothing):
    """Smooth a matte along the decision view's lightness (the Smoothing
    option, like the blur on a Resolve qualifier, but edge-aware). Keeps it
    in [0, its max]; a matte that is ~0 stays exactly 0."""
    p = np.ascontiguousarray(m, np.float32)
    top = float(p.max()) if p.size else 0.0
    if top <= 0.0:
        return p
    q = guided_filter(p, np.ascontiguousarray(guide_L, np.float32),
                      matte_radius(smoothing, p.shape), GUIDE_EPS)
    q = np.clip(q, 0.0, top)
    q[q < 1e-7] = 0.0
    return q.astype(np.float32, copy=False)
