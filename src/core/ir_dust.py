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

Revision 2 (spec/ir-dust-v2.md): the crosstalk is fitted per tile (the
picture's residue was local crosstalk), a line stage follows fibres, and each
candidate is judged by a small logistic model on eight features, the strongest
being whether the frame's own shots darken in the same shape (real dust
darkens them all; noise doesn't). Sensitivity is the probability needed. The
model starts from defaults fitted on the user's frames (`ir_dust_default`) and
learns from his corrections (`ir_dust_learn`). A long candidate is healed as
strokes along its skeleton, so a curled fibre is followed, not averaged.

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
MARK_KIND = "mark"          # a mark found in the colour shots (core/film_marks.py)
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
VIS_REACH_Z = 3.0           # a found speck grows over what touches it this dark (z) in the colour shots ...
VIS_REACH_MAX = 3.0         # ... up to this many times its size across (spec/ir-dust-v2.md rev. 2026-11-22 b)
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
# revision 2 (spec/ir-dust-v2.md)
LOCAL_TILE = 192           # blocks: the crosstalk is fitted again per tile
LOCAL_LAMBDA = 50.0        # ... pulled towards the frame's own k (a flat tile says nothing)
LINE_LEN = 9               # blocks: the line stage's segments
LINE_TURNS = 12
SEED_Z = 4.0               # candidates: seeds deeper than this (z) ...
GROW_Z = 2.0               # ... grown into this
SEED_LINE = 6.0            # ... or along a line deeper than this
GROW_LINE = 3.0
MAX_CAND_AREA = 4000       # blocks: a larger component is picture
RING_IN, RING_OUT = 3, 8   # blocks: the ring a candidate is compared with
JOIN_GAP = 4               # blocks: a fibre's pieces this close go together ...
JOIN_P = 0.2               # ... when the weaker one is at least this likely ...
# The IR noise the model was fitted at (the user's 8 s ISO 3200 shots: median
# local noise 0.0150-0.0162 log). A cleaner IR shot is read in these units, so
# its depths mean what they meant to the model (spec/ir-dust-v2.md rev. 2026-11-22)
NOISE_REF = 0.015
LINE_LIKE = 5.0            # ... and line-like (line depth, or ELONGATED)
DARK_IR = 0.20             # the IR shot under this share of its range: say so
BRIDGE_GAP = 24            # blocks: a fibre's flagged pieces this far apart ...
BRIDGE_TURN = 0.5          # ... each continuing towards the other (cos of the turn) ...
BRIDGE_DARK = 2.5          # ... are joined where the colour shots darken between them
BRIDGE_DARK_SHARE = 0.8    # (mean z below -BRIDGE_DARK, this share of the way below -1.5)
BRIDGE_STRAIGHT = 1.6      # ... along a path at most this much longer than the gap
BRIDGE_RIDGE = 0.7         # ... and a thin line there: this share of the way darker than
BRIDGE_SIDE = 1.5          # both sides (z) a little out (a picture's edge is dark on one)
IR_DEPTH_CAP = 8.0         # the model's IR depth and line features stop here: a
LINE_CAP = 12.0            # deeper IR dip with nothing in the frame's own shots is
                           # a hot pixel or an IR-only blob, not dust that shows
FEATURES = ("IR depth", "line", "visible dip", "same shape", "IR/visible",
            "picture edge", "log area", "visible deepest")

_cache_lock = threading.Lock()
_block_cache: Dict[Tuple[str, float], np.ndarray] = {}
_CACHE_MAX = 6


def sigma_for(sensitivity: float) -> float:
    """Revision 1's seed depth at a sensitivity (kept for its callers)."""
    s = max(0.0, min(100.0, float(sensitivity)))
    return SIGMA_AT_0 + (SIGMA_AT_100 - SIGMA_AT_0) * s / 100.0


def p_needed(sensitivity: float) -> float:
    """The probability a candidate needs at a sensitivity: 0 -> 0.90,
    50 -> 0.50, 100 -> 0.10 (logit-linear)."""
    s = max(0.0, min(100.0, float(sensitivity)))
    return 1.0 / (1.0 + math.exp(-2.2 * (1.0 - s / 50.0)))


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
        span = float(r.white_level) - float(np.mean(r.black_level_per_channel))
    with _cache_lock:
        _block_cache[key] = b
        _span_cache[key] = span
        while len(_block_cache) > _CACHE_MAX:
            _block_cache.pop(next(iter(_block_cache)))
    return b


_span_cache: Dict[Tuple[str, float], float] = {}


def level_of(path: str) -> Optional[float]:
    """How far up its range a RAW reaches: the 99.5th percentile of its blocks
    over (white - black). None when it can't be read."""
    try:
        b = read_blocks(path)
        key = (os.path.normpath(path), os.path.getmtime(path))
        span = _span_cache.get(key)
        if not span:
            return None
        return float(np.percentile(b[::7, ::7], 99.5)) / span
    except Exception:
        return None


def dark_ir_text(level: Optional[float]) -> str:
    """'' unless the IR shot is too dark to show faint dust (spec §3.5)."""
    if level is None or level >= DARK_IR:
        return ""
    return (f"The IR shot is dark ({max(1, round(level * 100))} % of the sensor's "
            "range): a longer IR exposure, such as 30 s, would show fainter dust.")


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


def local_crosstalk(L: np.ndarray, vis_logs: List[np.ndarray], k0: Sequence[float],
                    tile: int = LOCAL_TILE, lam: float = LOCAL_LAMBDA) -> np.ndarray:
    """ln IR less the picture, each shot's k fitted again per tile (spec
    §3.1): least squares on the high-passed logs, pulled towards the frame's
    own k0, the tiles' k smoothed and interpolated to every block. The
    picture's residue at edges was local crosstalk (measured: a blur or a
    sub-block shift didn't help, a local k did)."""
    if not vis_logs:
        return L
    y = _highpass(L)
    X = [_highpass(v) for v in vis_logs]
    h, w = L.shape
    ny, nx = max(1, -(-h // tile)), max(1, -(-w // tile))
    nc = len(vis_logs)
    k0 = np.asarray(list(k0) + [0.0] * (nc - len(k0)), np.float64)[:nc]
    coef = np.zeros((ny, nx, nc), np.float32)
    for j in range(ny):
        for i in range(nx):
            sl = (slice(j * tile, min(h, (j + 1) * tile)), slice(i * tile, min(w, (i + 1) * tile)))
            yy = y[sl][::2, ::2].ravel().astype(np.float64)
            A = np.stack([x[sl][::2, ::2].ravel() for x in X], 1).astype(np.float64)
            ok = np.isfinite(yy) & np.all(np.isfinite(A), 1)
            yy, A = yy[ok], A[ok]
            if yy.size >= 50:
                lo, hi = np.percentile(yy, [1, 99])
                sel = (yy > lo) & (yy < hi)
                yy, A = yy[sel], A[sel]
            coef[j, i] = np.linalg.solve(A.T @ A + lam * np.eye(nc), A.T @ yy + lam * k0)
    out = L.astype(np.float32).copy()
    for c, v in enumerate(vis_logs):
        cm = coef[:, :, c]
        if ny >= 2 and nx >= 2:
            cm = cv2.GaussianBlur(cm, (0, 0), 0.7)
        out -= cv2.resize(cm, (w, h), interpolation=cv2.INTER_LINEAR) * v
    return out


def _residual(L: np.ndarray, radius: int = RADIUS) -> Tuple[np.ndarray, np.ndarray]:
    """(d, sig): L less its closing background (dust < 0), centred on its
    local level, and the local noise. The background is an upper envelope,
    so clean film sits a little below 0: d is centred on its local level,
    then divided by the local noise (the hotspot leaves the middle less noisy
    than the corners). Both are taken with dust clipped out, so a speck can't
    raise its own threshold."""
    Ls = cv2.GaussianBlur(L, (0, 0), 1.0)
    el = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    bg = cv2.morphologyEx(Ls, cv2.MORPH_CLOSE, el)
    bg = cv2.GaussianBlur(bg, (0, 0), radius / 2.0)
    bg = np.maximum(bg, Ls)
    d = Ls - bg
    sub = d[::4, ::4]
    med = float(np.median(sub))
    mad = float(1.4826 * np.median(np.abs(sub - med))) or 1e-3
    lvl = smooth_field(np.clip(d, med - 3 * mad, med + 3 * mad), NOISE_SIGMA)
    dc = (d - lvl).astype(np.float32)
    d2 = np.minimum(dc * dc, (3.0 * mad) ** 2)
    sig = (np.sqrt(np.maximum(smooth_field(d2, NOISE_SIGMA), 0)) * 1.09 + 1e-4).astype(np.float32)
    return dc, sig


def _middle(shape) -> Tuple[slice, slice]:
    """The middle 80 % of a frame: where a frame's statistics are taken (the
    carrier's flat black at the edges would skew them)."""
    h, w = shape[:2]
    return slice(int(0.1 * h), max(int(0.9 * h), int(0.1 * h) + 1)), \
        slice(int(0.1 * w), max(int(0.9 * w), int(0.1 * w) + 1))


_LINE_KERNELS: Optional[List[np.ndarray]] = None


def _line_kernels() -> List[np.ndarray]:
    global _LINE_KERNELS
    if _LINE_KERNELS is None:
        ks = []
        r = LINE_LEN // 2 + 1
        for i in range(LINE_TURNS):
            th = math.pi * i / LINE_TURNS
            k = np.zeros((2 * r + 1, 2 * r + 1), np.float32)
            for t in np.linspace(-LINE_LEN / 2, LINE_LEN / 2, LINE_LEN * 4):
                x, y = r + t * math.cos(th), r + t * math.sin(th)
                x0, y0 = int(math.floor(x)), int(math.floor(y))
                fx, fy = x - x0, y - y0
                for dx, dy, wt in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)),
                                   (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
                    k[y0 + dy, x0 + dx] += wt
            ks.append(k / k.sum())
        _LINE_KERNELS = ks
    return _LINE_KERNELS


def line_map(z: np.ndarray) -> np.ndarray:
    """z averaged along short segments at LINE_TURNS turns, the deepest turn
    kept, in units of its own spread over the frame's middle: a matched
    filter for fibres, which add up along their length."""
    zc = np.clip(z.astype(np.float32), -12.0, 4.0)      # one deep speck mustn't make a line
    best = None
    for k in _line_kernels():
        r = cv2.filter2D(zc, cv2.CV_32F, k, borderType=cv2.BORDER_REFLECT)
        best = r if best is None else np.minimum(best, r)
    s = best[_middle(best.shape)].ravel()[::7]
    med = float(np.median(s))
    mad = float(1.4826 * np.median(np.abs(s - med))) or 1e-3
    return ((best - med) / mad).astype(np.float32)


@dataclass
class Candidate:
    """A possible speck: its box's corner, its mask (bool, the box), its
    FEATURES, the block where it is deepest, and whether it is line-like
    (a fibre's weaker pieces join it)."""
    x0: int
    y0: int
    mask: np.ndarray
    feats: np.ndarray
    seed: Tuple[int, int]
    line_like: bool
    length: float = 1.0
    width: float = 1.0
    depth: float = 0.0       # the IR's own depth, uncapped (the IR-only rule reads it)

    def centre(self) -> Tuple[float, float]:
        ys, xs = np.nonzero(self.mask)
        return self.x0 + float(xs.mean()) + 0.5, self.y0 + float(ys.mean()) + 0.5


def _shape_agreement(z: np.ndarray, vz: np.ndarray, cx: int, cy: int, half: int) -> float:
    """The correlation of the IR spot (min(z, 0)) with the frame's own shots'
    darkening (min(vz, 0)) on a square round it, the best of +-1 block
    shifts: dust darkens both in the same shape, noise doesn't."""
    h, w = z.shape
    if cy - half - 1 < 0 or cx - half - 1 < 0 or cy + half + 2 > h or cx + half + 2 > w:
        return 0.0
    a = np.minimum(z[cy - half:cy + half + 1, cx - half:cx + half + 1].astype(np.float32), 0)
    aa = a - a.mean()
    na = float((aa * aa).sum())
    best = -1.0
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            b = np.minimum(vz[cy - half + dy:cy + half + 1 + dy,
                              cx - half + dx:cx + half + 1 + dx].astype(np.float32), 0)
            bb = b - b.mean()
            den = math.sqrt(na * float((bb * bb).sum())) + 1e-6
            best = max(best, float((aa * bb).sum()) / den)
    return best


def find_candidates(z: np.ndarray, line: np.ndarray, vz: Optional[np.ndarray],
                    vzmin: Optional[np.ndarray], vd: Optional[np.ndarray],
                    ird: np.ndarray, grad: Optional[np.ndarray],
                    gmed: float) -> List[Candidate]:
    """Every possible speck (spec §3.2) with its features (§3.3). With no
    visible shot the colour shots' features are 0."""
    seeds = (z < -SEED_Z) | (line < -SEED_LINE)
    if not seeds.any():
        return []
    grown = ((z < -GROW_Z) | (line < -GROW_LINE)).astype(np.uint8)
    n, lab, st, _cen = cv2.connectedComponentsWithStats(grown, connectivity=8)
    hit = np.zeros(n, bool)
    hit[np.unique(lab[seeds])] = True
    hit[0] = False
    H, W = z.shape
    ring_out = np.ones((2 * RING_OUT + 1, 2 * RING_OUT + 1), np.uint8)
    ring_in = np.ones((2 * RING_IN + 1, 2 * RING_IN + 1), np.uint8)
    out: List[Candidate] = []
    for i in np.flatnonzero(hit):
        x0, y0, cw, ch, area = (int(st[i, j]) for j in range(5))
        if area > MAX_CAND_AREA:
            continue
        box = (slice(y0, y0 + ch), slice(x0, x0 + cw))
        m = lab[box] == i
        ys, xs = np.nonzero(m)
        nb = int(m.sum())
        zz = z[box][m].astype(np.float32)
        if nb >= 3:
            ev = np.linalg.eigvalsh(np.cov(np.stack([xs, ys]).astype(np.float64)) + 1e-6 * np.eye(2))
            length = 4.0 * math.sqrt(max(ev[-1], 1e-6))
            width = max(1.0, nb / max(length, 1.0))
        else:
            length = width = 1.0
        lz = float(-line[box][m].min())
        zmin = float(-zz.min())
        k = int(np.argmin(np.where(m, z[box], np.inf)))
        seed = (x0 + k % cw, y0 + k // cw)
        vbump = shape = ratio = vedge = zvmin = 0.0
        if vz is not None:
            # the ring 3..8 blocks out, clipped to the frame
            yy0, xx0 = max(0, y0 - RING_OUT), max(0, x0 - RING_OUT)
            yy1, xx1 = min(H, y0 + ch + RING_OUT), min(W, x0 + cw + RING_OUT)
            canvas = np.zeros((ch + 2 * RING_OUT, cw + 2 * RING_OUT), np.uint8)
            canvas[RING_OUT:RING_OUT + ch, RING_OUT:RING_OUT + cw] = m
            outer = cv2.dilate(canvas, ring_out)
            inner = cv2.dilate(canvas, ring_in)
            oy, ox = yy0 - (y0 - RING_OUT), xx0 - (x0 - RING_OUT)
            sel = (outer > 0) & (inner == 0)
            sel = sel[oy:oy + (yy1 - yy0), ox:ox + (xx1 - xx0)]
            inside = float(vz[box][m].astype(np.float32).mean())
            if sel.any():
                vbump = -(inside - float(vz[yy0:yy1, xx0:xx1][sel].astype(np.float32).mean()))
                if grad is not None:
                    vedge = math.log(max(float(np.percentile(grad[yy0:yy1, xx0:xx1][sel], 90)), 1e-9)
                                     / max(gmed, 1e-9))
            else:
                vbump = -inside
            cx, cy = x0 + int(round(xs.mean())), y0 + int(round(ys.mean()))
            half = max(7, cw // 2 + 4, ch // 2 + 4)
            shape = _shape_agreement(z, vz, cx, cy, half)
            vdm = float(vd[box][m].astype(np.float32).mean())
            r = float(ird[box][m].astype(np.float32).mean()) / min(vdm, -0.01)
            ratio = min(max(r, 0.0), 1.5) if vbump > 0.5 else 0.0
            zvmin = float(-vzmin[box][m].min())
        feats = np.array([min(zmin, IR_DEPTH_CAP), min(lz, LINE_CAP), vbump, shape, ratio,
                          vedge, math.log(nb), zvmin], np.float32)
        out.append(Candidate(x0=x0, y0=y0, mask=m, feats=feats, seed=seed,
                             line_like=bool(lz >= LINE_LIKE or length / width >= 3.0),
                             length=length, width=width, depth=max(zmin, lz * 7.0 / 9.0)))
    return out


@dataclass
class DustMap:
    z: np.ndarray                    # d / local noise, float16 (H_b, W_b); <0 at dust
    k: List[float] = field(default_factory=list)
    shift: Tuple[float, float] = (0.0, 0.0)
    noise: float = 0.0               # median local noise (log units)
    align: Optional[Alignment] = None
    size: Tuple[int, int] = (0, 0)   # (W_b, H_b)
    cands: List[Candidate] = field(default_factory=list)
    kind: str = "single"             # "merged" | "single" | "ir-only": which model judges it
    ir_level: Optional[float] = None  # the IR shot's 99.5th percentile, share of its range
    vz: Optional[np.ndarray] = None   # the frame's own shots' darkening (z), float16
    _nbrs: Optional[List[List[int]]] = None
    _skel: Optional[Dict[int, np.ndarray]] = None


def kind_for(n_visible: int) -> str:
    return "ir-only" if n_visible <= 0 else ("single" if n_visible == 1 else "merged")


def dust_map(ir: np.ndarray, visible: Optional[List[np.ndarray]] = None,
             flat: Optional[np.ndarray] = None, radius: int = RADIUS,
             region: Optional[np.ndarray] = None,
             ir_level: Optional[float] = None, upside_down: bool = False) -> DustMap:
    """The IR's dust: (ln IR - local background) / local noise, with the
    picture's crosstalk divided out (per tile) when visible block maps are
    given (one per light: white, or a trichrome's three), and every possible
    speck with its features (spec/ir-dust-v2.md §3).

    `upside_down` turns the de-pictured IR over (-ln IR) before looking:
    dust only ever darkens the IR, so whatever is found then is false, and
    how much is found counts the false finds without labels (spec §1)."""
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
        L = local_crosstalk(L, vis_logs, k)
    if upside_down:
        L = -L
    dc, sig = _residual(L, radius)
    z = dc / sig
    noise = float(np.median(sig[::8, ::8]))
    if 0.0 < noise < NOISE_REF:
        # a cleaner shot: the picture's leftovers aren't noise and would score
        # deeper in its own sigma; read it at the noise the model knows
        z = z * (noise / NOISE_REF)
    line = line_map(z)
    vz = vzmin = vd = grad = None
    gmed = 1.0
    if vis_logs:
        vzs, vds = [], []
        for v in vis_logs:
            vdc, vsig = _residual(v, radius)
            vzs.append(vdc / vsig)
            vds.append(vdc)
        vz = vzs[0] if len(vzs) == 1 else sum(vzs) / len(vzs)
        vzmin = vzs[0] if len(vzs) == 1 else np.minimum.reduce(vzs)
        vd = vds[0] if len(vds) == 1 else sum(vds) / len(vds)
        g = cv2.GaussianBlur(vsum, (0, 0), 1.0)
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0) / 8
        gy = cv2.Sobel(g, cv2.CV_32F, 0, 1) / 8
        grad = np.sqrt(gx * gx + gy * gy)
        gmed = float(np.median(grad[_middle(grad.shape)][::4, ::4])) + 1e-6
    cands = find_candidates(z, line, vz, vzmin, vd, dc, grad, gmed)
    return DustMap(z=z.astype(np.float16), k=[float(x) for x in k], shift=shift,
                   noise=float(np.median(sig[::8, ::8])), size=(ir.shape[1], ir.shape[0]),
                   align=al, cands=cands, kind=kind_for(len(vis_logs)), ir_level=ir_level,
                   vz=vz.astype(np.float16) if vz is not None else None)


# --------------------------------------------------------------------------- #
# judging the candidates
# --------------------------------------------------------------------------- #
def probabilities(dm: DustMap, model=None) -> np.ndarray:
    """Each candidate's chance of being dust that shows in the picture. With
    no visible shot only the IR's depth speaks (revision 1's depths: 0.5 at
    7 sigma)."""
    if not dm.cands:
        return np.zeros(0, np.float32)
    F = np.stack([c.feats for c in dm.cands]).astype(np.float64)
    if dm.kind == "ir-only":
        depth = np.array([c.depth for c in dm.cands], np.float64)
        return (1.0 / (1.0 + np.exp(-1.5 * (depth - 7.0)))).astype(np.float32)
    if model is None:
        from core import ir_dust_learn
        model = ir_dust_learn.model_for(dm.kind)
    mu, sd, w, b = (np.asarray(v, np.float64) for v in model)
    t = b + ((F - mu) / sd) @ w
    return (1.0 / (1.0 + np.exp(-np.clip(t, -40, 40)))).astype(np.float32)


def _neighbours(dm: DustMap) -> List[List[int]]:
    """For each candidate, the line-like candidates whose masks come within
    JOIN_GAP blocks of it (cached on the map)."""
    if dm._nbrs is not None:
        return dm._nbrs
    cs = dm.cands
    box = np.array([[c.x0, c.y0, c.x0 + c.mask.shape[1], c.y0 + c.mask.shape[0]] for c in cs],
                   np.int64).reshape(-1, 4)
    g = JOIN_GAP
    el = np.ones((2 * g + 1, 2 * g + 1), np.uint8)
    out: List[List[int]] = [[] for _ in cs]
    for i, c in enumerate(cs):
        near = np.flatnonzero((box[:, 0] <= box[i, 2] + g) & (box[:, 2] >= box[i, 0] - g)
                              & (box[:, 1] <= box[i, 3] + g) & (box[:, 3] >= box[i, 1] - g))
        if len(near) <= 1:
            continue
        x0, y0 = box[i, 0] - g, box[i, 1] - g
        hh, ww = box[i, 3] - box[i, 1] + 2 * g, box[i, 2] - box[i, 0] + 2 * g
        mine = np.zeros((hh, ww), np.uint8)
        mine[g:g + c.mask.shape[0], g:g + c.mask.shape[1]] = c.mask
        mine = cv2.dilate(mine, el) > 0
        for j in near:
            if j == i or not cs[j].line_like:
                continue
            o = cs[j]
            # the other's mask on my window
            ax0, ay0 = max(x0, o.x0), max(y0, o.y0)
            ax1 = min(x0 + ww, o.x0 + o.mask.shape[1])
            ay1 = min(y0 + hh, o.y0 + o.mask.shape[0])
            if ax1 <= ax0 or ay1 <= ay0:
                continue
            a = mine[ay0 - y0:ay1 - y0, ax0 - x0:ax1 - x0]
            b = o.mask[ay0 - o.y0:ay1 - o.y0, ax0 - o.x0:ax1 - o.x0]
            if (a & b).any():
                out[i].append(int(j))
    dm._nbrs = out
    return out


def flagged(dm: DustMap, sensitivity: float = SENS_DEFAULT,
            keep: Optional[np.ndarray] = None, model=None) -> List[int]:
    """The candidates flagged at a sensitivity: those likely enough with their
    deepest block inside `keep`, plus the line-like pieces of a fibre they
    touch that are at least JOIN_P likely (spec §3.4)."""
    if not dm.cands:
        return []
    p = probabilities(dm, model)
    need = p_needed(sensitivity)
    acc = set()
    for i, c in enumerate(dm.cands):
        if p[i] < need:
            continue
        if keep is not None and not keep[c.seed[1], c.seed[0]]:
            continue
        acc.add(i)
    queue = list(acc)
    if queue:
        nbrs = _neighbours(dm)
        while queue:
            i = queue.pop()
            for j in nbrs[i]:
                if j not in acc and p[j] >= JOIN_P:
                    acc.add(j)
                    queue.append(j)
    return sorted(acc)


# --------------------------------------------------------------------------- #
# spots
# --------------------------------------------------------------------------- #
def _polyline(ys: np.ndarray, xs: np.ndarray, step: float) -> List[Tuple[float, float]]:
    """Points along a long component: slices across its main axis, each the
    mean of its pixels (revision 1; kept as the fallback for a skeleton that
    comes out empty)."""
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


def thin(mask: np.ndarray) -> np.ndarray:
    """Zhang-Suen thinning of a small bool mask to one-block-wide lines."""
    img = np.pad(mask.astype(np.uint8), 1)
    for _ in range(200):
        changed = False
        for step in (0, 1):
            p2, p3, p4 = img[:-2, 1:-1], img[:-2, 2:], img[1:-1, 2:]
            p5, p6, p7 = img[2:, 2:], img[2:, 1:-1], img[2:, :-2]
            p8, p9, c = img[1:-1, :-2], img[:-2, :-2], img[1:-1, 1:-1]
            nbr = (p2.astype(np.int32) + p3 + p4 + p5 + p6 + p7 + p8 + p9)
            seq = (p2, p3, p4, p5, p6, p7, p8, p9, p2)
            trans = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.int32) for i in range(8))
            if step == 0:
                m1, m2 = p2 * p4 * p6, p4 * p6 * p8
            else:
                m1, m2 = p2 * p4 * p8, p2 * p6 * p8
            rem = (c == 1) & (nbr >= 2) & (nbr <= 6) & (trans == 1) & (m1 == 0) & (m2 == 0)
            if rem.any():
                img[1:-1, 1:-1][rem] = 0
                changed = True
        if not changed:
            break
    return img[1:-1, 1:-1].astype(bool)


def skeleton_paths(sk: np.ndarray) -> List[List[Tuple[int, int]]]:
    """A thinned mask as simple paths of (y, x): split at junctions, each
    loop walked round once. A diagonal step counts only where no straight
    step joins the two (a staircase isn't a junction)."""
    pts = set(zip(*(a.tolist() for a in np.nonzero(sk))))
    if not pts:
        return []

    def nb(p):
        y, x = p
        out = [(y + dy, x + dx) for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1))
               if (y + dy, x + dx) in pts]
        for dy, dx in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
            q = (y + dy, x + dx)
            if q in pts and (y + dy, x) not in pts and (y, x + dx) not in pts:
                out.append(q)
        return out

    nbrs = {p: nb(p) for p in pts}
    nodes = {p for p, n in nbrs.items() if len(n) != 2}
    used = set()
    paths: List[List[Tuple[int, int]]] = []
    for n0 in nodes:
        for q in nbrs[n0]:
            if (n0, q) in used:
                continue
            path = [n0, q]
            used.update({(n0, q), (q, n0)})
            prev, cur = n0, q
            while cur not in nodes:
                nxt = [r for r in nbrs[cur] if r != prev and (cur, r) not in used]
                if not nxt:
                    break
                r = nxt[0]
                used.update({(cur, r), (r, cur)})
                path.append(r)
                prev, cur = cur, r
            paths.append(path)
    on_paths = {p for path in paths for p in path} | nodes
    rest = pts - on_paths
    while rest:                                   # loops with no junction or end
        start = min(rest)
        path, prev, cur = [start], None, start
        seen = {start}
        while True:
            nxt = [r for r in nbrs[cur] if r != prev and r not in seen and r in rest]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            path.append(cur)
            seen.add(cur)
        if len(path) > 2 and start in nbrs[cur]:
            path.append(start)
        rest -= seen
        paths.append(path)
    return paths


