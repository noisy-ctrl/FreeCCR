"""Dust found in an infrared shot of the frame (spec/ir-dust-and-flat-sets.md).

Dust and hair block infrared; colour film's dyes and chromogenic B&W pass most
of it, so in an IR shot the picture all but disappears and the dust stays. The
camera's IR-cut filter leaves the IR shot ~15 stops down and noisy, the lens
throws an IR hotspot, and the dye still shows at ~16-24 % of its white-light
density (measured on the user's XP2, 2026-11-19). So:

1. both RAWs are read as 2x2 photosite blocks (no demosaic, camera-native
   linear; the block grid is the frame's own decode grid, so a block's
   normalised position is the frame's);
2. the IR is registered to the visible shot (phase correlation);
3. the picture is divided out: ln IR -= sum_c k_c ln V_c, k fitted per frame
   by least squares on high-passed logs (the dye's IR shadow is a power of its
   visible one);
4. a grey closing (disc of `radius` blocks) gives the local background with
   anything smaller than the disc taken out, so d = ln IR - background is ~0
   on clean film and negative at dust; the hotspot and the fall-off are far
   larger than the disc and cancel;
5. d is divided by a local noise map (the hotspot leaves the centre less noisy
   than the corners), seeds deeper than Sensitivity's sigma are grown by
   hysteresis into half of it;
6. each component becomes a dust spot of kind "ir": compact ones a dab at the
   centroid, long ones (hair) a polyline along their length.

Pure numpy + OpenCV + rawpy; no Qt.
"""
from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

KIND = "ir"
RADIUS = 5                  # closing disc radius, blocks: dust larger is rare
SENS_DEFAULT = 50           # the Dust panel's IR Sensitivity, 0..100
SIGMA_AT_0 = 10.0           # seeds deeper than this many sigma at sensitivity 0
SIGMA_AT_100 = 4.0          # ... and this many at 100 (default 50 -> 7)
GROW = 0.5                  # hysteresis: grow seeds into GROW x the threshold
MAX_AREA_FRAC = 0.002       # a component over 0.2 % of the frame is picture
MAX_AREA_MIN = 1500         # ... but never under this many blocks (a long hair)
ELONGATED = 3.5             # length / width above this: a hair, a polyline
SPOT_SCALE = 1.35           # area-equivalent radius x this, + SPOT_PAD blocks:
SPOT_PAD = 1.0              # the threshold keeps the core, the heal needs the skirt
HP_SIGMA = 25.0             # high-pass for the crosstalk fit and registration
MAX_SHIFT = 6.0             # blocks: a larger registration answer is ignored
NOISE_SIGMA = 25.0          # smoothing of the local noise map

_cache_lock = threading.Lock()
_block_cache: Dict[Tuple[str, float], np.ndarray] = {}
_CACHE_MAX = 6


def sigma_for(sensitivity: float) -> float:
    s = max(0.0, min(100.0, float(sensitivity)))
    return SIGMA_AT_0 + (SIGMA_AT_100 - SIGMA_AT_0) * s / 100.0


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def blocks_from_mosaic(raw: np.ndarray, black: Sequence[float], colors: np.ndarray) -> np.ndarray:
    """2x2 photosite blocks: the mean of the block's photosites, each less its
    own black level (any 2x2 CFA; a monochrome sensor too)."""
    h, w = raw.shape[:2]
    h -= h % 2
    w -= w % 2
    a = raw[:h, :w].astype(np.float32)
    bl = np.asarray(black, np.float32)
    c = colors[:h, :w]
    a -= bl[np.clip(c, 0, len(bl) - 1)]
    return (a[0::2, 0::2] + a[0::2, 1::2] + a[1::2, 0::2] + a[1::2, 1::2]) * 0.25


