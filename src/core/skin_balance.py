"""Skin picker (spec/skin-picker.md): turn the skin in the circles onto a skin
line, keeping its brightness and saturation, by a closed loop on the real
render. Qt-free: the measuring is passed in.

Angles are the vectorscope's: atan2(Cr, Cb) of Rec.601 Cb/Cr on display
values (core/scopes)."""
import math

import numpy as np

# The user's lab scans: 12 skin patches (faces, forearms, a baby; sun, shade,
# indoor) on 3 frames from two labs: medians 140.9 and 139.9, all 140.4,
# range 123-149 (2026-11-22). About 8 degrees yellower than the video line.
LAB_LINE_DEG = 140.4

# The middle 80 % of those patches (10th-90th percentile): the range of hues
# the labs print skin at. A face inside it is left alone (spec section 0: a
# face can't tell a cast from the person's own skin).
LAB_RANGE = (128.9, 146.4)

RANGES = (("lab", "Your labs'"), ("video", "Around the video skin line"))
MODES = (("show", "Show it on the vectorscope"), ("pull", "Pull it into the skin range"))
AMOUNTS = (100, 75, 50, 25)
DEFAULT_RANGE, DEFAULT_MODE, DEFAULT_AMOUNT = "lab", "show", 100
DEFAULT_LINE = DEFAULT_RANGE
LINES = RANGES

MIN_CHROMA = 3.0          # of 255: less than this has no hue to turn
MAX_OFF_DEG = 90.0        # further from the line than this isn't skin
KEEP_LO, KEEP_HI = 15.0, 85.0   # luma percentiles kept inside the circles
MIN_RADIUS_PX = 4.0       # a smaller circle (scene px) is ignored

_CB_W = np.array([-0.168736, -0.331264, 0.5])
_CR_W = np.array([0.5, -0.418688, -0.081312])
_Y_W = np.array([0.299, 0.587, 0.114])


def video_line_deg() -> float:
    """The +I axis as FreeCCR's vectorscope draws it (132.5 degrees)."""
    from core import scopes
    cb, cr = scopes.skin_tone_direction()
    return math.degrees(math.atan2(cr, cb)) % 360.0


def line_deg(which: str) -> float:
    return video_line_deg() if which == "video" else LAB_LINE_DEG


def skin_range(which: str):
    """(lo, hi) degrees: the labs' range, or the same range moved onto the
    video line (121-138)."""
    if which == "video":
        d = video_line_deg() - LAB_LINE_DEG
        return LAB_RANGE[0] + d, LAB_RANGE[1] + d
    return LAB_RANGE


def outside_by(angle: float, rng) -> float:
    """Signed degrees outside the range: < 0 below (towards magenta), > 0
    above (towards green), 0 inside."""
    lo, hi = rng
    below, above = wrap(angle - lo), wrap(angle - hi)
    if below < 0:
        return below
    if above > 0:
        return above
    return 0.0


def describe(angle: float, rng, which: str = "lab") -> str:
    """The Show hint: where the face's hue sits against the range."""
    whose = "your labs' range" if which == "lab" else "the video skin range"
    off = outside_by(angle, rng)
    if off == 0:
        return f"Skin at {angle:.0f}\u00b0: inside {whose}."
    side = "towards magenta" if off < 0 else "towards green"
    return f"Skin at {angle:.0f}\u00b0: {abs(off):.0f}\u00b0 outside {whose}, {side}."


def range_target(y_cb_cr, rng, amount: float):
    """Pull: (Y, Cb, Cr) the skin should end at, its hue turned `amount`
    (0..1) of the way to the range's nearest edge, luma and chroma kept; None
    when it is already inside."""
    off = outside_by(angle_of(y_cb_cr), rng)
    if off == 0:
        return None
    edge = angle_of(y_cb_cr) - off
    return target_point(y_cb_cr, edge, amount)


def line_direction(deg: float):
    """Unit (cb, cr) of a line at `deg` on the vectorscope."""
    r = math.radians(deg)
    return math.cos(r), math.sin(r)