def with_visible(c: Candidate, vz: Optional[np.ndarray]) -> Candidate:
    """The candidate with its mask grown over the speck as it shows in the
    colour shots: the connected area darker than -VIS_REACH_Z (z) that
    touches it, within VIS_REACH_MAX x its size (bigger = picture: left
    alone). The IR saw only part of the user's hooked speck and its heal left
    the rest (spec/ir-dust-v2.md revision 2026-11-22 b)."""
    if vz is None or not c.mask.any():
        return c
    H, W = vz.shape[:2]
    area = int(c.mask.sum())
    r = math.sqrt(area / math.pi)
    pad = int(math.ceil(max(4.0, (VIS_REACH_MAX - 1.0) * r)))
    x0, y0 = max(0, c.x0 - pad), max(0, c.y0 - pad)
    x1 = min(W, c.x0 + c.mask.shape[1] + pad)
    y1 = min(H, c.y0 + c.mask.shape[0] + pad)
    seed = np.zeros((y1 - y0, x1 - x0), np.uint8)
    seed[c.y0 - y0:c.y0 - y0 + c.mask.shape[0], c.x0 - x0:c.x0 - x0 + c.mask.shape[1]] = c.mask
    dark = (vz[y0:y1, x0:x1].astype(np.float32) < -VIS_REACH_Z).astype(np.uint8)
    n, lab = cv2.connectedComponents(dark | seed, connectivity=8)
    touch = np.unique(lab[cv2.dilate(seed, np.ones((3, 3), np.uint8)) > 0])
    grown = np.isin(lab, touch[touch > 0]) | (seed > 0)
    if int(grown.sum()) <= area or int(grown.sum()) > VIS_REACH_MAX ** 2 * max(area, 4):
        return c
    ys, xs = np.nonzero(grown)
    gx0, gy0 = int(xs.min()), int(ys.min())
    m = grown[gy0:int(ys.max()) + 1, gx0:int(xs.max()) + 1]
    nb = int(m.sum())
    ev = np.linalg.eigvalsh(np.cov(np.stack([xs, ys]).astype(np.float64)) + 1e-6 * np.eye(2))
    length = 4.0 * math.sqrt(max(ev[-1], 1e-6))
    import dataclasses
    return dataclasses.replace(c, x0=x0 + gx0, y0=y0 + gy0, mask=m, length=length,
                               width=max(1.0, nb / max(length, 1.0)))


