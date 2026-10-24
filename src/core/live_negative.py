"""The Live Capture Monitor's Negative preview: a rough positive of the
white-lit feed (spec/live-capture-monitor.md §6). For judging framing,
focus and the frame, not colour: the feed is the camera's rendered 8-bit
picture, and nothing here reaches FreeCCR's conversion. No camera profile
or Film Look is applied either: both describe a camera's RAW (the Film Look
a particular capture), and the feed is neither.

One 256-entry table per channel, applied per frame with cv2.LUT:
1. invert and stretch between the film base and a highlight (two points,
   in the feed's encoded values: quiet, which a density model on 8 bits is
   not; see the measurements in the spec);
2. optionally a per-channel gamma so a sampled neutral mid-tone lands
   neutral (the third point);
3. Temperature / Tint as channel gains normalised so the largest is 1 (no
   channel clips before the others);
4. Brightness as a curve, t ** 2^(-b/50), that keeps black and white.
"""
import math

import numpy as np

TEMP_MAX = 0.35
TINT_MAX = 0.35
SPAN_MIN = 4.0           # base and highlight closer than this: not a usable pair
CLIP_LEVEL = 250         # auto base ignores near-white pixels (sprocket holes, light)


def gains(temperature=0, tint=0):
    """(R, G, B) gains, the largest 1. +temperature warmer (more red, less
    blue), +tint more magenta (less green)."""
    wd = max(-100, min(100, float(temperature))) / 100.0 * TEMP_MAX
    td = max(-100, min(100, float(tint))) / 100.0 * TINT_MAX
    r = 1.0 + wd + td / 2.0
    g = 1.0 - td
    b = 1.0 - wd + td / 2.0
    m = max(r, g, b)
    return r / m, g / m, b / m


def mid_gammas(base, hi, mid):
    """Per-channel (B, G, R) gammas that make `mid` come out equal in all
    three channels (at the geometric mean of their two-point values)."""
    tm = []
    for c in range(3):
        span = max(SPAN_MIN, float(base[c]) - float(hi[c]))
        tm.append(min(0.999, max(1e-3, (float(base[c]) - float(mid[c])) / span)))
    target = math.exp(sum(math.log(t) for t in tm) / 3.0)
    return [math.log(target) / math.log(t) for t in tm]


def tables(base, hi, mid=None, temperature=0, tint=0, brightness=0):
    """(256, 1, 3) uint8 tables in B, G, R order (the feed's), for cv2.LUT.
    `base`, `hi`, `mid` are B, G, R samples of the feed (0..255)."""
    v = np.arange(256, dtype=np.float64)
    gam = mid_gammas(base, hi, mid) if mid is not None else [1.0, 1.0, 1.0]
    gr, gg, gb = gains(temperature, tint)
    gain_bgr = (gb, gg, gr)
    expo = 2.0 ** (-max(-100, min(100, float(brightness))) / 50.0)
    out = np.zeros((256, 1, 3), np.uint8)
    for c in range(3):
        span = max(SPAN_MIN, float(base[c]) - float(hi[c]))
        t = np.clip((float(base[c]) - v) / span, 0.0, 1.0)
        t = t ** gam[c]
        t = t * gain_bgr[c]
        t = t ** expo
        out[:, 0, c] = np.clip(np.rint(t * 255.0), 0, 255).astype(np.uint8)
    return out


def usable(base, hi):
    return base is not None and hi is not None and all(
        float(base[c]) - float(hi[c]) >= SPAN_MIN for c in range(3))


def _picture_area(frame, guide=None):
    h, w = frame.shape[:2]
    if guide:
        cx, cy = guide.get("cx", 0.5) * w, guide.get("cy", 0.5) * h
        gw, gh = guide.get("w", 0.8) * w, guide.get("h", 0.8) * h
        x0, x1 = int(max(0, cx - gw / 2)), int(min(w, cx + gw / 2))
        y0, y1 = int(max(0, cy - gh / 2)), int(min(h, cy + gh / 2))
        if x1 - x0 >= 16 and y1 - y0 >= 16:
            return frame[y0:y1, x0:x1]
    return frame[h // 10: h - h // 10, w // 10: w - w // 10]


def _brightest_film(frame):
    """The clearest film in view: the frame averaged in 8x8 blocks (so noise
    can't push it up the way a high percentile of single pixels does), blocks
    touching bare light (sprocket holes, gaps) left out, then the median of
    the brightest tenth."""
    import cv2
    h, w = frame.shape[:2]
    k = 8 if min(h, w) >= 64 else 1
    blocks = cv2.resize(frame, (max(1, w // k), max(1, h // k)), interpolation=cv2.INTER_AREA)
    peak = cv2.dilate(frame, np.ones((3, 3), np.uint8)) if k > 1 else frame
    peak = cv2.resize(peak, (blocks.shape[1], blocks.shape[0]), interpolation=cv2.INTER_NEAREST)
    flat = blocks.reshape(-1, 3).astype(np.float64)
    clear = peak.reshape(-1, 3).max(axis=1) < CLIP_LEVEL
    keep = flat[clear] if clear.sum() >= 20 else flat
    lum = keep.mean(axis=1)
    top = keep[lum >= np.percentile(lum, 90)]
    return tuple(float(x) for x in np.median(top, axis=0))


def auto_points(frame, guide=None):
    """(base, highlight, note) from a BGR feed frame. The base is the
    brightest even border band (Auto black point's rebate search), else the
    clearest film in view (`_brightest_film`); the highlight
    is each channel's 0.5th percentile of the picture area (the frame guide
    when on, else the middle 80 %)."""
    frame = np.asarray(frame)
    base, note = None, ""
    try:
        from core.auto_black_point import find_rebate
        res = find_rebate(frame)
        if res.black_point is not None:
            base = tuple(float(x) for x in res.black_point)
            note = f"Film base from the {res.side} border"
    except Exception:
        base = None
    if base is None:
        base = _brightest_film(frame)
        note = "Film base: the brightest film in view (no clear border)"
    area = _picture_area(frame, guide).reshape(-1, 3)[::3]
    hi = tuple(float(x) for x in np.percentile(area, 0.5, axis=0))
    return base, hi, note
