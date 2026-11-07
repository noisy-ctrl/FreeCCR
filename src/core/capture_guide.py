"""Capture's frame guide goes with the shot (spec/guide-follow-and-crop.md §3).

- A RECORD is made when Shoot starts with the guide placed: the guide (view
  fractions), a small grey copy of the live picture and its size.
  `begin_shot(record)` makes it the active one until the shot ends
  (`end_shot`) and `GRACE_S` after, for files that arrive late.
- Files are TIED to it: `remember(path)` (a USB shot's own file, exact) or
  `stamp(path)` (a hot-folder file, given the active record if it has none).
- On arrival `lookup(paths)` finds the record and `map_guide(record, raw)`
  lines the live picture up with the RAW's preview (the RAW may be stored
  turned, and the live view's field need not be exactly the RAW's), giving
  the guide on the scan: {cx, cy, w, h (fractions of the scan), angle, how,
  score, turn}, or None when the two pictures can't be lined up.

Qt-free; numpy + OpenCV. The registry is shared between Capture (GUI
thread) and the tether worker, so it is locked.
"""
from collections import OrderedDict
import math
import os
import threading
import time

import cv2
import numpy as np

GRACE_S = 60.0            # a shot's record stays active this long after it ends
MAX_PATHS = 300           # files remembered at most (oldest dropped)
LIVE_SIDE = 480           # the live copy kept with a record (long side, px)
MAP_SIDE = 320            # the pictures are lined up at this size (long side)
SHAPE_TOL = 0.03          # a turn is tried only when the shapes agree this well
ORIENT_MIN = 0.30         # the best turn's correlation must reach this ...
ORIENT_MARGIN = 0.08      # ... and beat the next turn by this
ORIENT_ASSUME = 0.45      # without a refinement, the turn alone needs this
ECC_MIN = 0.50            # the refinement's correlation
SCALE_TOL = 0.10          # the refinement may scale by at most this ...
ANISO_MAX = 0.02          # ... the same both ways within this ...
SHEAR_MAX = 0.02          # ... with this little shear ...
TURN_MAX = 2.0            # ... and turn at most this (degrees)

_lock = threading.Lock()
_active = {"record": None, "until": None}      # until None: the shot is running
_paths = OrderedDict()


def _now(now):
    return time.monotonic() if now is None else float(now)


def _key(path):
    try:
        return os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError, TypeError):
        return str(path)


# --------------------------------------------------------------------------- #
# the record and the registry
# --------------------------------------------------------------------------- #
def make_record(guide, frame, following=False, now=None):
    """A record of `guide` (view fractions) on the live picture `frame`
    (8-bit BGR or grey, the full live frame the guide's fractions are of)."""
    f = np.asarray(frame)
    h, w = f.shape[:2]
    grey = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) if f.ndim == 3 else f.astype(np.uint8)
    k = LIVE_SIDE / float(max(h, w))
    if k < 1.0:
        grey = cv2.resize(grey, (max(1, int(round(w * k))), max(1, int(round(h * k)))),
                          interpolation=cv2.INTER_AREA)
    return {"guide": {k2: guide[k2] for k2 in ("cx", "cy", "w", "h", "angle") if k2 in guide},
            "format": guide.get("format"), "live": grey, "live_size": (int(w), int(h)),
            "following": bool(following), "t": _now(now)}


def begin_shot(record, now=None):
    with _lock:
        _active["record"], _active["until"] = record, None


def end_shot(now=None):
    with _lock:
        if _active["record"] is not None and _active["until"] is None:
            _active["until"] = _now(now) + GRACE_S


def active(now=None):
    with _lock:
        rec, until = _active["record"], _active["until"]
    if rec is None or (until is not None and _now(now) > until):
        return None
    return rec


def remember(path, record=None, now=None):
    """Tie `path` to `record` (default: the active one). True when tied."""
    rec = record if record is not None else active(now)
    if rec is None or not path:
        return False
    with _lock:
        _paths[_key(path)] = rec
        _paths.move_to_end(_key(path))
        while len(_paths) > MAX_PATHS:
            _paths.popitem(last=False)
    return True


