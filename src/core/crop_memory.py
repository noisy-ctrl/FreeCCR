"""Crop memory: what auto crop learns from the crops you confirm, per scan
setup (spec/auto-crop-learning.md).

A small JSON file of box geometry in preview pixels: no pixels of the scan, no
file names. Examples are grouped per setup (camera, preview size, single or
merged) and, within a setup, into *learned frames* by confirmed size. Each
learned frame becomes a `core.auto_crop.Prior` for the detector.
"""

import json
import os
import threading
import time
import uuid
from typing import List, Optional

import numpy as np

from core.auto_crop import INSET_FRAC, Prior

VERSION = 1
MAX_PER_SETUP = 120
SIZE_TOL = 0.03          # confirmed boxes within 3% on both sides = one learned frame
HABIT_EXAMPLE_MAX = 0.06  # an example with every side moved < 6% teaches a habit
MIN_AREA = 0.25          # a confirmed box under 25% of the scan is not a frame
REFINE_MAX = 0.04        # re-confirming one image: sides moved < 4% = a refinement
DEFAULT_SPREAD = 0.05    # one example: position known to ~5% of the scan
MIN_SPREAD = 0.01
STILL_SPREAD = 0.03      # frames that sit within 3% can stand in when nothing is found
STRIP_SPREAD = 0.04      # a strip moves between scans: never a tight position prior
MAX_PRIORS = 4


def default_path() -> str:
    override = os.environ.get("FREECCR_CROP_MEMORY")     # tests / a portable setup
    if override:
        os.makedirs(os.path.dirname(os.path.abspath(override)), exist_ok=True)
        return override
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    folder = os.path.join(base, "FreeCCR")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "auto_crop_memory.json")


def setup_key(img, w: int, h: int) -> str:
    """`camera | preview WxH | single|merged`: one copy-stand setup."""
    info = getattr(img, "info", None) or {}
    cam = " ".join(str(v).strip() for v in (info.get("camera_make"), info.get("camera_model"))
                   if v and str(v).strip()) or "unknown camera"
    kind = "merged" if getattr(img, "is_merged", False) else "single"
    return f"{cam}|{int(w)}x{int(h)}|{kind}"


def box_of(rect_n, angle, w, h):
    """Normalised (x1, y1, x2, y2) + angle -> [cx, cy, bw, bh, angle] in px."""
    x1, y1, x2, y2 = rect_n
    return [float((x1 + x2) / 2 * w), float((y1 + y2) / 2 * h),
            float((x2 - x1) * w), float((y2 - y1) * h), float(angle or 0.0)]


def inward_shifts(det, final):
    """Per side, how far `final` sits inside `det` (L, R, T, B), as fractions of
    the detected box, measured in the detected box's own frame; plus the angle
    change. Positive = that edge was pulled in."""
    dcx, dcy, dw, dh, da = det
    fcx, fcy, fw, fh, fa = final
    t = np.deg2rad(da)
    c, s = np.cos(t), np.sin(t)
    vx, vy = fcx - dcx, fcy - dcy
    qx, qy = c * vx + s * vy, -s * vx + c * vy          # R(-a) . (C_f - C_d)
    return ((qx - fw / 2 + dw / 2) / dw, (dw / 2 - qx - fw / 2) / dw,
            (qy - fh / 2 + dh / 2) / dh, (dh / 2 - qy - fh / 2) / dh), fa - da


def det_record(fc):
    """What a detection contributes to an example: its box BEFORE any learned
    habit (so a habit is measured in full, not as the residual), its search
    box size, confidence and kind. None when nothing was found."""
    g = getattr(fc, "geom", None) or {}
    if fc is None or not g.get("out_rect") or not g.get("size"):
        return None
    w, h = g["size"]
    return {"box": [round(v, 3) for v in box_of(g["out_rect"], g.get("angle", fc.angle), w, h)],
            "gate": [round(float(v), 2) for v in g["gate"]],
            "conf": fc.confidence, "strip": bool(fc.strip), "fmt": fc.fmt or "",
            "soft": "".join(fc.soft or ()), "learned": fc.learned or ""}


def frame_like(final, w, h) -> bool:
    return final[2] * final[3] >= MIN_AREA * w * h


def _robust_spread(vals, dim):
    if len(vals) < 2:
        return DEFAULT_SPREAD * dim
    v = np.asarray(vals, float)
    return max(1.4826 * float(np.median(np.abs(v - np.median(v)))), MIN_SPREAD * dim)


