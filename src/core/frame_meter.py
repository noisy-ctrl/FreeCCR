"""Each frame's own exposure, metered inside the frame guide
(spec/frame-exposure.md). Qt-free.

A film base's exposures put the base at `TARGET` of the RAW's range. A frame
is denser than its base everywhere, so its thinnest part (the brightest in
the scan) sits lower; the suggestion lifts that part to `TARGET` with the
shutter and ISO only (a file records those, not the light, and conversion
scales the black point by each frame's exposure).

- `guide_mask` / `guide_histogram`: the frame guide's rotated box on a frame.
- `live_estimate`: a ratio within one live picture (the film base patch
  against the frame's thinnest part), so the live view's own brightening
  cancels out; approximate.
- `read_frame` / `reading_in_guide`: a RAW test shot's thinnest level inside
  the guide, exact.
- `suggest`: each colour's shutter / ISO through the film base's own ladder.
- `FrameMetering`: the Meter's shots, in `base_exposure.Calibration`'s shape.
"""
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from core import base_exposure as be

HIGH_PCT = 99.5            # the frame's thinnest part: this percentile inside the guide
LIVE_MAX_SIDE = 640
LIVE_MARGIN_STOPS = 1 / 3  # a live estimate aims this far under the target
BASE_CLIP = 250            # 8-bit live view: the base this bright is clipped
BASE_DARK = 12             # ... this dark, too dark to compare
BLOCK = 16                 # RAW: block means of BLOCK x BLOCK photosites
STRIP_ROWS = 512           # RAW: rows read at a time (memory)
MIN_CHANGE_STOPS = 1 / 6   # a suggestion closer than this to the base's is "no change"
METER_INSET = 0.03         # measured inside the guide this share in from each side: a
                           # guide laid a little over the rebate must not read the base


# --------------------------------------------------------------------------- #
# the guide on a frame
# --------------------------------------------------------------------------- #
def guide_corners(guide, w, h):
    """The guide's four corners in pixels of a w x h frame (as the canvas
    draws it: centre and size as fractions, turned by `angle` degrees)."""
    cx, cy = float(guide["cx"]) * w, float(guide["cy"]) * h
    gw, gh = float(guide["w"]) * w, float(guide["h"]) * h
    a = math.radians(float(guide.get("angle", 0.0)))
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for dx, dy in ((-gw / 2, -gh / 2), (gw / 2, -gh / 2), (gw / 2, gh / 2), (-gw / 2, gh / 2)):
        out.append((cx + dx * ca - dy * sa, cy + dx * sa + dy * ca))
    return np.array(out, np.float32)


def guide_mask(guide, w, h):
    """uint8 mask (255 inside the guide) for a w x h frame."""
    m = np.zeros((int(h), int(w)), np.uint8)
    pts = np.round(guide_corners(guide, w, h)).astype(np.int32)
    cv2.fillConvexPoly(m, pts, 255)
    return m


def inside_guide(guide, fx, fy, w=1.0, h=1.0):
    """Is the point (frame fractions; arrays work too) inside the guide?
    `w`/`h` give the frame's aspect, as the guide is turned in pixels."""
    x, y = np.asarray(fx, np.float64) * w, np.asarray(fy, np.float64) * h
    cx, cy = float(guide["cx"]) * w, float(guide["cy"]) * h
    gw, gh = float(guide["w"]) * w, float(guide["h"]) * h
    a = math.radians(float(guide.get("angle", 0.0)))
    dx, dy = x - cx, y - cy
    lx = dx * math.cos(a) + dy * math.sin(a)
    ly = -dx * math.sin(a) + dy * math.cos(a)
    inside = (np.abs(lx) <= gw / 2) & (np.abs(ly) <= gh / 2)
    return bool(inside) if inside.ndim == 0 else inside


def inset_guide(guide, inset=METER_INSET):
    """The guide shrunk by `inset` of its size on each side."""
    g = dict(guide)
    g["w"] = float(g["w"]) * (1 - 2 * inset)
    g["h"] = float(g["h"]) * (1 - 2 * inset)
    return g


