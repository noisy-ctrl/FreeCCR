"""Nothing clips at conversion (spec/convert-crop-and-clip.md §2). Qt-free.

A picture pixel CLIPS when, in the 8-bit render the preview and the
histogram use, any channel is 255 (white) or 0 (black), after a 3x3 median:
a lone dust speck or hot pixel doesn't decide the frame, a clipped area of
two pixels or more does. The guard drives that count to zero with one scalar
per end, on the real render (a closed loop: the stages after Auto tone -
AWB, contrast, the negative look, saturation - are not modelled).
"""
import cv2
import numpy as np

MODES = ("rolloff", "brightest", "legacy")
DEFAULT_MODE = "rolloff"
LABELS = {
    "rolloff": "Roll off the ends (nothing clips)",
    "brightest": "White at the brightest point (nothing clips)",
    "legacy": "As before (the brightest 1–2 % may clip)",
}

TOP_PAD = 0.04           # rolloff: the first shoulder reaches this past the brightest
                         # point (one mapped exactly to 1.0 is 255: clipped)
AIM = 0.985              # brightest: the first gain puts the brightest point here
REACH_MAX = 4.0          # rolloff: the shoulder may reach this many times further
REACH_PAD = 0.03         # ... with this much more reach per unit at least
GAIN_FLOOR = 0.7         # brightest: the gain may drop to this share of the first
LIFT_EXTRA_MAX = 0.06    # black: at most this much lift on top of Auto tone's
STEPS_WHITE = 3          # bisection steps (renders) per end
STEPS_BLACK = 4          # finer: an over-lift greys the shadows


def clean_mode(mode):
    return mode if mode in MODES else DEFAULT_MODE


def _area(arr, rect):
    if rect is None:
        return arr
    h, w = arr.shape[:2]
    x1, y1, x2, y2 = (float(v) for v in rect)
    return arr[max(0, int(y1 * h)):min(h, int(round(y2 * h))),
               max(0, int(x1 * w)):min(w, int(round(x2 * w)))]


def clip_counts(u16, rect=None):
    """(black, white, total): picture pixels with a channel at 0 / at 255 in
    the 8-bit render, after a 3x3 median, inside `rect`."""
    p8 = cv2.convertScaleAbs(np.ascontiguousarray(u16), alpha=255.0 / 65535.0)
    if p8.ndim == 3:
        p8 = np.ascontiguousarray(p8[..., :3])
    p8 = cv2.medianBlur(p8, 3)
    if p8.ndim == 2:
        p8 = p8[..., None]
    a = _area(p8, rect)
    black = int(np.count_nonzero(np.any(a == 0, axis=-1)))
    white = int(np.count_nonzero(np.any(a == 255, axis=-1)))
    return black, white, int(a.shape[0] * a.shape[1])


def robust_extremes(display, rect=None):
    """(lowest, highest) of the picture's display values (float, any range):
    the 3x3-median of each pixel's darkest and brightest channel, so a lone
    speck doesn't count."""
    a = np.ascontiguousarray(_area(np.asarray(display, np.float32), rect))
    if a.ndim == 2:
        a = a[..., None]
    lo = cv2.medianBlur(np.ascontiguousarray(a.min(axis=2)), 3)
    hi = cv2.medianBlur(np.ascontiguousarray(a.max(axis=2)), 3)
    return float(lo.min()), float(hi.max())


def bisect_clear(clips, lo, hi, steps=4):
    """The smallest x in [lo, hi] (to within (hi - lo) / 2**steps) for which
    `clips(x)` is False, assuming it is True at lo and only goes False as x
    grows. (x, cleared): when even `hi` clips it returns (hi, False) - as far
    as the guard may go. `hi` itself is only tried when no midpoint cleared."""
    cleared = False
    for _ in range(steps):
        mid = 0.5 * (lo + hi)
        if clips(mid):
            lo = mid
        else:
            hi, cleared = mid, True
    if not cleared:
        cleared = not clips(hi)
    return hi, cleared
