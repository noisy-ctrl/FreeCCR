#!/usr/bin/env python3
"""Dust revision 3 (spec/dust-detect-v3.md): edge ghosts aren't flagged, a
strand is followed past where the IR loses it, an IR find always loses what
the IR saw."""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402
from core import ccr_processor as cp  # noqa: E402
from core import dust_fill as dfl  # noqa: E402
from core import ir_dust, ir_dust_learn  # noqa: E402


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("FREECCR_IR_DUST_LEARNED", str(tmp_path / "learned.json"))
    monkeypatch.setattr(cp, "DUST_ENGINE", "patch")
    ir_dust_learn.reset_cache()
    yield
    ir_dust_learn.reset_cache()


# --------------------------------------------------------------------------- #
# 1. edge ghosts
# --------------------------------------------------------------------------- #
def _line_mask(h, w, p0, p1, width=3):
    m = np.zeros((h, w), np.uint8)
    cv2.line(m, p0, p1, 1, width)
    return m > 0


def test_a_line_along_a_step_is_a_ghost_a_dark_line_and_flat_ground_are_not():
    h, w = 120, 160
    m = _line_mask(h, w, (80, 10), (80, 110))
    step = np.zeros((h, w), np.float32)
    step[:, 80:] = 0.6                               # the colour shots step across it
    dark = np.zeros((h, w), np.float32)
    dark[m] = -0.6                                   # ... dip on it (a fibre)
    flat = np.zeros((h, w), np.float32)
    sm = lambda a: cv2.GaussianBlur(a, (0, 0), 1.0)  # noqa: E731
    assert ir_dust.edge_ghost(m, 0, 0, sm(step))
    assert not ir_dust.edge_ghost(m, 0, 0, sm(dark))
    assert not ir_dust.edge_ghost(m, 0, 0, sm(flat))
    # a fibre ACROSS the same step is no ghost: no step across its own line
    across = _line_mask(h, w, (20, 60), (140, 60))
    assert not ir_dust.edge_ghost(across, 0, 0, sm(step))


