#!/usr/bin/env python3
"""Colour Organics (spec/colour-organics.md, revision 2026-11-02): hues turned
by a curve measured on lab scans, at the same luma and chroma, greys
untouched, never reordered."""

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
                                organics_knots, organics_delta, organics_min_slope,
                                organics_max_slope, ORGANICS_TABLE,
                                ORGANICS_MIN_CENTRE_SLOPE, ORGANICS_REGIONS)

LUMA = np.array([0.299, 0.587, 0.114])


def _hsv(px):
    return colorsys.rgb_to_hsv(*[float(v) for v in px])


def _hdiff(a, b):
    d = (np.asarray(a, np.float64) - np.asarray(b, np.float64)) % 360.0
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
    for amount in (100, 200):
        out = _apply_colour_organics(x[None], amount)[0]
        s = np.array([_hsv(p)[1] for p in x])
        assert np.allclose(out[s < 0.06], x[s < 0.06], atol=1e-6)
        assert np.allclose(out @ LUMA, x @ LUMA, atol=1e-5)
        assert out.min() >= 0 and out.max() <= 1


def test_the_measured_amount_is_the_table():
    k = organics_knots(100)
    np.testing.assert_allclose(k, np.float32(ORGANICS_TABLE), atol=1e-6)
    # Catmull-Rom passes through its knots
    h = np.arange(36, dtype=np.float32) * 10.0
    np.testing.assert_allclose(organics_delta(h, k), k, atol=1e-5)
    np.testing.assert_allclose(organics_knots(200), 2 * k, atol=1e-5)


def test_it_turns_hues_the_way_the_labs_did():
    """Measured (spec table): reds towards orange, skin a little yellower,
    blues back towards sky; nothing where nothing was measured."""
    move = lambda h: float(_hdiff(organics_hue_map(np.float32([h]), 100), h)[0])
    assert move(355.0) > 1.0 and move(10.0) > 1.0          # reds -> orange
    assert move(40.0) > 1.0                                # skin, orange -> yellower
    assert move(210.0) < -1.0                              # blue -> towards sky
    for h in range(270, 311, 10):                          # violets: not measured
        assert abs(move(float(h))) < 0.5


@pytest.mark.parametrize("amount", [50, 100, 150, 200])
def test_the_hue_map_is_monotone_for_any_region_amounts(amount):
    rng = np.random.default_rng(amount)
    shapes = [None] + [{k: int(rng.integers(0, 41)) * 5 for k, *_r in ORGANICS_REGIONS}
                       for _ in range(6)]
    h = np.arange(0.0, 360.0, 0.05).astype(np.float32)
    for shape in shapes:
        k = organics_knots(amount, shape)
        assert 1.0 + organics_min_slope(k) >= ORGANICS_MIN_CENTRE_SLOPE - 1e-6
        m = organics_hue_map(h, amount, shape).astype(np.float64)
        step = _hdiff(np.roll(m, -1), m)[:-1] / 0.05
        assert step.min() > 0                              # never reorders hues


def test_the_hue_stretch_is_gentle():
    """The curve's steepest rise is how much hue differences (and colour grain
    in hue) can widen: report it and keep it below the old warp's 1.26."""
    lo, hi = organics_min_slope(organics_knots(100)), organics_max_slope(organics_knots(100))
    assert 1.0 + hi < 1.26
    assert 1.0 + lo > 0.5


def test_the_pixel_lands_on_the_mapped_hue():
    x = _sweep(sats=(0.3, 0.6), vals=(0.5, 0.8))[0]
    out = _apply_colour_organics(x[None], 100)[0]
    for a, b in zip(x, out):
        ha = _hsv(a)[0] * 360
        want = float(organics_hue_map(np.float32([ha]), 100)[0])
        assert abs(float(_hdiff(_hsv(b)[0] * 360, want))) < 0.05


def test_opencl_matches_cpu():
    img = (_sweep() * 65535).astype(np.uint16)
    for kw in (dict(colour_organics=50), dict(colour_organics=100),
               dict(colour_organics=200, organics_shape={"warm": 200, "blue": 0}),
               dict(colour_organics=80, sat_rolloff=60, saturation=25, vibrance=10)):
        cpu = adjust_image(img, **kw)
        gpu = adjust_image_opencl(img, **kw)
        np.testing.assert_allclose(cpu.astype(np.int64), gpu.astype(np.int64), atol=3)


