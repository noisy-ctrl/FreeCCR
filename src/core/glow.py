"""Glow: a soft highlight glow, bright areas blooming gently into their
surroundings like a diffusion filter on the enlarger (spec/highlight-glow.md).

On display values (sRGB-encoded 0..1), float32 throughout, the light itself
handled in LINEAR light (glow is light adding up):

- sources: each pixel's linear RGB times a soft knee on its lightness (OKLab
  L of the decision view, so grain can't switch a pixel on and off), so only
  tones round and above Start give light, in their own colour;
- spread: box-averaged down to a size where the narrow kernel is still >= 4
  px, blurred with two Gaussians (sigma and 3 sigma, 0.65 / 0.35: a core with a
  longer tail, like real diffusion) and scaled back up - a field this smooth
  scales up without steps. Sigma is a fraction of the picture's long side, so
  the glow is the same at every render size;
- the brightest areas keep their own detail: they take little of their own
  glow (a wider ramp than the source knee, so a ramp stays monotone), read on
  a lightness smoothed along edges at half the glow's size, so a highlight's
  texture can't modulate its own glow (that flattened it);
- screened on in linear light, 1 - (1 - lin)(1 - g): never above 1, never
  darker, local slope (1 - g) <= 1 so grain is never stretched;
- pixels with g < 1e-6 are returned exactly."""
import cv2
import numpy as np

from core.oklab import _lin, _enc, _M1, _M2, guided_smooth, guided_filter, scaled_radius, \
    smooth, DENOISE_R, DENOISE_EPS

GLOW_SHAPE_DEFAULT = {"size": 40, "start": 75}
GLOW_LIMITS = {"size": (0, 100), "start": (50, 95)}
GAIN = 0.6                # glow field x this at amount 100
SIZE_MIN, SIZE_MAX = 0.003, 0.04   # sigma as a fraction of the long side
CORE_W, TAIL_W, TAIL_X = 0.65, 0.35, 3.0
KNEE = 0.10               # source knee: start -/+ this (OKLab L)
HOLD, HOLD_KNEE = 1.0, 0.20    # how much a bright area keeps out of its own glow
HOLD_R = 0.5              # the hold's lightness smoothing radius, x sigma
HOLD_EPS = 0.10 ** 2      # ... edge-preserving: texture smooths, outlines stay
MIN_SIGMA_PX = 4.0        # the narrow kernel's sigma at the size it is blurred
G_EPS = 1e-6


def normalize_glow_shape(shape):
    """A clean options dict, or None when absent / not a dict / all default."""
    if not isinstance(shape, dict):
        return None
    out = {}
    for k, dflt in GLOW_SHAPE_DEFAULT.items():
        lo, hi = GLOW_LIMITS[k]
        try:
            out[k] = int(round(float(np.clip(float(shape.get(k, dflt)), lo, hi))))
        except (TypeError, ValueError):
            out[k] = dflt
    return None if out == GLOW_SHAPE_DEFAULT else out


def sigma_frac(size):
    """Narrow-kernel sigma as a fraction of the long side for Size 0..100."""
    return SIZE_MIN * (SIZE_MAX / SIZE_MIN) ** (float(size) / 100.0)


def _lightness(lin, chunk=1 << 20):
    """The decision view's lightness from LINEAR RGB (HxWx3): OKLab L,
    edge-preservingly denoised (chunked, so a 24 MP render needs no full
    OKLab copy)."""
    H, W = lin.shape[:2]
    flat = lin.reshape(-1, 3)
    L = np.empty(len(flat), np.float32)
    for s in range(0, len(flat), chunk):
        L[s:s + chunk] = np.cbrt(flat[s:s + chunk] @ _M1.T) @ _M2[0]
    return guided_smooth(L.reshape(H, W), scaled_radius(DENOISE_R, lin.shape),
                         DENOISE_EPS[0])