def wrap(deg: float) -> float:
    """An angle difference the short way round, in (-180, 180]."""
    d = (deg + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


def ycc(rgb):
    """(Y, Cb, Cr) of display RGB on 0..255 (the vectorscope's weights)."""
    rgb = np.asarray(rgb, dtype=np.float64)
    return np.array([rgb @ _Y_W, rgb @ _CB_W, rgb @ _CR_W])


def angle_of(y_cb_cr) -> float:
    return math.degrees(math.atan2(y_cb_cr[2], y_cb_cr[1])) % 360.0


def chroma_of(y_cb_cr) -> float:
    return math.hypot(y_cb_cr[1], y_cb_cr[2])


def refusal(y_cb_cr, line: float):
    """Why this colour can't be balanced on (a hint), or None."""
    if chroma_of(y_cb_cr) < MIN_CHROMA:
        return "There's no colour in that circle to balance on."
    off = abs(wrap(line - angle_of(y_cb_cr)))
    if off > MAX_OFF_DEG:
        return f"That doesn't look like skin: it's {off:.0f}° from the skin line."
    return None


def refusal_range(y_cb_cr, rng):
    """refusal() against a range: too little colour, or too far outside it
    to be skin."""
    if chroma_of(y_cb_cr) < MIN_CHROMA:
        return "There's no colour in that circle to read."
    off = abs(outside_by(angle_of(y_cb_cr), rng))
    if off > MAX_OFF_DEG:
        return (f"That doesn't look like skin: its colour is {off:.0f}\u00b0 "
                f"from the skin range.")
    return None


def knobs_for(img, positive=False):
    """Which sliders a pull moves: the Printer Lights (R/G/B Shift: a density
    offset) on a negative converted on its density ("bw" conversions,
    anchored or not), Temperature/Tint on a positive or a reference-frame
    conversion, where they are a true white balance."""
    ci = getattr(img, "conversion_inputs", None) or {}
    if not positive and ci.get("mode") == "bw":
        return ("ch_r_shift", "ch_g_shift", "ch_b_shift")
    return ("temperature", "tint")


def target_point(y_cb_cr, line: float, amount: float):
    """(Y, Cb, Cr) the skin should end at: its hue turned `amount` (0..1) of
    the way to `line` the short way round, its luma and chroma kept."""
    y, rho = float(y_cb_cr[0]), chroma_of(y_cb_cr)
    th = angle_of(y_cb_cr) + float(amount) * wrap(line - angle_of(y_cb_cr))
    r = math.radians(th)
    return np.array([y, rho * math.cos(r), rho * math.sin(r)])


def keep_pixels(rgb, mask=None):
    """Indices (into rgb.reshape(-1, 3)) of the masked pixels between the
    KEEP_LO and KEEP_HI luma percentiles: eyes, brows, beard shadow and shine
    drop out. Computed once, on the render at the starting values."""
    flat = np.asarray(rgb, dtype=np.float64).reshape(-1, 3)
    idx = np.arange(flat.shape[0])
    if mask is not None:
        idx = idx[np.asarray(mask, bool).reshape(-1)]
    if idx.size == 0:
        return idx
    y = flat[idx] @ _Y_W
    lo, hi = np.percentile(y, [KEEP_LO, KEEP_HI])
    keep = idx[(y >= lo) & (y <= hi)]
    return keep if keep.size else idx


def solve(measure, start, target, step, lo, hi, quantum, weights=None,
          iterations=6, tol=0.3):
    """Knob values (len(start)) whose measured output (len(target)) hits
    `target`: a damped Newton on the measured output, the Jacobian by finite
    differences of `step`; the answer is rounded to `quantum` and clamped to
    [lo, hi]. The best measured result is kept, so it is never worse than the
    start. Returns (values, error, renders)."""
    x = np.asarray(start, dtype=np.float64).copy()
    t = np.asarray(target, dtype=np.float64)
    w = np.ones_like(t) if weights is None else np.asarray(weights, dtype=np.float64)
    n = [0]

    def f(v):
        n[0] += 1
        return (np.asarray(measure(v), dtype=np.float64) - t) * w

    def snap(v):
        return np.clip(np.round(np.asarray(v) / quantum) * quantum, lo, hi)

    def err(r):
        return float(np.sqrt(np.mean(r ** 2)))

    r = f(x)
    best_x, best_e = x.copy(), err(r)
    for _ in range(iterations):
        if best_e <= tol:
            break
        J = np.empty((t.size, x.size))
        for k in range(x.size):
            h = step if x[k] + step <= hi else -step
            xp = x.copy()
            xp[k] += h
            J[:, k] = (f(xp) - r) / h
        dx, *_ = np.linalg.lstsq(J, -r, rcond=None)
        e0, moved = err(r), False
        for _half in range(5):
            xn = np.clip(x + dx, lo, hi)
            rn = f(xn)
            if err(rn) < e0:
                x, r, moved = xn, rn, True
                break
            dx = dx / 2.0
        if not moved:
            break
        if err(r) < best_e:
            best_x, best_e = x.copy(), err(r)
    xs = snap(best_x)
    es = err(f(xs))
    x0 = snap(start)
    if np.array_equal(xs, x0):
        return xs, es, n[0]
    e0 = err(f(x0))
    return (xs, es, n[0]) if es <= e0 else (x0, e0, n[0])
