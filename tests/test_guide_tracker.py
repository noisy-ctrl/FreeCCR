#!/usr/bin/env python3
"""Your frame guide kept on the frame (spec/guide-follow-and-crop.md §1):
the carrier's opening found in the live picture, and the tracker that maps
your guide through focusing moves but not through winding on."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

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


# --------------------------------------------------------------------------- #
# revision 2: snapping to a new frame
# --------------------------------------------------------------------------- #
PITCH_W, GAP = 900, 40           # a 35 mm frame and the clear film between frames
FILM_Y = (22, 757)               # the film's edges (the carrier is wider)
PIC_Y = (90, 690)                # the picture between the rebates
ENDS = (27, 986)                 # the carrier's ends
X0 = 67                          # the first frame's left edge, unwound


def strip(offset=0, ends=ENDS, noise=0.0, clear_left=False, seed=5):
    """A 35 mm strip in a carrier as the live view sees it: black outside the
    film and past the carrier's ends, orange base, sprocket holes top and
    bottom, frames of picture with clear film between them; moved `offset`
    px along the strip (frame k is a picture of its own)."""
    img = np.zeros((H, W, 3), np.float32)
    base = np.array((65, 139, 230), np.float32)
    img[FILM_Y[0]:FILM_Y[1], ends[0]:ends[1]] = base
    for k in range(-3, 4):
        x0 = X0 + k * (PITCH_W + GAP) + offset
        rng = np.random.default_rng(seed + k)
        pic = np.empty((PIC_Y[1] - PIC_Y[0], PITCH_W, 3), np.float32)
        pic[:] = base * 0.55
        for _ in range(60):
            cx, cy = rng.integers(0, PITCH_W), rng.integers(0, PIC_Y[1] - PIC_Y[0])
            lv = float(rng.uniform(0.3, 0.8))
            cv2.circle(pic, (int(cx), int(cy)), int(rng.integers(10, 70)), tuple(float(c) for c in base * lv), -1)
        if clear_left and k == 1:
            pic[:, :40] = base * 0.97                # a shadow as clear as the film
        pic = cv2.GaussianBlur(pic, (0, 0), 1.5)
        a, b = max(0, x0), min(W, x0 + PITCH_W)
        if b > a:
            img[PIC_Y[0]:PIC_Y[1], a:b] = pic[:, a - x0:b - x0]
    pitch = (PITCH_W + GAP) / 8.0
    for i in range(-30, 60):
        hx = int(round(X0 - 20 + i * pitch + offset))
        for y0 in (32, 712):
            img[y0:y0 + 34, max(0, hx):max(0, hx + 22)] = (235, 242, 243)
    img[:, :ends[0]] = 3.0
    img[:, ends[1]:] = 3.0
    if noise:
        img = img + np.random.default_rng(seed + 99).normal(0, noise, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


# your guide on frame 0: 6 px inside its left edge, 9 inside its right, 3 / 5
# inside the picture's top and bottom
HAND = {"cx": (X0 + 6 + X0 + PITCH_W - 9) / 2 / W, "cy": (PIC_Y[0] + 3 + PIC_Y[1] - 5) / 2 / H,
        "w": (PITCH_W - 15) / W, "h": (PIC_Y[1] - PIC_Y[0] - 8) / H, "angle": 0.0,
        "format": "135"}
WIND = PITCH_W + GAP             # one frame along


def _snapper(frame=None):
    f = strip() if frame is None else frame
    t = gt.GuideTracker()
    t.anchor(HAND, gt.find_opening(f), frame=f)
    return t


def test_the_frames_edges_are_found_beside_the_guide():
    rel = gt.edge_relation(strip(), HAND)
    px = {k: v * (W if k in gt.X_SIDES else H) for k, v in rel.items()}
    assert set(px) == set(gt.SIDES)
    assert px["left"] == pytest.approx(6, abs=0.5) and px["right"] == pytest.approx(-9, abs=0.5)
    assert px["top"] == pytest.approx(3, abs=0.5) and px["bottom"] == pytest.approx(-5, abs=0.5)


def test_a_new_frame_landing_along_takes_the_guide_with_your_margins():
    t = _snapper()
    for land in (-5, 7):                      # the next frame lands 5 px short / 7 px on
        t = _snapper()
        f = strip(offset=-WIND + land)
        g = t.snap(f, gt.find_opening(f))
        assert g is not None
        assert (g["cx"] - HAND["cx"]) * W == pytest.approx(land, abs=0.5)
        assert (g["cy"] - HAND["cy"]) * H == pytest.approx(0, abs=0.3)
        assert g["w"] == HAND["w"] and g["h"] == HAND["h"] and g["format"] == "135"
        assert t.shown == g and t.anchored               # the new reference
        assert gt.edge_relation(f, g).keys() == t.rel.keys()


def test_the_same_frame_or_noise_doesnt_move_it():
    t = _snapper(strip(noise=2.0))
    for seed in range(4):
        f = strip(noise=2.0, seed=5)
        assert t.snap(f, gt.find_opening(f)) is None
    f = strip(offset=-WIND)                     # landed exactly where the last one was
    assert t.snap(f, gt.find_opening(f)) is None


def test_a_side_the_carrier_hides_is_left_to_the_other():
    ends = (X0 + 30, ENDS[1])                    # the carrier covers the left gap
    t = _snapper(strip(ends=ends))
    assert "left" not in t.rel and "right" in t.rel
    f = strip(offset=-WIND - 5, ends=ends)
    g = t.snap(f, gt.find_opening(f))
    assert g is not None and (g["cx"] - HAND["cx"]) * W == pytest.approx(-5, abs=0.5)


def test_a_shadow_as_clear_as_the_film_doesnt_make_it_jump():
    """The next frame's left side is a shadow as clear as the film beside it:
    its edge reads 40 px in, the two sides disagree and the guide stays where
    it is (no snap) rather than jumping."""
    t = _snapper()
    f = strip(offset=-WIND - 5, clear_left=True)
    found = gt.frame_edges(f, HAND, expect={s: gt.guide_side(HAND, s) - d
                                             for s, d in t.rel.items()})
    assert found["left"] * W == pytest.approx(X0 - 5 + 40, abs=1.5)   # the shadow's edge
    assert found["right"] * W == pytest.approx(X0 - 5 + PITCH_W, abs=0.5)
    assert t.snap(f, gt.find_opening(f)) is None
    assert t.shown == HAND


def test_no_margins_known_or_too_far_or_turned_no_snap():
    t = gt.GuideTracker()
    t.anchor(HAND, gt.find_opening(strip()))          # no frame given: no margins
    f = strip(offset=-WIND - 5)
    assert t.snap(f, gt.find_opening(f)) is None
    t = _snapper()
    f = strip(offset=-WIND - 120)                     # 13 % along: not this frame's edge
    assert t.snap(f, gt.find_opening(f)) is None
    t = _snapper()
    t.shown = dict(HAND, angle=4.0)
    f = strip(offset=-WIND - 5)
    assert t.snap(f, gt.find_opening(f)) is None


def test_settled_after_moving_is_said_once():
    w = gt.SettleWatch()
    still = strip()
    assert not any(w.update(still) for _ in range(6))           # never moved
    assert not w.update(strip(offset=-WIND // 2))               # winding
    assert not w.update(strip(offset=-WIND - 5))
    seen = [w.update(strip(offset=-WIND - 5)) for _ in range(5)]
    assert seen == [False, False, True, False, False]           # three still looks
    # noise alone never counts as moving
    w = gt.SettleWatch()
    assert not any(w.update(strip(noise=3.0, seed=5)) for _ in range(8))
