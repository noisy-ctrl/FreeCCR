"""The frame in the live view, found automatically (spec/auto-frame-guide.md).

The user (2026-11-01): "Frame guide is automatic, follows the frame with
focus breathing, linear rail inaccuracies. When changing the frame in the
film carrier, it recognises that a complete frame isn't in shot."

`find_frame(bgr)` reads one live picture (8-bit BGR) on a ≤ `WORK_SIDE` px
copy and sorts every pixel into three kinds:
- HOLDER: dark (the carrier's mask round the film), under a threshold a
  quarter of the way from the picture's black to its brightest;
- BASE: clear film (the rebate, or the gap between frames while the film is
  wound through): within `BASE_NEAR` of the film's brightest level AND flat
  (relative local gradient under `BASE_FLAT`);
- PICTURE: the rest of the film.
Each side of the frame is found in `SLICES` pieces (the first run of
picture rows / columns coming in from outside), a straight line is fitted
through each side's pieces (one round of outlier rejection), and the four
lines give the guide: centre, size and turn, in the live frame's fractions
as `LiveCanvas.guide` holds them.

The frame is COMPLETE when all four sides are found, it doesn't run off the
picture, and no band of clear film crosses it (the gap between two frames:
rows, or columns, at least `GAP_SHARE` base for `GAP_MIN` of its size). A
`FrameFind` also carries the film base it saw (`base8`, the median colour of
the base pixels when there are enough), which is what the live exposure
estimate compares with when no base shows beside the guide.

`GuideFollower` turns finds into the guide on screen: it eases small moves
(breathing, a rail step) by half each time, takes a large one only when two
finds agree, and holds the last guide, marked waiting, while no complete
frame is in view.

Qt-free; numpy + OpenCV.
"""
from dataclasses import dataclass, field
import math

import cv2
import numpy as np

WORK_SIDE = 640          # px: the long side the picture is read at (384 before
                         # revision 2: a 10 px rebate at 1024 was 4 px)
HOLDER_AT = 0.25         # holder: under black + this × (bright − black)
HOLDER_MIN_SPREAD = 40   # 8-bit: less between black and bright = no holder in view
BASE_NEAR = 0.88         # base: at least this share of the film's brightest level
BASE_FLAT = 0.06         # ... and its relative local gradient under this
SLICES = 8               # pieces each side is found in
RUN = 3                  # rows / columns of picture in a row that start the frame
PIECE_MIN = 4            # pieces a side needs
FIT_TOL = 2.0            # px: a piece this far off its side's line is dropped
FIT_RMS = 2.5            # px: a side whose pieces scatter more is not found
GAP_SHARE = 0.85         # a row / column this much base is clear film (0.6 before
                         # revision 2: a bright tree trunk passed) ...
GAP_MIN = 0.015          # ... and this many of them in a row (share of the frame) a gap
GAP_SIDE = 0.10          # ... with at least this much of the frame on either side of it
BASE_MIN_SHARE = 0.004   # base pixels needed (share of the view) to report its colour
BASE_CLIP = 250
DARK_PICTURE = 24        # 8-bit: a picture this dark everywhere has nothing to find


@dataclass
class FrameFind:
    guide: dict = None               # cx, cy, w, h, angle (frame fractions, degrees)
    complete: bool = False           # a whole frame: not cut, not between frames
    cut: tuple = ()                  # sides where it runs off the picture
    between: bool = False            # clear film across it: the film is between frames
    gap_sides: tuple = ()            # sides with clear film (not a thin rebate) beyond them
    reason: str = ""
    rebate: dict = field(default_factory=dict)    # side -> rebate width trimmed (working px)
    bare: tuple = ()                 # sides where the picture meets the mask (no rebate)
    held: tuple = ()                 # sides whose rebate end couldn't be seen: last width used
    base8: np.ndarray = None         # B, G, R of the clear film seen, or None
    sides: dict = field(default_factory=dict)      # side -> pieces found

    @property
    def found(self):
        return self.guide is not None


def _reduce(bgr):
    h, w = bgr.shape[:2]
    k = WORK_SIDE / float(max(h, w))
    if k >= 1.0:
        return bgr
    return cv2.resize(bgr, (max(1, int(round(w * k))), max(1, int(round(h * k)))),
                      interpolation=cv2.INTER_AREA)


