"""A Film Look's shoulder, made smooth (spec/film-look-lut.md, revision
2026-11-01 (2)).

A fitted look's top end is the least supported part of the fit: lab scans
have few unclipped samples that dense, so the tone curve above them is
guessed. On "Noritsu Lomo 800" it went almost flat (red 245 → 247 over 0.14
density) and then jumped to 255: whites lost their detail, and a frame
brightened with Master Shift pushed them into that plateau. The user,
2026-11-01: "The Lomo 800 LUT is flattening whites and there's a loss of
detail."

`reshape(table, lo, hi, detail)` rebuilds the shoulder along the grey axis
and carries the change to every node:

1. The grey axis's luma `Y(x)` (Rec.709 weights on the table's own nodes,
   which is exactly the LUT along its diagonal: trilinear is linear between
   nodes there).
2. A slope floor `s = r · m`, `m` the median slope where 0.25 < Y < 0.75 and
   `r = FLOOR_MIN + detail · (FLOOR_MAX − FLOOR_MIN)`.
3. The knee: the first node with Y ≥ 0.5 after which the slope falls below
   the floor. Below it nothing changes.
4. From the knee, straight at the floor's slope to `top − ROLL`, then a
   Hermite roll (slope `s` → 0, C¹) into `top`, the highest level the old
   curve reaches (white, or a tinted top just under it). If that would end
   past the old curve's own white point (where it first comes within 0.005
   of `top`), one Hermite from the knee to that point instead, starting at
   the steepest slope that stays monotone: so at detail 0 nothing reaches
   white that didn't before; more detail buys contrast in the highlights
   with white reached earlier.
5. Per node, the new grey-axis luma becomes a HEADROOM ratio
   `(1 − Y'(x̄)) / (1 − Y(x̄))`, clipped to [0, 1], at the node's own luma
   input `x̄`, applied to all three outputs: `out' = 1 − (1 − out) · ratio`.
   So the tint the look gives its highlights is kept, nothing is ever darker
   than before (ratio ≤ 1), and below the knee the table is returned exactly.

`headroom(table, lo, hi)` (revision 4) carries the top end on past white
instead, so a later control that brings white down finds the detail there.

Qt-free; numpy only.
"""
import numpy as np

LUMA = np.array([0.2126, 0.7152, 0.0722])
FLOOR_MIN = 0.25        # detail 0: only the plateau and the jump are removed
FLOOR_MAX = 0.70        # detail 1: highlights keep 70 % of the mid-tone contrast
WHITE_Y = 0.995         # the old curve's white point: first node at or past this
ROLL = 0.04             # the last 10 levels before white are a soft roll, not a corner


def _hermite(x0, y0, m0, x1, y1, m1, x):
    h = x1 - x0
    t = np.clip((x - x0) / h, 0.0, 1.0)
    t2, t3 = t * t, t * t * t
    return ((2 * t3 - 3 * t2 + 1) * y0 + (t3 - 2 * t2 + t) * h * m0
            + (-2 * t3 + 3 * t2) * y1 + (t3 - t2) * h * m1)


def grey_axis(table):
    """(n, 3) outputs along the diagonal, R, G, B (table indexed [b][g][r])."""
    n = table.shape[0]
    i = np.arange(n)
    return np.asarray(table, np.float64)[i, i, i]


