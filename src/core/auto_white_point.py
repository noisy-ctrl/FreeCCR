"""Auto white point: make each frame's brightest area neutral, the way a
hand-sampled White Point on a near-clipping highlight does, without
neutralising a sunset. See spec/auto-white-point.md.

Qt-free. Everything works on the unconverted 1080 preview (the data the
manual sampler reads) and on optical density above the frame's own film base,
D = log10(base / value), per channel.

A white point fixes, per channel, which density becomes display white
(`_twopoint_invert`, density mode: d = D / Dmax). Its colour content is the
RATIO between the three Dmax values; its overall size is the frame's level.
"""
from typing import Optional, Sequence

import numpy as np

# The "brightest area": pixels whose mean density over the three channels sits
# between these percentiles of the picture area. The top 0.2 % is left out
# (specular glints, dust, a sliver of holder); the 1 % below it is the bright
# area a person would drag a marquee over.
JOINT_LO = 99.0
JOINT_HI = 99.8
INSET = 0.03              # keep clear of the picture's own edge
FALLBACK_INSET = 0.10     # no picture area known: the middle of the scan
MIN_PIXELS = 50
MIN_DMAX = 0.15           # a brightest area this thin is not a highlight

# How much of a frame's highlight colour is treated as a cast (removed) rather
# than as the scene (kept). Colour is split into temperature (warm/cool, the
# R-B axis) and tint (green/magenta), measured as the frame's Dmax ratio minus
# a reference ratio, in ratio units (0.06 ~ a 6 % slope difference).
# Tint is almost never the scene, so it is removed up to TINT_MAX. Temperature
# often is (a sunset, a blue sky), so only TEMP_ROLL of it is removed when the
# rest of the roll says what this film's neutral looks like; with nothing to
# compare against, a warm highlight is still mostly kept (TEMP_WARM) while a
# cool one, which is how colour negative usually comes out, is removed further.
TEMP_ROLL = 0.06
TEMP_WARM = 0.06
TEMP_COOL = 0.20
TINT_MAX = 0.15
MIN_ROLL = 3              # other frames needed before the roll is the reference

_UT = np.array([1.0, 0.0, -1.0]) / np.sqrt(2.0)     # temperature: + warm
_UG = np.array([-1.0, 2.0, -1.0]) / np.sqrt(6.0)    # tint: + green


def _rect_px(shape, rect, inset):
    h, w = shape[:2]
    x1, y1, x2, y2 = rect
    dx, dy = (x2 - x1) * inset, (y2 - y1) * inset
    a, b = int(round((y1 + dy) * h)), int(round((y2 - dy) * h))
    c, d = int(round((x1 + dx) * w)), int(round((x2 - dx) * w))
    return max(0, a), min(h, b), max(0, c), min(w, d)


def highlight_density(raw, black, rect=None) -> Optional[tuple]:
    """Per-channel density above the base of the frame's brightest area, in
    the array's channel order, or None when there is nothing usable.
    `rect` is the picture area (normalised x1, y1, x2, y2); None uses the
    middle of the scan."""
    if raw is None or black is None:
        return None
    a = np.asarray(raw)
    if a.ndim != 3 or a.shape[2] < 3:
        return None
    y1, y2, x1, x2 = _rect_px(a.shape, rect or (0, 0, 1, 1),
                              INSET if rect is not None else FALLBACK_INSET)
    if y2 - y1 < 8 or x2 - x1 < 8:
        return None
    px = a[y1:y2, x1:x2, :3].reshape(-1, 3).astype(np.float32)
    base = np.maximum(np.asarray(black, np.float32)[:3], 1.0)
    D = np.log10(base / np.maximum(px, 1.0))
    L = D.mean(axis=1)
    lo, hi = np.percentile(L, [JOINT_LO, JOINT_HI])
    sel = (L >= lo) & (L <= hi)
    if int(sel.sum()) < MIN_PIXELS:
        return None
    dmax = np.median(D[sel], axis=0)
    if not np.all(np.isfinite(dmax)) or float(dmax.min()) < MIN_DMAX:
        return None
    return tuple(float(v) for v in dmax)


def ratio(dmax) -> np.ndarray:
    d = np.asarray(dmax, np.float64)
    return d / d.mean()


def roll_reference(estimates: Sequence) -> Optional[np.ndarray]:
    """The roll's typical highlight colour (median Dmax ratio), or None with
    fewer than MIN_ROLL frames to go on."""
    rs = [ratio(e) for e in estimates if e is not None]
    if len(rs) < MIN_ROLL:
        return None
    return np.median(np.array(rs), axis=0)


def guarded_dmax(dmax, reference=None):
    """The Dmax to use as this frame's white point: the frame's own brightest
    area with the scene's part of its colour put back. Returns
    (dmax_used, info) where info = {"ref", "temp", "tint"} records the frame's
    deviation from the reference (before limiting).

    `reference` is the roll's ratio (roll_reference); without it the frame is
    compared with equal channels, the default slope's colour."""
    rf = ratio(dmax)
    if reference is not None:
        ref, kind = np.asarray(reference, np.float64), "roll"
        warm, cool = TEMP_ROLL, TEMP_ROLL
    else:
        ref, kind = np.ones(3), "default"
        warm, cool = TEMP_WARM, TEMP_COOL
    dev = rf - ref
    temp, tint = float(dev @ _UT), float(dev @ _UG)
    used = (ref + np.clip(tint, -TINT_MAX, TINT_MAX) * _UG
            + np.clip(temp, -cool, warm) * _UT)
    used = used * float(np.mean(dmax))
    info = {"ref": kind, "temp": round(temp, 4), "tint": round(tint, 4)}
    return tuple(float(v) for v in used), info


def white_point(black, dmax) -> tuple:
    """Scan values that sit `dmax` above `black` — the white point the
    two-point density conversion expects."""
    return tuple(float(b) / (10.0 ** float(d)) for b, d in zip(black, dmax))
