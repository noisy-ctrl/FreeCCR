"""Golden Highlights: more of a lab scan's gold in SUNLIT highlights, added at
unchanged lightness (spec/golden-highlights.md, revisions 2 and 3).

Display values (sRGB-encoded, 0..1) in OKLab. Only bright tones that are
already a little warm and low in colour - sunlit whites, creams, pale stone,
lit fabric - take it: skin (more colour, more orange), sky (blue), foliage and
every strongly coloured area are left alone, and pure greys only take a small
share (Neutral whites). Gold chroma is then added along one hue, easing back to
a clean cream at the very top.

How much gold each pixel gets is DECIDED on the denoised decision view
(core.oklab.decision_lab: preview resolution, edge-preserving) and ADDED to
each pixel's own colour, so the gold is a smooth field: a pixel's grain
passes through untouched. (Deciding on each pixel's own noisy colour gave
a warmer-by-chance grain speck more gold: colour grain rose 1.5-1.7x at 60.)"""
import numpy as np

from core.oklab import (to_oklab, from_oklab, smooth as _smooth, fit_move, decision_lab,
                         to_render_size)

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
# A grey has no reliable hue: the colour weight blends from the Neutral-whites
# share at C 0 to the sunlit gate by C 0.03. (A steep 0.004-0.012 blend, with
# hue read off a near-grey, let grain decide: up to 2x colour grain in
# near-white areas even on the decision view.)
NEUTRAL_BELOW = 0.0
NEUTRAL_ABOVE = 0.03
# The gold gate is steep in colour, so it is decided on a smoother view than
# Shadow Neutralise's (px at the decision resolution; the bilateral still keeps
# edges: gold on pale skin 1 px from a cream edge is ~1 % of the cream's).
DECISION_SIGMA = 3.0
CHUNK = 1 << 20


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
    """0..1 map of where the tool acts on a display image (for the canvas),
    decided as the render decides it."""
    shape = normalize_golden_shape(shape) or dict(GOLDEN_SHAPE_DEFAULT)
    lab = decision_lab(rgb01, sigma_space=DECISION_SIGMA)
    w = gold_weight(lab.reshape(-1, 3), shape) / np.float32(GOLD_CHROMA)
    return to_render_size(w.reshape(lab.shape[:2]), np.shape(rgb01))


def gold_map(img01, amount, shape, with_full=False):
    """Chroma to add per pixel (amount included), decided on the denoised
    view, at the render's size (and, with `with_full`, the render's per-pixel
    OKLab when it came free)."""
    lab, full = decision_lab(img01, with_full=True, sigma_space=DECISION_SIGMA)
    k = gold_weight(lab.reshape(-1, 3), shape) * np.float32(amount / 100.0)
    k = to_render_size(k.reshape(lab.shape[:2]), np.shape(img01))
    return (k, full) if with_full else k


# Chroma under this (a thousandth of a 16-bit code's worth) is not added: the
# pixel is returned exactly.
K_EPS = 1e-6


def _chunk(x, k, shape, lab=None):
    out = x.copy()
    sel = k > K_EPS
    if not sel.any():
        return out            # nothing to add: exactly the input (no round trip)
    lab = to_oklab(x[sel]) if lab is None else lab[sel]
    k = k[sel]
    hg = np.radians(shape["hue"])
    d = np.stack([np.zeros_like(k), k * np.float32(np.cos(hg)), k * np.float32(np.sin(hg))], 1)
    out[sel] = np.clip(fit_move(lab, d), 0.0, 1.0)
    return out


def apply_golden(img01, amount, shape=None):
    """Golden Highlights on an HxWx3 float image in 0..1. A new float32 array;
    the input itself at 0."""
    if not amount or amount <= 0:
        return img01
    shape = normalize_golden_shape(shape) or dict(GOLDEN_SHAPE_DEFAULT)
    a = np.asarray(img01, dtype=np.float32)
    flat = a.reshape(-1, 3)
    k, full = gold_map(a, float(amount), shape, with_full=True)
    k = k.reshape(-1)
    full = None if full is None else full.reshape(-1, 3)
    out = flat.copy()
    for s in range(0, flat.shape[0], CHUNK):
        sl = slice(s, s + CHUNK)
        if (k[sl] > K_EPS).any():
            out[sl] = _chunk(flat[sl], k[sl], shape, None if full is None else full[sl])
    return out.reshape(a.shape)
