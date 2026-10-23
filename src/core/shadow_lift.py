"""Shadow Lift: lifts black a little - a lab print's matte black, the
deepest shadows no longer quite black but slightly lighter, with their grain,
and the lift rolling off smoothly into the shadows (spec/shadow-lift.md,
revision 2: it was a shadow-opening curve with a black lift as an option; the
Shadows / Black Point sliders already open the shadows).

On display values (sRGB-encoded 0..1), float32, per pixel. A tone curve on
the pixel's own luma, so there is nothing for grain to steer:

- lift = v0 * (1 - smoothstep(Y / Vf)), Y the Rec.709 luma of the display
  values, v0 = amount / 100 * LIFT_MAX (0.10 = 25.5 / 255 at 100), Vf the
  Roll-off (display level, default 0.50);
- added equally to R, G and B: a neutral lift, as raising a curve's black
  point does;
- along grey the slope is 1 - v0 S'(u) / Vf: exactly 1 at black (the deepest
  shadows move up unchanged, grain and all), never below 1 - 1.5 v0 / Vf
  (0.70 at full lift and the default Roll-off), and 1 again where the lift
  ends, C1 there: nothing sudden;
- a pixel whose brightest channel would pass 1 takes only what fits;
- above the Roll-off the picture comes back exactly."""
import numpy as np

LIFT_SHAPE_DEFAULT = {"rolloff": 50}
LIFT_LIMITS = {"rolloff": (35, 80)}       # display %, where the lift has faded out
LIFT_MAX = 0.10                           # black's lift at amount 100 (display)
EPS = 1e-7                                # a lift under this leaves the pixel exactly
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
CHUNK = 1 << 20


def normalize_lift_shape(shape):
    """A clean options dict, or None when absent / not a dict / all default.
    (Options saved under revision 1's keys - range, black - mean the default.)"""
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


def params(amount, shape):
    """(black's lift v0, roll-off Vf), display units, float32."""
    shape = normalize_lift_shape(shape) or dict(LIFT_SHAPE_DEFAULT)
    v0 = min(max(float(amount), 0.0), 100.0) / 100.0 * LIFT_MAX
    return np.float32(v0), np.float32(shape["rolloff"] / 100.0)


def fade(Y, Vf):
    """1 - smoothstep(Y / Vf): 1 at black (flat), 0 from Vf on (flat)."""
    u = np.clip(np.asarray(Y, np.float32) / Vf, 0.0, 1.0)
    return (np.float32(1.0) - u * u * (np.float32(3.0) - np.float32(2.0) * u)).astype(np.float32)


def grey_curve(xs, amount, shape=None):
    """The tone curve along grey, display in -> display out."""
    v0, Vf = params(amount, shape)
    x = np.asarray(xs, np.float32)
    return np.minimum(x + v0 * fade(x, Vf), np.float32(1.0))


def weight_map(rgb01, shape=None):
    """0..1 map of where it acts (for the canvas): the share of black's lift
    each pixel gets."""
    _v0, Vf = params(100, shape)
    a = np.asarray(rgb01, np.float32)
    return fade(a[..., :3] @ LUMA, Vf)


def apply_shadow_lift(img01, amount, shape=None):
    """Shadow Lift on an HxWx3 float image in 0..1. A new float32 array; the
    input itself at 0."""
    if not amount or amount <= 0:
        return img01
    v0, Vf = params(amount, shape)
    x = np.asarray(img01, np.float32)
    flat = x.reshape(-1, 3)
    out = flat.copy()
    for s in range(0, len(flat), CHUNK):
        c = flat[s:s + CHUNK]
        add = v0 * fade(c @ LUMA, Vf)
        room = np.float32(1.0) - np.maximum(np.maximum(c[:, 0], c[:, 1]), c[:, 2])
        np.minimum(add, room, out=add)
        idx = np.nonzero(add > EPS)[0]
        if len(idx):
            out[s + idx] = c[idx] + add[idx, None]
    return out.reshape(x.shape)