def _edge_scene(h=300, w=400, seed=5, shift=0.7):
    """A frame with one strong vertical edge whose IR is a fraction of a
    block off (the ghost), plus a real fibre in both shots."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    pic = np.where(xx < 200, 0.25, 0.9).astype(np.float32)
    pic_v = cv2.GaussianBlur(pic, (0, 0), 1.0)
    M = np.float32([[1, 0, shift], [0, 1, 0]])
    pic_i = cv2.warpAffine(cv2.GaussianBlur(pic, (0, 0), 1.6), M, (w, h),
                           borderMode=cv2.BORDER_REPLICATE)
    fibre = np.ones((h, w), np.float32)
    cv2.line(fibre, (60, 60), (140, 240), 0.55, 2)
    fv = cv2.GaussianBlur(fibre, (0, 0), 0.6)
    fi = cv2.GaussianBlur(np.where(fibre < 1, 0.81, 1.0).astype(np.float32), (0, 0), 0.9)
    vis = 9000 * pic_v * fv * (1 + rng.normal(0, 0.01, (h, w)))
    ir = 600 * np.exp(0.2 * np.log(pic_i)) * fi * (1 + rng.normal(0, 0.02, (h, w)))
    return vis.astype(np.float32), ir.astype(np.float32)


def test_the_irs_line_along_an_edge_is_never_flagged_the_fibre_is():
    vis, ir = _edge_scene()
    dm = ir_dust.dust_map(ir, [vis])
    acc = ir_dust.flagged(dm, 100)
    along = [i for i, c in enumerate(dm.cands)
             if abs(c.centre()[0] - 200) < 6 and c.length / c.width >= ir_dust.GHOST_LW]
    if along:                                         # the finder saw the edge's line ...
        assert all(dm.cands[i].ghost for i in along)
        assert not set(along) & set(acc)              # ... and it isn't flagged at Dust 100
    fibre = [i for i in acc if 60 <= dm.cands[i].centre()[0] <= 140]
    assert fibre                                      # the fibre is
    assert not any(dm.cands[i].ghost for i in fibre)


# --------------------------------------------------------------------------- #
# skeletons
# --------------------------------------------------------------------------- #
def test_prune_takes_off_a_spur_and_keeps_the_ends_length():
    sk = np.zeros((40, 60), bool)
    sk[20, 5:55] = True                               # the fibre
    sk[14:20, 30] = True                              # a twig off it
    pr = ir_dust.prune(sk, 7)
    assert not pr[14:19, 30].any()
    assert pr[20, 5] and pr[20, 54]                   # both real ends kept
    assert len(ir_dust.skeleton_paths(pr)) == 1


def test_a_staircase_end_is_an_end():
    sk = np.zeros((20, 20), bool)
    for k in range(8):                                # a diagonal drawn as a staircase
        sk[5 + k, 5 + k] = True
        sk[5 + k, 6 + k] = True
    ends = ir_dust._ends(sk)
    assert ends.sum() == 2


# --------------------------------------------------------------------------- #
# 2. tails
# --------------------------------------------------------------------------- #
def _tail_scene(h=420, w=640, seed=7, ir_on=True, vis_tail=True):
    """A fibre the IR shows solid for its first part and then only as a row
    of faint dots too weak to be found by themselves (the user's strand),
    the colour shots faintly all along."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    pic = (0.4 + 0.3 * xx / w).astype(np.float32)
    head = np.ones((h, w), np.float32)
    cv2.line(head, (180, 120), (300, 200), 0.0, 2)
    tail = np.ones((h, w), np.float32)
    cv2.line(tail, (300, 200), (360, 240), 0.0, 2)
    t_ir = np.where((xx.astype(int) % 6) < 3, tail, 1.0)    # a row of dots
    di = np.where(head < 1, 0.78, 1.0) * (np.where(t_ir < 1, 0.96, 1.0) if ir_on else 1.0)
    dv = np.where(head < 1, 0.6, 1.0) * (np.where(tail < 1, 0.9, 1.0) if vis_tail else 1.0)
    di = cv2.GaussianBlur(di.astype(np.float32), (0, 0), 0.9)
    dv = cv2.GaussianBlur(dv.astype(np.float32), (0, 0), 0.6)
    vis = 9000 * pic * dv * (1 + rng.normal(0, 0.01, (h, w)))
    ir = 600 * np.exp(0.2 * np.log(pic)) * di * (1 + rng.normal(0, 0.02, (h, w)))
    return vis.astype(np.float32), ir.astype(np.float32), tail < 1


def _cover(spots, h, w, pick=lambda s: True):
    cov = np.zeros((h, w), np.uint8)
    for s in spots:
        if not pick(s):
            continue
        pts = np.array([[x * w, y * h] for x, y in s["pts"]], np.int32)
        r = max(1, int(round(s["r"] * w)))
        if len(pts) == 1:
            cv2.circle(cov, tuple(int(v) for v in pts[0]), r, 1, -1)
        else:
            cv2.polylines(cov, [pts.reshape(-1, 1, 2)], False, 1, 2 * r)
    return cov > 0


def test_a_strand_is_followed_where_the_ir_shows_it_only_as_dots():
    vis, ir, tail = _tail_scene()
    h, w = ir.shape
    dm = ir_dust.dust_map(ir, [vis])
    spots = ir_dust.spots_from(dm, ir_dust.SENS_DEFAULT)
    tails = [s for s in spots if "ir_tail" in s]
    assert tails and all("ir_f" not in s for s in tails)      # nothing to learn from
    without = _cover(spots, h, w, lambda s: "ir_tail" not in s)
    with_ = _cover(spots, h, w)
    assert with_[tail].mean() > without[tail].mean() + 0.3
    assert with_[tail].mean() >= 0.8


def test_no_tail_where_only_the_colour_shots_go_on():
    """A line in the colour shots with nothing in the IR is the picture's
    (on the Gold 200 frames such tails ran along its lines and edges)."""
    vis, ir, tail = _tail_scene(ir_on=False)
    dm = ir_dust.dust_map(ir, [vis])
    assert not [s for s in ir_dust.spots_from(dm, ir_dust.SENS_DEFAULT) if "ir_tail" in s]


def test_tail_thresholds_follow_the_irs_own_noise():
    """A cleaner IR shot is read at the model's noise (z scaled down), so
    what beats chance there is nearer 0: the threshold is measured per map."""
    vis, ir, _ = _tail_scene(ir_on=False, vis_tail=False)
    dm = ir_dust.dust_map(ir, [vis])
    tz, tv, tj, tf = ir_dust.tail_thresholds(dm)
    assert tz < 0 and tv < 0 and tj < 0 and tf < 0
    assert ir_dust.tail_thresholds(dm) == (tz, tv, tj, tf)     # cached on the map
    rng = np.random.default_rng(1)
    smooth = cv2.GaussianBlur(ir, (0, 0), 3.0)
    clean = (smooth * (1 + rng.normal(0, 0.004, ir.shape))).astype(np.float32)
    dmc = ir_dust.dust_map(clean, [vis])
    assert dmc.noise < ir_dust.NOISE_REF
    assert ir_dust.tail_thresholds(dmc)[0] > tz


# --------------------------------------------------------------------------- #
# 3. selection
# --------------------------------------------------------------------------- #
def test_a_strokes_core_is_narrower_than_its_area():
    vis, ir, _ = _tail_scene()
    dm = ir_dust.dust_map(ir, [vis])
    strokes = [s for s in ir_dust.spots_from(dm, ir_dust.SENS_DEFAULT)
               if len(s["pts"]) > 1 and "ir_c" in s]
    assert strokes
    assert all(0 < s["ir_rc"] < s["r"] for s in strokes)


def _grain(h, w, seed=1, level=0.45, amp=0.03, sigma=0.8):
    rng = np.random.default_rng(seed)
    g = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), sigma)
    return level + amp * g / g.std()