def read_blocks(path: str) -> np.ndarray:
    """A RAW's 2x2 blocks (float32, black subtracted), cached by path+mtime."""
    key = (os.path.normpath(path), os.path.getmtime(path))
    with _cache_lock:
        hit = _block_cache.get(key)
    if hit is not None:
        return hit
    import rawpy
    with rawpy.imread(path) as r:
        b = blocks_from_mosaic(r.raw_image_visible, r.black_level_per_channel,
                               r.raw_colors_visible)
    with _cache_lock:
        _block_cache[key] = b
        while len(_block_cache) > _CACHE_MAX:
            _block_cache.pop(next(iter(_block_cache)))
    return b


# --------------------------------------------------------------------------- #
# the dust map
# --------------------------------------------------------------------------- #
def _log(a: np.ndarray) -> np.ndarray:
    return np.log(np.maximum(a, 0.5)).astype(np.float32)


def smooth_field(a: np.ndarray, sigma: float) -> np.ndarray:
    """A wide Gaussian blur done at a quarter of the size: the fields it makes
    (backgrounds, noise levels) vary over tens of blocks, far slower than the
    sampling, and a full-size sigma-25 blur took a second each."""
    f = 4 if sigma >= 8 and min(a.shape[:2]) >= 64 else 1
    if f == 1:
        return cv2.GaussianBlur(a, (0, 0), sigma)
    h, w = a.shape[:2]
    small = cv2.resize(a, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / f)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _highpass(a: np.ndarray) -> np.ndarray:
    return a - smooth_field(a, HP_SIGMA)


def register(ir_log: np.ndarray, vis_log: np.ndarray) -> Tuple[float, float]:
    """(dx, dy) that moves the IR onto the visible frame, in blocks. Mirror
    slap or a knock between the shots; measured < 1 block on the user's rig."""
    h, w = vis_log.shape
    n = 1024 if min(h, w) >= 1300 else 1 << int(math.log2(max(64, min(h, w) * 0.7)))
    y0, x0 = (h - n) // 2, (w - n) // 2           # the middle: no carrier edges
    a = vis_log[y0:y0 + n, x0:x0 + n]
    b = ir_log[y0:y0 + n, x0:x0 + n]
    a = cv2.GaussianBlur(a - cv2.GaussianBlur(a, (0, 0), 12), (0, 0), 1.5).astype(np.float64)
    b = cv2.GaussianBlur(b - cv2.GaussianBlur(b, (0, 0), 12), (0, 0), 1.5).astype(np.float64)
    win = cv2.createHanningWindow((n, n), cv2.CV_64F)
    (dx, dy), resp = cv2.phaseCorrelate(a, b, win)
    if not np.isfinite(dx) or abs(dx) > MAX_SHIFT or abs(dy) > MAX_SHIFT or resp < 0.02:
        return 0.0, 0.0
    return float(-dx), float(-dy)


def fit_crosstalk(ir_log: np.ndarray, vis_logs: List[np.ndarray],
                  region: Optional[np.ndarray] = None) -> np.ndarray:
    """k per visible channel: ln IR's high-pass ~ sum_c k_c ln V_c's, least
    squares on every 3rd block (dust is too rare to steer it), k in 0..0.6."""
    if not vis_logs:
        return np.zeros(0, np.float32)
    y = _highpass(ir_log)[::3, ::3].ravel()
    X = np.stack([_highpass(v)[::3, ::3].ravel() for v in vis_logs], 1)
    ok = np.isfinite(y) & np.all(np.isfinite(X), 1)
    if region is not None:
        ok &= region[::3, ::3].ravel()
    y, X = y[ok], X[ok]
    if y.size < 100:
        return np.zeros(len(vis_logs), np.float32)
    # drop the extremes of the picture (clipped highlights, holder) once
    lo, hi = np.percentile(y, [1, 99])
    keep = (y > lo) & (y < hi)
    k, *_ = np.linalg.lstsq(X[keep], y[keep], rcond=None)
    return np.clip(k, 0.0, 0.6).astype(np.float32)


