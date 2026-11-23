#!/usr/bin/env python3
"""The heal's diffusion fallback keeps 16-bit precision, and an edit at the
plan's own scale looks for a source again where the plan fell back
(spec/dust-heal-fallback.md)."""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402
from core import ccr_processor as cp  # noqa: E402


@pytest.fixture(autouse=True)
def _clone_engine(monkeypatch):
    """These tests pin the earlier one-source clone heal, which stays
    selectable (FREECCR_DUST_ENGINE=clone); the default patch fill has its
    own tests (tests/test_dust_patch_fill.py, spec/dust-patch-fill.md)."""
    from core import ccr_processor as _cp
    monkeypatch.setattr(_cp, "DUST_ENGINE", "clone")



def _narrow_gradient(h=300, w=400):
    """A sky as a converted base holds it: a smooth gradient over only ~60
    16-bit codes (well under one 8-bit step of 257)."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    v = 30000 + 60.0 * xx / w + 20.0 * yy / h
    return np.stack([v, v + 10, v + 25], axis=-1).astype(np.uint16)


def test_the_fallback_fill_keeps_16_bit_precision():
    img = _narrow_gradient()
    mask = np.zeros(img.shape[:2], np.uint8)
    cv2.circle(mask, (200, 150), 12, 255, -1)
    out = cp._telea16(img, mask, 3)
    sel = mask > 0
    assert np.array_equal(out[~sel], img[~sel])                # nothing else moves
    fill = out[sel].astype(np.int32)
    want = img[sel].astype(np.int32)
    assert np.abs(fill - want).max() <= 8                      # within a few codes
    # the old 8-bit fill snapped to multiples of 257: an error of ~100 codes here
    old8 = cv2.inpaint(cv2.convertScaleAbs(img, alpha=255.0 / 65535.0), mask, 3,
                       cv2.INPAINT_TELEA).astype(np.int32) * 257
    assert np.abs(old8[sel] - want).max() > 50


def test_a_forced_fallback_heal_has_no_8_bit_steps():
    img = _narrow_gradient()
    spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.03}
    h, w = img.shape[:2]
    # a plan that chose diffusion here, replayed at twice the plan's scale
    big = cv2.resize(img, (w * 2, h * 2), interpolation=cv2.INTER_LINEAR)
    plan = [(0.5, 0.5, None, None, None, False)]
    out = cp._heal_impl(big, [spot], 3, cp.DUST_FEATHER_DEFAULT, plan=plan, scale_up=2.0)
    m = cp.rasterize_dust_mask([spot], h * 2, w * 2) > 0
    err = np.abs(out[m].astype(np.int32) - big[m].astype(np.int32))
    assert err.max() <= 12                                     # was ~100+ with the 8-bit fill


def test_an_edit_looks_again_where_the_plan_fell_back():
    rng = np.random.default_rng(17)
    img = np.clip(rng.normal(30000, 3000, (400, 400, 3)), 0, 65535).astype(np.uint16)
    spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.03}
    first = []
    cp.apply_dust_removal(img, [spot], collect_plan=first)
    (cy, cx, off, *_), = first
    assert off is not None                                      # a clean source exists
    stale = [(cy, cx, None, None, None, False)]                  # an old plan's fallback
    again = []
    cp.apply_dust_removal(img, [spot], collect_plan=again, prior_plan=stale)
    assert again[0][2] is not None                              # it found a source this time


def test_a_cross_scale_replay_keeps_the_plans_fallback():
    rng = np.random.default_rng(17)
    img = np.clip(rng.normal(30000, 3000, (400, 400, 3)), 0, 65535).astype(np.uint16)
    big = cv2.resize(img, (800, 800), interpolation=cv2.INTER_LINEAR)
    spot = {"kind": "brush", "pts": [[0.5, 0.5]], "r": 0.03}
    plan = [(0.5, 0.5, None, None, None, False)]
    collect = []
    cp._heal_impl(big, [spot], 3, cp.DUST_FEATHER_DEFAULT, plan=plan, scale_up=2.0,
                  collect=collect, plan_only=True)
    assert collect[0][2] is None                                # the preview's choice stands
