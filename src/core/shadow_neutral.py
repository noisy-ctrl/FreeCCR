"""Shadow Neutralise: the lower end of the shadows towards a neutral, inky
black, the way a good lab scan renders them (spec/shadow-neutralise.md).

On display values (sRGB-encoded 0..1) in OKLab, float32 throughout:

- WHERE it acts is a matte decided on the denoised decision view of the
  picture (core.oklab.decision_lab: edge-preserving guided filters at the
  render's own resolution), never on a pixel's own noisy colour:
  m = k . t(L) . g(C), with
  - tone weight `t(L)`: 1 in the deepest tones, easing smoothly to 0 at
    Range (OKLab L; default 0.30, display ~44/255), so the rest of the
    shadows, the midtones and highlights are never touched;
  - colour gate `g(C) = exp(-(C/Reach)^2)`: a shadow cast (low colour) is
    mostly taken out, a strongly coloured dark object (a red car, navy, deep
    foliage) keeps most of its colour;
  then smoothed along the picture's edges (Smoothing option,
  core.oklab.finish_matte: soft inside an area, no spill across an edge);
- each pixel's own (a, b) is scaled by `1 - m` towards neutral along its own
  hue, at unchanged lightness. m is a smooth field <= 1, so a pixel's colour
  grain is only ever reduced, never stretched, and luma grain and detail are
  untouched;
- Depth (option, 0 by default) deepens the darkest tones a little: a per-pixel
  tone curve, L and chroma together scaled by `1 - k.0.25.depth.t_d(L)` with
  its own wide fade t_d = 1 - smoothstep(L/Range) (slope >= 0.75 at black,
  at most ~1.07 where it fades, black stays black, nothing clips);
- a pixel the move would take out of 0..1 gets the largest fraction that fits
  (bisection); pixels it doesn't move are returned EXACTLY."""
import numpy as np

from core.oklab import to_oklab, smooth, fit_move, decision_lab, finish_matte

# Range is the OKLab lightness x 100 where it has faded out: the LOWER END of
# the shadows (30 = display level ~44/255; the limits run from ~10 to ~85).
SHADOW_SHAPE_DEFAULT = {"range": 30, "reach": 45, "depth": 0, "smooth": 30}
SHADOW_LIMITS = {"range": (15, 45), "reach": (10, 100), "depth": (0, 100),
                 "smooth": (0, 100)}
FULL_FRAC = 0.5          # fully neutral below half of Range, fading out above
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
    """Where it neutralises: 1 below half of Range, smoothly to 0 at Range."""
    end = shape["range"] / 100.0
    return (1.0 - smooth((L - FULL_FRAC * end) / ((1.0 - FULL_FRAC) * end))).astype(np.float32)


def depth_weight(L, shape):
    """Where Depth deepens: 1 at black, smoothly to 0 at Range over the whole
    span (a wider fade than tone_weight, so the deepening never steepens the
    tones in a narrow band: peak slope ~1.07 at Depth 100)."""
    end = shape["range"] / 100.0
    return (1.0 - smooth(L / end)).astype(np.float32)


def colour_gate(lab, shape):
    """1 for a neutral, falling smoothly with chroma: exp(-(C/Reach)^2). A
    cast (C ~0.01-0.035) is mostly taken out, a strong colour hardly touched.
    A Gaussian, because any gate that steps down faster stretches chroma
    noise more where it falls (peak stretch 1.45x here, 2.3x for a plateau
    then smoothstep)."""
    C = np.hypot(lab[:, 1], lab[:, 2])
    c0 = np.float32(shape["reach"] * REACH_UNIT)
    return np.exp(-(C / c0) ** 2).astype(np.float32)


def _matte(smooth_lab, shape, k=1.0, depth=0.0):
    """k . t . g on the decision view (with `depth`, for the canvas map, at
    least depth . t_d where Depth deepens), in chunks, then finished along the
    picture's edges."""
    H, W = smooth_lab.shape[:2]
    flat = smooth_lab.reshape(-1, 3)
    m = np.empty(H * W, np.float32)
    for s in range(0, len(flat), CHUNK):
        f = flat[s:s + CHUNK]
        v = np.float32(k) * tone_weight(f[:, 0], shape) * colour_gate(f, shape)
        if depth:
            v = np.maximum(v, np.float32(depth) * depth_weight(f[:, 0], shape))
        m[s:s + CHUNK] = v
    return finish_matte(m.reshape(H, W), smooth_lab[..., 0], shape["smooth"])


def strength_map(img01, k, shape, with_full=False):
    """The neutralising matte m = k . t . g at the render's size (and, with
    `with_full`, the render's own per-pixel OKLab, computed on the way)."""
    smooth_lab, lab = decision_lab(img01)
    m = _matte(smooth_lab, shape, k)
    return (m, lab) if with_full else m


def weight_map(rgb01, shape=None):
    """0..1 map of where the tool acts on a display image (for the canvas):
    the neutralising weight, or Depth's weight where that is more."""
    shape = normalize_shadow_shape(shape) or dict(SHADOW_SHAPE_DEFAULT)
    smooth_lab, _lab = decision_lab(rgb01)
    return _matte(smooth_lab, shape, 1.0, shape["depth"] / 100.0)


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
            depth_weight(lab[:, 0], shape)
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
    out[moved] = np.clip(fit_move(lm, new - lm), 0.0, 1.0)
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
    full = full.reshape(-1, 3)
    out = flat.copy()
    # Contiguous chunks (no gathering); inside, only the pixels that move are
    # converted back, the rest are copied exactly. With Depth every pixel in a
    # chunk is looked at, since the tone curve reads its own L.
    for s in range(0, flat.shape[0], CHUNK):
        sl = slice(s, s + CHUNK)
        if not shape["depth"] and not (m[sl] > M_EPS).any():
            continue
        out[sl] = _chunk(flat[sl], m[sl], k, shape, full[sl])
    return out.reshape(a.shape)