@dataclass
class DustMap:
    z: np.ndarray                    # d / local noise, float16 (H_b, W_b); <0 at dust
    k: List[float] = field(default_factory=list)
    shift: Tuple[float, float] = (0.0, 0.0)
    noise: float = 0.0               # median local noise (log units)
    size: Tuple[int, int] = (0, 0)   # (W_b, H_b)


def dust_map(ir: np.ndarray, visible: Optional[List[np.ndarray]] = None,
             flat: Optional[np.ndarray] = None, radius: int = RADIUS,
             region: Optional[np.ndarray] = None) -> DustMap:
    """The IR's dust as a z map: (ln IR - local background) / local noise,
    with the picture's crosstalk divided out when visible block maps are
    given (one per light: white, or a trichrome's three)."""
    L = _log(ir)
    if flat is not None:
        L = L - _log(cv2.resize(flat, (L.shape[1], L.shape[0]),
                                interpolation=cv2.INTER_LINEAR))
    vis_logs = [_log(v) for v in (visible or []) if v is not None and v.shape == ir.shape]
    shift = (0.0, 0.0)
    k = np.zeros(0, np.float32)
    if vis_logs:
        vsum = vis_logs[0] if len(vis_logs) == 1 else _log(sum(np.exp(v) for v in vis_logs))
        shift = register(L, vsum)
        if shift != (0.0, 0.0):
            m = np.float32([[1, 0, shift[0]], [0, 1, shift[1]]])
            L = cv2.warpAffine(L, m, (L.shape[1], L.shape[0]), flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REPLICATE)
        k = fit_crosstalk(L, vis_logs, region)
        for kc, v in zip(k, vis_logs):
            if kc:
                L = L - kc * v
    Ls = cv2.GaussianBlur(L, (0, 0), 1.0)
    el = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    bg = cv2.morphologyEx(Ls, cv2.MORPH_CLOSE, el)
    bg = cv2.GaussianBlur(bg, (0, 0), radius / 2.0)
    bg = np.maximum(bg, Ls)
    d = Ls - bg
    # The background is an upper envelope, so clean film sits a little below
    # 0: centre d on its local level, then divide by the local noise (the
    # hotspot leaves the middle less noisy than the corners). Both are taken
    # with dust clipped out, so a speck can't raise its own threshold.
    sub = d[::4, ::4]
    med = float(np.median(sub))
    mad = float(1.4826 * np.median(np.abs(sub - med))) or 1e-3
    lvl = smooth_field(np.clip(d, med - 3 * mad, med + 3 * mad), NOISE_SIGMA)
    dc = d - lvl
    d2 = np.minimum(dc * dc, (3.0 * mad) ** 2)
    sig = np.sqrt(np.maximum(smooth_field(d2, NOISE_SIGMA), 0)) * 1.09 + 1e-4
    z = (dc / sig).astype(np.float16)
    return DustMap(z=z, k=[float(x) for x in k], shift=shift,
                   noise=float(np.median(sig[::8, ::8])), size=(ir.shape[1], ir.shape[0]))


# --------------------------------------------------------------------------- #
# spots
# --------------------------------------------------------------------------- #
def _polyline(ys: np.ndarray, xs: np.ndarray, step: float) -> List[Tuple[float, float]]:
    """Points along a long component: slices across its main axis, each the
    mean of its pixels (follows a gently curved hair)."""
    pts = np.stack([xs, ys], 1).astype(np.float64)
    c = pts.mean(0)
    u = np.linalg.svd(pts - c, full_matrices=False)[2][0]
    t = (pts - c) @ u
    t0, t1 = float(t.min()), float(t.max())
    n = max(2, int(math.ceil((t1 - t0) / max(step, 1.0))) + 1)
    edges = np.linspace(t0, t1, n + 1)
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        sel = (t >= a) & (t <= b)
        if sel.any():
            out.append(tuple(pts[sel].mean(0)))
    return out


