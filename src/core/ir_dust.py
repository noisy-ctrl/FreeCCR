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
# aligning the IR shot with the frame (spec/ir-dust-and-flat-sets.md §6):
# phase correlation in a 3 x 3 grid of tiles, a similarity fitted to them
ALIGN_SPOTS = (0.2, 0.5, 0.8)       # tile centres, share of the width
ALIGN_SPOTS_Y = (0.22, 0.5, 0.78)   # ... and of the height
ALIGN_MIN_RESP = 0.05      # a tile's correlation peak under this says nothing
ALIGN_RESID = 1.5          # blocks: a tile further than this from the fit is out
ALIGN_MAX_SCALE = 0.03     # |scale - 1| over this: not the same shot
ALIGN_MAX_TURN = 1.5       # degrees
ALIGN_MIN_TILES = 4

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


@dataclass
class Alignment:
    """How the IR shot was moved onto the frame: `matrix` (2 x 3, IR block
    -> frame block), its scale, turn (degrees) and shift at the centre
    (blocks), the tiles that agreed, and `how` ("similarity", "shift" or
    "none": nothing agreed, the shot is used as it is)."""
    matrix: List[List[float]]
    scale: float = 1.0
    turn: float = 0.0
    shift: Tuple[float, float] = (0.0, 0.0)
    tiles: int = 0
    how: str = "none"

    def moved(self) -> bool:
        return (abs(self.scale - 1) > 2e-4 or abs(self.turn) > 0.01
                or math.hypot(*self.shift) > 0.05)


IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]


def _tile_size(h, w):
    if min(h, w) >= 1700:
        return 512
    return max(32, 1 << int(math.log2(max(32, min(h, w) * 0.32))))


def tile_shifts(ir_log: np.ndarray, vis_log: np.ndarray):
    """[(x, y, dx, dy, response)]: per tile of a 3 x 3 grid, the move (blocks)
    that puts the IR's content on the frame's there."""
    h, w = vis_log.shape
    n = _tile_size(h, w)
    win = cv2.createHanningWindow((n, n), cv2.CV_64F)
    out = []
    for fy in ALIGN_SPOTS_Y:
        for fx in ALIGN_SPOTS:
            y0 = int(min(max(0, fy * h - n / 2), h - n))
            x0 = int(min(max(0, fx * w - n / 2), w - n))
            a = vis_log[y0:y0 + n, x0:x0 + n]
            b = ir_log[y0:y0 + n, x0:x0 + n]
            a = cv2.GaussianBlur(a - cv2.GaussianBlur(a, (0, 0), 12), (0, 0), 1.5)
            b = cv2.GaussianBlur(b - cv2.GaussianBlur(b, (0, 0), 12), (0, 0), 1.5)
            (dx, dy), resp = cv2.phaseCorrelate(a.astype(np.float64), b.astype(np.float64),
                                                win)
            if not (np.isfinite(dx) and np.isfinite(dy)) or max(abs(dx), abs(dy)) > n / 4:
                resp = 0.0
            out.append((x0 + n / 2, y0 + n / 2, float(-dx), float(-dy), float(resp)))
    return out


def fit_similarity(tiles, w, h):
    """(a, b, tx, ty, tiles used) of the move d(q) = [a -b; b a](q - c) + t
    through the tiles that agree (the worst dropped while it is further than
    ALIGN_RESID), or None."""
    pts = [(x - w / 2, y - h / 2, dx, dy) for x, y, dx, dy, r in tiles if r >= ALIGN_MIN_RESP]
    keep = list(range(len(pts)))
    while len(keep) >= ALIGN_MIN_TILES:
        A, rhs = [], []
        for i in keep:
            x, y, dx, dy = pts[i]
            A.append([x, -y, 1.0, 0.0])
            rhs.append(dx)
            A.append([y, x, 0.0, 1.0])
            rhs.append(dy)
        p, *_ = np.linalg.lstsq(np.array(A), np.array(rhs), rcond=None)
        res = [math.hypot(p[0] * x - p[1] * y + p[2] - dx, p[1] * x + p[0] * y + p[3] - dy)
               for x, y, dx, dy in (pts[i] for i in keep)]
        worst = int(np.argmax(res))
        if res[worst] <= ALIGN_RESID:
            return float(p[0]), float(p[1]), float(p[2]), float(p[3]), len(keep)
        keep.pop(worst)
    return None


def _similarity_matrix(a, b, tx, ty, w, h):
    cx, cy = w / 2.0, h / 2.0
    return np.array([[1 + a, -b, tx - a * cx + b * cy],
                     [b, 1 + a, ty - b * cx - a * cy]], np.float64)


def _shift_only(tiles):
    """The median move of the tiles that agree within ALIGN_RESID, or None."""
    good = [(dx, dy) for _x, _y, dx, dy, r in tiles if r >= ALIGN_MIN_RESP]
    if len(good) < 2:
        return None
    mx = float(np.median([g[0] for g in good]))
    my = float(np.median([g[1] for g in good]))
    near = [g for g in good if math.hypot(g[0] - mx, g[1] - my) <= ALIGN_RESID]
    return (mx, my, len(near)) if len(near) >= 2 else None