def shoulder_curve(Y, x, detail=0.0):
    """(Y', info) for the grey-axis luma Y at nodes x; info None when the
    curve has no knee (nothing to do)."""
    Y = np.asarray(Y, np.float64)
    x = np.asarray(x, np.float64)
    step = x[1] - x[0]
    slope = np.diff(Y) / step
    mid = 0.5 * (Y[1:] + Y[:-1])
    sel = (mid > 0.25) & (mid < 0.75)
    if not sel.any():
        return Y.copy(), None
    m = float(np.median(slope[sel]))
    if m <= 0:
        return Y.copy(), None
    detail = float(np.clip(detail, 0.0, 1.0))
    floor = (FLOOR_MIN + detail * (FLOOR_MAX - FLOOR_MIN)) * m
    knees = [i for i in range(len(slope)) if Y[i] >= 0.5 and slope[i] < floor]
    if not knees:
        return Y.copy(), None
    k = knees[0]
    xk, yk = float(x[k]), float(Y[k])
    # the top the old curve reaches (white, or a tinted top a little under it)
    top = float(Y[k:].max())
    if yk >= top - ROLL:
        return Y.copy(), None
    past = np.nonzero((Y >= top - (1.0 - WHITE_Y)) & (x > xk))[0]
    x_white_old = float(x[past[0]]) if len(past) else float(x[-1])
    # straight at the floor's slope up to top - ROLL, then a Hermite roll
    # into the top over 2·ROLL/floor (C1 at both joins) ...
    x_roll = xk + (top - ROLL - yk) / floor
    x_white = x_roll + 2.0 * ROLL / floor
    out = Y.copy()
    tail = x >= xk
    if x_white <= x_white_old:
        xt = x[tail]
        out[tail] = np.where(xt <= x_roll, yk + floor * (xt - xk),
                             np.where(xt >= x_white, top,
                                      _hermite(x_roll, top - ROLL, floor,
                                               x_white, top, 0.0, xt)))
        m0 = floor
    else:
        # ... unless that would end past where the top was already reached:
        # then one Hermite from the knee to the old white point, with the
        # start slope that fits (monotone needs it within 3x the mean slope)
        x_white = x_white_old
        mean = (top - yk) / max(x_white - xk, step)
        m0 = min(floor, 3.0 * mean)
        out[tail] = np.where(x[tail] >= x_white, top,
                             _hermite(xk, yk, m0, x_white, top, 0.0, x[tail]))
    out = np.maximum(out, Y)        # never under the old curve (ratio <= 1 anyway)
    return out, {"knee": xk, "knee_y": yk, "white": x_white,
                 "white_before": x_white_old, "floor": floor, "mid_slope": m,
                 "start_slope": m0, "top": top}


def reshape(table, lo, hi, detail=0.0):
    """(new table, info) — see the module docstring. `table` (n, n, n, 3)
    indexed [b][g][r] over [lo, hi] on every axis."""
    table = np.asarray(table, np.float64)
    n = table.shape[0]
    x = np.linspace(float(lo), float(hi), n)
    Y = grey_axis(table) @ LUMA
    Yn, info = shoulder_curve(Y, x, detail)
    if info is None:
        return table.copy(), None
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio_nodes = np.where(1.0 - Y > 1e-4, (1.0 - Yn) / (1.0 - Y), 0.0)
    ratio_nodes = np.clip(ratio_nodes, 0.0, 1.0)
    ratio_nodes[x < info["knee"]] = 1.0
    # each output channel by ITS OWN input density (r for red, ...): on the
    # grey axis that is exactly the new curve; off it, a channel moves
    # towards white only as far as the grey axis would at its own density
    out = table.copy()
    axis_of = {0: 2, 1: 1, 2: 0}                    # output channel -> its input axis
    for c in range(3):
        shape = [1, 1, 1]
        shape[axis_of[c]] = n
        ratio = np.broadcast_to(ratio_nodes.reshape(shape), table.shape[:3])
        moved = ratio < 1.0
        out[..., c][moved] = np.clip(1.0 - (1.0 - table[..., c][moved]) * ratio[moved],
                                     0.0, 1.0)
    return out, info               # nodes not moved are returned exactly


KNEE_Y = 0.97           # headroom: a channel's last steady stretch starts here (0.95
                        # moved near-white saturated colours up to 4 levels as rendered)
TOP_MIN = 1.0 - 2.0 / 255   # ... for a look whose whites reach white; one whose top
                            # stops under it (a matte white) would have it brightened


