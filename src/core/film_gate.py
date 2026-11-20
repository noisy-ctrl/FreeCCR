"""A camera gate's outline (DRAFT, the user 2026-11-20: "the film gate isn't
perfectly square. It stays on the same camera and doesn't vary between frames.
Pick one and stick with it for the same format" … "Maybe model off of my own
negatives?" … "It should be a little squarer").

Where a gate was measured on the user's scans (`core.film_gate_data`), the
outline is that gate made squarer (`squared`): its wander along each side
kept at SQUARE_KEEP of its size and capped at SQUARE_CAP mm (a deep nick is
flattened), its corners' rounding × CORNER_KEEP. Other formats get a drawn
gate, the same each time for the format, until one is measured.

One outline per film format, fixed (seeded by the format's key), in mm on
the frame's image area: each side a little bowed and wavy, with a few small
bumps and nicks (a filed or worn gate), corners slightly rounded. The
outline only ever comes INWARD of the image area (0 .. -MAX_IN mm), so the
picture under it is always there: a frame drawn into its slot and clipped by
the gate loses at most MAX_IN mm at an edge. Qt-free."""
import math
import random
from typing import List, Tuple

MAX_IN = 0.28            # mm: the deepest the gate comes into the picture
CORNER_R = (0.25, 0.55)  # mm: the corners' rounding, per corner
STEP = 0.1               # mm between outline points


def _side_profile(length: float, rng: random.Random) -> List[float]:
    """Inward offsets (mm, >= 0) along one side, every STEP mm."""
    n = max(2, int(round(length / STEP)) + 1)
    bow = rng.uniform(0.0, 0.10)                      # a slight bow, deepest mid-side
    waves = [(rng.uniform(4.0, 22.0), rng.uniform(0, 2 * math.pi), rng.uniform(0.01, 0.035))
             for _ in range(3)]
    bumps = []
    for _ in range(rng.randint(1, 4)):                # nicks and bumps of a worn gate
        bumps.append((rng.uniform(0.08, 0.92) * length, rng.uniform(0.25, 1.4),
                      rng.uniform(-0.12, 0.16)))
    out = []
    for i in range(n):
        s = i * length / (n - 1)
        u = s / length
        v = bow * math.sin(math.pi * u)
        v += sum(a * math.sin(2 * math.pi * s / wl + ph) for wl, ph, a in waves)
        v += sum(h * math.exp(-((s - c) / w) ** 2) for c, w, h in bumps)
        out.append(v)
    lo = min(out)
    out = [v - lo + 0.02 for v in out]                # all inward, the shallowest 0.02 mm
    hi = max(out)
    if hi > MAX_IN:
        out = [v * MAX_IN / hi for v in out]
    return out


def gate_outline(fmt: str, width_mm: float, height_mm: float) -> List[Tuple[float, float]]:
    """The gate's outline for `fmt` on a width x height mm image area, as
    points (x, y) mm clockwise from the top-left, the image area's own
    coordinates (0..width, 0..height). The same format always gives the same
    gate (the sides are seeded by the format and which side they are, so a
    frame's size, e.g. a turned frame, keeps the gate's character)."""
    rng = random.Random(f"freeccr-gate-{fmt}")
    radii = [rng.uniform(*CORNER_R) for _ in range(4)]
    sides = [_side_profile(width_mm, random.Random(f"gate-{fmt}-top")),
             _side_profile(height_mm, random.Random(f"gate-{fmt}-right")),
             _side_profile(width_mm, random.Random(f"gate-{fmt}-bottom")),
             _side_profile(height_mm, random.Random(f"gate-{fmt}-left"))]
    W, H = width_mm, height_mm
    pts: List[Tuple[float, float]] = []

    def along(side, k, n):
        s = k / (n - 1)
        return s

    # top: left -> right, inward = +y
    top, right, bottom, left = sides
    n = len(top)
    for k, d in enumerate(top):
        pts.append((W * k / (n - 1), d))
    n = len(right)
    for k, d in enumerate(right):
        pts.append((W - d, H * k / (n - 1)))
    n = len(bottom)
    for k, d in enumerate(bottom):
        pts.append((W - W * k / (n - 1), H - d))
    n = len(left)
    for k, d in enumerate(left):
        pts.append((d, H - H * k / (n - 1)))
    return _round_corners(pts, W, H, radii)


