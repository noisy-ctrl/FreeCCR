"""Your frame guide, kept on the frame (spec/guide-follow-and-crop.md §1).

You place Capture's frame guide; this keeps it there. In the user's carrier
the black mask is the carrier's opening, and focusing or a rail step scales
and shifts everything, mask and film together, while winding on moves only
the film inside the opening. So the guide follows the OPENING:

- `find_opening(bgr)`: the opening's sides in one live picture, as positions
  in view fractions (top / bottom at the view's middle column, left / right
  at its middle row), found the way parked revision 2 of the frame finder
  found them (`spec/auto-frame-guide.md`): each side in 16 slices coming in
  from the view's edge, a robust line through them.
- `GuideTracker`: anchored on your guide and the opening seen then, it
  solves a scale about the view's centre plus a shift from the sides seen
  now, and maps your guide through it. Steady: a median of the last few
  solutions and a dead band, and a large jump only on two agreeing finds.

Qt-free; numpy + OpenCV.
"""
from dataclasses import dataclass, field

import cv2
import numpy as np

WORK_SIDE = 640           # px: the long side the picture is read at
HOLDER_AT = 0.25          # holder: under black + this × (bright − black)
HOLDER_MIN_SPREAD = 40    # 8-bit: less between black and bright = no mask in view
RUN = 3                   # film pixels in a row that start the opening
OPEN_SLICES = 16          # slices each side is found in
OPEN_MIN = 6              # agreeing slices a side needs
OPEN_TOL = 1.5            # px: a slice this far off its side's line is dropped
OPEN_RMS = 1.2            # px

SIDES = ("left", "right", "top", "bottom")
X_SIDES = ("left", "right")
Y_SIDES = ("top", "bottom")

# following
HIST = 5                  # the guide is the median of this many solutions ...
DEAD_CENTRE = 0.0025      # ... and moves only when that leaves this dead band:
DEAD_SIZE = 0.0035        # centre (of the view), size (relative)
S_RANGE = (0.85, 1.15)    # a scale outside this is no focusing move: ignored
SHIFT_MAX = 0.15          # nor a shift over this (of the view)
JUMP_S = 0.05             # a move bigger than this in scale ...
JUMP_D = 0.04             # ... or shift needs ...
AGREE = 0.01              # ... the next solution within this to be taken
LOST_TICKS = 2            # this many finds with no side to follow: lost
PAIR_MIN = 0.05           # an opposite pair this close together can't give a scale


@dataclass
class Opening:
    sides: dict = field(default_factory=dict)    # side -> position (view fraction)
    cut: tuple = ()                              # sides that run off the view
    size: tuple = (0, 0)                         # working picture (w, h)


# --------------------------------------------------------------------------- #
# the opening
# --------------------------------------------------------------------------- #
def _reduce(bgr):
    h, w = bgr.shape[:2]
    k = WORK_SIDE / float(max(h, w))
    if k >= 1.0:
        return bgr
    return cv2.resize(bgr, (max(1, int(round(w * k))), max(1, int(round(h * k)))),
                      interpolation=cv2.INTER_AREA)


def _max3(small):
    if small.ndim == 2:
        return small
    b, g, r = cv2.split(small[:, :, :3])
    return cv2.max(cv2.max(b, g), r)