def spots_from(dm: DustMap, sensitivity: float = SENS_DEFAULT,
               keep: Optional[np.ndarray] = None) -> List[dict]:
    """Dust spots (kind "ir", normalised to the frame) at a sensitivity.
    `keep` (bool, block grid) limits seeds to the picture (crop)."""
    z = dm.z.astype(np.float32)
    w_b, h_b = dm.size
    t = sigma_for(sensitivity)
    seeds = z < -t
    if keep is not None:
        seeds &= keep
    if not seeds.any():
        return []
    grown = (z < -t * GROW).astype(np.uint8)
    n, lab, st, cen = cv2.connectedComponentsWithStats(grown, connectivity=8)
    hit = np.zeros(n, bool)
    hit[np.unique(lab[seeds])] = True
    hit[0] = False
    max_area = max(MAX_AREA_FRAC * w_b * h_b, MAX_AREA_MIN)
    spots = []
    for i in np.flatnonzero(hit):
        area = int(st[i, cv2.CC_STAT_AREA])
        if area > max_area:
            continue
        x0, y0, cw, ch = (int(st[i, j]) for j in (0, 1, 2, 3))
        ys, xs = np.nonzero(lab[y0:y0 + ch, x0:x0 + cw] == i)
        ys = ys + y0 + 0.5
        xs = xs + x0 + 0.5
        if area >= 6:
            ev = np.linalg.eigvalsh(np.cov(np.stack([xs, ys])))
            length = 4.0 * math.sqrt(max(ev[-1], 1e-6))
            width = max(1.0, area / max(length, 1.0))
        else:
            length, width = 1.0, 1.0
        if length / width > ELONGATED:
            r = width / 2.0 * SPOT_SCALE + SPOT_PAD
            pts = _polyline(ys, xs, step=r)
        else:
            r = math.sqrt(area / math.pi) * SPOT_SCALE + SPOT_PAD
            pts = [(float(cen[i][0]) + 0.5, float(cen[i][1]) + 0.5)]
        spots.append({"kind": KIND,
                      "pts": [[float(x) / w_b, float(y) / h_b] for x, y in pts],
                      "r": float(r) / w_b})
    return spots


def mask_from(dm: DustMap, sensitivity: float = SENS_DEFAULT,
              keep: Optional[np.ndarray] = None) -> np.ndarray:
    """The hysteresis mask itself (bool, block grid), for the overlay."""
    z = dm.z.astype(np.float32)
    t = sigma_for(sensitivity)
    seeds = z < -t
    if keep is not None:
        seeds &= keep
    grown = (z < -t * GROW).astype(np.uint8)
    n, lab = cv2.connectedComponents(grown, connectivity=8)
    hit = np.zeros(n, bool)
    hit[np.unique(lab[seeds])] = True
    hit[0] = False
    return hit[lab]


# --------------------------------------------------------------------------- #
# a frame
# --------------------------------------------------------------------------- #
def visible_paths(img) -> List[str]:
    """The frame's own shots: a merge's three sources, else its file."""
    srcs = list(getattr(img, "merge_sources", None) or [])
    if srcs:
        return srcs
    p = getattr(img, "file_path", None)
    return [p] if p else []


def can_find(img) -> Optional[str]:
    """None when the frame can be searched with IR, else why not."""
    ir = getattr(img, "ir_source", None)
    if not ir:
        return "No IR shot attached to this frame."
    if not os.path.exists(ir):
        return "The IR shot isn't there any more: " + os.path.basename(ir)
    if getattr(img, "source_ops", None):
        return "IR dust isn't found on slices yet."
    return None


def frame_dust_map(img, flat: Optional[np.ndarray] = None) -> DustMap:
    """The IR dust map of a frame (reads its IR shot and its own shots)."""
    ir = read_blocks(img.ir_source)
    vis = []
    for p in visible_paths(img):
        try:
            b = read_blocks(p)
        except Exception:
            continue
        if b.shape == ir.shape:
            vis.append(b)
    return dust_map(ir, vis, flat=flat)


