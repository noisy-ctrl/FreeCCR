#!/usr/bin/env python3
"""Saturation Roll-off (spec/saturation-rolloff.md): a soft ceiling on strong
colours; muted colours and greys untouched, linear-light luminance kept."""

import colorsys
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_processor import (adjust_image, adjust_image_opencl,  # noqa: E402
                                _apply_sat_rolloff, rolloff_curve, ROLLOFF_GAMMA)


def _hsv(px):
    return colorsys.rgb_to_hsv(*[float(v) for v in px])


def _lum(px):
    lin = np.asarray(px, np.float64) ** ROLLOFF_GAMMA
    return float(lin @ [0.2126, 0.7152, 0.0722])


def _sweep():
    """Every hue at several saturations and brightnesses, as [0,1] floats."""
    cols = []
    for h in np.linspace(0, 1, 24, endpoint=False):
        for s in np.linspace(0, 1, 11):
            for v in (0.3, 0.6, 0.95):
                cols.append(colorsys.hsv_to_rgb(h, s, v))
    return np.array(cols, np.float32)[None]


def test_zero_is_identity():
    x = _sweep()
    assert np.array_equal(_apply_sat_rolloff(x, 0), np.clip(x, 0, 1))
    img = (x * 65535).astype(np.uint16)
    assert np.array_equal(adjust_image(img, sat_rolloff=0), img)


def test_greys_and_muted_colours_untouched():
    x = _sweep()[0]
    out = _apply_sat_rolloff(x[None], 100)[0]
    s = np.array([_hsv(p)[1] for p in x])
    keep = s <= 0.5 - 1e-3                     # the knee at full
    assert np.allclose(out[keep], x[keep], atol=1e-6)
    assert not np.allclose(out[~keep], x[~keep], atol=1e-3)


@pytest.mark.parametrize("amount", [30, 70, 100])
def test_strong_colours_land_on_the_curve_with_hue_and_luminance_kept(amount):
    x = _sweep()[0]
    out = _apply_sat_rolloff(x[None], amount)[0]
    assert out.min() >= 0 and out.max() <= 1             # never clips
    for a, b in zip(x, out):
        ha, sa, va = _hsv(a)
        hb, sb, vb = _hsv(b)
        if sa < 0.05:
            continue
        assert sb == pytest.approx(float(rolloff_curve(sa, amount)), abs=2e-3)
        d = abs(ha - hb); d = min(d, 1 - d)
        assert d < 2e-3                                     # same hue
        assert _lum(b) == pytest.approx(_lum(a), rel=2e-3, abs=1e-5)


def test_the_curve_is_a_soft_knee():
    s = np.linspace(0, 1, 1001)
    c = rolloff_curve(s, 100)
    assert np.all(np.diff(c) >= -1e-7)                      # monotone
    assert c[500] == pytest.approx(0.5, abs=1e-6)           # the knee
    assert c[-1] == pytest.approx(0.716, abs=0.005)         # ~Ember's default
    slope = np.diff(c) / np.diff(s)
    assert slope[500] == pytest.approx(1.0, abs=0.01)       # no kink at the knee


def test_a_strong_blue_keeps_its_lightness():
    out = _apply_sat_rolloff(np.array([[[0.0, 0.0, 1.0]]], np.float32), 100)[0, 0]
    assert out[2] > 0.45                     # a luma-on-display grey gave ~0.28
    assert _hsv(out)[1] == pytest.approx(float(rolloff_curve(1.0, 100)), abs=2e-3)


def test_runs_after_saturation_so_it_catches_boosts():
    img = (_sweep() * 65535).astype(np.uint16)
    boosted = adjust_image(img, saturation=60)
    capped = adjust_image(img, saturation=60, sat_rolloff=100)
    sb = np.array([_hsv(p / 65535)[1] for p in boosted[0]])
    sc = np.array([_hsv(p / 65535)[1] for p in capped[0]])
    assert sc.max() <= float(rolloff_curve(1.0, 100)) + 2e-3
    assert sb.max() > 0.99


def test_opencl_matches_cpu():
    img = (_sweep() * 65535).astype(np.uint16)
    for kw in (dict(sat_rolloff=40), dict(sat_rolloff=100),
               dict(sat_rolloff=80, saturation=30, vibrance=20, sub_saturation=15)):
        cpu = adjust_image(img, **kw)
        gpu = adjust_image_opencl(img, **kw)
        np.testing.assert_allclose(cpu.astype(np.int64), gpu.astype(np.int64), atol=3)


def test_slider_sits_under_saturation():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel, SYNC_GROUPS, _BASIC_LABELS
    keys = SlidersPanel.ADJUSTMENT_KEYS
    assert keys.index("sat_rolloff") == keys.index("saturation") + 1
    assert "sat_rolloff" in dict((g, k) for g, _l, k in SYNC_GROUPS)["sat"]
    assert _BASIC_LABELS["sat_rolloff"] == "Saturation Roll-off"
    p = SlidersPanel()
    sl = p.sliders[keys.index("sat_rolloff")]
    assert sl.value_range() == (0, 100) and sl.value() == 0