def _spread(b, sigma_px):
    """Two-Gaussian blur of the HxWx3 source map, on a reduced copy."""
    H, W = b.shape[:2]
    f = max(1, int(sigma_px // MIN_SIGMA_PX))
    if f > 1:
        small = cv2.resize(b, (max(1, round(W / f)), max(1, round(H / f))),
                           interpolation=cv2.INTER_AREA)
    else:
        small = b
    s = sigma_px * small.shape[1] / float(W)
    core = cv2.GaussianBlur(small, (0, 0), s, borderType=cv2.BORDER_REFLECT)
    tail = cv2.GaussianBlur(small, (0, 0), s * TAIL_X, borderType=cv2.BORDER_REFLECT)
    g = CORE_W * core + TAIL_W * tail
    if f > 1:
        g = cv2.resize(g, (W, H), interpolation=cv2.INTER_LINEAR)
    return np.maximum(g, 0.0)


def glow_field(img01, amount, shape, with_lin=False):
    """The linear-light glow g (HxWx3) to screen on (amount included), and,
    with `with_lin`, the picture's linear RGB computed on the way."""
    a = np.asarray(img01, np.float32)
    H, W = a.shape[:2]
    lin = _lin(a).astype(np.float32)
    L = _lightness(lin)
    start = shape["start"] / 100.0
    src = smooth((L - (start - KNEE)) / (2 * KNEE)).astype(np.float32)
    b = lin * src[..., None]
    sigma_px = sigma_frac(shape["size"]) * max(H, W)
    g = _spread(np.ascontiguousarray(b), sigma_px)
    Lh = guided_filter(L, L, max(1, int(round(HOLD_R * sigma_px))), HOLD_EPS)
    hold = 1.0 - HOLD * smooth((Lh - (start - HOLD_KNEE)) / (2 * HOLD_KNEE))
    k = np.float32(min(float(amount), 100.0) / 100.0 * GAIN) * hold.astype(np.float32)
    g = g.astype(np.float32, copy=False)
    g *= k[..., None]
    np.clip(g, 0.0, 1.0, out=g)
    return (g, lin) if with_lin else g


def _cmax(x):
    """Brightest channel of an HxWx3 array (faster than .max(axis=2))."""
    return np.maximum(np.maximum(x[..., 0], x[..., 1]), x[..., 2])


def _screen(lin, g):
    """Display value of lin screened with g, in linear light:
    1 - (1 - lin)(1 - g) = lin + g(1 - lin), encoded, in 0..1."""
    lit = 1.0 - lin
    lit *= g
    lit += lin
    out = _enc(lit)
    np.clip(out, 0.0, 1.0, out=out)
    return out


def weight_map(rgb01, shape=None):
    """0..1 map of where the glow lands (for the canvas): how much it lifts
    each pixel's display value at amount 100 (brightest channel), scaled so
    a lift of a quarter of the range reads full."""
    a = np.asarray(rgb01, np.float32)
    d = apply_glow(a, 100, shape) - a
    return np.clip(_cmax(d) * 4.0, 0.0, 1.0).astype(np.float32)


def apply_glow(img01, amount, shape=None):
    """Glow on an HxWx3 float image in 0..1. A new float32 array; the input
    itself at 0."""
    if not amount or amount <= 0:
        return img01
    shape = normalize_glow_shape(shape) or dict(GLOW_SHAPE_DEFAULT)
    a = np.asarray(img01, np.float32)
    g, lin = glow_field(a, amount, shape, with_lin=True)
    sel = _cmax(g) >= G_EPS
    n = int(np.count_nonzero(sel))
    if n == 0:
        return a.copy()
    if n > sel.size // 4:          # whole-array: faster than a big gather
        out = _screen(lin, g).astype(np.float32, copy=False)
        if n < sel.size:
            keep = ~sel
            out[keep] = a[keep]
        return out
    out = a.copy()
    out[sel] = _screen(lin[sel], g[sel])
    return out
