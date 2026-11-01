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

WORK_SIDE = 384          # px: the long side the picture is read at
HOLDER_AT = 0.25         # holder: under black + this × (bright − black)
HOLDER_MIN_SPREAD = 40   # 8-bit: less between black and bright = no holder in view
BASE_NEAR = 0.88         # base: at least this share of the film's brightest level
BASE_FLAT = 0.06         # ... and its relative local gradient under this
SLICES = 8               # pieces each side is found in
RUN = 3                  # rows / columns of picture in a row that start the frame
PIECE_MIN = 4            # pieces a side needs
FIT_TOL = 2.0            # px: a piece this far off its side's line is dropped
FIT_RMS = 2.5            # px: a side whose pieces scatter more is not found
GAP_SHARE = 0.6          # a row / column this much base is clear film ...
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
    v = cv2.blur(small.max(axis=2).astype(np.float32), (3, 3))
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


def find_frame(bgr):
    """A FrameFind for one live picture (8-bit BGR)."""
    if bgr is None or np.asarray(bgr).ndim != 3 or min(bgr.shape[:2]) < 32:
        return FrameFind(reason="No live picture.")
    small = _reduce(bgr)
    h, w = small.shape[:2]
    if float(np.percentile(small.max(axis=2), 99.5)) < DARK_PICTURE:
        return FrameFind(reason="The picture is dark: is the light on?")
    v, holder, base, pic, level = classify(small)
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
    # complete? no clear film across it (between frames), and not off the
    # picture (a frame cut by the view's edge is still followed: focusing
    # can make it a little bigger than the view)
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


def _gap_inside(base, cx, cy, wid, hei, ang):
    """A band of clear film across the frame (rows or columns in the frame's
    own axes, 3 % in from its sides)."""
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
    return (_crossing_band(box.mean(axis=1) >= GAP_SHARE)
            or _crossing_band(box.mean(axis=0) >= GAP_SHARE))


def _crossing_band(on):
    """A run of clear-film rows with picture on BOTH sides of it (at least
    GAP_SIDE of the frame each): a band against the frame's edge is its
    rebate, not a gap between frames."""
    n = len(on)
    need = max(2, GAP_MIN * n)
    start = None
    for i, v in enumerate(list(on) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= need and start >= GAP_SIDE * n and i <= (1 - GAP_SIDE) * n:
                return True
            start = None
    return False


# --------------------------------------------------------------------------- #
# following
# --------------------------------------------------------------------------- #
EASE = 0.5               # a small move is taken this much each find
SMALL_MOVE = 0.03        # centre within this (of the frame), size within SIZE_SMALL
SIZE_SMALL = 0.05
AGREE = 0.02             # two finds this close agree (a large move is then taken)
SHRUNK = 0.96            # a frame this much smaller than the last whole one is part of one
                         # (focus breathing measured within ±2 % on the user's recording)
PART_WAIT_S = 2.0         # ... unless it stays that size this long (another format)
RATIO_TOL = 0.05         # with a film format set, a frame off its ratio by more is part of one


def _close(a, b, centre, size):
    return (abs(a["cx"] - b["cx"]) <= centre and abs(a["cy"] - b["cy"]) <= centre
            and abs(a["w"] / b["w"] - 1) <= size and abs(a["h"] / b["h"] - 1) <= size)


class GuideFollower:
    """The guide on screen from a stream of finds. `state`: "following"
    (the last find was a whole frame), "waiting" (no whole frame now: the
    guide is the last one, held), "none" (no frame found yet)."""

    def __init__(self):
        self.guide = None
        self.state = "none"
        self.reason = ""
        self._pending = None         # a large move waiting for a second find
        self._last_whole = None
        self._shrunk_since = None

    def reset(self):
        self.__init__()

    def update(self, find, now, ratio=None, frame_size=None):
        """Take one FrameFind; returns True when the guide changed. A whole
        frame is followed; so is one the view's edge cuts (no clear film
        across it), with the note in `reason`. `ratio` (long/short of the
        film format, when one is set) and `frame_size` (the live picture's
        w, h in px) let a frame off its format's shape count as part of one."""
        g = find.guide if find is not None else None
        if g is None or find.between or not (find.complete or find.cut):
            self.state = "waiting" if self.guide is not None else "none"
            self.reason = (find.reason if find is not None else "") or "No whole frame in view."
            self._pending = None
            return False
        cut = set(find.cut)
        if ratio and frame_size and not cut:
            wpx, hpx = g["w"] * frame_size[0], g["h"] * frame_size[1]
            shape = max(wpx, hpx) / max(1e-6, min(wpx, hpx))
            if abs(shape / float(ratio) - 1.0) > RATIO_TOL:
                self.state = "waiting" if self.guide is not None else "none"
                self.reason = "No whole frame in view (its shape isn't the film format's)."
                self._pending = None
                return False
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
                self.state = "waiting" if self.guide is not None else "none"
                self.reason = "No whole frame in view yet."
                return False
        else:
            self._shrunk_since = None
        self.reason = find.reason if find.cut else ""
        if self.guide is not None and _close(g, self.guide, SMALL_MOVE, SIZE_SMALL):
            self._pending = None
            new = {k: self.guide[k] + EASE * (g[k] - self.guide[k])
                   for k in ("cx", "cy", "w", "h", "angle")}
            new["format"] = None
            changed = any(abs(new[k] - self.guide[k]) > 1e-4
                          for k in ("cx", "cy", "w", "h", "angle"))
            self.guide = new
        else:
            if self._pending is None or not _close(g, self._pending, AGREE, AGREE):
                self._pending = dict(g)
                self.state = "waiting" if self.guide is not None else "none"
                self.reason = "Finding the frame…"
                return False
            self.guide = dict(g)
            self._pending = None
            changed = True
        self.state = "following"
        if find.complete:
            self._last_whole = dict(self.guide)
        return changed
