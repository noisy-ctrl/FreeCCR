"""Scale the White Point and Black Point sliders so a chosen share of the
picture clips at each end (spec/histogram-clip-levels.md). Qt-free.

A closed loop on the real render, as the neutral solves are: `measure(white,
black)` renders the frame with those two slider values and returns the
(black, white) shares of the picture that clip (core.clip_guard's
definition). Both shares are non-decreasing in their own slider, so each is
a bisection on the slider's own grid.
"""
import math

SHARES = (0.0, 0.02)            # the menu's two choices, per end
STEP = 0.1                      # the sliders move in tenths


def _grid(lo, hi, step):
    n = int(round((float(hi) - float(lo)) / step))

    def at(i):
        return round(float(lo) + i * step, 6)
    return n, at


def largest_within(f, share, lo, hi, step=STEP):
    """The largest x on the grid lo, lo + step, …, hi with f(x) <= share,
    for f non-decreasing. Returns (x, where): where is "" inside the range,
    "top" when even hi is within (more travel would clip less than asked
    for), "bottom" when even lo is over (the slider can't reach it)."""
    n, at = _grid(lo, hi, step)
    if f(at(n)) <= share:
        return at(n), "top"
    if f(at(0)) > share:
        return at(0), "bottom"
    a, b = 0, n                      # f(at(a)) <= share < f(at(b))
    while b - a > 1:
        m = (a + b) // 2
        if f(at(m)) <= share:
            a = m
        else:
            b = m
    return at(a), ""


def refine(f, share, x0, lo, hi, step=STEP):
    """`largest_within` near a guess x0 (from a smaller render): widen a
    bracket round x0 by doubling, then bisect. Same answer and `where` as
    largest_within for f non-decreasing, in a few calls when x0 is close."""
    n, at = _grid(lo, hi, step)
    i0 = min(max(int(round((float(x0) - float(lo)) / step)), 0), n)
    if f(at(i0)) <= share:
        a, k = i0, 1
        while True:                       # find an index over, going up
            j = min(i0 + k, n)
            if f(at(j)) > share:
                b = j
                break
            a = j
            if j == n:
                return at(n), "top"
            k *= 2
    else:
        b, k = i0, 1
        while True:                       # find an index within, going down
            j = max(i0 - k, 0)
            if f(at(j)) <= share:
                a = j
                break
            b = j
            if j == 0:
                return at(0), "bottom"
            k *= 2
    while b - a > 1:
        m = (a + b) // 2
        if f(at(m)) <= share:
            a = m
        else:
            b = m
    return at(a), ""


def solve(measure, share, white_range, black_range, black0=0.0, step=STEP,
          coarse=None):
    """`coarse` (optional): a cheaper measure (a smaller render) solved
    first; its answer is then refined on `measure` (`refine`), which is
    what the full render is used for."""
    if coarse is not None:
        w0, b0, cinfo = solve(coarse, share, white_range, black_range, black0, step)
        return _refine_pair(measure, share, white_range, black_range, w0, b0, step,
                            cinfo["renders"])
    return _solve_pair(measure, share, white_range, black_range, black0, step)


def _memo(measure):
    memo, calls = {}, [0]

    def m(w, b):
        key = (round(w, 6), round(b, 6))
        if key not in memo:
            calls[0] += 1
            memo[key] = tuple(float(v) for v in measure(key[0], key[1]))
        return memo[key]
    return m, calls


def _refine_pair(measure, share, white_range, black_range, w0, b0, step, coarse_n):
    m, calls = _memo(measure)
    (wlo, whi), (blo, bhi) = white_range, black_range
    white, wlim = refine(lambda w: m(w, b0)[1], share, w0, wlo, whi, step)
    black, blim = refine(lambda b: m(white, b)[0], share, b0, blo, bhi, step)
    if black != b0:
        white, wlim = refine(lambda w: m(w, black)[1], share, white, wlo, whi, step)
    b_share, w_share = m(white, black)
    return white, black, {"renders": calls[0], "coarse_renders": coarse_n,
                          "black_share": b_share, "white_share": w_share,
                          "white_limit": wlim, "black_limit": blim}


def _solve_pair(measure, share, white_range, black_range, black0=0.0, step=STEP):
    """(white, black, info). `measure(white, black)` -> (black share, white
    share). White first at the frame's Black Point, then Black, then White
    once more: the Black Point remap lifts the highlights a little, so White
    is kept if it is still within and one step up is over, else solved
    again. info: renders, the shares at the answer, "white_limit" /
    "black_limit" ("" / "top" / "bottom")."""
    m, calls = _memo(measure)
    wlo, whi = white_range
    blo, bhi = black_range
    black = min(max(float(black0), blo), bhi)
    white, wlim = largest_within(lambda w: m(w, black)[1], share, wlo, whi, step)
    black, blim = largest_within(lambda b: m(white, b)[0], share, blo, bhi, step)
    within = m(white, black)[1] <= share
    up = round(white + step, 6)
    if not (within and (up > whi + 1e-9 or m(up, black)[1] > share)):
        white, wlim = largest_within(lambda w: m(w, black)[1], share, wlo, whi, step)
    b_share, w_share = m(white, black)
    return white, black, {"renders": calls[0], "black_share": b_share,
                          "white_share": w_share, "white_limit": wlim,
                          "black_limit": blim}


def percent(share):
    """A share as the hint says it: 0.4 %, 2 %, 0.03 %."""
    p = 100.0 * float(share)
    if p == 0:
        return "0 %"
    if p >= 1:
        return f"{p:.1f}".rstrip("0").rstrip(".") + " %"
    digits = max(1, -int(math.floor(math.log10(p))))
    return f"{p:.{digits}f} %"


def describe(white, black_shown, share, info):
    """The hint after a solve. `black_shown` is the Black Point as its slider
    shows it (reversed from the stored value)."""
    def num(v):
        v = round(float(v), 1)
        return str(int(v)) if v.is_integer() else f"{v:.1f}"
    head = f"White Point {num(white)} · Black Point {num(black_shown)}: "
    notes = []
    for end, lim, got in (("White Point", info.get("white_limit"), info.get("white_share")),
                          ("Black Point", info.get("black_limit"), info.get("black_share"))):
        if lim == "bottom" and got is not None and got > share:
            notes.append(f"{end} is at the end of its range: {percent(got)} still clips")
        elif lim == "top" and got is not None and got <= share:
            what = "white" if end == "White Point" else "black"
            if share <= 0 or got <= 0:
                notes.append(f"{end} is at the end of its range: the picture "
                             f"doesn't reach {what}")
            elif got < share:
                notes.append(f"{end} is at the end of its range: only {percent(got)} "
                             f"reaches {what}")
    if notes:
        return head + "; ".join(notes) + "."
    if share <= 0:
        return head + "the picture just reaches white and black."
    return head + f"{percent(share)} clips at each end."
