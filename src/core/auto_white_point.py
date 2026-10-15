"""Auto white point: make each frame's brightest near-grey area neutral, the
way a hand-sampled White Point on a near-clipping highlight does, without
neutralising a sunset or a blue sky. See spec/auto-white-point.md and
spec/white-point-choice.md.

Qt-free. Everything works on the unconverted 1080 preview (the data the
manual sampler reads) and on optical density above the frame's own film base,
D = log10(base / value), per channel.

A white point fixes, per channel, which density becomes display white
(`_twopoint_invert`, density mode: d = D / Dmax). Its colour content is the
RATIO between the three Dmax values; its overall size is the frame's level.
"""
from dataclasses import dataclass, field
from typing import Optional, Sequence

import cv2
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

# The brightest near-grey area (spec/white-point-choice.md §3): the picture is
# averaged into blocks (areas, like a marquee, with the grain averaged out);
# candidates are the brightest blocks that are even inside; the most neutral
# of them, against the roll's neutral, are the white.
BLOCK_FRAC = 1 / 60       # block side, as a fraction of the picture's short side
CAND_PCT = (90.0, 99.8)   # candidate blocks: the brightest 10 %, glints left out
NEUTRAL_MAX = 0.10        # a block this far from the roll's neutral is coloured
NEUTRAL_SHARE = 0.25      # the most neutral quarter of the candidates...
MIN_BLOCKS = 8            # ...and at least this many blocks
MAX_AREAS = 8             # outline rectangles kept for Show

_UT = np.array([1.0, 0.0, -1.0]) / np.sqrt(2.0)     # temperature: + warm
_UG = np.array([-1.0, 2.0, -1.0]) / np.sqrt(6.0)    # tint: + green


def _rect_px(shape, rect, inset):
    h, w = shape[:2]
    x1, y1, x2, y2 = rect
    dx, dy = (x2 - x1) * inset, (y2 - y1) * inset
    a, b = int(round((y1 + dy) * h)), int(round((y2 - dy) * h))
    c, d = int(round((x1 + dx) * w)), int(round((x2 - dx) * w))
    return max(0, a), min(h, b), max(0, c), min(w, d)


@dataclass
class Highlights:
    """What a frame's bright areas look like: the brightest area's density
    (`dmax`, as before), and the candidate blocks for a near-grey pick."""
    dmax: tuple
    cand_d: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    cand_rc: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), int))
    grid: tuple = (0, 0, 1, 1, 1, 1, 1)   # x0, y0, block, rows, cols, raw W, raw H

    @property
    def level(self) -> float:
        return float(np.mean(self.dmax))


def measure_highlights(raw, black, rect=None) -> Optional[Highlights]:
    """The frame's bright areas, in density above `black`, inside `rect`
    (normalised picture area; None uses the middle of the scan). None when
    there is nothing usable."""
    if raw is None or black is None:
        return None
    a = np.asarray(raw)
    if a.ndim != 3 or a.shape[2] < 3:
        return None
    y1, y2, x1, x2 = _rect_px(a.shape, rect or (0, 0, 1, 1),
                              INSET if rect is not None else FALLBACK_INSET)
    if y2 - y1 < 8 or x2 - x1 < 8:
        return None
    px = a[y1:y2, x1:x2, :3].astype(np.float32)
    base = np.maximum(np.asarray(black, np.float32)[:3], 1.0)
    D = np.log10(base / np.maximum(px, 1.0))
    L = D.mean(axis=2)
    lo, hi = np.percentile(L, [JOINT_LO, JOINT_HI])
    sel = (L >= lo) & (L <= hi)
    if int(sel.sum()) < MIN_PIXELS:
        return None
    dmax = np.median(D[sel], axis=0)
    if not np.all(np.isfinite(dmax)) or float(dmax.min()) < MIN_DMAX:
        return None
    hl = Highlights(tuple(float(v) for v in dmax))
    h, w = L.shape
    b = max(4, int(round(min(h, w) * BLOCK_FRAC)))
    rows, cols = h // b, w // b
    hl.grid = (x1, y1, b, rows, cols, a.shape[1], a.shape[0])
    if rows < 4 or cols < 4:
        return hl
    Db = D[:rows * b, :cols * b].reshape(rows, b, cols, b, 3).mean(axis=(1, 3))
    Lp = L[:rows * b, :cols * b].reshape(rows, b, cols, b)
    Lb, Ls = Lp.mean(axis=(1, 3)), Lp.std(axis=(1, 3))
    clo, chi = np.percentile(Lb, CAND_PCT)
    cand = (Lb >= clo) & (Lb <= chi) & (Db.min(axis=2) > 0.02)
    if int(cand.sum()) >= 2:
        cand &= Ls <= np.median(Ls[cand])      # even areas, not edges
    rr, cc = np.nonzero(cand)
    hl.cand_d = Db[rr, cc].astype(np.float64)
    hl.cand_rc = np.stack([rr, cc], axis=1)
    return hl


