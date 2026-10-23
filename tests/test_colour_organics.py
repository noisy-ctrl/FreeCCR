#!/usr/bin/env python3
"""Colour Organics (spec/colour-organics.md): hues drawn into a few print
families by a monotone warp, at the same luma and chroma, greys untouched."""

import colorsys
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_processor import (adjust_image, adjust_image_opencl,  # noqa: E402
                                _apply_colour_organics, organics_hue_map,
                                ORGANICS_ANCHORS, ORGANICS_STRENGTH, ORGANICS_WIDTH)

FULL = ORGANICS_STRENGTH
LUMA = np.array([0.299, 0.587, 0.114])


def _hsv(px):
    return colorsys.rgb_to_hsv(*[float(v) for v in px])


def _hdiff(a, b):
    d = (a - b) % 360.0
    return np.where(d > 180, d - 360, d)


def _sweep(sats=(0.0, 0.1, 0.3, 0.6, 0.9), vals=(0.35, 0.7, 0.95), n=72):
    cols = [colorsys.hsv_to_rgb(h, s, v) for h in np.linspace(0, 1, n, endpoint=False)
            for s in sats for v in vals]
    return np.array(cols, np.float32)[None]


def test_zero_is_identity():
    x = _sweep()
    assert np.array_equal(_apply_colour_organics(x, 0), np.clip(x, 0, 1))
    img = (x * 65535).astype(np.uint16)
    assert np.array_equal(adjust_image(img, colour_organics=0), img)


def test_greys_untouched_luma_kept_never_clips():
    x = _sweep()[0]
    out = _apply_colour_organics(x[None], 100)[0]
    s = np.array([_hsv(p)[1] for p in x])
    assert np.allclose(out[s < 0.06], x[s < 0.06], atol=1e-6)
    assert np.allclose(out @ LUMA, x @ LUMA, atol=1e-5)
    assert out.min() >= 0 and out.max() <= 1


def test_anchors_and_span_middles_stay_put():
    a = np.array(ORGANICS_ANCHORS + (ORGANICS_ANCHORS[0] + 360,))
    mids = (a[:-1] + a[1:]) / 2 % 360
    for h in list(ORGANICS_ANCHORS) + list(mids):
        assert float(organics_hue_map(h, FULL)) == pytest.approx(h % 360, abs=1e-3)


def test_the_hue_map_is_monotone_with_bounded_slope():
    h = np.linspace(0, 360, 36001)[:-1].astype(np.float32)
    m = organics_hue_map(h, FULL)
    step = _hdiff(np.roll(m, -1), m)[:-1] / 0.01
    assert step.min() > 0                         # never reorders hues
    lo = 1 - FULL * (1 - ORGANICS_WIDTH)
    hi = 1 + FULL * ORGANICS_WIDTH
    assert step.min() == pytest.approx(lo, abs=0.02)
    assert step.max() == pytest.approx(hi, abs=0.02)
    a = np.array(ORGANICS_ANCHORS + (ORGANICS_ANCHORS[0] + 360,))
    widest = np.diff(a).max()
    assert np.abs(_hdiff(m, h)).max() < 0.075 * widest   # small moves (~8 degrees)


def test_hues_move_towards_an_anchor_never_past_it():
    for a in ORGANICS_ANCHORS:
        for off in (2.0, 6.0, 12.0):
            for sgn in (-1, 1):
                h = (a + sgn * off) % 360
                m = float(organics_hue_map(h, FULL))
                moved = _hdiff(m, h)
                left = _hdiff(a, h)
                assert np.sign(moved) == np.sign(left)
                assert abs(moved) < abs(left)


def test_a_family_of_hues_is_drawn_together():
    hues = np.linspace(85, 115, 61)               # around the foliage anchor
    before = np.std(hues)
    after = np.std(organics_hue_map(hues.astype(np.float32), FULL))
    assert after < 0.6 * before


def test_the_pixel_lands_on_the_mapped_hue():
    x = _sweep(sats=(0.3, 0.6), vals=(0.5, 0.8))[0]
    out = _apply_colour_organics(x[None], 100)[0]
    for a, b in zip(x, out):
        ha = _hsv(a)[0] * 360
        want = float(organics_hue_map(ha, FULL))
        assert abs(float(_hdiff(_hsv(b)[0] * 360, want))) < 0.05


def test_opencl_matches_cpu():
    img = (_sweep() * 65535).astype(np.uint16)
    for kw in (dict(colour_organics=50), dict(colour_organics=100),
               dict(colour_organics=80, sat_rolloff=60, saturation=25, vibrance=10)):
        cpu = adjust_image(img, **kw)
        gpu = adjust_image_opencl(img, **kw)
        np.testing.assert_allclose(cpu.astype(np.int64), gpu.astype(np.int64), atol=3)


def test_slider_helper_text_and_group():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel, SYNC_GROUPS, _BASIC_LABELS
    keys = SlidersPanel.ADJUSTMENT_KEYS
    # Golden Highlights and Shadow Neutralise sit between them
    # (spec/golden-highlights.md, spec/shadow-neutralise.md)
    assert keys.index("golden_highlights") == keys.index("sub_saturation") + 1
    assert keys.index("shadow_neutralise") == keys.index("golden_highlights") + 1
    assert keys.index("glow") == keys.index("shadow_neutralise") + 1
    assert keys.index("colour_organics") == keys.index("glow") + 1
    assert "colour_organics" in dict((g, k) for g, _l, k in SYNC_GROUPS)["sat"]
    assert _BASIC_LABELS["colour_organics"] == "Colour Organics"
    p = SlidersPanel()
    sl = p.sliders[keys.index("colour_organics")]
    assert sl.value_range() == (0, 150) and sl.value() == 0      # to 150 since 2026-10-22
    tip = p.colour_organics_slider_layout.itemAt(0).widget().toolTip()
    assert "lab scanners" in tip and "print" in tip
    assert not hasattr(p, "colour_organics_hint")     # no line in the panel
