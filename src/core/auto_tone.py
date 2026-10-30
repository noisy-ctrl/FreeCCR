"""Auto tone: keep shadow and highlight detail a frame would otherwise lose
at black or white, and leave a frame alone when little is lost.
See spec/auto-tone.md.

Qt-free. Works on the DISPLAY values of a windowed density base (0 = the
film base / black, 1 = the white point) after Auto Gain — the values the
window clamp is about to cut at 0 and 1. Three pieces, each measured on the
picture area when the frame is converted and off when not needed:

  lift      picture content below black (clearer than the measured base:
            uneven light, a base taken from elsewhere on the roll) is
            brought back to black by a density offset;
  toe       when a lot of the picture is near black, the slope at black is
            raised so shadows open up (black stays black);
  shoulder  when a lot is above white, values from a knee up to the
            picture's near-top are rolled into the top of the range
            (continuous slope at the knee, white stays white).
"""
from typing import Optional

import numpy as np

LUMA = (0.299, 0.587, 0.114)          # RGB weights, as Auto Gain
SAMPLE_MAX = 1_000_000                 # pixels measured (strided above this)

LIFT_PCT = 0.5                         # the picture's dark end
LIFT_MAX = 0.15                        # never shift by more than this

TOE_LEVEL = 0.09                       # "near black": ~12/255 once the negative
                                       # look's brightness curve (x^1.3) is applied
TOE_START = 0.03                       # share of the picture near black before acting
TOE_GAIN = 4.0                         # extra slope per unit of share beyond that
TOE_MAX = 1.0                          # slope at black at most 1 + TOE_MAX
TOE_END = 0.35                         # the toe blends out by here

HI_START = 0.005                       # share above white before acting
HI_FULL = 0.04                         # share above white for the full shoulder
HI_PCT = 99.7                          # top of the range to keep (the rest may clip)
HI_MAX = 1.3                           # beyond this is glints and lamps: let it clip
KNEE_GAIN = 1.25                       # the knee drops by this x the overshoot
KNEE_MIN, KNEE_MAX = 0.7, 0.9


def _rect_slice(shape, rect):
    if rect is None:
        return slice(None), slice(None)
    h, w = shape[:2]
    x1, y1, x2, y2 = rect
    return (slice(max(0, int(y1 * h)), min(h, int(round(y2 * h)))),
            slice(max(0, int(x1 * w)), min(w, int(round(x2 * w)))))


def _solve_sigma(u: float) -> float:
    """sigma with sigma * (1 - exp(-u / sigma)) == 1, for u > 1: the shoulder
    Y = sigma * (1 - exp(-X / sigma)) then has slope 1 at X = 0 and Y(u) = 1."""
    lo, hi = 1e-6, 1e6
    for _ in range(200):
        mid = (lo * hi) ** 0.5
        if mid * (1.0 - np.exp(-u / mid)) < 1.0:
            lo = mid
        else:
            hi = mid
    return float((lo * hi) ** 0.5)


def measure(display: np.ndarray, rect=None) -> Optional[dict]:
    """Parameters for `apply` from the display values (H, W, 3 float, RGB)
    of a converted frame, measured inside `rect` (normalised picture area).
    None when nothing needs doing."""
    ys, xs = _rect_slice(display.shape, rect)
    d = np.asarray(display[ys, xs, :3], np.float32).reshape(-1, 3)
    if d.shape[0] < 1000:
        return None
    step = max(1, d.shape[0] // SAMPLE_MAX)
    d = d[::step]
    lum = d @ np.asarray(LUMA, np.float32)
    params = {}
    lo = float(np.percentile(lum, LIFT_PCT))
    lift = min(-lo, LIFT_MAX) if lo < 0 else 0.0
    if lift > 0:
        params["lift"] = round(lift, 5)
        d = (d + lift) / (1.0 + lift)
        lum = (lum + lift) / (1.0 + lift)
    dark = float(np.mean(lum < TOE_LEVEL))
    if dark > TOE_START:
        params["toe"] = round(min(TOE_MAX, (dark - TOE_START) * TOE_GAIN), 4)
    mx = d.max(axis=1)
    over = float(np.mean(mx > 1.0))
    if over > HI_START:
        # How far to reach: the picture's near-top, capped (lamps and glints
        # are allowed to clip), and only as far as the share above white
        # warrants - a few small highlights get a gentle shoulder, a sky that
        # is a large part of the frame the full one.
        strength = min(1.0, (over - HI_START) / (HI_FULL - HI_START))
        top = 1.0 + (min(float(np.percentile(mx, HI_PCT)), HI_MAX) - 1.0) * strength
        if top > 1.0 + 1e-3:
            params.update(shoulder(top))
    return params or None


def shoulder(top: float) -> dict:
    """The shoulder's parameters for rolling everything up to `top` into
    white: the knee drops with the overshoot (between KNEE_MIN and KNEE_MAX)
    and sigma gives slope 1 at the knee and `top` -> exactly 1."""
    top = float(top)
    knee = float(np.clip(1.0 - KNEE_GAIN * (top - 1.0), KNEE_MIN, KNEE_MAX))
    u = (top - knee) / (1.0 - knee)
    return {"knee": round(knee, 4), "top": round(top, 4),
            "sigma": round(_solve_sigma(u), 6)}


def apply(d: np.ndarray, params: Optional[dict]) -> np.ndarray:
    """Apply in place to float display values (any shape); returns `d`."""
    if not params:
        return d
    lift = float(params.get("lift", 0.0))
    if lift > 0:
        d += np.float32(lift)
        d /= np.float32(1.0 + lift)
    t = float(params.get("toe", 0.0))
    if t > 0:
        # x + t x (1 - x/T)^2 on [0, T]: slope 1 + t at black, 1 at T, monotone
        # for t < 3; below black the same slope carries on (margin kept).
        x = np.clip(d, 0.0, TOE_END)
        bump = x * (1.0 - x / np.float32(TOE_END)) ** 2
        bump = np.where(d < 0, d, bump)
        d += np.float32(t) * bump.astype(np.float32)
    if "knee" in params:
        k, s = float(params["knee"]), float(params["sigma"])
        over = d > k
        if np.any(over):
            X = (d[over] - k) / (1.0 - k)
            d[over] = k + (1.0 - k) * s * (1.0 - np.exp(-X / s))
    return d