def _spots_of(c: Candidate, idx: int, w_b: int, h_b: int,
              vz: Optional[np.ndarray] = None) -> List[dict]:
    """A candidate as dust spots: a dab at the centre, or for a long one
    strokes along its skeleton (a curled fibre is followed, not averaged).
    With the colour shots' darkening `vz`, the spots cover the speck as it
    shows there too (`with_visible`; a find made into a dab only: a fibre
    is already followed along its skeleton)."""
    ys, xs = np.nonzero(c.mask)
    area = len(xs)
    tag = {"ir_f": [round(float(v), 3) for v in c.feats], "ir_c": int(idx)}

    def spot(pts, r):
        return dict({"kind": KIND,
                     "pts": [[float(x) / w_b, float(y) / h_b] for x, y in pts],
                     "r": float(r) / w_b}, **tag)

    sk = thin(c.mask) if area >= 6 and (c.length / c.width > 2.0 or area >= 40) else None
    # long = its skeleton is long for its width (a curl or a loop too, which
    # the spread of its pixels alone calls round)
    ls = int(sk.sum()) if sk is not None else 0
    width = area / max(ls, 1)
    if sk is not None and ls / width > ELONGATED:
        r = width / 2.0 * SPOT_SCALE + SPOT_PAD
        step = max(1, int(round(r)))
        out = []
        paths = skeleton_paths(sk)
        longest = max((len(p) for p in paths), default=0)
        for path in paths:
            if len(path) < 3 and longest >= 3:
                continue                         # a stub off a junction
            keep_i = list(range(0, len(path), step))
            if keep_i[-1] != len(path) - 1:
                keep_i.append(len(path) - 1)
            pts = [(c.x0 + path[i][1] + 0.5, c.y0 + path[i][0] + 0.5) for i in keep_i]
            out.append(spot(pts, r))
        if out:
            return out
        pts = _polyline(ys + c.y0 + 0.5, xs + c.x0 + 0.5, step=r)
        return [spot(pts, r)]
    if vz is not None:
        grown = with_visible(c, vz)
        if grown is not c:
            return _spots_of(grown, idx, w_b, h_b)     # a dab, or strokes if it's a hook
    r = math.sqrt(area / math.pi) * SPOT_SCALE + SPOT_PAD
    cx, cy = c.centre()
    return [spot([(cx, cy)], r)]