class CropMemory:
    """The JSON store. Thread-safe: Convert All reads priors on a worker thread
    while the Crop panel records on the GUI thread."""

    def __init__(self, path: Optional[str] = None):
        self._path = path
        self._data = None
        self._lock = threading.RLock()

    @property
    def path(self) -> str:
        if self._path is None:
            self._path = default_path()
        return self._path

    def _load(self):
        if self._data is not None:
            return self._data
        data = None
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = None
        if not (isinstance(data, dict) and data.get("version") == VERSION
                and isinstance(data.get("setups"), dict)):
            data = {"version": VERSION, "setups": {}}
        self._data = data
        return data

    def _save(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f)
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"Could not save the crop memory: {e}")

    # --- reading ---------------------------------------------------------
    def examples(self, key) -> list:
        with self._lock:
            return list(self._load()["setups"].get(key, []))

    def get(self, key, ex_id):
        return next((e for e in self.examples(key) if e.get("id") == ex_id), None)

    def counts(self):
        """(learned examples, setups)."""
        with self._lock:
            setups = self._load()["setups"]
            return sum(len(v) for v in setups.values()), sum(1 for v in setups.values() if v)

    # --- writing ---------------------------------------------------------
    def record(self, key, final, w, h, det=None, strip=False, fmt="", ex_id=None) -> str:
        """Add (or, with `ex_id`, replace) one confirmed crop. `final`, and
        `det["box"]`, are [cx, cy, bw, bh, angle] px (see `box_of`)."""
        with self._lock:
            setups = self._load()["setups"]
            lst = setups.setdefault(key, [])
            ex = {"id": ex_id or uuid.uuid4().hex[:12], "t": time.time(), "w": int(w), "h": int(h),
                  "final": [round(float(v), 3) for v in final], "det": det,
                  "strip": bool(strip), "fmt": fmt or ""}
            if ex_id is not None:
                lst[:] = [e for e in lst if e.get("id") != ex_id]
            lst.append(ex)
            del lst[:-MAX_PER_SETUP]
            self._save()
            return ex["id"]

    def forget(self):
        with self._lock:
            self._data = {"version": VERSION, "setups": {}}
            self._save()

    # --- priors ----------------------------------------------------------
    def priors(self, key) -> List[Prior]:
        """The learned frames of one setup, most-used first."""
        exs = self.examples(key)
        clusters = []
        for ex in reversed(exs):                       # most recent first
            fw, fh = ex["final"][2:4]
            for c in clusters:
                rw, rh = c[0]["final"][2:4]
                if (c[0]["strip"] == ex["strip"] and abs(fw / rw - 1) < SIZE_TOL
                        and abs(fh / rh - 1) < SIZE_TOL):
                    c.append(ex)
                    break
            else:
                clusters.append([ex])
        out = [_prior_of(c) for c in clusters]
        out.sort(key=lambda p: -p.n)
        return out[:MAX_PRIORS]


def _prior_of(cluster) -> Prior:
    w, h = cluster[0]["w"], cluster[0]["h"]
    fin = np.array([e["final"] for e in cluster], float)
    fw, fh = float(np.median(fin[:, 2])), float(np.median(fin[:, 3]))
    cx, cy = float(np.median(fin[:, 0])), float(np.median(fin[:, 1]))
    sx, sy = _robust_spread(fin[:, 0], w), _robust_spread(fin[:, 1], h)
    strip = bool(cluster[0]["strip"])
    if strip:
        sx = max(sx, STRIP_SPREAD * w)
    per_side, dangles, shrinks = ([], [], [], []), [], []
    for e in cluster:
        det = e.get("det")
        if not det or not det.get("box"):
            continue
        sh, da = inward_shifts(det["box"], e["final"])
        soft = det.get("soft", "")
        seen = [i for i, side in enumerate("LRTB") if side not in soft]
        # a habit is how you move edges the detector SAW; a soft side is placed
        # by your frame size instead (core.auto_crop._snap_soft)
        if seen and max(abs(sh[i]) for i in seen) < HABIT_EXAMPLE_MAX:
            for i in seen:
                per_side[i].append(sh[i])
            dangles.append(da)
        if det.get("gate"):
            shrinks.append((det["gate"][0] - det["box"][2], det["gate"][1] - det["box"][3]))
    habit = tuple(float(np.median(v)) if v else 0.0 for v in per_side)
    dangle = float(np.median(dangles)) if dangles else 0.0
    if shrinks:
        shw, shh = (float(v) for v in np.median(shrinks, axis=0))
    else:
        shw = shh = 2 * (INSET_FRAC * min(fw, fh) + 5)
    hl, hr, ht, hb = habit
    # the detector's output box for this frame (before the habit), then its
    # search box: what the search must look for
    ow, oh = fw / max(1 - hl - hr, 0.5), fh / max(1 - ht - hb, 0.5)
    n = len(cluster)
    still = sx <= STILL_SPREAD * w and sy <= STILL_SPREAD * h
    fmts = [e.get("fmt") for e in cluster if e.get("fmt")]
    # the search box sits where the detector's output did: undo the habit's
    # asymmetry (pulling only the left edge in moves your box's centre right)
    gcx, gcy = cx - (hl - hr) / 2 * ow, cy - (ht - hb) / 2 * oh
    return Prior(gate=(ow + shw, oh + shh), centre=(gcx, gcy), spread=(sx, sy), habit=habit,
                 dangle=dangle, size=(fw, fh), out=(ow, oh), box_centre=(cx, cy),
                 angle=float(np.median(fin[:, 4])),
                 fallback=(n >= 2 and still) or (n == 1 and not strip),
                 fmt=max(set(fmts), key=fmts.count) if fmts else "", kind="matched", n=n,
                 strip=strip)


_memory = None


def memory() -> CropMemory:
    global _memory
    if _memory is None:
        _memory = CropMemory()
    return _memory