def headroom(table, lo, hi):
    """(new table, info): the look's top end carried on past white
    (spec/film-look-lut.md revision 4), or (copy, None) when the look's
    whites don't reach white (TOP_MIN) or its grey axis never nears it.

    For each output channel, along the grey axis: from the first node where
    that channel reaches KNEE_Y, its curve goes on as a straight line at the
    slope it had over the two segments before, above 1.0 where it gets there.
    The difference d_c(x) (never negative) is ADDED to that output channel at
    its own input density (red by red, ...): below the knee nothing changes,
    the grey axis keeps its tint, nothing is darker. With the display's clamp
    the look renders as before except for the last levels before white;
    brought down later (Master Gain, the White Point), the densities that used
    to be one white come apart."""
    table = np.asarray(table, np.float64)
    n = table.shape[0]
    x = np.linspace(float(lo), float(hi), n)
    step = x[1] - x[0]
    grey = grey_axis(table)                               # (n, 3): R, G, B
    Y = grey @ LUMA
    if float(Y.max()) < TOP_MIN:
        return table.copy(), None                         # its whites aren't white
    out = table.copy()
    axis_of = {0: 2, 1: 1, 2: 0}                          # output channel -> its input axis
    knees, slopes, line_y = [], [], np.zeros_like(grey)
    for c in range(3):
        cur = grey[:, c]
        at = np.nonzero(cur >= KNEE_Y)[0]
        if not len(at) or at[0] < 2:
            return table.copy(), None
        k = int(at[0])
        slope = float((cur[k] - cur[k - 2]) / (2.0 * step))
        if slope <= 0:
            return table.copy(), None
        line = cur.copy()
        line[k:] = cur[k] + slope * (x[k:] - x[k])
        line_y[:, c] = line
        d = np.maximum(line - cur, 0.0)
        shape = [1, 1, 1]
        shape[axis_of[c]] = n
        out[..., c] = table[..., c] + np.broadcast_to(d.reshape(shape), table.shape[:3])
        knees.append(float(x[k]))
        slopes.append(slope)
    Yn = line_y @ LUMA
    white_now = x[np.nonzero(Yn >= 1.0)[0][0]] if np.any(Yn >= 1.0) else x[-1]
    return out, {"knee": min(knees), "knee_y": float(np.interp(min(knees), x, Y)),
                 "slope": float(np.dot(slopes, LUMA)), "knees": knees, "slopes": slopes,
                 "white_was": float(x[np.nonzero(Y >= WHITE_Y)[0][0]])
                 if np.any(Y >= WHITE_Y) else float(x[-1]),
                 "white_now": float(white_now), "top": float(Yn[-1])}


def report(table, new, lo, hi):
    """Measured numbers for the log and the tests: grey-axis luma levels (of
    255) over the top densities, before and after, and monotonicity."""
    n = table.shape[0]
    x = np.linspace(float(lo), float(hi), n)
    Y0 = grey_axis(table) @ LUMA
    Y1 = grey_axis(new) @ LUMA

    def span(Y, a, b):
        return float((np.interp(b, x, Y) - np.interp(a, x, Y)) * 255.0)

    def flattest(Y, a, b, step=0.05):
        """The fewest levels any `step`-wide density band in [a, b] gets,
        among bands still below white (clipped is clipped, not flat)."""
        starts = np.arange(a, b - step + 1e-9, 0.01)
        spans = [span(Y, s, s + step) for s in starts
                 if np.interp(s + step, x, Y) < 0.999]
        return min(spans) if spans else 0.0
    def white_at(Y):
        hit = np.nonzero(Y >= WHITE_Y)[0]
        return float(x[hit[0]]) if len(hit) else float(x[-1])
    return {
        "white_at": (white_at(Y0), white_at(Y1)),
        "levels_0.65_0.80": (span(Y0, 0.65, 0.80), span(Y1, 0.65, 0.80)),
        "levels_0.80_0.97": (span(Y0, 0.80, 0.97), span(Y1, 0.80, 0.97)),
        "flattest_0.05_band_0.65_0.97": (flattest(Y0, 0.65, 0.97), flattest(Y1, 0.65, 0.97)),
        "monotone_after": bool(np.all(np.diff(Y1) >= -1e-9)),
        "never_darker": bool(np.all(new >= np.asarray(table, np.float64) - 1e-9)),
    }