def align(ir_log: np.ndarray, vis_log: np.ndarray) -> Alignment:
    """The IR shot moved onto the frame: a similarity (shift, scale from a
    focus or rail move, a small turn) fitted to 3 x 3 tiles' phase
    correlation, measured again after the first move and composed."""
    h, w = vis_log.shape
    total = np.eye(3)
    used, how = 0, "none"
    cur = ir_log
    for _round in range(2):
        tiles = tile_shifts(cur, vis_log)
        fit = fit_similarity(tiles, w, h)
        if fit is not None:
            a, b, tx, ty, used = fit
            step = _similarity_matrix(a, b, tx, ty, w, h)
            how = "similarity"
        else:
            sh = _shift_only(tiles)
            if sh is None:
                break
            step = np.array([[1, 0, sh[0]], [0, 1, sh[1]]], np.float64)
            used = sh[2]
            how = how if how == "similarity" else "shift"
        total = np.vstack([step, [0, 0, 1]]) @ total
        cur = cv2.warpAffine(ir_log, total[:2].astype(np.float32), (w, h),
                             flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    m = total[:2]
    scale = math.hypot(m[0, 0], m[1, 0])
    turn = math.degrees(math.atan2(m[1, 0], m[0, 0]))
    centre = m @ np.array([w / 2.0, h / 2.0, 1.0]) - np.array([w / 2.0, h / 2.0])
    if how == "none" or abs(scale - 1) > ALIGN_MAX_SCALE or abs(turn) > ALIGN_MAX_TURN:
        return Alignment(IDENTITY, tiles=used, how="none")
    return Alignment(m.tolist(), scale=scale, turn=turn,
                     shift=(float(centre[0]), float(centre[1])), tiles=used, how=how)


def align_text(al: Optional["Alignment"]) -> str:
    """'' when the IR shot sat on the frame, else how it sat (in the frame's
    pixels: a block is 2 x 2 photosites), before it was lined up."""
    if al is None:
        return ""
    if al.how == "none":
        return ("The IR shot couldn't be lined up with this frame (is it this "
                "frame's?): spots may sit beside their specks.")
    bits = []
    ir_scale = 1.0 / al.scale if al.scale else 1.0
    if abs(ir_scale - 1) >= 5e-4:
        pct = (ir_scale - 1) * 100
        bits.append(f"{abs(pct):.1f} % {'larger' if pct > 0 else 'smaller'}")
    d = 2 * math.hypot(*al.shift)
    if d >= 1:
        bits.append(f"{d:.0f} px off")
    if abs(al.turn) >= 0.05:
        bits.append(f"turned {abs(al.turn):.2f}°")
    if not bits:
        return ""
    return "The IR shot was lined up with the frame (it was " + ", ".join(bits) + ")."


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
    align: Optional[Alignment] = None
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
    al = None
    if vis_logs:
        vsum = vis_logs[0] if len(vis_logs) == 1 else _log(sum(np.exp(v) for v in vis_logs))
        al = align(L, vsum)
        shift = al.shift
        if al.moved():
            L = cv2.warpAffine(L, np.float32(al.matrix), (L.shape[1], L.shape[0]),
                               flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
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
                   noise=float(np.median(sig[::8, ::8])), size=(ir.shape[1], ir.shape[0]),
                   align=al)


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


def ir_flat_for(img, shape, ir_blocks=None) -> Optional[np.ndarray]:
    """The library's IR flat for the frame's format, as a light map on the
    block grid (1 / gain), moved with the carrier opening when both know it
    (spec/ir-dust-and-flat-sets.md §5); None when there is none. The opening
    is the IR shot's own (`ir_blocks`): the flat corrects that shot, before
    it is lined up, and an IR flat read against an IR shot reads the mask's
    edge the same way (the white shot's left edge read 1.4 % of the width
    further out than its IR pair's); else the frame's."""
    try:
        from core import flat_field as ff
        probe = type("IRFrame", (), {})()
        probe.scan_light = "ir"
        probe.merge_sources = None
        probe.film_format = ff.frame_format(img)
        opening = ff.opening_of(ir_blocks) if ir_blocks is not None else None
        opening = opening or getattr(img, "flat_opening", None)
        prof = ff.profile_for(probe, opening=opening)
        if prof is None or ff.profile_light(prof) != "ir":
            return None
        move = ff.track(prof, opening)
        gain = ff.gain_for_size(ff.tracked_profile(prof, move), shape[1], shape[0])
        return (1.0 / np.maximum(gain.mean(axis=2), 1e-3)).astype(np.float32)
    except Exception:
        return None


def frame_dust_map(img, flat: Optional[np.ndarray] = None) -> DustMap:
    """The IR dust map of a frame (reads its IR shot and its own shots)."""
    ir = read_blocks(img.ir_source)
    if flat is None:
        flat = ir_flat_for(img, ir.shape, ir_blocks=ir)
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