def spots_from(dm: DustMap, sensitivity: float = SENS_DEFAULT,
               keep: Optional[np.ndarray] = None, model=None) -> List[dict]:
    """Dust spots (kind "ir", normalised to the frame) at a sensitivity.
    `keep` (bool, block grid) limits them to the picture (crop). Each spot
    carries its candidate's features ("ir_f") and number ("ir_c")."""
    w_b, h_b = dm.size
    spots: List[dict] = []
    acc = flagged(dm, sensitivity, keep, model)
    for i in acc:
        spots.extend(_spots_of(dm.cands[i], i, w_b, h_b, vz=dm.vz))
    spots.extend(bridges(dm, acc, spots))
    return spots


# --------------------------------------------------------------------------- #
# bridges: a fibre's pieces joined where the IR barely shows it (spec/ir-dust-v2.md §3.6)
# --------------------------------------------------------------------------- #
def _skeleton_of(dm: DustMap, i: int) -> np.ndarray:
    """A candidate's skeleton points (x, y) in blocks, cached on the map."""
    if dm._skel is None:
        dm._skel = {}
    if i not in dm._skel:
        c = dm.cands[i]
        ys, xs = np.nonzero(thin(c.mask))
        if not len(xs):
            ys, xs = np.nonzero(c.mask)
        dm._skel[i] = np.stack([xs + c.x0, ys + c.y0], 1).astype(np.float64)
    return dm._skel[i]