def _strand(H=140, W=260, faint=0.07, seed=11):
    """His strand: one end bright, the rest a faint row of dots, green only."""
    base = np.repeat(_grain(H, W, seed=seed)[..., None], 3, 2)
    line = np.zeros((H, W), np.float32)
    cv2.line(line, (20, 30), (240, 110), 1.0, 2, cv2.LINE_AA)
    xx = np.arange(W)[None, :]
    amp = np.where(xx < 60, 0.30, faint) * (xx % 5 < 3)
    dirty = base.copy()
    dirty[..., 1] += line * amp
    return base, dirty, line, xx


def test_the_irs_core_carries_the_tracer_along_a_strands_faint_stretch():
    """Traced against its bright end's peak, the faint stretch is grain (the
    remnant on his frame 12); anchored on where the IR saw it, it is followed."""
    H, W = 140, 260
    base, dirty, line, xx = _strand()
    E = np.sqrt(np.clip(dirty, 0, 1)).astype(np.float32)
    reg = np.zeros((H, W), np.uint8)
    cv2.line(reg, (20, 30), (240, 110), 1, 14)
    core = np.zeros((H, W), np.uint8)
    cv2.line(core, (20, 30), (240, 110), 1, 4)
    faint = (line > 0.3) & (xx >= 70)
    free, _ = dfl.speck_mask(dfl._planes(E), reg > 0, 1)
    anchored, _ = dfl.speck_mask(dfl._planes(E), reg > 0, 1, anchor=core > 0)
    assert free is not None and free[faint].mean() < 0.1        # measured 0.00
    assert anchored[faint].mean() > 0.4                         # measured 0.64


def test_a_faint_dotted_strand_found_in_the_ir_is_healed_end_to_end():
    H, W = 140, 260
    base, dirty, line, xx = _strand()
    img = (np.clip(dirty, 0, 1) * 65535).astype(np.uint16)
    clean = (np.clip(base, 0, 1) * 65535).astype(np.uint16)
    pts = [[x / W, (30 + (x - 20) * 80 / 220) / H] for x in range(20, 241, 10)]
    out = dfl.heal(img, [{"kind": "ir", "pts": pts, "r": 7.0 / W, "ir_rc": 2.0 / W}])
    g = lambda a: a[..., 1].astype(np.float32) / 257             # noqa: E731
    on = line > 0.3
    faint, bright = on & (xx >= 70), on & (xx < 60)
    was = g(img) - g(clean)
    left = g(out) - g(clean)
    assert was[faint].mean() > 8 and was[bright].mean() > 30    # 10.8, 45.6 levels
    assert abs(left[faint].mean()) < 1.5                        # measured -0.6
    assert abs(left[bright].mean()) < 4.0                       # measured 1.2


def test_a_defect_in_one_colour_is_traced():
    H = W = 120
    base = np.repeat(_grain(H, W, seed=4)[..., None], 3, 2)
    yy, xx = np.ogrid[:H, :W]
    d = np.hypot(xx - 60, yy - 60)
    dirty = base.copy()
    dirty[..., 2] += 0.3 * np.exp(-(d / 3.0) ** 2)    # blue only: z 9.8 there, 4.6 in the grey
    region = d <= 14
    Y = dfl._planes(np.sqrt(np.clip(dirty, 0, 1)).astype(np.float32))
    m, info = dfl.speck_mask(Y, region, 1)
    assert m is not None and m[60, 60]
    assert int(m.sum()) < 0.5 * int(region.sum())     # the speck, not the area
    m0, _ = dfl.speck_mask(Y[..., 0], region, 1)      # the grey alone doesn't see it
    assert m0 is None or int(m0.sum()) < int(m.sum())


def test_a_long_stroke_is_traced_in_pieces():
    pts = [[x / 1000, 0.5] for x in range(100, 900, 20)]
    spot = {"kind": "ir", "pts": pts, "r": 0.003}
    pieces = dfl._pieces(spot, 1000, 1000)
    assert len(pieces) > 1
    lens = [(p["pts"][-1][0] - p["pts"][0][0]) * 1000 for p in pieces]
    assert max(lens) <= dfl.PIECE_LEN + 1e-6
    for a, b in zip(pieces[:-1], pieces[1:]):         # sharing their joints
        assert a["pts"][-1] == b["pts"][0]
    assert dfl._pieces({"kind": "ir", "pts": pts[:2], "r": 0.003}, 1000, 1000) == \
        [{"kind": "ir", "pts": pts[:2], "r": 0.003}]


def test_ir_core_only_for_ir_spots():
    s = {"kind": "ir", "pts": [[0.5, 0.5]], "r": 0.02}
    m, y0, x0 = dfl.ir_core(s, 200, 200)
    assert 0 < m.sum() < np.pi * 4 ** 2 * 1.5          # half of r 4 px -> ~2 px radius
    assert dfl.ir_core(dict(s, kind="brush"), 200, 200) is None
    assert dfl.ir_core(dict(s, kind="mark"), 200, 200) is None