def stamp(path, now=None):
    """A hot-folder file is ready: give it the active record unless it has
    one already (a USB shot's file was remembered exactly)."""
    with _lock:
        if _key(path) in _paths:
            return False
    return remember(path, now=now)


def lookup(paths):
    """The record of the first of `paths` that has one, or None."""
    with _lock:
        for p in paths or ():
            rec = _paths.get(_key(p))
            if rec is not None:
                return rec
    return None


def clear():
    with _lock:
        _paths.clear()
        _active["record"], _active["until"] = None, None


# --------------------------------------------------------------------------- #
# lining the live picture up with the RAW
# --------------------------------------------------------------------------- #
def _log_norm(img):
    """Log luminance normalised to its 1st–99.5th percentiles (float32 0..1)."""
    a0 = np.asarray(img)
    a = a0[..., :3].astype(np.float32).mean(axis=2) if a0.ndim == 3 else a0.astype(np.float32)
    if a0.dtype == np.uint16 or a.max() > 255.5:
        full = 65535.0
    else:
        full = 255.0
        a = np.power(a / 255.0, 2.2) * 255.0          # the live view is display-encoded
    L = np.log10(np.maximum(a, full / 4096.0) / full)
    lo, hi = np.percentile(L, (1, 99.5))
    return np.clip((L - lo) / max(1e-6, hi - lo), 0.0, 1.0).astype(np.float32)


def _grad(L, size, sigma):
    """Gradient magnitude at `size`, zero mean and unit spread (for ECC)."""
    gx, gy = _gfield(L, size, sigma)
    g = np.sqrt(gx * gx + gy * gy)
    return ((g - g.mean()) / max(1e-6, float(g.std()))).astype(np.float32)