def _heads_towards(pts: np.ndarray, end: np.ndarray, target: np.ndarray) -> bool:
    """Whether a piece, at its point `end`, runs on towards `target`: the way
    out of the piece there (end less the piece's points within 6 blocks of
    it) is within BRIDGE_TURN of the way to the target. A piece too short to
    have a way (a dab) agrees with anything."""
    near = pts[np.hypot(*(pts - end).T) <= 6.0]
    if len(near) < 3:
        return True
    out = end - near.mean(0)
    to = target - end
    no, nt = float(np.hypot(*out)), float(np.hypot(*to))
    if no < 0.8 or nt < 1e-6:
        return True
    return float(out @ to) / (no * nt) >= BRIDGE_TURN


def dark_path(vz: np.ndarray, a: Tuple[int, int], b: Tuple[int, int],
              margin: int = 6) -> Optional[List[Tuple[int, int]]]:
    """The cheapest 8-connected path from a to b ((x, y) blocks) where the
    frame's own shots are darkest (step cost x clip(1 + vz / 4, 0.2, 2)),
    searched in their box plus `margin`. None when there is none."""
    import heapq
    H, W = vz.shape
    x0, x1 = max(0, min(a[0], b[0]) - margin), min(W, max(a[0], b[0]) + margin + 1)
    y0, y1 = max(0, min(a[1], b[1]) - margin), min(H, max(a[1], b[1]) + margin + 1)
    cost = np.clip(1.0 + vz[y0:y1, x0:x1].astype(np.float32) / 4.0, 0.2, 2.0)
    h, w = cost.shape
    start, goal = (a[1] - y0, a[0] - x0), (b[1] - y0, b[0] - x0)
    dist = np.full((h, w), np.inf)
    prev = -np.ones((h, w, 2), np.int32)
    dist[start] = 0.0
    heap = [(0.0, start)]
    steps = [(dy, dx, math.hypot(dy, dx)) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx]
    while heap:
        d, (y, x) = heapq.heappop(heap)
        if (y, x) == goal:
            break
        if d > dist[y, x]:
            continue
        for dy, dx, L in steps:
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w:
                nd = d + L * 0.5 * (cost[y, x] + cost[ny, nx])
                if nd < dist[ny, nx]:
                    dist[ny, nx] = nd
                    prev[ny, nx] = (y, x)
                    heapq.heappush(heap, (nd, (ny, nx)))
    if not np.isfinite(dist[goal]):
        return None
    path, cur = [], goal
    while cur != start:
        path.append((cur[1] + x0, cur[0] + y0))
        cur = tuple(prev[cur])
    path.append((a[0], a[1]))
    return path[::-1]