def keep_mask_for(img, size: Tuple[int, int]) -> Optional[np.ndarray]:
    """The crop on the block grid (seeds only inside it; the carrier and the
    rebate's markings aren't dust). None with no crop."""
    rect = getattr(img, "crop_rect", None)
    if rect is None:
        return None
    raw = getattr(img, "resized_raw", None)
    if raw is None:
        return None
    from core.ccr_processor import _crop_keep_mask
    h, w = raw.shape[:2]
    m = _crop_keep_mask(tuple(rect), float(getattr(img, "crop_angle", 0.0) or 0.0), h, w)
    if m is None:
        return None
    return cv2.resize(m.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST) > 0


# --------------------------------------------------------------------------- #
# the frame's state (GUI-free; the Dust panel and the canvas call these)
# --------------------------------------------------------------------------- #
def attach(img, path: Optional[str]) -> None:
    """Give a frame its IR shot (None takes it away). Earlier findings go."""
    img.ir_source = os.path.normpath(path) if path else None
    img.ir_dust_pending = None
    img._ir_dust_map = None


def find(img, sensitivity: Optional[float] = None, flat=None) -> List[dict]:
    """Find the frame's IR dust and flag it (img.ir_dust_pending), not healed.
    The map is kept in memory so a new Sensitivity needs no re-read."""
    why = can_find(img)
    if why:
        raise ValueError(why)
    dm = getattr(img, "_ir_dust_map", None)
    if dm is None or getattr(img, "_ir_dust_map_src", None) != img.ir_source:
        dm = frame_dust_map(img, flat=flat)
        img._ir_dust_map = dm
        img._ir_dust_map_src = img.ir_source
    return rethreshold(img, sensitivity)


def rethreshold(img, sensitivity: Optional[float] = None) -> List[dict]:
    """Flag the cached map's dust again at a sensitivity (no map: [])."""
    dm = getattr(img, "_ir_dust_map", None)
    sens = SENS_DEFAULT if sensitivity is None else int(sensitivity)
    img.ir_dust_sens = sens
    if dm is None:
        return list(img.ir_dust_pending or [])
    spots = spots_from(dm, sens, keep_mask_for(img, dm.size))
    img.ir_dust_pending = spots
    return spots


def pending(img) -> List[dict]:
    return list(getattr(img, "ir_dust_pending", None) or [])


def heal(img) -> int:
    """Move the flagged spots into the frame's dust spots (replacing earlier
    IR spots; hand and AI spots stay). The caller takes the undo snapshot and
    re-renders. Returns how many."""
    new = pending(img)
    img.dust_spots = [s for s in (img.dust_spots or []) if s.get("kind") != KIND] + new
    img.ir_dust_pending = None
    return len(new)


def _touches(spot: dict, pts: List[List[float]], r: float, aspect: float) -> bool:
    rs = float(spot.get("r", 0.0))
    for sx, sy in spot.get("pts") or []:
        for px, py in pts:
            dx = sx - px
            dy = (sy - py) * aspect
            if dx * dx + dy * dy <= (r + 0.5 * rs) ** 2:
                return True
    return False


def erase(img, pts: List[List[float]], r: float, aspect: float) -> Tuple[int, int]:
    """Remove every spot under an eraser stroke (normalised points, radius as
    a fraction of the width, aspect = height / width): from the healed spots
    (any kind) and from the flagged IR ones. Returns (healed, flagged)."""
    spots = list(getattr(img, "dust_spots", None) or [])
    kept = [s for s in spots if not _touches(s, pts, r, aspect)]
    flagged = pending(img)
    kept_f = [s for s in flagged if not _touches(s, pts, r, aspect)]
    n_h, n_f = len(spots) - len(kept), len(flagged) - len(kept_f)
    if n_h:
        img.dust_spots = kept
    if n_f:
        img.ir_dust_pending = kept_f or None
    return n_h, n_f
