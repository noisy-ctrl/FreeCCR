"""The flat field map (spec/flat-field-map.md): what a frame's flat field
correction does, as a display layer. Qt-free: the canvas draws the result.

Nothing here reaches the picture's pixels."""
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

EXAGGERATIONS = (1, 2, 4, 8)
DEFAULT_GAIN = 2
CONTOUR_STEP = {1: 0.1, 2: 0.05, 4: 0.02, 8: 0.01}
FULL_AT_EV = 0.5            # at ×1 a half-stop change is the full wash
WASH_ALPHA = 0.7
MAX_CONTOURS = 40
PINK = (255, 64, 170)       # lifts (the user's colour, 2026-11-20)
BLUE = (90, 170, 255)       # lowers: brighter than the flat's centre
WORK_LONG = 1600            # the full frame is rebuilt at this long side


def exaggeration(k) -> int:
    try:
        k = int(k)
    except (TypeError, ValueError):
        return DEFAULT_GAIN
    return k if k in EXAGGERATIONS else DEFAULT_GAIN


def frame_gain(profile, full_hw: Sequence[int], source_ops, out_wh: Tuple[int, int],
               mono: bool = False) -> Optional[np.ndarray]:
    """The (h, w, 3) gain the decode multiplied the frame by, at `out_wh`:
    built at the full decode's shape, cut by the frame's slice chain (a
    sliced frame shows its own part), resized to the preview's size."""
    from core import flat_field
    if profile is None or not full_hw:
        return None
    fh, fw = int(full_hw[0]), int(full_hw[1])
    if fh <= 0 or fw <= 0:
        return None
    s = WORK_LONG / float(max(fh, fw))
    w, h = max(2, int(round(fw * s))), max(2, int(round(fh * s)))
    g = flat_field.gain_for_size(profile, w, h).astype(np.float32, copy=True)
    if source_ops:
        from core.ccr_image import CCRImage
        for rotation, region in source_ops:
            g = CCRImage.apply_source_op(g, rotation, region)
        g = np.ascontiguousarray(g)
        g[g <= 0] = 1.0                      # a turned slice's empty corners: no change
    if mono:
        g = np.repeat(g.mean(axis=2, keepdims=True), 3, axis=2)
    ow, oh = int(out_wh[0]), int(out_wh[1])
    if (g.shape[1], g.shape[0]) != (ow, oh):
        g = cv2.resize(g, (ow, oh), interpolation=cv2.INTER_LINEAR)
    return np.ascontiguousarray(g, dtype=np.float32)


def ev_map(gain: np.ndarray) -> np.ndarray:
    """The change in stops: the mean of the three channels' log2 gain."""
    return np.log2(np.maximum(gain, 1e-4)).mean(axis=2).astype(np.float32)


def levels(ev: np.ndarray, k: int) -> List[float]:
    """The contour levels: every CONTOUR_STEP[k], 0 left out."""
    step = CONTOUR_STEP[exaggeration(k)]
    lo, hi = float(ev.min()), float(ev.max())
    out = [n * step for n in range(int(np.floor(lo / step)), int(np.ceil(hi / step)) + 1)
           if n != 0 and lo < n * step < hi]
    if len(out) > MAX_CONTOURS:                   # thin them evenly
        keep = int(np.ceil(len(out) / MAX_CONTOURS))
        out = out[::keep]
    return out


def layer_rgba(ev: np.ndarray, k: int, contours: bool = True) -> np.ndarray:
    """The map as straight (not premultiplied) RGBA uint8: the pink / blue
    wash, then the contour lines on top."""
    k = exaggeration(k)
    h, w = ev.shape
    a = np.clip(np.abs(ev) * k / FULL_AT_EV, 0.0, 1.0) * WASH_ALPHA
    out = np.zeros((h, w, 4), np.uint8)
    lift = ev >= 0
    out[..., :3] = np.where(lift[..., None], np.array(PINK, np.uint8),
                            np.array(BLUE, np.uint8))
    out[..., 3] = np.round(a * 255).astype(np.uint8)
    if contours:
        thick = max(1, int(round(max(h, w) / 700)))
        for lv in levels(ev, k):
            m = (ev >= lv).astype(np.uint8) if lv > 0 else (ev <= lv).astype(np.uint8)
            cs, _ = cv2.findContours(m, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            cs = [c for c in cs if len(c) >= 3]
            if not cs:
                continue
            cv2.polylines(out, cs, True, (0, 0, 0, 150), thick + 2, cv2.LINE_AA)
            cv2.polylines(out, cs, True, (255, 255, 255, 235), thick, cv2.LINE_AA)
    return out


def label_points(ev: np.ndarray, k: int) -> List[Tuple[float, float, str]]:
    """(x, y, text) for each contour: where it crosses the line from the
    centre to the strongest corner (lifts) or towards the lowest point."""
    h, w = ev.shape
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    corners = [(0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)]
    top = max(corners, key=lambda c: ev[c])
    low = np.unravel_index(int(np.argmin(ev)), ev.shape)
    out = []
    step = CONTOUR_STEP[exaggeration(k)]
    digits = 2 if step < 0.1 else 1
    for lv in levels(ev, k):
        ty, tx = top if lv > 0 else low
        for t in np.linspace(0.0, 1.0, 400):
            y = int(round(cy + (ty - cy) * t))
            x = int(round(cx + (tx - cx) * t))
            v = ev[y, x]
            if (lv > 0 and v >= lv) or (lv < 0 and v <= lv):
                out.append((float(x), float(y), f"{lv:+.{digits}f}"))
                break
    return out


def summary(profile, ev: Optional[np.ndarray]) -> dict:
    """Figures for the legend: the largest lift and lowering on this frame
    (mean of channels), and the flat's own per-channel corner figures."""
    corners = ((getattr(profile, "stats", None) or {}).get("corner_stops")
               if profile is not None else None) or []
    out = {"lift": 0.0, "lower": 0.0, "corners": [float(c) for c in corners][:3]}
    if ev is not None and ev.size:
        out["lift"] = max(0.0, float(ev.max()))
        out["lower"] = min(0.0, float(ev.min()))
    return out