def _reduce(bgr, max_side):
    h, w = bgr.shape[:2]
    k = max(1, int(math.ceil(max(h, w) / float(max_side))))
    if k == 1:
        return bgr
    return cv2.resize(bgr, (max(1, w // k), max(1, h // k)), interpolation=cv2.INTER_AREA)


def _inner_mask(guide, small, full):
    """The guide's mask on a reduced frame; one pixel in from the edge when
    it was reduced, as an edge pixel there blends the inside with the outside."""
    h, w = small.shape[:2]
    mask = guide_mask(guide, w, h)
    if small.shape[:2] != full.shape[:2]:
        mask = cv2.erode(mask, np.ones((3, 3), np.uint8))
    return mask


def guide_histogram(bgr, guide, max_side=480):
    """(3, 256) counts, R, G, B, of the whole frame inside the guide."""
    small = _reduce(bgr, max_side)
    mask = _inner_mask(guide, small, bgr)
    out = np.zeros((3, 256), np.float32)
    for i, c in enumerate((2, 1, 0)):
        out[i] = cv2.calcHist([small], [c], mask, [256], [0, 256]).ravel()
    return out


# --------------------------------------------------------------------------- #
# the live estimate
# --------------------------------------------------------------------------- #
def srgb_linear(v8):
    v = np.asarray(v8, np.float64) / 255.0
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


@dataclass
class LiveEstimate:
    ratio: float = None             # the frame's thinnest part / the base, linear (max of RGB)
    ratios: dict = field(default_factory=dict)
    reason: str = ""

    @property
    def ok(self):
        return self.ratio is not None

    @property
    def stops(self):
        """How much longer than the base's exposure would bring the frame's
        thinnest part to the base's place."""
        return -math.log2(self.ratio) if self.ok and self.ratio > 0 else None


def live_estimate(bgr, guide, base_pos):
    """`bgr` the live frame (8-bit), `guide` the frame guide, `base_pos`
    (fx, fy, frame_w, frame_h) the Film Base click. A LiveEstimate (with a
    `reason` when there is none)."""
    if bgr is None:
        return LiveEstimate(reason="No live picture.")
    if guide is None:
        return LiveEstimate(reason="Turn the frame guide on and place it on the frame.")
    if base_pos is None:
        return LiveEstimate(reason="Click the film base once (Negative Preview → Film Base) "
                                   "to compare the frame with.")
    fx, fy = float(base_pos[0]), float(base_pos[1])
    H0, W0 = bgr.shape[:2]
    if inside_guide(guide, fx, fy, W0, H0):
        return LiveEstimate(reason="The film base click is inside the frame guide: click "
                                   "the border beside the frame.")
    small = _reduce(bgr, LIVE_MAX_SIDE)
    h, w = small.shape[:2]
    inner = inset_guide(guide)
    x0, y0, x1, y1 = be.patch_around(fx, fy, w, h)
    c0, c1 = int(math.floor(x0 * w)), max(int(math.ceil(x1 * w)), int(math.floor(x0 * w)) + 1)
    r0, r1 = int(math.floor(y0 * h)), max(int(math.ceil(y1 * h)), int(math.floor(y0 * h)) + 1)
    patch = small[r0:r1, c0:c1].reshape(-1, 3)
    if patch.size == 0:
        return LiveEstimate(reason="The film base click is off the picture.")
    base8 = np.median(patch, axis=0)                     # B, G, R
    if np.any(base8 >= BASE_CLIP):
        return LiveEstimate(reason="The film base is clipped in the live view: the camera's "
                                   "live view is too bright to compare.")
    if np.any(base8 < BASE_DARK):
        return LiveEstimate(reason="The film base is too dark in the live view to compare.")
    mask = _inner_mask(inner, small, bgr) > 0
    if mask.sum() < 50:
        return LiveEstimate(reason="The frame guide is off the picture.")
    blur = cv2.blur(small, (3, 3))
    frame8 = np.array([np.percentile(blur[..., c][mask], HIGH_PCT) for c in range(3)])
    base_l = srgb_linear(base8)
    frame_l = srgb_linear(np.maximum(frame8, 0.5))
    ratios = {"R": float(frame_l[2] / base_l[2]), "G": float(frame_l[1] / base_l[1]),
              "B": float(frame_l[0] / base_l[0])}
    return LiveEstimate(ratio=max(ratios.values()), ratios=ratios)


# --------------------------------------------------------------------------- #
# a RAW test shot, inside the guide
# --------------------------------------------------------------------------- #
def reading_in_guide(mosaic, colors, desc, black, white, guide, block=BLOCK,
                     strip_rows=STRIP_ROWS):
    """The thinnest level of each colour inside the guide: block means of
    `block` x `block` photosites (black subtracted, as a share of black to
    white), the HIGH_PCT percentile of the blocks wholly inside the guide
    (`METER_INSET` in from its sides). Read in strips of rows, so the whole
    sensor never sits in float at once."""
    guide = inset_guide(guide)
    H, W = mosaic.shape
    pts = guide_corners(guide, W, H)
    c0 = max(0, int(math.floor(pts[:, 0].min() / block)) * block)
    r0 = max(0, int(math.floor(pts[:, 1].min() / block)) * block)
    c1 = min(W - W % block, int(math.ceil(pts[:, 0].max() / block)) * block)
    r1 = min(H - H % block, int(math.ceil(pts[:, 1].max() / block)) * block)
    out = be.Reading()
    if c1 <= c0 or r1 <= r0:
        return out
    letters = {}
    for i, ch in enumerate(desc):
        letters.setdefault(ch, []).append(i)
    vals = {ch: [] for ch in letters}
    nbc = (c1 - c0) // block
    x_left = (c0 + np.arange(nbc) * block) / W
    x_right = (c0 + (np.arange(nbc) + 1) * block) / W
    step = max(block, (strip_rows // block) * block)
    for rs in range(r0, r1, step):
        re_ = min(r1, rs + step)
        nbr = (re_ - rs) // block
        if nbr <= 0:
            continue
        sub = np.asarray(mosaic[rs:rs + nbr * block, c0:c1], np.float32)
        cols = np.asarray(colors[rs:rs + nbr * block, c0:c1])
        y_top = (rs + np.arange(nbr) * block) / H
        y_bot = (rs + (np.arange(nbr) + 1) * block) / H
        inside = np.ones((nbr, nbc), bool)
        for xe in (x_left, x_right):              # every corner inside: the whole block
            for ye in (y_top, y_bot):
                inside &= inside_guide(guide, xe[None, :], ye[:, None], W, H)
        for ch, idxs in letters.items():
            sums = np.zeros((nbr, nbc), np.float64)
            counts = np.zeros((nbr, nbc), np.float64)
            for i in idxs:
                m = cols == i
                if not m.any():
                    continue
                span = max(1e-6, float(white) - float(black[i]))
                v = np.where(m, (sub - float(black[i])) / span, 0.0)
                sums += v.reshape(nbr, block, nbc, block).sum(axis=(1, 3))
                counts += m.reshape(nbr, block, nbc, block).sum(axis=(1, 3))
            ok = inside & (counts > 0)
            if ok.any():
                vals[ch].append((sums[ok] / counts[ok]).astype(np.float32))
    for ch, parts in vals.items():
        if parts:
            v = np.concatenate(parts)
            out.levels[ch] = float(np.percentile(v, HIGH_PCT))
            out.spreads[ch] = 0.0
            out.counts[ch] = int(v.size)
    return out


def read_frame(path, guide):
    """A RAW's thinnest level per colour inside the guide (a Reading)."""
    import rawpy
    with rawpy.imread(path) as raw:
        vis = np.array(raw.raw_image_visible, copy=True)
        cols = np.array(raw.raw_colors_visible, copy=True)
        desc = bytes(raw.color_desc).decode("ascii", "ignore")
        black = list(raw.black_level_per_channel)
        white = float(raw.white_level)
    return reading_in_guide(vis, cols, desc, black, white, guide)


# --------------------------------------------------------------------------- #
# the suggestion
# --------------------------------------------------------------------------- #
def stops_text(stops):
    """To the nearest third: "+1⅓", "−⅔", "±0"."""
    n = int(round(float(stops) * 3))
    if n == 0:
        return "±0"
    whole, third = divmod(abs(n), 3)
    frac = {0: "", 1: "⅓", 2: "⅔"}[third]
    num = (str(whole) if whole else "") + frac
    return ("+" if n > 0 else "−") + num


@dataclass
class Suggestion:
    exposures: dict = field(default_factory=dict)   # colour -> {"shutter", "iso", "levels"}
    stops: dict = field(default_factory=dict)       # colour -> vs the film base's
    live: bool = False
    notes: list = field(default_factory=list)

    @property
    def changes(self):
        return any(abs(s) >= MIN_CHANGE_STOPS for s in self.stops.values())

    def text(self):
        if not self.stops:
            return ""
        keys = list(self.stops)
        if self.live:
            s = max(self.stops.values(), key=abs)
            if abs(s) < MIN_CHANGE_STOPS:
                return "This frame: ≈ as the film base (live view)"
            return f"This frame: ≈ {stops_text(s)} stops (live view)"
        if keys == ["w"]:
            return f"This frame, metered: {stops_text(self.stops['w'])} stops"
        return "This frame, metered: " + " · ".join(
            f"{k.upper()} {stops_text(self.stops[k])}" for k in keys) + " stops"


def frame_plan(entry, level, shutters, isos, live=False):
    """One colour: the exposure that brings a frame whose thinnest part reads
    `level` at the film base's exposure (`entry`) to the target."""
    e0 = be.exposure_of(entry["shutter"], entry["iso"])
    if not e0 or level is None or level <= 0:
        return None
    rules = be.default_rules(isos)
    if live:
        rules["target"] = be.TARGET * 2 ** (-LIVE_MARGIN_STOPS)
    plan = be.plan_exposure(float(level) / e0, shutters, isos, rules)
    note = ""
    if plan.dim < 1.0:
        note = "at the shortest speed: brighter than the base inside the guide"
    if plan.short:
        note = "at the longest speed the rules allow"
    e1 = be.exposure_of(plan.shutter, plan.iso)
    return {"shutter": plan.shutter, "iso": plan.iso,
            "levels": dict(entry["levels"]) if entry.get("levels") else None,
            "stops": math.log2(e1 / e0) if e1 else 0.0, "note": note}


def suggest(base, trichrome, levels, shutters, isos, live=False):
    """A Suggestion for each colour of the scan type from the frame's
    thinnest `levels` {colour: share} at the film base's exposures; None
    when the film base wasn't measured for this scan type."""
    if be.exposures_for(base, trichrome) is None:
        return None
    keys = be.COLOURS if trichrome else ("w",)
    cols = base["colours"]
    out = Suggestion(live=bool(live))
    for k in keys:
        p = frame_plan(cols[k], levels.get(k), shutters, isos, live)
        if p is None:
            return None
        out.exposures[k] = {"shutter": p["shutter"], "iso": p["iso"], "levels": p["levels"]}
        out.stops[k] = p["stops"]
        if p["note"]:
            out.notes.append(f"{be.NAMES.get(k, k)}: {p['note']}")
    return out


def live_levels(base, trichrome, ratio):
    """The frame's thinnest level per colour from a live estimate: the film
    base's reading times one ratio for all colours."""
    keys = be.COLOURS if trichrome else ("w",)
    cols = (base or {}).get("colours") or {}
    return {k: float(cols[k]["base"]) * float(ratio) for k in keys if k in cols}


class FrameMetering:
    """Meter: one RAW test shot per colour at the film base's exposures, in
    `base_exposure.Calibration`'s shape (`next_shot`, `landed`, `failed`,
    `cancel`, `done`, `error`, `shots`, `result` {colour: level})."""

    def __init__(self, base, trichrome):
        self.exposures = be.exposures_for(base, trichrome)
        self.order = list(be.COLOURS if trichrome else ("w",))
        self.levels = {}
        self.clipped = []
        self.shots = 0
        self.done = False
        self.error = None
        self.result = None
        self.notes = []
        self._current = None
        if not self.exposures:
            self.done = True
            self.error = "Measure the film base for this scan type first."

    def next_shot(self):
        if self.done:
            return None
        for c in self.order:
            if c not in self.levels:
                self._current = c
                e = self.exposures[c]
                return c, e.get("levels"), {"shutter": e["shutter"], "iso": e["iso"]}
        self.done = True
        self.result = dict(self.levels)
        return None

    def landed(self, reading):
        c = self._current
        if c is None or self.done:
            return
        level = reading.level(c) if reading.levels else 0.0
        if level <= 0:
            self.failed("The test shot had nothing inside the frame guide.")
            return
        if level >= be.CLIP_TRUST:
            self.clipped.append(c)
        self.levels[c] = level
        self.shots += 1
        self._current = None

    def failed(self, text):
        self.done = True
        self.error = text

    def cancel(self):
        if not self.done:
            self.done = True
            self.error = "cancelled"