def _ridge_share(vz: np.ndarray, path: List[Tuple[int, int]], off: float) -> float:
    """The share of a path's points darker (in the frame's own shots) than
    both sides `off` blocks out across it by BRIDGE_SIDE: a fibre is a thin
    dark line, the dark side of a picture's edge has dark on one side."""
    H, W = vz.shape
    pts = np.asarray(path, np.float64)
    n = len(pts)
    if n < 3:
        return 1.0
    good = 0
    for t in range(n):
        a, b = pts[max(0, t - 2)], pts[min(n - 1, t + 2)]
        d = b - a
        L = math.hypot(*d)
        if L < 1e-6:
            continue
        nx, ny = -d[1] / L, d[0] / L
        x, y = pts[t]
        here = float(vz[int(y), int(x)])
        sides = []
        for sgn in (-1, 1):
            sx, sy = int(round(x + sgn * off * nx)), int(round(y + sgn * off * ny))
            if 0 <= sx < W and 0 <= sy < H:
                sides.append(float(vz[sy, sx]))
        if len(sides) == 2 and min(sides) - here >= BRIDGE_SIDE:
            good += 1
    return good / n


def bridges(dm: DustMap, acc: List[int], spots: List[dict]) -> List[dict]:
    """Strokes joining a fibre's flagged pieces where the IR barely shows the
    stretch between them (a translucent fibre) but the frame's own shots do:
    two line-like flagged pieces whose nearest skeleton points are at most
    BRIDGE_GAP blocks apart, each running on towards the other, and a path
    between them along which the colour shots are dark (mean z below
    -BRIDGE_DARK, BRIDGE_DARK_SHARE of it below -1.5) and not much longer
    than the gap. Bridges carry no features (nothing to learn from)."""
    if dm.vz is None or len(acc) < 2:
        return []
    w_b, h_b = dm.size
    vz = dm.vz
    r_of: Dict[int, float] = {}
    for s in spots:
        c = s.get("ir_c")
        if c is not None:
            r_of[c] = min(r_of.get(c, 1e9), float(s["r"]) * w_b)
    pieces = [i for i in acc if dm.cands[i].line_like]
    out: List[dict] = []
    done = set()
    boxes = {i: (dm.cands[i].x0, dm.cands[i].y0, dm.cands[i].x0 + dm.cands[i].mask.shape[1],
                 dm.cands[i].y0 + dm.cands[i].mask.shape[0]) for i in pieces}
    for ai, i in enumerate(pieces):
        bi = boxes[i]
        for j in pieces[ai + 1:]:
            bj = boxes[j]
            if (bj[0] > bi[2] + BRIDGE_GAP or bi[0] > bj[2] + BRIDGE_GAP
                    or bj[1] > bi[3] + BRIDGE_GAP or bi[1] > bj[3] + BRIDGE_GAP):
                continue
            A, B = _skeleton_of(dm, i), _skeleton_of(dm, j)
            d = np.hypot(A[:, None, 0] - B[None, :, 0], A[:, None, 1] - B[None, :, 1])
            k = int(np.argmin(d))
            ka, kb = divmod(k, d.shape[1])
            gap = float(d[ka, kb])
            if gap > BRIDGE_GAP or gap < 2.0 or (i, j) in done:
                continue
            ea, eb = A[ka], B[kb]
            if not (_heads_towards(A, ea, eb) and _heads_towards(B, eb, ea)):
                continue
            path = dark_path(vz, (int(ea[0]), int(ea[1])), (int(eb[0]), int(eb[1])))
            if not path or len(path) < 2:
                continue
            length = sum(math.hypot(p[0] - q[0], p[1] - q[1]) for p, q in zip(path, path[1:]))
            if length > BRIDGE_STRAIGHT * gap + 2.0:
                continue
            inner = path[1:-1] or path
            vals = np.array([float(vz[y, x]) for x, y in inner])
            if vals.mean() > -BRIDGE_DARK or (vals <= -1.5).mean() < BRIDGE_DARK_SHARE:
                continue
            r = min(r_of.get(i, SPOT_PAD + 1.5), r_of.get(j, SPOT_PAD + 1.5))
            if _ridge_share(vz, path, max(3.0, r)) < BRIDGE_RIDGE:
                continue                         # an edge of the picture, not a fibre
            step = max(1, int(round(r)))
            keep_i = list(range(0, len(path), step))
            if keep_i[-1] != len(path) - 1:
                keep_i.append(len(path) - 1)
            out.append({"kind": KIND,
                        "pts": [[(path[t][0] + 0.5) / w_b, (path[t][1] + 0.5) / h_b] for t in keep_i],
                        "r": r / w_b, "ir_bridge": [int(i), int(j)]})
            done.add((i, j))
    return out


