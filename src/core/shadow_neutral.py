"""Shadow Neutralise: shadows towards a neutral, inky black, the way a good
lab scan renders them (spec/shadow-neutralise.md).

On display values (sRGB-encoded 0..1) in OKLab, float32 throughout:

- how much it acts is DECIDED on the denoised decision view of the picture
  (core.oklab.decision_lab: preview resolution, edge-preserving), never on a
  pixel's own noisy colour, so grain can't steer it:
  m = k . t(L) . g(C), with
  - tone weight `t(L)`: 1 through the deep shadows, easing smoothly to 0 at
    Range (OKLab L), so midtones and highlights are never touched;
  - colour gate `g(C) = exp(-(C/Reach)^2)`: a shadow cast (low colour) is
    mostly taken out, a strongly coloured dark object (a red car, navy, deep
    foliage) keeps most of its colour;
- each pixel's own (a, b) is then scaled by `1 - m` towards neutral along its
  own hue, at unchanged lightness. m is a smooth field <= 1, so a pixel's
  colour grain is only ever reduced, never stretched, and luma grain and
  detail are untouched;
- Depth (option, 0 by default) deepens the darkest tones a little: a per-pixel
  tone curve, L and chroma together scaled by `1 - k.0.25.depth.t(L)`, slope
  >= 0.75 at black, black stays black, nothing clips;
- a pixel the move would take out of 0..1 gets the largest fraction that fits
  (bisection); pixels it doesn't move are returned EXACTLY."""
import numpy as np

from core.oklab import (to_oklab, from_oklab, smooth, fit_move, decision_lab,
                         to_render_size)

SHADOW_SHAPE_DEFAULT = {"range": 50, "reach": 45, "depth": 0}
SHADOW_LIMITS = {"range": (20, 70), "reach": (10, 100), "depth": (0, 100)}
FULL_FRAC = 0.4          # fully neutral below 40 % of Range, fading out above
REACH_UNIT = 0.001       # Reach 45 -> OKLab chroma 0.045 (a cast is ~0.01-0.035)
DEPTH_MAX = 0.25         # Depth 100 at full amount: L x 0.75 in the deepest tones
CHUNK = 1 << 20


def normalize_shadow_shape(shape):
    """A clean options dict, or None when absent / not a dict / all default."""
    if not isinstance(shape, dict):
        return None
    out = {}
    for k, dflt in SHADOW_SHAPE_DEFAULT.items():
        lo, hi = SHADOW_LIMITS[k]
        try:
            out[k] = int(round(float(np.clip(float(shape.get(k, dflt)), lo, hi))))
        except (TypeError, ValueError):
            out[k] = dflt
    return None if out == SHADOW_SHAPE_DEFAULT else out


def tone_weight(L, shape):
    """1 in the deep shadows, smoothly to 0 at Range (OKLab L)."""
    end = shape["range"] / 100.0
    return (1.0 - smooth((L - FULL_FRAC * end) / ((1.0 - FULL_FRAC) * end))).astype(np.float32)


def colour_gate(lab, shape):
    """1 for a neutral, falling smoothly with chroma: exp(-(C/Reach)^2). A
    cast (C ~0.01-0.035) is mostly taken out, a strong colour hardly touched.
    A Gaussian, because any gate that steps down faster stretches chroma
    noise more where it falls (peak stretch 1.45x here, 2.3x for a plateau
    then smoothstep)."""
    C = np.hypot(lab[:, 1], lab[:, 2])
    c0 = np.float32(shape["reach"] * REACH_UNIT)
    return np.exp(-(C / c0) ** 2).astype(np.float32)


def strength_map(img01, k, shape, with_full=False):
    """m = k . t . g, decided on the denoised view, at the render's size
    (and, with `with_full`, the render's per-pixel OKLab when it came free)."""
    lab, full = decision_lab(img01, with_full=True)
    flat = lab.reshape(-1, 3)
    m = np.float32(k) * tone_weight(flat[:, 0], shape) * colour_gate(flat, shape)
    m = to_render_size(m.reshape(lab.shape[:2]), np.shape(img01))
    return (m, full) if with_full else m


def weight_map(rgb01, shape=None):
    """0..1 map of where the tool acts on a display image (for the canvas):
    the neutralising weight, or the tone weight wherever Depth also acts."""
    shape = normalize_shadow_shape(shape) or dict(SHADOW_SHAPE_DEFAULT)
    lab = decision_lab(rgb01)
    flat = lab.reshape(-1, 3)
    t = tone_weight(flat[:, 0], shape)
    g = np.maximum(colour_gate(flat, shape), np.float32(shape["depth"] / 100.0))
    return to_render_size((t * g).reshape(lab.shape[:2]), np.shape(rgb01))


# A strength under this moves a pixel by less than a thousandth of a 16-bit
# code; such pixels are left exactly as they are.
M_EPS = 1e-5


def _chunk(x, m, k, shape, lab=None):
    out = x.copy()
    if lab is None:
        lab = to_oklab(x)
    f = 1.0 - m
    if shape["depth"]:
        s = 1.0 - np.float32(k * DEPTH_MAX * shape["depth"] / 100.0) * \
            tone_weight(lab[:, 0], shape)
        f = f * s             # chroma with the lightness: same saturation
        moved = (m > M_EPS) | (s < 1.0)
    else:
        moved = m > M_EPS
    if not moved.any():
        return out            # nothing moved: exactly the input
    lm, fm = lab[moved], f[moved]
    new = np.empty_like(lm)
    new[:, 0] = lm[:, 0] * s[moved] if shape["depth"] else lm[:, 0]
    new[:, 1] = lm[:, 1] * fm
    new[:, 2] = lm[:, 2] * fm
    res = from_oklab(new)
    out[moved] = np.clip(fit_move(res, lm, new - lm), 0.0, 1.0)
    return out


def apply_shadow_neutralise(img01, amount, shape=None):
    """Shadow Neutralise on an HxWx3 float image in 0..1. A new float32
    array; the input itself at 0."""
    if not amount or amount <= 0:
        return img01
    shape = normalize_shadow_shape(shape) or dict(SHADOW_SHAPE_DEFAULT)
    k = min(float(amount), 100.0) / 100.0
    a = np.asarray(img01, dtype=np.float32)
    flat = a.reshape(-1, 3)
    m, full = strength_map(a, k, shape, with_full=True)
    m = m.reshape(-1)
    full = None if full is None else full.reshape(-1, 3)
    out = flat.copy()
    # Contiguous chunks (no gathering); inside, only the pixels that move are
    # converted back, the rest are copied exactly. With Depth every pixel in a
    # chunk is looked at, since the tone curve reads its own L.
    for s in range(0, flat.shape[0], CHUNK):
        sl = slice(s, s + CHUNK)
        if not shape["depth"] and not (m[sl] > M_EPS).any():
            continue
        out[sl] = _chunk(flat[sl], m[sl], k, shape, None if full is None else full[sl])
    return out.reshape(a.shape)
