#!/usr/bin/env python3
"""Auto crop on holder edges read in pieces (spec/auto-crop.md, revision
2026-10-22): a film turned in its holder, a gate that shows on only part of an
edge, an even rebate versus a picture as thin as the base, a margin that is
not film base, and the crop memory ignoring habits learned from the older
detector."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402

from core import auto_crop as ac  # noqa: E402
from core.ccr_processor import apply_crop_to_image  # noqa: E402
from tests.test_auto_crop import BASE, HOLDER, H, W, _picture, _to_raw  # noqa: E402


def _holder_645(turn=0.0, thin_bottom_left=False, seed=1):
    """Holder, a clear-film window, then a 645 picture turned `turn` degrees
    inside it (a film sitting askew in its holder), leaving a rebate of ~20 px
    that is a wedge when turned."""
    v = np.full((H, W), HOLDER, np.float32)
    v[30:H - 30, 40:W - 40] = BASE
    ph = H - 2 * 30 - 2 * 22
    pw = int(round(ph * 56 / 41.5))
    pic = _picture(ph, pw, seed=seed)
    if thin_bottom_left:
        pic[-60:, :pw // 2] = BASE * 0.995           # as thin as the base there
    canvas = np.zeros((H, W), np.float32)
    mask = np.zeros((H, W), np.float32)
    x0, y0 = (W - pw) // 2, (H - ph) // 2
    canvas[y0:y0 + ph, x0:x0 + pw] = pic
    mask[y0:y0 + ph, x0:x0 + pw] = 1.0
    if turn:
        m = cv2.getRotationMatrix2D((W / 2, H / 2), turn, 1.0)
        canvas = cv2.warpAffine(canvas, m, (W, H), flags=cv2.INTER_LINEAR)
        mask = cv2.warpAffine(mask, m, (W, H), flags=cv2.INTER_LINEAR)
    v = np.where(mask > 0.5, canvas, v)
    return _to_raw(v), (x0, y0, x0 + pw, y0 + ph)


def _rebate_left_in(raw, fc, band=3):
    """Fraction of each edge's outermost `band` px of the crop that is at the
    film base's level (rebate left in). The pictures here never get within
    10 % of the base."""
    c = ac.log_luminance(apply_crop_to_image(raw, fc.rect, fc.angle))
    base = np.log10(BASE)
    edges = {"L": c[:, :band], "R": c[:, -band:], "T": c[:band], "B": c[-band:]}
    return {k: float(np.mean(e > base - 0.03)) for k, e in edges.items()}


@pytest.mark.parametrize("turn", [0.5, -0.7])
def test_film_turned_in_its_holder_leaves_no_wedge_of_rebate(turn):
    raw, _truth = _holder_645(turn)
    fc = ac.detect_frame(raw)
    assert fc.usable, fc.reason
    left = _rebate_left_in(raw, fc)
    assert max(left.values()) < 0.01, left
    # squared to the picture, not the holder (the picture was turned with
    # getRotationMatrix2D(turn); a crop angle a samples R(a) = its inverse)
    assert fc.angle == pytest.approx(-turn, abs=0.15), fc.angle


def test_gate_seen_on_part_of_an_edge_still_places_it():
    """The picture is as thin as the base along half of the bottom edge: the
    other half's gate places the whole edge (it used to stop on the rebate
    and call the edge soft)."""
    raw, truth = _holder_645(thin_bottom_left=True)
    fc = ac.detect_frame(raw)
    assert fc.usable, fc.reason
    assert "B" not in fc.soft
    x0, y0, x1, y1 = fc.debug["deskewed_rect"]
    assert truth[3] - 0.03 * (truth[3] - truth[1]) <= y1 <= truth[3], (y1, truth)


def test_even_rebate_versus_picture_as_thin_as_base():
    """One piece of edge: an even rebate then a step is a gate; the same
    levels with picture texture across the edge are not."""
    rng = np.random.default_rng(0)
    q = np.concatenate([np.linspace(-2.5, -0.5, 8), np.full(14, -0.5), np.full(30, -0.8)])
    even = np.tile(q, (100, 1)).astype(np.float32) + rng.normal(0, 0.003, (100, q.size))
    got = ac._segment_gate(even, -0.5, ac.GATE_STEP, 0.05)
    assert got is not None and 20 <= got[0] <= 26 and got[1] == pytest.approx(-0.5, abs=0.02)
    textured = even.copy()
    textured[:, 8:22] += rng.normal(0, 0.08, (100, 14)).astype(np.float32)
    assert ac._segment_gate(textured, -0.5, ac.GATE_STEP, 0.05) is None


def test_gate_line_drops_pieces_from_the_inside_first():
    """Picture as thin as the rebate can only push a piece's gate inward:
    the line goes through the outer pieces."""
    t = np.arange(8) * 100.0 + 50
    g = np.array([20, 20, 21, 20, 34, 37, 21, 20], float)
    m, c, lo, hi = ac._fit_gate_line(t, g)
    assert abs(m) < 0.005 and 19.5 < c < 21.5 and lo < 50 and hi > 750


def test_margin_darker_than_the_picture_is_not_rebate():
    """A slide/target margin: even, then a step down, but darker than the
    picture beside it. Film base is the brightest thing on a negative, so this
    is picture and stays in the crop."""
    v = np.full((H, W), HOLDER, np.float32)
    v[30:H - 30, 40:W - 40] = 0.06                    # even grey margin, 25 px
    v[55:H - 55, 65:W - 65] = 0.01                    # a dark frame line
    v[65:H - 65, 75:W - 75] = _picture(H - 130, W - 150, seed=5, lo=0.02, hi=0.6)
    raw = _to_raw(v)
    L = cv2.GaussianBlur(ac.log_luminance(raw), (0, 0), 1.0)
    _rect, moved, _soft, _pl, slopes = ac._peel(L, (40, 30, W - 40, H - 30))
    assert not slopes and max(moved.values()) < 50, moved


def test_detector_version_rides_on_the_detection():
    from core.crop_memory import det_record
    raw, _ = _holder_645()
    assert det_record(ac.detect_frame(raw))["v"] == ac.DETECTOR_VERSION


def test_habit_learned_against_an_older_detector_is_not_applied(tmp_path):
    """Crops you confirmed while the detector still left rebate in teach your
    frame size, not "pull the left edge in 2.5 %": the new detector already
    does that, and applying it again would cut the picture."""
    from core.crop_memory import CropMemory, box_of, det_record
    mem = CropMemory(str(tmp_path / "mem.json"))
    raw, _ = _holder_645()
    fc = ac.detect_frame(raw)
    h, w = raw.shape[:2]
    det = det_record(fc)
    cx, cy, bw, bh, a = box_of(fc.geom["out_rect"], fc.geom["angle"], w, h)
    final = [cx + 0.0125 * bw, cy, bw * 0.975, bh, a]      # left edge in 2.5 %
    old = dict(det)
    old.pop("v")                                           # recorded by the old detector
    for _ in range(2):
        mem.record("k", final, w, h, det=old)
    p = mem.priors("k")[0]
    assert p.habit == (0.0, 0.0, 0.0, 0.0)
    assert p.size[0] == pytest.approx(bw * 0.975, abs=0.5)
    mem.forget()
    for _ in range(2):
        mem.record("k", final, w, h, det=det)
    assert mem.priors("k")[0].habit[0] == pytest.approx(0.025, abs=0.003)
