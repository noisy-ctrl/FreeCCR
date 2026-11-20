"""Marks the IR can't see, found in the frame's own shots
(spec/colour-shot-marks.md, the user 2026-11-20: "A finder that works from the
colour shots, for marks the IR can't see").

Two kinds, both on the RAW's 2x2 blocks (camera-native, linear, no picture
rendering), as a residual against the local background in log units divided
by the local noise (the grain), exactly as IR dust's maps:

- **light** marks: lighter on the negative than round them, so they print
  dark: emulsion damage, pinholes, a missing dye (the user's brown mark on
  002). Read as the deepest of the shots (a mark may have lost one dye).
- **dark** marks: darker on the negative, so they print white: dust and
  fibres the IR doesn't show (a translucent fibre; specks too small for it).
  Read as the mean of the shots (dust is grey). Where the IR has already
  flagged a speck, it isn't flagged again here.

What separates a mark from the picture is that it STANDS OUT from where it
lies: its own edge is far steeper than anything in a ring round it (the
picture's detail is softened by the taking lens; a mark on the film is as
sharp as the scan), and the ring is even (a picture's edge is dark on one
side). Pure numpy + OpenCV; no Qt.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from core import ir_dust

SEED = 7.0            # a mark is deeper than this (z) somewhere ...
GROW = 3.0            # ... and is everything joined to that deeper than this
MAX_AREA = 1500       # blocks: a larger one is picture
RING_IN, RING_OUT = 3, 8
HALO = 4              # blocks round dark dust where no light mark is looked for
MAX_LENGTH = 60       # blocks: a longer light line is the picture's (a wire, a frame)
KINDS = ("light", "dark")
SPOT_KIND = "mark"    # a dust spot found here (healed like "auto")
SENS_DEFAULT = 50
STEP_MAX = 0.5        # the ring's two sides differ by at most this share of the mark's contrast
AREA_MAX = 800        # blocks flagged at most
LIGHT_IR_MAX = 2.5    # a prints-dark mark this dark in the IR is dust's halo
DARK_IR_MIN = 1.8     # a prints-white mark needs this much IR support (with an IR shot)
FEATURES = ("depth", "stands out", "ring step", "log area", "elongation",
            "IR support", "colour")


@dataclass
class Mark:
    kind: str                       # "light" | "dark"
    x0: int
    y0: int
    mask: np.ndarray
    feats: np.ndarray               # FEATURES
    seed: Tuple[int, int]

    def centre(self) -> Tuple[float, float]:
        ys, xs = np.nonzero(self.mask)
        return self.x0 + float(xs.mean()) + 0.5, self.y0 + float(ys.mean()) + 0.5


@dataclass
class MarkMap:
    marks: List[Mark] = field(default_factory=list)
    size: Tuple[int, int] = (0, 0)  # (W_b, H_b)
    shots: int = 0
    has_ir: bool = False


def signed_residual(L: np.ndarray, radius: int = ir_dust.RADIUS):
    """(d, sig): L less a background that neither bright nor dark specks
    move (the mean of open-close and close-open, so a bright mark doesn't
    make a dark ring round itself as IR dust's upper envelope would), centred
    on its local level, and the local noise. d < 0 darker, > 0 lighter."""
    Ls = cv2.GaussianBlur(L, (0, 0), 1.0)
    el = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    oc = cv2.morphologyEx(cv2.morphologyEx(Ls, cv2.MORPH_OPEN, el), cv2.MORPH_CLOSE, el)
    co = cv2.morphologyEx(cv2.morphologyEx(Ls, cv2.MORPH_CLOSE, el), cv2.MORPH_OPEN, el)
    bg = cv2.GaussianBlur(0.5 * (oc + co), (0, 0), radius / 2.0)
    d = Ls - bg
    sub = d[::4, ::4]
    med = float(np.median(sub))
    mad = float(1.4826 * np.median(np.abs(sub - med))) or 1e-3
    lvl = ir_dust.smooth_field(np.clip(d, med - 3 * mad, med + 3 * mad), ir_dust.NOISE_SIGMA)
    dc = (d - lvl).astype(np.float32)
    d2 = np.minimum(dc * dc, (3.0 * mad) ** 2)
    sig = (np.sqrt(np.maximum(ir_dust.smooth_field(d2, ir_dust.NOISE_SIGMA), 0)) + 1e-4).astype(np.float32)
    return dc, sig


def _ring(m: np.ndarray):
    """(outer, inner) dilations of a mask padded by RING_OUT."""
    h, w = m.shape
    can = np.zeros((h + 2 * RING_OUT, w + 2 * RING_OUT), np.uint8)
    can[RING_OUT:RING_OUT + h, RING_OUT:RING_OUT + w] = m
    outer = cv2.dilate(can, np.ones((2 * RING_OUT + 1,) * 2, np.uint8)) > 0
    inner = cv2.dilate(can, np.ones((2 * RING_IN + 1,) * 2, np.uint8)) > 0
    return outer & ~inner


def find_marks(visible: Sequence[np.ndarray], ir_z: Optional[np.ndarray] = None,
               ir_flagged: Optional[np.ndarray] = None,
               kinds: Sequence[str] = KINDS) -> MarkMap:
    """Every possible mark of each kind with its features. `visible` are the
    frame's own shots' blocks (one, or a trichrome's three), `ir_z` the IR
    dust map's z on the same grid (None: no IR shot), `ir_flagged` (bool) the
    blocks the IR finder has flagged (dark marks there are left to it)."""
    vl = [ir_dust._log(v) for v in visible]
    H, W = vl[0].shape
    vsum = vl[0] if len(vl) == 1 else ir_dust._log(sum(np.exp(v) for v in vl))
    g = cv2.GaussianBlur(vsum, (0, 0), 1.0)
    grad = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0) / 8, cv2.Sobel(g, cv2.CV_32F, 0, 1) / 8)
    out = MarkMap(size=(W, H), shots=len(vl), has_ir=ir_z is not None)
    # a dust speck's bright halo on the negative isn't a mark: no light mark
    # within HALO blocks of IR-flagged dust or of a deep dark mark
    res = [signed_residual(v) for v in vl]
    zd = [d / sg for d, sg in res]                         # < 0 darker on the negative
    z_dark = zd[0] if len(zd) == 1 else sum(zd) / len(zd)
    dark_any = np.zeros((H, W), bool)
    if ir_flagged is not None:
        dark_any |= ir_flagged
    dark_any |= z_dark < -SEED
    near_dark = cv2.dilate(dark_any.astype(np.uint8), np.ones((2 * HALO + 1,) * 2, np.uint8)) > 0
    ir_near = (cv2.dilate(ir_flagged.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
               if ir_flagged is not None else None)
    for kind in kinds:
        zs = zd if kind == "dark" else [-z for z in zd]
        if len(zs) == 1:
            z = zs[0]
        elif kind == "light":
            z = np.minimum.reduce(zs)
        else:
            z = z_dark
        seeds = z < -SEED
        if kind == "light":
            seeds &= ~near_dark
        if kind == "dark" and ir_flagged is not None:
            seeds &= ~cv2.dilate(ir_flagged.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
        if not seeds.any():
            continue
        grown = (z < -GROW).astype(np.uint8)
        n, lab, st, _c = cv2.connectedComponentsWithStats(grown, connectivity=8)
        hit = np.zeros(n, bool)
        hit[np.unique(lab[seeds])] = True
        hit[0] = False
        for i in np.flatnonzero(hit):
            x0, y0, cw, ch, area = (int(st[i, j]) for j in range(5))
            if area > MAX_AREA:
                continue
            yy0, xx0 = y0 - RING_OUT, x0 - RING_OUT
            if yy0 < 0 or xx0 < 0 or y0 + ch + RING_OUT > H or x0 + cw + RING_OUT > W:
                continue
            m = lab[y0:y0 + ch, x0:x0 + cw] == i
            if kind == "dark" and ir_near is not None and \
                    ir_near[y0:y0 + ch, x0:x0 + cw][m].mean() >= 0.3:
                continue                         # the IR finder has this one
            ring = _ring(m)
            box = (slice(yy0, y0 + ch + RING_OUT), slice(xx0, x0 + cw + RING_OUT))
            m_d = cv2.dilate(m.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
            g_in = float(grad[y0:y0 + ch, x0:x0 + cw][m_d].max())
            g_ring = float(np.percentile(grad[box][ring], 90))
            stand = g_in / (g_ring + 1e-6)
            # the ring's two halves across the mark's long axis: a picture's
            # edge is dark on one side
            ys, xs = np.nonzero(m)
            cx, cy = xs.mean() + RING_OUT, ys.mean() + RING_OUT
            if len(xs) >= 3:
                ev, evec = np.linalg.eigh(np.cov(np.stack([xs, ys]).astype(np.float64)) + 1e-6 * np.eye(2))
                u = evec[:, -1]
                length = 4.0 * math.sqrt(max(ev[-1], 1e-6))
                elong = length / max(1.0, len(xs) / max(length, 1.0))
            else:
                u = np.array([1.0, 0.0])
                elong = 1.0
            ry, rx = np.nonzero(ring)
            side = (rx - cx) * (-u[1]) + (ry - cy) * u[0]
            vals = vsum[box][ring]
            a, b = vals[side > 0], vals[side <= 0]
            # as a share of the mark's own contrast (log): an edge's two sides
            # differ by about as much as the "mark" along it stands out
            dmean = res[0][0] if len(res) == 1 else sum(r[0] for r in res) / len(res)
            contrast = float(np.abs(dmean[y0:y0 + ch, x0:x0 + cw][m]).max()) + 1e-3
            step = (abs(float(np.median(a)) - float(np.median(b))) / contrast
                    if len(a) and len(b) else 0.0)
            depth = float(-z[y0:y0 + ch, x0:x0 + cw][m].min())
            irs = 0.0
            if ir_z is not None:
                zz = ir_z[y0:y0 + ch, x0:x0 + cw][m].astype(np.float32)
                irs = float(-zz.sum() / math.sqrt(len(zz) * 12.6))   # + = darker in IR
            colour = 0.0
            if len(zs) == 3:
                d = [float(zz_[y0:y0 + ch, x0:x0 + cw][m].mean()) for zz_ in zs]
                colour = float(np.std(d) / (abs(np.mean(d)) + 1.0))
            k = int(np.argmin(np.where(m, z[y0:y0 + ch, x0:x0 + cw], np.inf)))
            feats = np.array([depth, stand, step, math.log(area), elong, irs, colour], np.float32)
            out.marks.append(Mark(kind=kind, x0=x0, y0=y0, mask=m, feats=feats,
                                  seed=(x0 + k % cw, y0 + k // cw)))
    return out


def thresholds(sensitivity: float) -> Tuple[float, float]:
    """(depth, stands out) a mark needs at a Sensitivity: 11 / 9 at 0,
    8 / 7 at 50, 6 / 5.5 at 100, linear either side of 50."""
    t = max(0.0, min(100.0, float(sensitivity)))
    if t <= 50:
        u = t / 50.0
        return 11.0 + (8.0 - 11.0) * u, 9.0 + (7.0 - 9.0) * u
    u = (t - 50) / 50.0
    return 8.0 + (6.0 - 8.0) * u, 7.0 + (5.5 - 7.0) * u


def passes(mk: Mark, sensitivity: float = SENS_DEFAULT, has_ir: bool = False) -> bool:
    """The test (spec §2): deep, standing out from an even ring, small; a
    prints-dark mark not a long line and not dark in the IR (dust's halo);
    a prints-white one, when there is an IR shot, a little darker in it."""
    depth, stand, step, larea, _elong, irs, _colour = (float(v) for v in mk.feats)
    need_d, need_s = thresholds(sensitivity)
    if depth < need_d or stand < need_s or step > STEP_MAX or larea > math.log(AREA_MAX):
        return False
    if mk.kind == "light":
        ys, xs = np.nonzero(mk.mask)
        if max(np.ptp(xs), np.ptp(ys)) + 1 > MAX_LENGTH or irs > LIGHT_IR_MAX:
            return False
    elif has_ir and irs < DARK_IR_MIN:
        return False
    return True


def likely(mk: Mark) -> bool:
    """The draft's test at the default (kept for the draft tools)."""
    return passes(mk, SENS_DEFAULT, has_ir=False)


def flagged(mm: MarkMap, sensitivity: float = SENS_DEFAULT,
            keep: Optional[np.ndarray] = None) -> List[int]:
    """The marks flagged at a Sensitivity, their deepest block inside `keep`."""
    out = []
    for i, mk in enumerate(mm.marks):
        if keep is not None and not keep[mk.seed[1], mk.seed[0]]:
            continue
        if passes(mk, sensitivity, mm.has_ir):
            out.append(i)
    return out


def spots_from(mm: MarkMap, sensitivity: float = SENS_DEFAULT,
               keep: Optional[np.ndarray] = None) -> List[dict]:
    """The flagged marks as dust spots of kind "mark" (a dab, or strokes
    along the skeleton, as IR dust's), carrying their features."""
    w_b, h_b = mm.size
    spots: List[dict] = []
    for i in flagged(mm, sensitivity, keep):
        mk = mm.marks[i]
        ys, xs = np.nonzero(mk.mask)
        if len(xs) >= 3:
            ev = np.linalg.eigvalsh(np.cov(np.stack([xs, ys]).astype(np.float64)) + 1e-6 * np.eye(2))
            length = 4.0 * math.sqrt(max(ev[-1], 1e-6))
            width = max(1.0, len(xs) / max(length, 1.0))
        else:
            length = width = 1.0
        c = ir_dust.Candidate(x0=mk.x0, y0=mk.y0, mask=mk.mask, feats=mk.feats, seed=mk.seed,
                              line_like=False, length=length, width=width)
        for sp in ir_dust._spots_of(c, i, w_b, h_b):
            sp["kind"] = SPOT_KIND
            sp["mk_f"] = sp.pop("ir_f")
            sp["mk_c"] = sp.pop("ir_c")
            sp["mk_k"] = mk.kind
            spots.append(sp)
    return spots


# --------------------------------------------------------------------------- #
# a frame (GUI-free; the Dust panel calls these). Flagged marks share the
# frame's pending list with IR dust (img.ir_dust_pending), kind "mark".
# --------------------------------------------------------------------------- #
RAW_EXT = (".raf", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".pef",
           ".3fr", ".iiq", ".srw", ".erf", ".kdc", ".mrw", ".nrw", ".rwl", ".x3f")


def can_find(img) -> Optional[str]:
    """None when the frame's own shots can be searched for marks, else why
    not (they must be RAW files that are still there)."""
    import os
    paths = ir_dust.visible_paths(img)
    if not paths:
        return "No shot to look at."
    if getattr(img, "source_ops", None):
        return "Marks aren't found on slices yet."
    if any(os.path.splitext(p)[1].lower() not in RAW_EXT for p in paths):
        return "Marks are found in RAW shots; this frame isn't one."
    for p in paths:
        if not os.path.exists(p):
            return "The frame's shot isn't there any more: " + os.path.basename(p)
    return None


def frame_mark_map(img) -> MarkMap:
    """The marks in a frame's own shots, leaving out what its IR finder has
    flagged (when it has looked)."""
    vis = [ir_dust.read_blocks(p) for p in ir_dust.visible_paths(img)]
    shape = vis[0].shape
    vis = [v for v in vis if v.shape == shape]
    dm = getattr(img, "_ir_dust_map", None)
    ir_z = fl = None
    if dm is not None and tuple(dm.z.shape) == tuple(shape):
        ir_z = dm.z.astype(np.float32)
        fl = np.zeros(shape, bool)
        sens = getattr(img, "ir_dust_sens", None)
        keep = ir_dust.keep_mask_for(img, dm.size)
        for i in ir_dust.flagged(dm, ir_dust.SENS_DEFAULT if sens is None else sens, keep):
            c = dm.cands[i]
            fl[c.y0:c.y0 + c.mask.shape[0], c.x0:c.x0 + c.mask.shape[1]] |= c.mask
    return find_marks(vis, ir_z, fl)


def find(img, sensitivity: Optional[float] = None) -> List[dict]:
    """Find the frame's marks and flag them (pending, not healed); the map is
    kept in memory so a new Marks sensitivity needs no re-read."""
    why = can_find(img)
    if why:
        raise ValueError(why)
    img._mark_map = frame_mark_map(img)
    return rethreshold(img, sensitivity)


def rethreshold(img, sensitivity: Optional[float] = None) -> List[dict]:
    """Flag the cached map's marks again at a Sensitivity, keeping the IR's
    flagged dust; with no map the pending marks stay as they are."""
    mm = getattr(img, "_mark_map", None)
    sens = SENS_DEFAULT if sensitivity is None else int(sensitivity)
    img.mark_sens = sens
    pending = list(getattr(img, "ir_dust_pending", None) or [])
    if mm is None:
        return [s for s in pending if s.get("kind") == SPOT_KIND]
    spots = spots_from(mm, sens, ir_dust.keep_mask_for(img, mm.size))
    rest = [s for s in pending if s.get("kind") != SPOT_KIND]
    img.ir_dust_pending = (rest + spots) or None
    return spots


def examples_from(spots: List[dict], label: int, weight: float):
    """{kind: [(features, label, weight)]} for marks, one per mark."""
    out, seen = {}, set()
    for s in spots:
        if s.get("kind") != SPOT_KIND or not s.get("mk_f"):
            continue
        key = (s.get("mk_c"), tuple(s["mk_f"]))
        if key in seen:
            continue
        seen.add(key)
        out.setdefault("marks-" + str(s.get("mk_k", "dark")), []).append((s["mk_f"], label, weight))
    return out