def mask_from(dm: DustMap, sensitivity: float = SENS_DEFAULT,
              keep: Optional[np.ndarray] = None, model=None) -> np.ndarray:
    """The flagged candidates' masks (bool, block grid), for the overlay."""
    w_b, h_b = dm.size
    out = np.zeros((h_b, w_b), bool)
    for i in flagged(dm, sensitivity, keep, model):
        c = dm.cands[i]
        out[c.y0:c.y0 + c.mask.shape[0], c.x0:c.x0 + c.mask.shape[1]] |= c.mask
    return out


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
    return dust_map(ir, vis, flat=flat, ir_level=level_of(img.ir_source))


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
        return [s for s in (img.ir_dust_pending or []) if s.get("kind") == KIND]
    spots = spots_from(dm, sens, keep_mask_for(img, dm.size))
    marks = [s for s in (img.ir_dust_pending or []) if s.get("kind") == MARK_KIND]
    img.ir_dust_pending = (spots + marks) or None
    return spots


def find_all(img, sensitivity: Optional[float] = None,
             mark_sensitivity: Optional[float] = None) -> List[dict]:
    """Find a frame's dust with its IR shot (when it has one) and the marks
    in its own shots (spec/colour-shot-marks.md), both flagged, not healed.
    Raises only when neither can be looked for."""
    from core import film_marks
    why_ir, why_marks = can_find(img), film_marks.can_find(img)
    if why_ir and why_marks:
        raise ValueError(why_ir if getattr(img, "ir_source", None) else why_marks)
    if why_ir is None:
        find(img, sensitivity)
    if why_marks is None:
        film_marks.find(img, mark_sensitivity)
    return pending(img)


