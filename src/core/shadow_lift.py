"""Shadow Lift: opens the shadows the way a lab print does - shadow detail
comes up, pure black stays black - with an option to lift black itself for a
matte, faded-print look (spec/shadow-lift.md).

Per pixel, in OKLab on the display values, float32. A tone curve depends only
on the pixel's own tone, so there is no decision for grain to steer:

- open: s = 1 + a (1 - L/R)^2 below Range R (0 above, C1 there), L and the
  colour (a, b) scaled by s together, so a lifted shadow keeps its
  saturation instead of greying; black stays 0. Slope >= 1 - a/3 (monotone),
  steepest at black (1 + a);
- lift black (option): F = a * 0.16 * black/100 of neutral lightness added,
  faded out as (1 - L1/Rf)^2 over Rf = max(R, 0.50) (C1 there): its
  flattening falls nearest black, where the opening is steepest, so the two
  don't stack (slope >= 0.39 with every option at full, >= 0.52 at the
  default Range);
- a move that would leave 0..1 is shortened to fit (core.oklab.fit_move);
  pixels it doesn't move come back exactly."""
import numpy as np

from core.oklab import to_oklab, fit_move

LIFT_SHAPE_DEFAULT = {"range": 45, "black": 0}
LIFT_LIMITS = {"range": (25, 65), "black": (0, 100)}
OPEN_MAX = 1.0            # s at black at full amount: 1 + OPEN_MAX
FADE_MAX = 0.16           # OKLab L of neutral light at black, full amount + Lift black 100
FADE_MIN_SPAN = 0.50      # the lift of black fades out over at least this much L
L_EPS = 1e-6              # a change under this (OKLab L) leaves the pixel exactly
FIT_STEPS = 12            # the fit-in-range bisection (as core.oklab.fit_move)
CHUNK = 1 << 20


def normalize_lift_shape(shape):
    """A clean options dict, or None when absent / not a dict / all default."""
    if not isinstance(shape, dict):
        return None
    out = {}
    for k, dflt in LIFT_SHAPE_DEFAULT.items():
        lo, hi = LIFT_LIMITS[k]
        try:
            out[k] = int(round(float(np.clip(float(shape.get(k, dflt)), lo, hi))))
        except (TypeError, ValueError):
            out[k] = dflt
    return None if out == LIFT_SHAPE_DEFAULT else out


def _params(amount, shape):
    a = min(float(amount), 100.0) / 100.0
    R = shape["range"] / 100.0
    F = a * FADE_MAX * shape["black"] / 100.0
    Rf = max(R, FADE_MIN_SPAN)
    return np.float32(a * OPEN_MAX), np.float32(R), np.float32(F), np.float32(Rf)


def open_weight(L, R):
    """(1 - L/R)^2 below R, 0 above."""
    t = np.clip(L / R, 0.0, 1.0)
    return ((1.0 - t) * (1.0 - t)).astype(np.float32)


def fade_weight(L, Rf):
    """(1 - L/Rf)^2 below Rf, 0 above: how much of the black lift is left."""
    u = np.float32(1.0) - np.clip(L / Rf, 0.0, 1.0)
    return (u * u).astype(np.float32)


def lifted(lab, a, R, F, Rf):
    """The target OKLab of each colour (N x 3) and whether it moved."""
    L = lab[:, 0]
    s = np.float32(1.0) + a * open_weight(L, R)
    L1 = L * s
    new = np.empty_like(lab)
    new[:, 1] = lab[:, 1] * s
    new[:, 2] = lab[:, 2] * s
    if F > 0:
        new[:, 0] = L1 + F * fade_weight(L1, Rf)
    else:
        new[:, 0] = L1
    moved = np.abs(new[:, 0] - L) > L_EPS
    return new, moved


def grey_curve(xs, amount, shape=None):
    """The tone curve on OKLab L (for the popup's preview and tests)."""
    shape = normalize_lift_shape(shape) or dict(LIFT_SHAPE_DEFAULT)
    a, R, F, Rf = _params(amount, shape)
    lab = np.zeros((len(xs), 3), np.float32)
    lab[:, 0] = np.asarray(xs, np.float32)
    return lifted(lab, a, R, F, Rf)[0][:, 0]


def weight_map(rgb01, shape=None):
    """0..1 map of where it acts (for the canvas): the open weight, or the
    black lift's where that is more."""
    shape = normalize_lift_shape(shape) or dict(LIFT_SHAPE_DEFAULT)
    a = np.asarray(rgb01, np.float32)
    H, W = a.shape[:2]
    L = to_oklab(a.reshape(-1, 3))[:, 0]
    R = np.float32(shape["range"] / 100.0)
    w = open_weight(L, R)
    if shape["black"]:
        Rf = np.float32(max(R, FADE_MIN_SPAN))
        w = np.maximum(w, (shape["black"] / 100.0) * fade_weight(L, Rf))
    return np.clip(w, 0.0, 1.0).reshape(H, W).astype(np.float32)


def apply_shadow_lift(img01, amount, shape=None):
    """Shadow Lift on an HxWx3 float image in 0..1. A new float32 array; the
    input itself at 0."""
    if not amount or amount <= 0:
        return img01
    shape = normalize_lift_shape(shape) or dict(LIFT_SHAPE_DEFAULT)
    a, R, F, Rf = _params(amount, shape)
    x = np.asarray(img01, np.float32)
    flat = x.reshape(-1, 3)
    out = flat.copy()
    for s in range(0, len(flat), CHUNK):
        chunk = flat[s:s + CHUNK]
        lab = to_oklab(chunk)
        new, moved = lifted(lab, a, R, F, Rf)
        if moved.any():
            idx = np.nonzero(moved)[0]
            out[s + idx] = np.clip(fit_move(lab[idx], new[idx] - lab[idx], FIT_STEPS),
                                   0.0, 1.0)
    return out.reshape(x.shape)
