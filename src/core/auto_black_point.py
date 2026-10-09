"""Auto black point: measure the film base on each frame from its clear border.

No learned model: the clear border is the thinnest film there is (brightest in
every channel of the raw scan), it is an even strip, and it sits along the
frame edges between the dark holder and the denser image. See
spec/auto-black-point.md.
"""
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

EDGE_DEPTH = 0.065        # search the outer 6.5 % of the frame on each side
PROFILE_SPAN = 0.14       # profile taken over the central 14 % of the edge
BAND_SPAN = 0.60          # band measured over the central 60 % of the edge
MAX_SPREAD = 0.06         # flatness: IQR / median per channel
CLIP_SHARE = 0.25         # >= 25 % of a band's pixels sitting at the frame's
                          # ceiling in a channel = clipped (grain never does that)
MIN_STEP = 1.02           # band must be >= 2 % brighter than the image inside it
AGREE = 0.08              # two bands agree when every channel is within 8 %
ROLL_JUMP = 0.35          # all channels off the roll consensus by > 35 % -> suspect

HIGH, MEDIUM, LOW = "high", "medium", "low"


@dataclass
class Band:
    side: str
    value: np.ndarray            # per-channel median, input units
    spread: np.ndarray           # per-channel IQR / median
    rect: tuple                  # (x1, y1, x2, y2) normalised
    step: float                  # band luminance / inner luminance
    valid: bool = False
    why: str = ""


@dataclass
class RebateResult:
    black_point: Optional[tuple]          # array channel order, input units
    confidence: str                        # high | medium | low
    side: Optional[str] = None
    rect: Optional[tuple] = None           # normalised (x1, y1, x2, y2)
    reason: str = ""
    bands: list = field(default_factory=list)

    def summary(self):
        if self.black_point is None:
            return f"no clear border found ({self.reason})"
        return f"{self.side} border ({self.reason})"


def _lum(a):
    return a[..., 0] + a[..., 1] + a[..., 2]


N_SEGMENTS = 10           # the strip is followed in segments along the edge


def _oriented(raw, side):
    """View with the edge at row 0 and the image beyond (rows = across)."""
    if side == "top":
        return raw
    if side == "bottom":
        return raw[::-1]
    if side == "left":
        return raw.transpose(1, 0, 2)
    return raw[:, ::-1].transpose(1, 0, 2)


def _band(raw, side, interior_p99, vmax):
    h, w = raw.shape[:2]
    v = _oriented(raw, side)
    across, along = v.shape[:2]
    depth = max(8, int(round(EDGE_DEPTH * across)))
    half_t = max(1, int(round(0.0015 * across)))
    lo, hi = int(along * (0.5 - BAND_SPAN / 2)), int(along * (0.5 + BAND_SPAN / 2))
    edges = np.linspace(lo, hi, N_SEGMENTS + 1).astype(int)
    seg_vals, seg_steps, rows, pix = [], [], [], []
    for s0, s1 in zip(edges[:-1], edges[1:]):
        seg = v[:depth + 6 * half_t + 8, s0:s1].astype(np.float64)
        prof = _lum(seg[:depth].mean(1))
        i = int(np.argmax(prof))
        a, b = max(i - half_t, 0), i + half_t + 1
        px = seg[a:b].reshape(-1, 3)
        inner = seg[b + 2 * half_t + 1:b + 4 * half_t + 3].reshape(-1, 3)
        seg_vals.append(np.median(px, 0))
        seg_steps.append(_lum(np.median(px, 0)) / max(_lum(np.median(inner, 0)), 1e-9)
                         if inner.size else 1.0)
        rows.append((a, b))
        pix.append(px)
    seg_vals = np.array(seg_vals)
    med = np.median(seg_vals, 0)
    q1, q3 = np.percentile(seg_vals, [25, 75], 0)
    spread = (q3 - q1) / np.maximum(med, 1e-9)
    step = float(np.median(seg_steps))
    a0, b0 = min(r[0] for r in rows), max(r[1] for r in rows)
    # rect in normalised image coords
    if side == "top":
        rect = (lo / w, a0 / h, hi / w, b0 / h)
    elif side == "bottom":
        rect = (lo / w, (h - b0) / h, hi / w, (h - a0) / h)
    elif side == "left":
        rect = (a0 / w, lo / h, b0 / w, hi / h)
    else:
        rect = ((w - b0) / w, lo / h, (w - a0) / w, hi / h)
    band = Band(side, med, spread, rect, step)
    allpx = np.concatenate(pix)
    if np.any(spread > MAX_SPREAD):
        band.why = "not an even strip"
    elif any(np.mean(allpx[:, c] >= 0.999 * vmax[c]) >= CLIP_SHARE for c in range(3)):
        band.why = "clipped"
    elif step < MIN_STEP:
        band.why = "no step to the image"
    elif np.any(med < 0.9 * interior_p99):
        band.why = "darker than the image's thinnest areas"
    else:
        band.valid = True
    return band