def _longest_run(mask1d):
    best, start = (0, -1, -1), None
    for i, on in enumerate(list(mask1d) + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start > best[0]:
                best = (i - start, start, i - 1)
            start = None
    return best


def _theil_sen(points, need=OPEN_MIN, tol=OPEN_TOL):
    """Robust line q = a + b·s: Theil–Sen, then least squares on the points
    within `tol`; (a, b, rms, kept) or None."""
    if len(points) < need:
        return None
    s = np.array([p[0] for p in points], np.float64)
    q = np.array([p[1] for p in points], np.float64)
    i, j = np.triu_indices(len(s), 1)
    ds = s[j] - s[i]
    ok = np.abs(ds) > 1e-9
    if not ok.any():
        return None
    b = float(np.median((q[j] - q[i])[ok] / ds[ok]))
    a = float(np.median(q - b * s))
    keep = np.abs(q - (a + b * s)) <= tol
    if keep.sum() < need:
        return None
    b, a = np.polyfit(s[keep], q[keep], 1)
    rms = float(np.sqrt(np.mean((q[keep] - (a + b * s[keep])) ** 2)))
    return float(a), float(b), rms, int(keep.sum())


def _slices(lo, hi, n=OPEN_SLICES):
    edges = np.linspace(lo + 0.1 * (hi - lo), hi - 0.1 * (hi - lo), n + 1)
    return [(int(a), max(int(b), int(a) + 1)) for a, b in zip(edges[:-1], edges[1:])]


def _bands(arr, side, slices, start, depth):
    """Mean profiles of `arr` over each of the (contiguous) `slices`, from
    index `start` counted from the view's `side` edge, `depth` long:
    (len(slices), depth), index 0 nearest that edge."""
    h, w = arr.shape[:2]
    n = h if side in Y_SIDES else w
    start = max(0, min(n, int(start)))
    stop = max(start, min(n, int(start + depth)))
    lo, hi = slices[0][0], slices[-1][1]
    cuts = [a - lo for a, _b in slices]
    widths = np.array([b - a for a, b in slices], np.float32)
    if side in Y_SIDES:
        sub = arr[start:stop, lo:hi] if side == "top" else arr[n - stop:n - start, lo:hi][::-1]
        prof = np.moveaxis(np.add.reduceat(sub, cuts, axis=1), 1, 0)
    else:
        sub = arr[lo:hi, start:stop] if side == "left" else arr[lo:hi, n - stop:n - start][:, ::-1]
        prof = np.add.reduceat(sub, cuts, axis=0)
    return prof / widths.reshape((-1,) + (1,) * (prof.ndim - 1))


def _first_true(on, need):
    """The first index starting `need` True values in a row, or None."""
    m = len(on) - need + 1
    if m <= 0:
        return None
    c = on[:m].copy()
    for k in range(1, need):
        c &= on[k:m + k]
    i = int(np.argmax(c))
    return i if c[i] else None


def _to_image(side, n, pos):
    return float(pos) if side in ("top", "left") else float(n - pos)


def _opening_lines(v, holder):
    """{"lines": side -> (a, b) (top/bottom: y = a + b·x; left/right:
    x = a + b·y, working px), "cut": [...]} or None (no opening)."""
    h, w = v.shape
    if not holder.any():
        return None
    film = (~holder).astype(np.float32)
    _n, r0, r1 = _longest_run(film.mean(axis=1) >= 0.5)
    _n, c0, c1 = _longest_run(film.mean(axis=0) >= 0.5)
    if r0 < 0 or c0 < 0 or r1 - r0 < 0.15 * h or c1 - c0 < 0.15 * w:
        return None
    lines, cut = {}, []
    reach = {"top": r0, "bottom": h - 1 - r1, "left": c0, "right": w - 1 - c1}
    for side in ("top", "bottom", "left", "right"):
        along = (c0, c1) if side in Y_SIDES else (r0, r1)
        n = h if side in Y_SIDES else w
        depth = int(min(n // 2, reach[side] + 0.1 * n + 8))
        sl = _slices(*along)
        fps = _bands(film, side, sl, 0, depth)
        vps = _bands(v, side, sl, 0, depth)
        pts, on_film = [], 0
        for (a, b), fp, vp in zip(sl, fps, vps):
            if fp[0] >= 0.5:
                on_film += 1
                continue
            e = _first_true(fp >= 0.5, RUN)
            if e is None:
                continue
            lo_lv = float(np.median(vp[max(0, e - 6):max(1, e - 2)]))
            hi_lv = float(np.mean(vp[e + 2:e + 6])) if e + 6 <= len(vp) else float(vp[-1])
            mid = 0.5 * (lo_lv + hi_lv)
            pos = float(e)
            for i in range(max(1, e - 4), min(len(vp), e + 5)):
                if vp[i - 1] < mid <= vp[i]:
                    pos = i - 1 + (mid - vp[i - 1]) / max(1e-6, vp[i] - vp[i - 1])
                    break
            # profile index i is the pixel centred i + 0.5 from the edge
            pts.append(((a + b) / 2.0, _to_image(side, n, pos + 0.5)))
        if on_film >= OPEN_SLICES // 2:
            cut.append(side)
            continue
        fit = _theil_sen(pts)
        if fit is not None and fit[2] <= OPEN_RMS:
            lines[side] = fit[:2]
    if len(lines) < 2:
        return None
    return {"lines": lines, "cut": cut}


def find_opening(bgr):
    """The carrier's opening in one live picture (8-bit BGR or grey), or None
    when no mask is in view."""
    if bgr is None:
        return None
    small = _reduce(np.asarray(bgr))
    v = cv2.blur(_max3(small).astype(np.float32), (3, 3))
    black, bright = np.percentile(v, (1, 99.5))
    if bright - black < HOLDER_MIN_SPREAD:
        return None
    holder = v < black + HOLDER_AT * (bright - black)
    op = _opening_lines(v, holder)
    if op is None:
        return None
    h, w = v.shape
    sides = {}
    for side, (a, b) in op["lines"].items():
        if side in Y_SIDES:
            sides[side] = (a + b * (w / 2.0)) / h
        else:
            sides[side] = (a + b * (h / 2.0)) / w
    return Opening(sides=sides, cut=tuple(op["cut"]), size=(w, h))


# --------------------------------------------------------------------------- #
# following
# --------------------------------------------------------------------------- #
def solve(ref, now, last=(1.0, 0.0, 0.0)):
    """(s, dx, dy, sides used) taking the anchored opening `ref` to `now`
    (side -> view fraction), or None with no side in common. Scale from the
    opposite pairs seen, else the last; each axis' shift from its sides seen,
    else the last."""
    common = [k for k in SIDES if k in ref and k in now]
    if not common:
        return None
    vals = []
    for a, b in (X_SIDES, Y_SIDES):
        if a in common and b in common:
            span = ref[b] - ref[a]
            if span > PAIR_MIN:
                vals.append(((now[b] - now[a]) / span, span))
    s = (sum(v * wt for v, wt in vals) / sum(wt for _v, wt in vals)) if vals else float(last[0])
    out = []
    for axis, keep in ((X_SIDES, last[1]), (Y_SIDES, last[2])):
        seen = [k for k in axis if k in common]
        if seen:
            out.append(float(np.mean([now[k] - (0.5 + s * (ref[k] - 0.5)) for k in seen])))
        else:
            out.append(float(keep))
    return float(s), out[0], out[1], len(common)


def apply(sol, guide):
    """`guide` (cx, cy, w, h in view fractions) through the solution."""
    s, dx, dy = sol[:3]
    g = dict(guide)
    g["cx"] = 0.5 + s * (float(guide["cx"]) - 0.5) + dx
    g["cy"] = 0.5 + s * (float(guide["cy"]) - 0.5) + dy
    g["w"] = float(guide["w"]) * s
    g["h"] = float(guide["h"]) * s
    return g


def _outside_dead_band(a, b):
    return (abs(a["cx"] - b["cx"]) > DEAD_CENTRE or abs(a["cy"] - b["cy"]) > DEAD_CENTRE
            or abs(a["w"] / max(1e-9, b["w"]) - 1) > DEAD_SIZE
            or abs(a["h"] / max(1e-9, b["h"]) - 1) > DEAD_SIZE)


class GuideTracker:
    """Keeps your guide on the frame: `anchor(guide, opening)` when you place
    it, `update(opening)` with each find; returns the guide to show when it
    moved, else None. `state`: "idle" (not anchored), "waiting" (anchored, no
    opening seen yet), "following", "lost"."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.ref_guide = None
        self.ref = None
        self.shown = None
        self.hist = []
        self.last = (1.0, 0.0, 0.0)
        self.pending = None
        self.misses = 0
        self.state = "idle"

    @property
    def anchored(self):
        return self.ref_guide is not None and self.ref is not None

    @property
    def reason(self):
        if self.state == "lost":
            return "Not following: no edge of the carrier in view"
        return ""

    def anchor(self, guide, opening=None):
        self.ref_guide = dict(guide)
        self.shown = dict(guide)
        self.hist, self.last, self.pending, self.misses = [], (1.0, 0.0, 0.0), None, 0
        self.ref = dict(opening.sides) if (opening is not None and opening.sides) else None
        self.state = "following" if self.ref else "waiting"

    def _missed(self):
        self.misses += 1
        if self.misses >= LOST_TICKS and self.state != "waiting":
            self.state = "lost"

    def update(self, opening):
        if self.ref_guide is None:
            return None
        if opening is None or not opening.sides:
            self._missed()
            return None
        if self.ref is None:                     # anchored while none was in view
            self.ref = dict(opening.sides)
            self.state, self.misses = "following", 0
            return None
        sol = solve(self.ref, opening.sides, self.last)
        if sol is None:
            self._missed()
            return None
        self.misses, self.state = 0, "following"
        s, dx, dy, n = sol
        if not (S_RANGE[0] <= s <= S_RANGE[1]) or max(abs(dx), abs(dy)) > SHIFT_MAX:
            return None
        ls, ldx, ldy = self.last
        if abs(s / ls - 1) > JUMP_S or max(abs(dx - ldx), abs(dy - ldy)) > JUMP_D:
            p = self.pending
            if (p is None or abs(s / p[0] - 1) > AGREE
                    or max(abs(dx - p[1]), abs(dy - p[2])) > AGREE):
                self.pending = (s, dx, dy)
                return None
            self.hist = []                       # a real jump: start the median afresh
        self.pending = None
        self.last = (s, dx, dy)
        self.hist = (self.hist + [(s, dx, dy)])[-HIST:]
        if n >= 2:                               # a side first seen now joins the reference
            for k, v in opening.sides.items():
                if k not in self.ref:
                    d = dx if k in X_SIDES else dy
                    self.ref[k] = 0.5 + (v - 0.5 - d) / s
        med = tuple(float(np.median([h[i] for h in self.hist])) for i in range(3))
        g = apply(med, self.ref_guide)
        if self.shown is None or _outside_dead_band(g, self.shown):
            self.shown = g
            return dict(g)
        return None