def classify(small):
    """(v, holder, base, picture, base_level) on the reduced picture."""
    v = cv2.blur(_max3(small).astype(np.float32), (3, 3))
    y = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = cv2.Sobel(y, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(y, cv2.CV_32F, 0, 1, ksize=3)
    tex = cv2.blur(np.sqrt(gx * gx + gy * gy), (5, 5)) / np.maximum(cv2.blur(y, (5, 5)), 8.0)
    black, bright = np.percentile(v, (1, 99.5))
    if bright - black >= HOLDER_MIN_SPREAD:
        holder = v < black + HOLDER_AT * (bright - black)
    else:
        holder = np.zeros(v.shape, bool)
    film = ~holder
    level = float(np.percentile(v[film], 99)) if film.any() else float(bright)
    base = film & (v > BASE_NEAR * level) & (tex < BASE_FLAT)
    return v, holder, base, film & ~base, level


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


def _first_run(profile, frac=0.6, need=RUN, reverse=False):
    """Index where `need` entries in a row reach `frac`, scanning from the
    start (or the end): the first of them, in the profile's own order."""
    n = len(profile)
    order = range(n - 1, -1, -1) if reverse else range(n)
    run = 0
    for i in order:
        if profile[i] >= frac:
            run += 1
            if run >= need:
                return i + need - 1 if reverse else i - need + 1
        else:
            run = 0
    return None


def _fit(points):
    """Least-squares line q = a + b·s through (s, q), one round of outlier
    rejection; (a, b, rms) or None."""
    if len(points) < PIECE_MIN:
        return None
    s = np.array([p[0] for p in points], np.float64)
    q = np.array([p[1] for p in points], np.float64)
    keep = np.ones(len(s), bool)
    for _ in range(2):
        if keep.sum() < PIECE_MIN:
            return None
        b, a = np.polyfit(s[keep], q[keep], 1)
        res = np.abs(q - (a + b * s))
        new = res <= max(FIT_TOL, np.median(res[keep]) * 2.5)
        if np.array_equal(new, keep):
            break
        keep = new
    if keep.sum() < PIECE_MIN:
        return None
    b, a = np.polyfit(s[keep], q[keep], 1)
    rms = float(np.sqrt(np.mean((q[keep] - (a + b * s[keep])) ** 2)))
    return float(a), float(b), rms


def _side_pieces(pic, side, rough, reach):
    """Edge positions of one side in SLICES pieces: (along, across) pairs."""
    h, w = pic.shape
    c0, r0, c1, r1 = rough
    pts = []
    if side in ("top", "bottom"):
        edges = np.linspace(c0 + 0.1 * (c1 - c0), c1 - 0.1 * (c1 - c0), SLICES + 1)
        at = r0 if side == "top" else r1
        lo, hi = max(0, at - reach), min(h, at + reach + 1)
        for a, b in zip(edges[:-1], edges[1:]):
            a, b = int(a), max(int(b), int(a) + 1)
            prof = pic[lo:hi, a:b].mean(axis=1)
            e = _first_run(prof, reverse=(side == "bottom"))
            if e is not None:
                pts.append(((a + b) / 2.0, lo + e + (0.0 if side == "top" else 1.0)))
    else:
        edges = np.linspace(r0 + 0.1 * (r1 - r0), r1 - 0.1 * (r1 - r0), SLICES + 1)
        at = c0 if side == "left" else c1
        lo, hi = max(0, at - reach), min(w, at + reach + 1)
        for a, b in zip(edges[:-1], edges[1:]):
            a, b = int(a), max(int(b), int(a) + 1)
            prof = pic[a:b, lo:hi].mean(axis=0)
            e = _first_run(prof, reverse=(side == "right"))
            if e is not None:
                pts.append(((a + b) / 2.0, lo + e + (0.0 if side == "left" else 1.0)))
    return pts


def _corners(lines):
    """The four corners from the side lines (top/bottom: y = a + b x;
    left/right: x = a + b y): TL, TR, BR, BL."""
    def meet(h_line, v_line):
        ah, bh = h_line
        av, bv = v_line
        # y = ah + bh x ; x = av + bv y  ->  x = av + bv (ah + bh x)
        x = (av + bv * ah) / (1.0 - bv * bh)
        return x, ah + bh * x
    t, b, l, r = (lines[k][:2] for k in ("top", "bottom", "left", "right"))
    return [meet(t, l), meet(t, r), meet(b, r), meet(b, l)]


def find_frame(bgr, prior=None):
    """A FrameFind for one live picture (8-bit BGR): the carrier's opening
    first, its rebate trimmed off (revision 2); the picture-first method
    when no mask is in view. `prior`: side -> rebate width (working px) from
    the last whole frame (FrameFinder keeps it)."""
    if bgr is None or np.asarray(bgr).ndim != 3 or min(bgr.shape[:2]) < 32:
        return FrameFind(reason="No live picture.")
    small = _reduce(bgr)
    vmax = _max3(small)
    if float(np.percentile(vmax[::2, ::2], 99.5)) < DARK_PICTURE:
        return FrameFind(reason="The picture is dark: is the light on?")
    v = cv2.blur(vmax.astype(np.float32), (3, 3))
    black, bright = np.percentile(v[::2, ::2], (1, 99.5))
    if bright - black >= HOLDER_MIN_SPREAD:
        holder = v < black + HOLDER_AT * (bright - black)
        opening = _find_opening(v, holder)
        if opening is not None:
            film = ~holder
            level = float(np.percentile(v[::2, ::2][film[::2, ::2]], 99)) if film.any() \
                else float(bright)
            return _frame_in_opening(small, v, holder, level, opening, prior)
    v, holder, base, pic, level = classify(small)
    return _picture_first(small, v, base, pic)


def _max3(small):
    b, g, r = cv2.split(small)
    return cv2.max(cv2.max(b, g), r)


# --------------------------------------------------------------------------- #
# mask first (revision 2)
# --------------------------------------------------------------------------- #
OPEN_SLICES = 16          # slices each side of the opening is found in
OPEN_MIN = 6              # agreeing slices a side needs
OPEN_TOL = 1.5            # px: a slice this far off its side's line is dropped
OPEN_RMS = 1.2            # px
EDGE_SKIP = 0.007         # of the long side: the mask's blurred edge, skipped
REBATE_AGREE = 0.10       # a slice's colour by the mask within this (per channel) of
                          # the clear film's: that slice has a rebate
REBATE_BRIGHT = 0.9       # clear film: its brightest channel at least this of the film's p99
REBATE_MATCH = 0.10       # a side's slices this near the clear film's colour are its rebate
REBATE_RUN_TOL = 0.08     # inward, the colour stays within this of the side's own: rebate
REBATE_FLAT = 0.05        # clear film's spread across a slice stays under this (of its level)
REBATE_SUPPORT = 0.35     # ... and at least this share of the side's rebate slices
REBATE_TOL = 1.5          # px: an edge piece this far off the picture's edge line is dropped
REBATE_RMS = 1.0          # px
REBATE_MIN = 4            # rebate slices whose inner ends line up: the picture's edge
REBATE_TURN = 0.015       # ... turned at most this much (slope, ~0.9°) from the mask's side
REBATE_MAX = 0.06         # a band wider than this (of the frame) is clear film, not a rebate
REBATE_LOOK = 0.12        # how far in (of the frame) a rebate is looked for


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
    """Mean profiles of `arr` ((h, w) or (h, w, 3)) over each of the
    (contiguous) `slices`, from index `start` (counted from the view's `side`
    edge), `depth` long: (len(slices), depth[, 3]), index 0 nearest that edge."""
    h, w = arr.shape[:2]
    n = h if side in ("top", "bottom") else w
    start = max(0, min(n, int(start)))
    stop = max(start, min(n, int(start + depth)))
    lo, hi = slices[0][0], slices[-1][1]
    cuts = [a - lo for a, _b in slices]
    widths = np.array([b - a for a, b in slices], np.float32)
    if side in ("top", "bottom"):
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
    """A distance `pos` from the view's `side` edge, as an image coordinate."""
    return float(pos) if side in ("top", "left") else float(n - pos)


def _from_image(side, n, q):
    return float(q) if side in ("top", "left") else float(n - q)


def _find_opening(v, holder):
    """The carrier's opening: {"lines": side -> (a, b) in image coords
    (top/bottom: y = a + b·x; left/right: x = a + b·y), "cut": sides where
    film reaches the view's edge, "rough": (c0, r0, c1, r1)}; None when no
    mask is in view (fewer than 2 sides of an opening)."""
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
        along = (c0, c1) if side in ("top", "bottom") else (r0, r1)
        n = h if side in ("top", "bottom") else w
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
    return {"lines": lines, "cut": cut, "rough": (c0, r0, c1, r1)}


def _line_at(line, s):
    return line[0] + line[1] * s


def _dev(x, colour):
    """How far colour(s) `x` are from `colour`: the largest channel
    difference over the colour's brightest channel (so a channel near 0
    doesn't make noise look like a change)."""
    return np.max(np.abs(np.asarray(x, np.float32) - colour), axis=-1) / max(float(colour.max()), 24.0)


def _edge_in(seg, colour, spread=None):
    """Where the rebate ends in one slice's colour profile `seg` (index 0 by
    the mask): None when it doesn't within the profile. The first two
    positions in a row off the rebate's colour start the edge; the edge is
    then put where the level crosses half way to the picture's (the camera's
    sharpening leaves a bright fringe on the rebate side of the edge, which a
    first-difference test would stop at). `spread`: the colour's spread
    across the slice at each position; clear film is flat, so texture ends
    the rebate even where the picture has its colour (a brick wall)."""
    off = _dev(seg, colour) > REBATE_RUN_TOL
    if spread is not None:
        off |= spread > REBATE_FLAT * max(float(colour.max()), 24.0)
    j = _first_true(off, 2)
    if j is None:
        return None
    lv = seg.sum(axis=1)
    lr = float(colour.sum())
    if j + 5 > len(lv):
        return float(j)
    lp = float(np.mean(lv[j + 2:j + 5]))
    if abs(lp - lr) < 0.08 * lr:
        return float(j)                    # a change of colour, not of level
    mid = 0.5 * (lr + lp)
    sgn = 1.0 if lp > lr else -1.0
    for k in range(max(1, j), min(len(lv), j + 5)):
        if sgn * (lv[k] - mid) >= 0 > sgn * (lv[k - 1] - mid):
            return k - 0.5 + (mid - lv[k - 1]) / (lv[k] - lv[k - 1])
    return float(j)


def _rebates(fs, lines, rough, level, prior=None):
    """Trim the rebate off each found side of the opening.

    Clear film can't be told from the picture by colour (a bright trunk reads
    as the rebate does), so the rebate is found by WHERE it is: against the
    mask, in the clear film's colour, ending in a straight line. Where the
    picture beside the rebate has the clear film's colour too (deep shadows,
    or a bright wall out of focus) its end can't be seen: then the width last
    measured on that side (`prior`, side -> working px) is used, else none.
    Returns (inner lines by side, clear film colour BGR or None, wide sides,
    widths in working px, sides that had no rebate, sides held at the prior)."""
    h, w = fs.shape[:2]
    c0, r0, c1, r1 = rough
    skip = max(2, int(round(EDGE_SKIP * max(h, w))))
    slices = {}
    for side, line in lines.items():
        along = (c0, c1) if side in ("top", "bottom") else (r0, r1)
        n = h if side in ("top", "bottom") else w
        frame_len = (r1 - r0) if side in ("top", "bottom") else (c1 - c0)
        look = int(max(skip + 8, REBATE_LOOK * frame_len))
        sl = _slices(*along)
        es = [int(round(_from_image(side, n, _line_at(line, (a + b) / 2.0)))) for a, b in sl]
        e0 = min(es)                                 # the mask's edge, from the view's edge
        band = _bands(fs, side, sl, e0 + skip, max(es) - e0 + look)
        band2 = _bands(fs * fs, side, sl, e0 + skip, max(es) - e0 + look)
        spread = np.sqrt(np.maximum(band2 - band * band, 0.0)).max(axis=-1)
        rows = []
        for (a, b), e, prof, sp in zip(sl, es, band, spread):
            seg = prof[e - e0:e - e0 + look]
            if len(seg) >= 8:
                rows.append(((a + b) / 2.0, e, seg, sp[e - e0:e - e0 + look]))
        slices[side] = (rows, n, frame_len, look)
    # the clear film's colour: the brightest consistent colour by the mask
    # (each slice's colour by the mask: the median of its first 3 positions,
    # so a rebate only just wider than the skip still reads as clear film)
    refs = [np.median(r[2][:3], axis=0) for rows, *_ in slices.values() for r in rows]
    inner, wide, widths, bare, held = {}, [], {}, [], []
    prior = prior or {}
    if not refs:
        return inner, None, wide, widths, bare, held
    refs = np.array(refs, np.float32)
    bright = refs[refs.max(axis=1) >= REBATE_BRIGHT * level]
    if len(bright) < REBATE_MIN:
        return inner, None, wide, widths, bare, held
    c = np.median(bright, axis=0)
    near = bright[_dev(bright, c) <= REBATE_AGREE]
    if len(near) < REBATE_MIN:
        return inner, None, wide, widths, bare, held
    colour = np.median(near, axis=0)
    for side, (rows, n, frame_len, look) in slices.items():
        line = lines[side]
        here = [r for r in rows if _dev(np.median(r[2][:3], axis=0), colour) <= REBATE_MATCH
                and float(np.median(r[3][:3])) <= REBATE_FLAT * max(float(colour.max()), 24.0)]
        if len(here) < 3:
            bare.append(side)                             # the picture meets the mask
            continue
        own = np.median(np.array([np.median(r[2][:3], axis=0) for r in here]), axis=0)
        pts = []
        for s_mid, e, seg, sp in here:
            k = _edge_in(seg, own, sp)
            if k is not None:                             # None: clear film all the way in
                pts.append((s_mid, _to_image(side, n, e + skip + k)))
        inward = 1.0 if side in ("top", "left") else -1.0
        need = max(REBATE_MIN, int(math.ceil(REBATE_SUPPORT * len(here))))
        fit = _theil_sen(pts, need=need, tol=REBATE_TOL)
        width = None
        if fit is not None and fit[2] <= REBATE_RMS and abs(fit[1] - line[1]) <= REBATE_TURN:
            mids = [p[0] for p in pts]
            width = float(np.median([inward * (_line_at(fit[:2], m) - _line_at(line, m))
                                     for m in mids]))
        if width is None or width <= 0:
            if side in prior:                             # its end can't be seen now
                width = float(prior[side])
                inner[side] = (line[0] + inward * width, line[1])
                widths[side] = width
                held.append(side)
            continue
        inner[side] = fit[:2]
        widths[side] = width
        if width > REBATE_MAX * frame_len:
            wide.append(side)                             # clear film wider than a rebate
    return inner, colour, wide, widths, bare, held


def _frame_in_opening(small, v, holder, level, opening, prior=None):
    h, w = small.shape[:2]
    out = FrameFind()
    lines = dict(opening["lines"])
    cut = list(opening["cut"])
    missing = [s for s in ("top", "bottom", "left", "right") if s not in lines and s not in cut]
    if missing:
        out.reason = "The frame's " + " and ".join(missing) + " edge isn't clear."
        return out
    fs = small.astype(np.float32)
    inner, colour, wide, widths, bare, held = _rebates(fs, lines, opening["rough"], level,
                                                       prior)
    out.rebate = widths
    out.bare = tuple(bare)
    out.held = tuple(held)
    sides = {k: inner.get(k, lines[k]) for k in lines}
    edge = {"top": (0.0, 0.0), "bottom": (float(h), 0.0),
            "left": (0.0, 0.0), "right": (float(w), 0.0)}
    for side in cut:                                  # the view's own edge
        sides[side] = edge[side]
    if colour is not None and np.all(colour < BASE_CLIP):
        out.base8 = colour.astype(np.float64)
    corners = _corners({k: (a, b, 0.0) for k, (a, b) in sides.items()})
    (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = corners
    wid = 0.5 * (math.hypot(trx - tlx, try_ - tly) + math.hypot(brx - blx, bry - bly))
    hei = 0.5 * (math.hypot(blx - tlx, bly - tly) + math.hypot(brx - trx, bry - try_))
    if wid < 0.15 * w or hei < 0.15 * h:
        out.reason = "No frame in view."
        return out
    turns = [math.atan(sides[k][1]) for k in ("top", "bottom") if k not in cut]
    turns += [-math.atan(sides[k][1]) for k in ("left", "right") if k not in cut]
    ang = math.degrees(float(np.mean(turns))) if turns else 0.0
    cx = float(np.mean([c[0] for c in corners]))
    cy = float(np.mean([c[1] for c in corners]))
    out.guide = {"cx": cx / w, "cy": cy / h, "w": wid / w, "h": hei / h, "angle": ang,
                 "format": None}
    out.sides = {k: 1 for k in sides}
    # between frames: clear film across it, judged with the rebate's colour
    if colour is not None and _clear_across(small, holder, colour, cx, cy, wid, hei, ang):
        out.between = True
        out.reason = "No whole frame in view: clear film runs across it (between frames)."
        return out
    out.gap_sides = tuple(wide)
    out.cut = tuple(cut)
    if out.cut:
        out.reason = "The frame runs off the picture (" + " and ".join(out.cut) + ")."
        return out
    out.complete = True
    return out


def _clear_across(small, holder, colour, cx, cy, wid, hei, ang):
    """Clear film (the rebate's colour on all three channels, and flat)
    in a band across the frame; read at half resolution (a band is
    `GAP_MIN` of the frame or more)."""
    half = cv2.resize(small, (small.shape[1] // 2, small.shape[0] // 2),
                      interpolation=cv2.INTER_AREA)
    tol = REBATE_AGREE * max(float(colour.max()), 24.0)
    lo = np.clip(colour - tol, 0, 255).astype(np.uint8)
    hi = np.clip(colour + tol, 0, 255).astype(np.uint8)
    near = cv2.inRange(half, lo, hi) > 0
    hold = cv2.resize(holder.astype(np.uint8), (half.shape[1], half.shape[0]),
                      interpolation=cv2.INTER_NEAREST) > 0
    clear = near & (_texture(half) < BASE_FLAT) & ~hold
    return _gap_inside(clear, cx / 2, cy / 2, wid / 2, hei / 2, ang, anywhere=True)


def _texture(small):
    y = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = cv2.Sobel(y, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(y, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.blur(np.sqrt(gx * gx + gy * gy), (5, 5)) / np.maximum(cv2.blur(y, (5, 5)), 8.0)


# --------------------------------------------------------------------------- #
# picture first (no mask in view)
# --------------------------------------------------------------------------- #
def _picture_first(small, v, base, pic):
    h, w = small.shape[:2]
    out = FrameFind()
    nb = int(base.sum())
    if nb >= BASE_MIN_SHARE * h * w:
        med = np.median(small[base], axis=0)
        if np.all(med < BASE_CLIP):
            out.base8 = med.astype(np.float64)
    _n, r0, r1 = _longest_run(pic.mean(axis=1) >= 0.5)
    _n, c0, c1 = _longest_run(pic.mean(axis=0) >= 0.5)
    if r0 < 0 or c0 < 0 or r1 - r0 < 0.15 * h or c1 - c0 < 0.15 * w:
        out.reason = "No frame in view."
        return out
    rough = (c0, r0, c1, r1)
    reach = int(max(6, 0.06 * max(h, w)))
    lines = {}
    for side in ("top", "bottom", "left", "right"):
        pts = _side_pieces(pic, side, rough, reach)
        out.sides[side] = len(pts)
        fit = _fit(pts)
        if fit is not None and fit[2] <= FIT_RMS:
            lines[side] = fit
    if len(lines) < 4:
        missing = [s for s in ("top", "bottom", "left", "right") if s not in lines]
        out.reason = "The frame's " + " and ".join(missing) + " edge isn't clear."
        return out
    corners = _corners(lines)
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = corners
    wid = 0.5 * (math.hypot(trx - tlx, try_ - tly) + math.hypot(brx - blx, bry - bly))
    hei = 0.5 * (math.hypot(blx - tlx, bly - tly) + math.hypot(brx - trx, bry - try_))
    ang = math.degrees(np.mean([math.atan(lines["top"][1]), math.atan(lines["bottom"][1]),
                                -math.atan(lines["left"][1]), -math.atan(lines["right"][1])]))
    cx, cy = float(np.mean(xs)), float(np.mean(ys))
    out.guide = {"cx": cx / w, "cy": cy / h, "w": wid / w, "h": hei / h, "angle": ang,
                 "format": None}
    if _gap_inside(base, cx, cy, wid, hei, ang):
        out.between = True
        out.reason = "No whole frame in view: clear film runs across it (between frames)."
        return out
    out.gap_sides = tuple(_clear_beyond(base, cx, cy, wid, hei, ang))
    out.cut = tuple(_runs_off(pic, rough))
    if out.cut:
        out.reason = "The frame runs off the picture (" + " and ".join(out.cut) + ")."
        return out
    out.complete = True
    return out


def _runs_off(pic, rough, rows=2, share=0.5):
    """Sides where the picture itself reaches the view's edge (its outermost
    rows / columns, across the frame's span, mostly picture): the frame
    continues past the view there. A frame that merely comes close, with its
    rebate or the carrier's edge in the last rows, doesn't."""
    h, w = pic.shape
    c0, r0, c1, r1 = rough
    cs = slice(int(c0 + 0.1 * (c1 - c0)), int(c1 - 0.1 * (c1 - c0)) + 1)
    rs = slice(int(r0 + 0.1 * (r1 - r0)), int(r1 - 0.1 * (r1 - r0)) + 1)
    off = []
    if pic[:rows, cs].mean() >= share:
        off.append("top")
    if pic[h - rows:, cs].mean() >= share:
        off.append("bottom")
    if pic[rs, :rows].mean() >= share:
        off.append("left")
    if pic[rs, w - rows:].mean() >= share:
        off.append("right")
    return off


def _clear_beyond(base, cx, cy, wid, hei, ang, near=0.02, far=0.05):
    """Sides with clear film just beyond them, past where a rebate strip
    would be (between `near` and `far` of the frame's size outside it): a
    gap between frames, so the frame may be a part of one."""
    h, w = base.shape
    m = cv2.getRotationMatrix2D((cx, cy), ang, 1.0)
    up = cv2.warpAffine(base.astype(np.uint8), m, (w, h), flags=cv2.INTER_NEAREST,
                        borderValue=0).astype(np.float32)
    x0, x1 = cx - wid / 2, cx + wid / 2
    y0, y1 = cy - hei / 2, cy + hei / 2
    xs = slice(max(0, int(x0 + 0.1 * wid)), min(w, int(x1 - 0.1 * wid)) + 1)
    ys = slice(max(0, int(y0 + 0.1 * hei)), min(h, int(y1 - 0.1 * hei)) + 1)
    bands = {
        "top": (slice(max(0, int(y0 - far * hei)), max(0, int(y0 - near * hei))), xs),
        "bottom": (slice(min(h, int(y1 + near * hei)), min(h, int(y1 + far * hei))), xs),
        "left": (ys, slice(max(0, int(x0 - far * wid)), max(0, int(x0 - near * wid)))),
        "right": (ys, slice(min(w, int(x1 + near * wid)), min(w, int(x1 + far * wid)))),
    }
    out = []
    for side, (rs, cs) in bands.items():
        patch = up[rs, cs]
        if patch.size >= 8 and float(patch.mean()) >= GAP_SHARE:
            out.append(side)
    return out


def _gap_inside(base, cx, cy, wid, hei, ang, anywhere=False):
    """A band of clear film across the frame (rows or columns in the frame's
    own axes, 3 % in from its sides). `anywhere`: the rebate has already been
    trimmed (mask first), so a band at the frame's edge counts too: it has
    picture beyond it, so it is the gap between two frames."""
    h, w = base.shape
    m = cv2.getRotationMatrix2D((cx, cy), ang, 1.0)
    # sample the frame's own grid: undo the turn, then cut the inner box
    upright = cv2.warpAffine(base.astype(np.uint8), m, (w, h), flags=cv2.INTER_NEAREST,
                             borderValue=0)
    x0, x1 = int(round(cx - wid / 2 + 0.03 * wid)), int(round(cx + wid / 2 - 0.03 * wid))
    y0, y1 = int(round(cy - hei / 2 + 0.03 * hei)), int(round(cy + hei / 2 - 0.03 * hei))
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return False
    box = upright[y0:y1, x0:x1].astype(np.float32)
    return (_crossing_band(box.mean(axis=1) >= GAP_SHARE, anywhere)
            or _crossing_band(box.mean(axis=0) >= GAP_SHARE, anywhere))


def _crossing_band(on, anywhere=False):
    """A run of clear-film rows with picture on BOTH sides of it (at least
    GAP_SIDE of the frame each): a band against the frame's edge is its
    rebate, not a gap between frames (unless `anywhere`)."""
    n = len(on)
    need = max(2, GAP_MIN * n)
    side = 0 if anywhere else GAP_SIDE * n
    start = None
    for i, v in enumerate(list(on) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= need and start >= side and i <= n - side:
                return True
            start = None
    return False


class FrameFinder:
    """find_frame with a memory: each side's rebate width from the last
    whole frame, used where the rebate's inner end can't be seen this time
    (the picture beside it in the clear film's colour)."""

    def __init__(self):
        self.prior = {}

    def reset(self):
        self.prior = {}

    def find(self, bgr):
        f = find_frame(bgr, self.prior)
        if f.complete and not f.gap_sides:
            new = {k: v for k, v in f.rebate.items() if k not in f.held}
            new.update({k: self.prior[k] for k in f.held if k in self.prior})
            self.prior = new
        return f


# --------------------------------------------------------------------------- #
# following
# --------------------------------------------------------------------------- #
SMALL_MOVE = 0.03        # centre within this (of the frame), size within SIZE_SMALL
SIZE_SMALL = 0.05
AGREE = 0.02             # two finds this close agree (a large move is then taken)
SHRUNK = 0.96            # a frame this much smaller than the last whole one is part of one
                         # (focus breathing measured within ±2 % on the user's recording)
PART_WAIT_S = 2.0         # ... unless it stays that size this long (another format)
RATIO_TOL = 0.08         # with a film format set, a frame off its ratio by more is part of
                         # one (0.05 before revision 2, when the rebate wasn't trimmed)
HIST = 5                 # the guide is the median of this many agreeing finds ...
DEAD_CENTRE = 0.0025     # ... and moves only when that leaves this dead band: centre
DEAD_SIZE = 0.0035       # (of the view), size (relative),
DEAD_ANGLE = 0.12        # turn (degrees)
KEYS = ("cx", "cy", "w", "h", "angle")


def _close(a, b, centre, size):
    return (abs(a["cx"] - b["cx"]) <= centre and abs(a["cy"] - b["cy"]) <= centre
            and abs(a["w"] / b["w"] - 1) <= size and abs(a["h"] / b["h"] - 1) <= size)


def _outside_dead_band(a, b):
    return (abs(a["cx"] - b["cx"]) > DEAD_CENTRE or abs(a["cy"] - b["cy"]) > DEAD_CENTRE
            or abs(a["w"] / b["w"] - 1) > DEAD_SIZE or abs(a["h"] / b["h"] - 1) > DEAD_SIZE
            or abs(a["angle"] - b["angle"]) > DEAD_ANGLE)


def shape_to_format(g, ratio, frame_size):
    """The guide `g` given the film format's shape (long/short `ratio`): the
    largest rectangle of that shape inside it, same centre and turn."""
    fw, fh = frame_size
    wpx, hpx = g["w"] * fw, g["h"] * fh
    want = float(ratio) if wpx >= hpx else 1.0 / float(ratio)
    if wpx / max(hpx, 1e-6) > want:
        wpx = hpx * want
    else:
        hpx = wpx / want
    return dict(g, w=wpx / fw, h=hpx / fh)


class GuideFollower:
    """The guide on screen from a stream of finds. `state`: "following"
    (the last find was a whole frame), "waiting" (no whole frame now: the
    guide is the last one, held), "none" (no frame found yet).

    Steady (revision 2): the guide is the per-value median of the last HIST
    finds that agree with it, and moves only when that median leaves the dead
    band, so noise doesn't move it 4 times a second. After waiting, a frame is
    taken on two agreeing finds, even where the old one was (winding on can
    pass a part frame that happens to sit there)."""

    def __init__(self):
        self.guide = None
        self.state = "none"
        self.reason = ""
        self._pending = None         # a large move waiting for a second find
        self._last_whole = None
        self._shrunk_since = None
        self._hist = []
        self._confirm = False        # after waiting: the next frame needs two finds

    def reset(self):
        self.__init__()

    def _wait(self, reason):
        self.state = "waiting" if self.guide is not None else "none"
        self.reason = reason
        self._pending = None
        self._hist = []
        self._confirm = self.guide is not None
        return False

    def update(self, find, now, ratio=None, frame_size=None):
        """Take one FrameFind; returns True when the guide changed. A whole
        frame is followed; so is one the view's edge cuts (no clear film
        across it), with the note in `reason`. `ratio` (long/short of the
        film format, when one is set) and `frame_size` (the live picture's
        w, h in px): a frame off the format's shape counts as part of one,
        and one on it is given exactly that shape."""
        g = find.guide if find is not None else None
        if g is None or find.between or not (find.complete or find.cut):
            return self._wait((find.reason if find is not None else "") or "No whole frame in view.")
        cut = set(find.cut)
        if ratio and frame_size and not cut:
            wpx, hpx = g["w"] * frame_size[0], g["h"] * frame_size[1]
            shape = max(wpx, hpx) / max(1e-6, min(wpx, hpx))
            if abs(shape / float(ratio) - 1.0) > RATIO_TOL:
                return self._wait("No whole frame in view (its shape isn't the film format's).")
        # a frame much smaller than the last whole one is part of a frame
        # (the gap just left the view) until it has stayed that size a while;
        # a dimension the view's edge cuts can't tell
        small_w = (not cut & {"left", "right"} and self._last_whole is not None
                   and g["w"] < SHRUNK * self._last_whole["w"])
        small_h = (not cut & {"top", "bottom"} and self._last_whole is not None
                   and g["h"] < SHRUNK * self._last_whole["h"])
        if small_w or small_h:
            # (the clock keeps running until a frame of the usual size, so a
            # smaller frame that stays is then taken like any large move;
            # never one with clear film beyond a side: that is part of a
            # frame, the film stopped part-way through winding on)
            if self._shrunk_since is None:
                self._shrunk_since = now
            if find.gap_sides or now - self._shrunk_since < PART_WAIT_S:
                return self._wait("No whole frame in view yet.")
        else:
            self._shrunk_since = None
        if ratio and frame_size and not cut:
            g = shape_to_format(g, ratio, frame_size)
        self.reason = find.reason if find.cut else ""
        changed = False
        if (self.guide is not None and not self._confirm
                and _close(g, self.guide, SMALL_MOVE, SIZE_SMALL)):
            self._pending = None
            self._hist = (self._hist + [dict(g)])[-HIST:]
            target = {k: float(np.median([h[k] for h in self._hist])) for k in KEYS}
            if _outside_dead_band(target, self.guide):
                self.guide = dict(target, format=None)
                changed = True
        else:
            if self._pending is None or not _close(g, self._pending, AGREE, AGREE):
                self._pending = dict(g)
                self.state = "waiting" if self.guide is not None else "none"
                self.reason = "Finding the frame…"
                return False
            self.guide = {k: 0.5 * (g[k] + self._pending[k]) for k in KEYS}
            self.guide["format"] = None
            self._hist = [dict(self._pending), dict(g)]
            self._pending = None
            self._confirm = False
            changed = True
        self.state = "following"
        if find.complete:
            self._last_whole = dict(self.guide)
        return changed