def _agree(a, b):
    return bool(np.all(np.abs(a / np.maximum(b, 1e-9) - 1) <= AGREE))


def find_rebate(raw) -> RebateResult:
    """Measure the film base on one frame. `raw` is the unconverted preview
    (H, W, 3), any numeric dtype, values in the same units a manual sample
    would use (so the result is a drop-in black point)."""
    raw = np.asarray(raw)
    if raw.ndim != 3 or raw.shape[2] < 3 or min(raw.shape[:2]) < 40:
        return RebateResult(None, LOW, reason="image too small")
    raw = raw[..., :3]
    h, w = raw.shape[:2]
    interior = raw[h // 6:h - h // 6, w // 6:w - w // 6].reshape(-1, 3)
    p99 = np.percentile(interior[::7], 99, axis=0)
    vmax = np.maximum(raw.reshape(-1, 3).max(axis=0).astype(np.float64), 1.0)
    bands = [_band(raw, s, p99, vmax) for s in ("top", "bottom", "left", "right")]
    valid = [b for b in bands if b.valid]
    if not valid:
        clipped = [b for b in bands if b.why == "clipped"]
        if clipped:
            chans = "/".join("RGB"[c] for c in range(3)
                             if any(np.mean(b.value[c] >= 0.98 * vmax[c]) for b in clipped))
            return RebateResult(None, LOW, reason=f"border clipped in {chans or 'a channel'}: "
                                "expose that channel shorter", bands=bands)
        whys = ", ".join(f"{b.side}: {b.why}" for b in bands)
        return RebateResult(None, LOW, reason=whys, bands=bands)
    # groups of mutually agreeing bands
    best_group = []
    for b in valid:
        group = [c for c in valid if _agree(b.value, c.value)]
        if len(group) > len(best_group) or (
                len(group) == len(best_group)
                and max(_lum(c.value) for c in group) > max(_lum(c.value) for c in best_group)):
            best_group = group
    if len(best_group) >= 2:
        pick = max(best_group, key=lambda c: _lum(c.value))
        conf = HIGH
        reason = f"{len(best_group)} edges agree"
    else:
        pick = max(valid, key=lambda c: _lum(c.value))
        conf = MEDIUM
        reason = "one usable edge" if len(valid) == 1 else "edges disagree; brightest used"
    # A border at (or within reach of) the sensor ceiling is not a measurement:
    # if any edge clipped, or the pick sits within 10 % of the ceiling in a
    # channel where another edge clipped / 2 % anyway, fall back and say why.
    names = "RGB"
    clipped_ch = {c for b in bands if b.why == "clipped" for c in range(3)
                  if b.value[c] >= 0.98 * vmax[c]}
    near = [c for c in range(3)
            if pick.value[c] >= 0.98 * vmax[c]
            or (c in clipped_ch and pick.value[c] >= 0.90 * vmax[c])]
    if near:
        chans = "/".join(names[c] for c in near)
        return RebateResult(tuple(float(v) for v in pick.value), LOW, pick.side,
                            pick.rect, f"border clipped in {chans}: expose that "
                            "channel shorter", bands)
    return RebateResult(tuple(float(v) for v in pick.value), conf, pick.side,
                        pick.rect, reason, bands)


def roll_check(results):
    """Downgrade frames whose base jumps against the roll in EVERY channel the
    same way (light round the film, not a drifting LED). Returns the roll
    consensus (or None) and updates results in place."""
    good = [np.array(r.black_point) for r in results
            if r.black_point is not None and r.confidence in (HIGH, MEDIUM)]
    if len(good) < 3:
        return (tuple(float(v) for v in np.median(good, 0)) if good else None)
    cons = np.median(good, 0)
    for r in results:
        if r.black_point is None or r.confidence == LOW:
            continue
        ratio = np.array(r.black_point) / np.maximum(cons, 1e-9) - 1
        if np.all(ratio > ROLL_JUMP) or np.all(ratio < -ROLL_JUMP):
            r.confidence = LOW
            r.reason = "differs from the rest of the roll in every channel"
    good = [np.array(r.black_point) for r in results
            if r.black_point is not None and r.confidence in (HIGH, MEDIUM)]
    return tuple(float(v) for v in np.median(good, 0)) if good else None