def pending(img) -> List[dict]:
    return list(getattr(img, "ir_dust_pending", None) or [])


def scan_kind(img) -> str:
    """Which learned model a frame's IR dust is judged by: its map's, else
    from its own shots (a merge's three or one)."""
    dm = getattr(img, "_ir_dust_map", None)
    if dm is not None:
        return dm.kind
    return kind_for(len(visible_paths(img)))


def _examples_from(spots: List[dict], label: int, weight: float) -> List[tuple]:
    """One example per candidate the spots came from (a fibre's strokes share
    one), from the features each IR spot carries."""
    seen, out = set(), []
    for s in spots:
        f = s.get("ir_f")
        if s.get("kind") != KIND or not f or len(f) != len(FEATURES):
            continue
        key = (s.get("ir_c"), tuple(f))
        if key in seen:
            continue
        seen.add(key)
        out.append((f, label, weight))
    return out


def _marks_examples(spots, label, weight):
    try:
        from core import film_marks
        return film_marks.examples_from(spots, label, weight)
    except Exception:
        return {}


def _learn_marks(by_kind) -> None:
    """Marks' corrections are kept for a later fitted model (spec
    colour-shot-marks §4); nothing reads them yet."""
    if not by_kind:
        return
    try:
        from core import ir_dust_learn
        for kind, ex in by_kind.items():
            ir_dust_learn.add(kind, ex)
    except Exception:
        pass


def _learn(img, examples: List[tuple]) -> None:
    if not examples:
        return
    try:
        from core import ir_dust_learn
        kind = scan_kind(img)
        if kind in ir_dust_learn.KINDS:
            ir_dust_learn.add(kind, examples)
    except Exception:
        pass


def heal(img, learn: bool = True) -> int:
    """Move the flagged spots into the frame's dust spots (replacing earlier
    IR spots; hand and AI spots stay). The caller takes the undo snapshot and
    re-renders. Each candidate healed is a light "dust" example for the
    learned model (spec/ir-dust-v2.md §4). Returns how many."""
    new = pending(img)
    if learn:
        from core import ir_dust_learn
        _learn(img, _examples_from(new, 1, ir_dust_learn.HEAL_WEIGHT))
        _learn_marks(_marks_examples(new, 1, ir_dust_learn.HEAL_WEIGHT))
    img.dust_spots = [s for s in (img.dust_spots or [])
                      if s.get("kind") not in (KIND, MARK_KIND)] + new
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


def erase(img, pts: List[List[float]], r: float, aspect: float,
          learn: bool = True) -> Tuple[int, int]:
    """Remove every spot under an eraser stroke (normalised points, radius as
    a fraction of the width, aspect = height / width): from the healed spots
    (any kind) and from the flagged IR ones. Each IR candidate erased is a
    "not dust" example for the learned model. Returns (healed, flagged)."""
    spots = list(getattr(img, "dust_spots", None) or [])
    kept = [s for s in spots if not _touches(s, pts, r, aspect)]
    flagged_spots = pending(img)
    kept_f = [s for s in flagged_spots if not _touches(s, pts, r, aspect)]
    n_h, n_f = len(spots) - len(kept), len(flagged_spots) - len(kept_f)
    if learn and (n_h or n_f):
        gone = [s for s in spots if s not in kept] + [s for s in flagged_spots if s not in kept_f]
        _learn(img, _examples_from(gone, 0, 1.0))
        _learn_marks(_marks_examples(gone, 0, 1.0))
    if n_h:
        img.dust_spots = kept
    if n_f:
        img.ir_dust_pending = kept_f or None
    return n_h, n_f


def learn_from_stroke(img, pts: List[List[float]], r: float, aspect: float) -> int:
    """A hand stroke (normalised points, radius a share of the width) over a
    candidate that wasn't flagged: dust the finder missed, a "dust" example
    (spec §4). Only while the frame's map is in memory. Returns how many."""
    dm = getattr(img, "_ir_dust_map", None)
    if dm is None or not dm.cands or dm.kind == "ir-only" or not pts:
        return 0
    w_b, h_b = dm.size
    flagged_now = {s.get("ir_c") for s in pending(img)}
    flagged_now |= {s.get("ir_c") for s in (getattr(img, "dust_spots", None) or [])
                    if s.get("kind") == KIND}
    P = np.asarray(pts, np.float64) * np.array([w_b, h_b])
    rad = float(r) * w_b
    out = []
    for i, c in enumerate(dm.cands):
        if i in flagged_now:
            continue
        cx, cy = c.centre()
        cr = math.sqrt(c.mask.sum() / math.pi)
        if np.min(np.hypot(P[:, 0] - cx, (P[:, 1] - cy))) <= rad + cr:
            out.append((c.feats.tolist(), 1, 1.0))
    _learn(img, out)
    return len(out)