def _round_corners(pts, W, H, radii):
    """Cut each corner to a quarter circle of its radius (inside the outline)."""
    corners = [(0.0, 0.0, 1, 1), (W, 0.0, -1, 1), (W, H, -1, -1), (0.0, H, 1, -1)]
    out = []
    for x, y in pts:
        for (cx, cy, sx, sy), r in zip(corners, radii):
            ox, oy = cx + sx * r, cy + sy * r          # the rounding circle's centre
            dx, dy = x - ox, y - oy
            if dx * sx < 0 and dy * sy < 0:            # in the corner's square
                d = math.hypot(dx, dy)
                if d > r:
                    x, y = ox + dx * r / d, oy + dy * r / d
        out.append((x, y))
    return out


SQUARE_KEEP = 0.5        # the measured wander kept along each side
SQUARE_CAP = 0.06        # mm: and never deeper than this
CORNER_KEEP = 0.35       # the measured corners' rounding kept
MEDIAN_SHARE = 0.045     # the running median's width, share of the side


def squared(gate: dict, keep: float = SQUARE_KEEP, cap: float = SQUARE_CAP,
            corner_keep: float = CORNER_KEEP) -> dict:
    """A measured gate made a little squarer (the user's wish): each side's
    profile scaled by `keep` and softly capped at `cap` mm, corners' radii
    scaled by `corner_keep`. keep 1, cap None, corner_keep 1 = as measured."""
    sides = {}
    for k, v in gate["sides"].items():
        # narrow dents first: a running median over ~4.5 % of the side (the
        # measured long side carries dents at the sprocket pitch, the holes
        # seen past the edge, not the gate)
        h = max(1, int(round(MEDIAN_SHARE * len(v) / 2)))
        v = [sorted(v[max(0, i - h):i + h + 1])[len(v[max(0, i - h):i + h + 1]) // 2]
             for i in range(len(v))]
        lo = min(v)
        prof = [(x - lo) * keep for x in v]
        if cap is not None:
            prof = [cap * math.tanh(x / cap) for x in prof]   # a soft cap, no flat tops
        sides[k] = prof
    return dict(gate, sides=sides,
                corners={k: r * corner_keep for k, r in gate["corners"].items()})


def measured_outline(gate: dict, width_mm: float, height_mm: float) -> List[Tuple[float, float]]:
    """A measured gate's outline on a width x height mm image area (its
    profiles stretched to the area's sides; ends held to the corners)."""
    u0, u1 = gate["u"]
    W, H = width_mm, height_mm

    def at(prof, s):                       # s in 0..1 along the side
        n = len(prof)
        t = (min(max(s, u0), u1) - u0) / (u1 - u0) * (n - 1)
        i = min(int(t), n - 2)
        f = t - i
        return prof[i] * (1 - f) + prof[i + 1] * f

    pts = []
    sd = gate["sides"]
    n = max(2, int(round(W / STEP)) + 1)
    for k in range(n):
        pts.append((W * k / (n - 1), at(sd["top"], k / (n - 1))))
    n = max(2, int(round(H / STEP)) + 1)
    for k in range(n):
        pts.append((W - at(sd["right"], k / (n - 1)), H * k / (n - 1)))
    n = max(2, int(round(W / STEP)) + 1)
    for k in range(n):                    # the bottom runs right to left
        pts.append((W - W * k / (n - 1), H - at(sd["bottom"], 1 - k / (n - 1))))
    n = max(2, int(round(H / STEP)) + 1)
    for k in range(n):
        pts.append((at(sd["left"], 1 - k / (n - 1)), H - H * k / (n - 1)))
    c = gate["corners"]
    return _round_corners(pts, W, H, [c["tl"], c["tr"], c["br"], c["bl"]])


def outline_for(fmt: str, width_mm: float, height_mm: float, square: bool = True):
    """The gate for a format: the user's measured one (squarer unless
    `square` is False), else the drawn one."""
    from core.film_gate_data import GATES
    g = GATES.get(fmt)
    if g is not None:
        return measured_outline(squared(g) if square else g, width_mm, height_mm)
    return gate_outline(fmt, width_mm, height_mm)
