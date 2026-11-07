#!/usr/bin/env python3
"""Your frame guide kept on the frame (spec/guide-follow-and-crop.md §1):
the carrier's opening found in the live picture, and the tracker that maps
your guide through focusing moves but not through winding on."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from core import guide_tracker as gt  # noqa: E402

W, H = 1024, 768
OPEN = (60, 50, 980, 720)          # the carrier's opening in the "world"
GUIDE = {"cx": 0.51, "cy": 0.50, "w": 0.80, "h": 0.78, "angle": 0.4, "format": "6x7"}


def _film(wind=0, seed=1):
    """A negative: orange base with darker blobs (the picture), moved `wind`
    px along the strip."""
    rng = np.random.default_rng(seed)
    big = np.zeros((H, W + 800, 3), np.float32)
    big[:] = (65, 139, 230)
    for _ in range(140):
        x, y = rng.integers(0, W + 800), rng.integers(0, H)
        r = int(rng.integers(8, 60))
        lv = float(rng.uniform(0.35, 0.85))
        cv2.circle(big, (int(x), int(y)), r, (65 * lv, 139 * lv, 230 * lv), -1)
    big = cv2.GaussianBlur(big, (0, 0), 2.0)
    x0 = 400 - int(wind)
    return big[:, x0:x0 + W]


def scene(s=1.0, dx=0.0, dy=0.0, wind=0, noise=0.0, opening=OPEN, seed=1):
    """The live picture: the film behind the carrier's mask, then the whole
    of it scaled `s` about the view's centre and shifted (dx, dy) of the view
    (focusing / the rail)."""
    world = np.full((H, W, 3), 3.0, np.float32)
    x0, y0, x1, y1 = opening
    film = _film(wind, seed)
    ya, yb, xa, xb = max(0, y0), min(H, y1), max(0, x0), min(W, x1)
    world[ya:yb, xa:xb] = film[ya:yb, xa:xb]
    m = np.float32([[s, 0, (1 - s) * W / 2 + dx * W], [0, s, (1 - s) * H / 2 + dy * H]])
    out = cv2.warpAffine(world, m, (W, H), flags=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_REPLICATE)
    if noise:
        out = out + np.random.default_rng(seed + 7).normal(0, noise, out.shape)
    return np.clip(out, 0, 255).astype(np.uint8)


def _expected(s, dx, dy):
    return gt.apply((s, dx, dy), GUIDE)


# --------------------------------------------------------------------------- #
# the opening
# --------------------------------------------------------------------------- #
def test_the_opening_is_found_to_a_fraction_of_a_pixel():
    op = gt.find_opening(scene())
    assert op is not None and set(op.sides) == set(gt.SIDES) and op.cut == ()
    x0, y0, x1, y1 = OPEN
    for side, want in (("left", x0 / W), ("right", x1 / W), ("top", y0 / H), ("bottom", y1 / H)):
        assert abs(op.sides[side] - want) < 0.0012, (side, op.sides[side], want)


def test_an_opening_bigger_than_the_view_is_cut_on_those_sides():
    op = gt.find_opening(scene(opening=(60, 50, W + 200, H + 200)))
    assert op is not None
    assert set(op.sides) == {"left", "top"} and set(op.cut) == {"right", "bottom"}


def test_no_mask_no_opening():
    flat = np.full((H, W, 3), (65, 139, 230), np.uint8)
    assert gt.find_opening(flat) is None
    assert gt.find_opening(None) is None


# --------------------------------------------------------------------------- #
# following
# --------------------------------------------------------------------------- #
def _anchored(**kw):
    t = gt.GuideTracker()
    t.anchor(GUIDE, gt.find_opening(scene(**kw)))
    return t


def test_focusing_moves_the_guide_with_the_frame():
    t = _anchored()
    assert t.state == "following"
    g = t.update(gt.find_opening(scene(s=1.02, dx=0.005, dy=-0.004)))
    assert g is not None
    want = _expected(1.02, 0.005, -0.004)
    for k in ("cx", "cy", "w", "h"):
        assert abs(g[k] - want[k]) < 0.001, (k, g[k], want[k])
    assert g["angle"] == GUIDE["angle"] and g["format"] == "6x7"


def test_winding_on_does_not_move_it():
    t = _anchored()
    for wind in (40, 120, 260, 400):
        assert t.update(gt.find_opening(scene(wind=wind, seed=1))) is None
    assert t.shown == GUIDE and t.state == "following"


def test_noise_does_not_move_it():
    t = _anchored(noise=3.0, seed=2)
    moved = [t.update(gt.find_opening(scene(noise=3.0, seed=10 + i))) for i in range(20)]
    assert all(m is None for m in moved)


def test_only_left_and_top_seen_keeps_the_scale_and_follows_the_shift():
    big = (60, 50, W + 200, H + 200)
    t = gt.GuideTracker()
    t.anchor(GUIDE, gt.find_opening(scene(opening=big)))
    g = t.update(gt.find_opening(scene(dx=0.01, dy=0.006, opening=big)))
    assert g is not None
    want = _expected(1.0, 0.01, 0.006)
    for k in ("cx", "cy", "w", "h"):
        assert abs(g[k] - want[k]) < 0.001, (k, g[k], want[k])


def _op(**sides):
    return gt.Opening(sides=dict(sides))


REF = dict(left=0.06, right=0.96, top=0.07, bottom=0.94)


def _moved(s, dx, dy, sides=REF):
    return {k: (0.5 + s * (v - 0.5) + (dx if k in gt.X_SIDES else dy)) for k, v in sides.items()}


def test_a_large_jump_needs_two_agreeing_finds():
    t = gt.GuideTracker()
    t.anchor(GUIDE, _op(**REF))
    far = _moved(1.10, 0.0, 0.0)
    assert t.update(_op(**far)) is None and t.pending is not None
    g = t.update(_op(**far))
    assert g is not None and abs(g["w"] - GUIDE["w"] * 1.10) < 1e-6
    # a one-off jump is ignored
    t2 = gt.GuideTracker()
    t2.anchor(GUIDE, _op(**REF))
    assert t2.update(_op(**_moved(1.10, 0, 0))) is None
    assert t2.update(_op(**_moved(1.0, 0, 0))) is None and t2.shown == GUIDE


def test_the_guide_is_a_median_inside_a_dead_band():
    t = gt.GuideTracker()
    t.anchor(GUIDE, _op(**REF))
    assert t.update(_op(**_moved(1.001, 0.001, 0.0))) is None      # inside the band
    g = t.update(_op(**_moved(1.01, 0.0, 0.0)))
    assert g is not None
    # one wild-ish (but not a jump) find among steady ones doesn't move it
    for _ in range(4):
        t.update(_op(**_moved(1.01, 0.0, 0.0)))
    assert t.update(_op(**_moved(1.04, 0.03, 0.0))) is None


def test_no_edge_in_view_is_lost_after_two_finds_and_held():
    t = gt.GuideTracker()
    t.anchor(GUIDE, _op(**REF))
    assert t.update(None) is None and t.state == "following"
    assert t.update(None) is None and t.state == "lost"
    assert "no edge of the carrier" in t.reason
    g = t.update(_op(**_moved(1.02, 0, 0)))
    assert t.state == "following" and g is not None


def test_anchored_with_nothing_in_view_anchors_at_the_first_opening():
    t = gt.GuideTracker()
    t.anchor(GUIDE, None)
    assert t.state == "waiting" and not t.anchored
    assert t.update(None) is None and t.state == "waiting"
    assert t.update(_op(**_moved(1.03, 0.01, 0))) is None       # becomes the reference
    assert t.anchored and t.state == "following"
    g = t.update(_op(**_moved(1.03 * 1.02, 0.01, 0)))
    assert g is not None and abs(g["w"] - GUIDE["w"] * 1.02) < 1e-3


def test_a_side_seen_later_joins_the_reference():
    t = gt.GuideTracker()
    t.anchor(GUIDE, _op(left=REF["left"], top=REF["top"], bottom=REF["bottom"]))
    t.update(_op(**_moved(1.0, 0.002, 0.0)))                     # right comes into view
    assert "right" in t.ref and abs(t.ref["right"] - REF["right"]) < 1e-9
    for _ in range(3):                                           # the median catches up
        t.update(_op(**_moved(1.02, 0.002, 0.0)))
    assert abs(t.last[0] - 1.02) < 1e-9                          # the scale needs the pair
    assert abs(t.shown["w"] - GUIDE["w"] * 1.02) < 1e-6


def test_solve_by_pairs_and_single_sides():
    s, dx, dy, n = gt.solve(REF, _moved(1.02, 0.01, -0.005))
    assert abs(s - 1.02) < 1e-9 and abs(dx - 0.01) < 1e-9 and abs(dy + 0.005) < 1e-9 and n == 4
    one = {"left": REF["left"]}
    s, dx, dy, n = gt.solve(one, {"left": REF["left"] + 0.01}, last=(1.0, 0.0, 0.003))
    assert s == 1.0 and abs(dx - 0.01) < 1e-9 and dy == 0.003 and n == 1
    assert gt.solve({"left": 0.1}, {"top": 0.1}) is None