def test_the_fitter_recovers_a_curve_and_leaves_unmeasured_hues_alone():
    from core.organics_fit import fit_curve, curve_rms
    from core.ccr_processor import organics_cr_weights
    true = 6.0 * np.sin(np.radians(np.arange(36) * 10.0 * 2))
    true[24:33] = 0.0                                      # 240..320: no data there
    centres = np.array([h + 5.0 for h in range(0, 360, 10) if not 240 <= h < 330])
    idx, w = organics_cr_weights(centres)
    meas = (w * true[idx]).sum(1)
    cells = np.stack([centres, meas, np.full(len(centres), 30.0),
                      np.full(len(centres), 900.0)], 1)
    k = fit_curve(cells, lam=0.05, lam0=0.01)
    assert curve_rms(k, cells) < 0.6
    assert np.abs(k[26:30]).max() < 1.0                    # mid-gap: pulled to 0


def test_slider_helper_text_and_group():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel, SYNC_GROUPS, _BASIC_LABELS
    keys = SlidersPanel.ADJUSTMENT_KEYS
    # Golden Highlights and Shadow Neutralise sit between them
    # (spec/golden-highlights.md, spec/shadow-neutralise.md)
    assert "sub_saturation" not in keys                    # removed 2026-10-23
    assert keys.index("golden_highlights") == keys.index("vibrance") + 1
    assert keys.index("shadow_neutralise") == keys.index("golden_highlights") + 1
    assert keys.index("shadow_lift") == keys.index("shadow_neutralise") + 1
    assert keys.index("glow") == keys.index("shadow_lift") + 1
    assert keys.index("colour_organics") == keys.index("glow") + 1
    assert "colour_organics" in dict((g, k) for g, _l, k in SYNC_GROUPS)["sat"]
    assert _BASIC_LABELS["colour_organics"] == "Colour Organics"
    p = SlidersPanel()
    sl = p.sliders[keys.index("colour_organics")]
    # shows 0..100, stores 0..200 (revision 2026-10-23)
    assert sl.value_range() == (0, 200) and sl.maximum() == 100 and sl.value() == 0
    tip = p.colour_organics_slider_layout.itemAt(0).widget().toolTip()
    assert "lab scans" in tip and "measured" in tip.lower()
    assert not hasattr(p, "colour_organics_hint")     # no line in the panel


def test_subtracted_sat_slider_is_gone_and_old_values_are_dropped(monkeypatch):
    """Removed 2026-10-23 (the per-colour Subtractive Saturations section
    covers it): no slider, no sync entry, and a value an older edit stored no
    longer reaches the render."""
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel, SYNC_GROUPS
    sys.path.insert(0, os.path.dirname(__file__))
    from test_x_tones import _Stub, _capture, _img
    from core.ccr_image import CCRImage
    assert "sub_saturation" not in SlidersPanel.ADJUSTMENT_KEYS
    assert all("sub_saturation" not in k for _g, _l, k in SYNC_GROUPS)
    p = SlidersPanel()
    assert not any(lbl.text() == "Subtracted Sat" for lbl in
                   p.findChildren(type(p._organics_label)))
    seen = _capture(monkeypatch)
    CCRImage.apply_adjustments(_Stub({"sub_saturation": 40}), _img())
    assert seen.get("sub_saturation", 0) == 0


def test_the_fit_tool_runs_from_saved_moves(tmp_path, capsys):
    """tools/fit_organics.py --moves: a refit from measured moves needs no
    RAWs; it reports both labs predicted from each other."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
    import fit_organics
    rng = np.random.default_rng(0)
    store = {"labs": np.array(["lab one", "lab two"])}
    for lab in ("lab one", "lab two"):
        store[f"{lab}|frames"] = np.array(["A.raf~a.tif"])
        h = rng.uniform(0, 240, 6000)
        store[f"{lab}|0|h"] = h
        store[f"{lab}|0|d"] = 4.0 * np.sin(np.radians(h)) + rng.normal(0, 1, h.size)
        store[f"{lab}|0|w"] = np.full(h.size, 0.4)
        store[f"{lab}|0|v"] = rng.uniform(0.4, 0.95, h.size)
    moves = tmp_path / "moves.npz"
    np.savez_compressed(moves, **store)
    out = tmp_path / "out"
    assert fit_organics.main(["--lab", "x", "--scans", "x", "--lab", "y", "--scans", "y",
                              "--moves", str(moves), "--out", str(out)]) == 0
    report = (out / "report.txt").read_text()
    assert "lab one predicted from the others" in report
    assert "lab two predicted from the others" in report