def highlight_density(raw, black, rect=None) -> Optional[tuple]:
    """Per-channel density above the base of the frame's brightest area, in
    the array's channel order, or None when there is nothing usable."""
    hl = measure_highlights(raw, black, rect)
    return hl.dmax if hl is not None else None


def _areas(hl: Highlights, picked) -> list:
    """Picked blocks as up to MAX_AREAS rectangles (connected blocks merged),
    normalised to the raw preview."""
    x0, y0, b, rows, cols, W, H = hl.grid
    mask = np.zeros((rows, cols), np.uint8)
    rc = hl.cand_rc[picked]
    mask[rc[:, 0], rc[:, 1]] = 1
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(mask, connectivity=8)
    boxes = sorted((tuple(int(v) for v in stats[i]) for i in range(1, n)),
                   key=lambda s_: -s_[4])[:MAX_AREAS]
    return [[(x0 + bx * b) / W, (y0 + by * b) / H,
             (x0 + (bx + bw) * b) / W, (y0 + (by + bh) * b) / H]
            for bx, by, bw, bh, _area in boxes]


def pick_neutral(hl: Optional[Highlights], reference):
    """The colour (density ratio) of the frame's brightest near-grey area
    against the roll's neutral `reference`, and its outline rectangles; or
    (None, []) when there is no roll or no bright area near grey."""
    if hl is None or reference is None or len(hl.cand_d) < MIN_BLOCKS:
        return None, []
    ref = np.asarray(reference, np.float64)
    rat = hl.cand_d / hl.cand_d.mean(axis=1, keepdims=True)
    dev = rat - ref
    dist = np.hypot(dev @ _UT, dev @ _UG)
    k = max(MIN_BLOCKS, int(np.ceil(len(dist) * NEUTRAL_SHARE)))
    order = np.argsort(dist)[:k]
    picked = order[dist[order] <= NEUTRAL_MAX]
    if len(picked) < MIN_BLOCKS:
        return None, []
    return ratio(np.median(hl.cand_d[picked], axis=0)), _areas(hl, picked)


def ratio(dmax) -> np.ndarray:
    d = np.asarray(dmax, np.float64)
    return d / d.mean()


def roll_reference(estimates: Sequence) -> Optional[np.ndarray]:
    """The roll's neutral: the median colour (density ratio) of the frames'
    estimates, each a near-grey ratio or a brightest-area Dmax; None with
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


def frame_white_dmax(hl: Highlights, reference):
    """The Dmax for "this frame's highlight". The near-grey pick decides
    WHICH area is white (the stone, not the foliage beside it); the roll
    guard still limits how much of that area's colour is removed (warm/cool
    is where a sunset or a sky lives, so that stays capped), and the level is
    the brightest area's. With no near-grey area, or no roll, it is the
    guarded brightest area, as before. Returns (dmax_used, info) with the
    deviation, "neutral" (the picked ratio or None) and "areas"."""
    neutral, areas = pick_neutral(hl, reference)
    colour = neutral * hl.level if neutral is not None else hl.dmax
    used, info = guarded_dmax(colour, reference)
    used = tuple(float(v) for v in ratio(used) * hl.level)
    info.update(neutral=[float(v) for v in neutral] if neutral is not None else None,
                areas=areas)
    return used, info


def roll_white_dmax(level: float, reference):
    """The Dmax for "roll average": the roll's neutral at this frame's level
    (equal channels, the default slope's colour, without a roll)."""
    ref = (np.asarray(reference, np.float64) if reference is not None else np.ones(3))
    return (tuple(float(v) for v in ratio(ref) * level),
            {"ref": "roll" if reference is not None else "default"})
