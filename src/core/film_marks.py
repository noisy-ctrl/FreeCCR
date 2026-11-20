"""Marks the IR can't see, found in the frame's own shots (DRAFT,
spec/colour-shot-marks.md, the user 2026-11-20: "A finder that works from the
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
    out = MarkMap(size=(W, H), shots=len(vl))
    # a dust speck's bright halo on the negative isn't a mark: no light mark
    # within HALO blocks of IR-flagged dust or of a deep dark mark
    dark_any = np.zeros((H, W), bool)
    if ir_flagged is not None:
        dark_any |= ir_flagged
    if "light" in kinds:
        zd = [d / sg for d, sg in (ir_dust._residual(v) for v in vl)]
        dark_any |= (sum(zd) / len(zd)) < -SEED
    near_dark = cv2.dilate(dark_any.astype(np.uint8), np.ones((2 * HALO + 1,) * 2, np.uint8)) > 0
    for kind in kinds:
        s = -1.0 if kind == "light" else 1.0
        res = [ir_dust._residual(s * v) for v in vl]
        zs = [d / sg for d, sg in res]
        if len(zs) == 1:
            z = zs[0]
        elif kind == "light":
            z = np.minimum.reduce(zs)
        else:
            z = sum(zs) / len(zs)
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
            noise = float(np.median(res[0][1][box])) or 1e-3
            step = abs(float(np.median(a)) - float(np.median(b))) / noise if len(a) and len(b) else 0.0
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


def likely(mk: Mark) -> bool:
    """The draft's hand-set test (to be replaced by a fitted model): deep,
    standing out from an even ring, small, and for a light mark not dark in
    the IR (that is dust's halo, the IR finder has it)."""
    depth, stand, step, larea, elong, irs, _colour = (float(v) for v in mk.feats)
    if depth < 8.0 or stand < 7.0 or step > 4.0 or larea > math.log(800):
        return False
    ys, xs = np.nonzero(mk.mask)
    if mk.kind == "light" and max(np.ptp(xs), np.ptp(ys)) + 1 > MAX_LENGTH:
        return False
    if mk.kind == "light" and irs > 2.5:
        return False
    return True