def _gfield(L, size, sigma):
    a = cv2.resize(L, size, interpolation=cv2.INTER_AREA)
    a = cv2.GaussianBlur(a, (0, 0), sigma)
    return (cv2.Sobel(a, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(a, cv2.CV_32F, 0, 1, ksize=3))


def _agreement(f1, f2):
    """How alike two gradient fields are: the cosine of the (gx, gy) fields
    (1 = the same; a picture turned half round scores low, as its gradients
    point the other way)."""
    (ax, ay), (bx, by) = f1, f2
    num = float(np.sum(ax * bx + ay * by))
    den = math.sqrt(float(np.sum(ax * ax + ay * ay)) * float(np.sum(bx * bx + by * by)))
    return num / den if den > 0 else 0.0


def _turn_point(x, y, w, h, k):
    """(x, y) in a w × h picture -> the same point after np.rot90(k)."""
    k %= 4
    if k == 0:
        return x, y
    if k == 1:
        return y, w - x
    if k == 2:
        return w - x, h - y
    return h - y, x


def corners(guide, w, h):
    """The guide's corners TL, TR, BR, BL in px of a w × h picture."""
    cx, cy = guide["cx"] * w, guide["cy"] * h
    gw, gh = guide["w"] * w, guide["h"] * h
    a = math.radians(guide.get("angle", 0.0) or 0.0)
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for qx, qy in ((-gw / 2, -gh / 2), (gw / 2, -gh / 2), (gw / 2, gh / 2), (-gw / 2, gh / 2)):
        out.append((cx + ca * qx - sa * qy, cy + sa * qx + ca * qy))
    return out


def box_from_corners(pts):
    """(cx, cy, w, h, angle) of the box through corners TL, TR, BR, BL (px),
    its angle brought within ±45° (width and height swapped as needed)."""
    p = np.asarray(pts, np.float64)
    c = p.mean(axis=0)
    top = p[1] - p[0]
    w = 0.5 * (np.linalg.norm(p[1] - p[0]) + np.linalg.norm(p[2] - p[3]))
    h = 0.5 * (np.linalg.norm(p[3] - p[0]) + np.linalg.norm(p[2] - p[1]))
    ang = math.degrees(math.atan2(top[1], top[0]))
    while ang > 45.0:
        ang -= 90.0
        w, h = h, w
    while ang < -45.0:
        ang += 90.0
        w, h = h, w
    return float(c[0]), float(c[1]), float(w), float(h), float(ang)


def map_guide(record, raw):
    """The record's guide on the scan `raw` (the unconverted preview, any
    size), or None. See the module docstring."""
    if record is None or raw is None:
        return None
    try:
        live = np.asarray(record["live"])
        R = np.asarray(raw)
        H, W = R.shape[:2]
        lh, lw = live.shape[:2]
        if min(H, W, lh, lw) < 32:
            return None
        k_map = MAP_SIDE / float(max(H, W))
        mw, mh = max(32, int(round(W * k_map))), max(32, int(round(H * k_map)))
        Lr, Ll = _log_norm(R), _log_norm(live)
        f_raw = _gfield(Lr, (mw, mh), 2.0)
        scores = {}
        for k in range(4):
            lk = np.rot90(Ll, k)
            ka = lk.shape[1] / float(lk.shape[0])
            if abs(ka / (W / float(H)) - 1) > SHAPE_TOL:
                continue
            scores[k] = _agreement(f_raw, _gfield(np.ascontiguousarray(lk), (mw, mh), 2.0))
        if not scores:
            return None
        # a RAW is decoded as the sensor sees it (user_flip=0), like the live
        # view: no turn is expected, and is taken when it agrees at all; a
        # turn is taken only when it clearly agrees better
        if scores.get(0, -1.0) >= ORIENT_MIN:
            k, best = 0, scores[0]
        else:
            ranked = sorted(((v, kk) for kk, v in scores.items()), reverse=True)
            best, k = ranked[0]
            if best < ORIENT_MIN or (len(ranked) > 1 and best - ranked[1][0] < ORIENT_MARGIN):
                return None
        lk = np.ascontiguousarray(np.rot90(Ll, k))
        A = np.eye(2, 3, dtype=np.float32)
        how, score = "assumed", best
        try:
            t_raw = _grad(Lr, (mw, mh), 1.5)
            t_live = _grad(lk, (mw, mh), 1.5)
            rho, warp = cv2.findTransformECC(
                t_raw, t_live, np.eye(2, 3, dtype=np.float32), cv2.MOTION_AFFINE,
                (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 100, 1e-6), None, 5)
            M = warp[:, :2].astype(np.float64)
            sx, sy = np.linalg.norm(M[:, 0]), np.linalg.norm(M[:, 1])
            shear = float(np.dot(M[:, 0], M[:, 1]) / max(1e-9, sx * sy))
            turn = math.degrees(math.atan2(M[1, 0], M[0, 0]))
            if (rho >= ECC_MIN and abs(sx - 1) <= SCALE_TOL and abs(sy - 1) <= SCALE_TOL
                    and abs(sx / sy - 1) <= ANISO_MAX and abs(shear) <= SHEAR_MAX
                    and abs(turn) <= TURN_MAX):
                A, how, score = warp, "matched", float(rho)
        except cv2.error:
            pass
        if how == "assumed" and best < ORIENT_ASSUME:
            return None
        # live px -> turned -> map px -> (inverse ECC warp) -> raw map px -> raw px
        Ainv = cv2.invertAffineTransform(np.asarray(A, np.float64))
        tw, th = lk.shape[1], lk.shape[0]
        pts = []
        for x, y in corners(record["guide"], lw, lh):
            tx, ty = _turn_point(x, y, lw, lh, k)
            mx, my = tx * mw / float(tw), ty * mh / float(th)
            rx = Ainv[0, 0] * mx + Ainv[0, 1] * my + Ainv[0, 2]
            ry = Ainv[1, 0] * mx + Ainv[1, 1] * my + Ainv[1, 2]
            pts.append((rx * W / float(mw), ry * H / float(mh)))
        cx, cy, bw, bh, ang = box_from_corners(pts)
        return {"cx": cx / W, "cy": cy / H, "w": bw / W, "h": bh / H, "angle": ang,
                "format": record.get("format"), "how": how,
                "score": round(float(score), 3), "turn": 90 * k}
    except Exception as e:                       # never let a guide stop a capture
        print(f"Capture guide: could not line up the live picture ({e})")
        return None
