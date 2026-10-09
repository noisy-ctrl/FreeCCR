#!/usr/bin/env python3
"""Auto black point (spec/auto-black-point.md): find the film base on each
frame from its clear border, with roll checks and stated fallbacks."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.auto_black_point import (find_rebate, roll_check, RebateResult,  # noqa: E402
                                   HIGH, MEDIUM, LOW)

BASE = np.array([0.42, 0.35, 0.38]) * 65535


def frame(base=BASE, h=720, w=1080, edges=("top", "bottom", "left", "right"),
          seed=0, tilt=0.0, gap=None, clip_level=None):
    """Synthetic raw scan: black holder, a clear-film border band (base +
    grain) on the given edges, and a denser textured image inside."""
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3))
    img[...] = 0.03 * base                                     # holder
    fx0, fy0, fx1, fy1 = 30, 25, w - 30, h - 25                # film area
    img[fy0:fy1, fx0:fx1] = base                               # clear film
    ix0, iy0, ix1, iy1 = fx0 + 12, fy0 + 12, fx1 - 12, fy1 - 12
    for side in ("top", "bottom", "left", "right"):            # remove borders not wanted
        if side not in edges:
            if side == "top":
                iy0 = fy0
            elif side == "bottom":
                iy1 = fy1
            elif side == "left":
                ix0 = fx0
            else:
                ix1 = fx1
    yy, xx = np.mgrid[iy0:iy1, ix0:ix1]
    tex = 0.25 + 0.2 * np.sin(xx / 37.0) * np.cos(yy / 23.0) + 0.1 * rng.random(xx.shape)
    img[iy0:iy1, ix0:ix1] = base * tex[..., None] * np.array([1.0, 0.95, 0.9])
    if tilt:
        import cv2
        M = cv2.getRotationMatrix2D((w / 2, h / 2), tilt, 1.0)
        img = cv2.warpAffine(img.astype(np.float32), M, (w, h)).astype(np.float64)
    img *= 1 + 0.01 * rng.standard_normal(img.shape)          # grain
    if gap is not None:                                        # light round the film
        img[2:20, 200:900] = gap
    if clip_level is not None:
        img = np.minimum(img, clip_level)
    return np.clip(img, 0, 65535).astype(np.uint16)


def test_finds_the_base_with_high_confidence():
    r = find_rebate(frame())
    assert r.confidence == HIGH
    np.testing.assert_allclose(r.black_point, BASE, rtol=0.03)
    x1, y1, x2, y2 = r.rect
    assert 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1


def test_slightly_askew_film_still_found():
    r = find_rebate(frame(tilt=0.8))
    assert r.confidence in (HIGH, MEDIUM)
    np.testing.assert_allclose(r.black_point, BASE, rtol=0.04)


def test_border_on_one_edge_only_is_medium():
    r = find_rebate(frame(edges=("left",)))
    assert r.confidence == MEDIUM and r.side == "left"
    np.testing.assert_allclose(r.black_point, BASE, rtol=0.03)


def test_no_border_is_low_with_no_value():
    r = find_rebate(frame(edges=()))
    assert r.confidence == LOW and r.black_point is None


def test_light_round_the_film_is_not_chosen():
    # a brighter no-film gap on one edge; the other three edges agree on base
    r = find_rebate(frame(gap=BASE * 2.6))
    assert r.confidence == HIGH
    np.testing.assert_allclose(r.black_point, BASE, rtol=0.03)


def test_clipped_border_falls_back_with_reason():
    clip = BASE * np.array([1.5, 0.98, 1.5])      # green clips at the border
    r = find_rebate(frame(clip_level=clip))
    assert r.confidence == LOW
    assert "clipped" in r.reason and "G" in r.reason


def test_edge_printing_does_not_move_the_value():
    f = frame()
    f[28:33, 300:340] = (0.1 * BASE).astype(np.uint16)          # lettering in the top band
    f[30:32, 600:620] = (0.2 * BASE).astype(np.uint16)
    r = find_rebate(f)
    np.testing.assert_allclose(r.black_point, BASE, rtol=0.03)


def test_any_light_level_and_channel_balance():
    for base in (BASE * 0.3, BASE * np.array([1.0, 0.5, 1.6])):
        r = find_rebate(frame(base=base))
        assert r.confidence == HIGH
        np.testing.assert_allclose(r.black_point, base, rtol=0.03)


def _res(bp, conf=HIGH):
    return RebateResult(tuple(bp), conf, "top", (0, 0, 1, 0.02), "")


def test_roll_check_accepts_one_channel_drifting():
    rs = [_res(BASE), _res(BASE * 1.01), _res(BASE * [1, 1, 1.7]), _res(BASE * 0.99)]
    cons = roll_check(rs)
    assert all(r.confidence == HIGH for r in rs)
    np.testing.assert_allclose(cons, BASE, rtol=0.02)


def test_roll_check_flags_a_whole_frame_jump():
    rs = [_res(BASE), _res(BASE * 1.01), _res(BASE * 2.2), _res(BASE * 0.99)]
    roll_check(rs)
    assert rs[2].confidence == LOW
    assert [r.confidence for i, r in enumerate(rs) if i != 2] == [HIGH] * 3


# --------------------------------------------------------------------------- #
# backend
# --------------------------------------------------------------------------- #

@pytest.fixture
def backend(monkeypatch):
    from core.ccr_backend import ccr_backend
    monkeypatch.setattr(ccr_backend, "black_point_bgr", None)
    monkeypatch.setattr(ccr_backend, "white_point_bgr", None)
    monkeypatch.setattr(ccr_backend, "film_stock_slopes", None)
    monkeypatch.setattr(ccr_backend, "density_bwpoint", False)
    monkeypatch.setattr(ccr_backend, "auto_awb", False, raising=False)
    return ccr_backend


def test_fallback_order(backend, monkeypatch):
    low = RebateResult(None, LOW, reason="none")
    assert backend.resolve_auto_black_point(_res(BASE)) == (tuple(BASE), "auto")
    assert backend.resolve_auto_black_point(low) == (None, "none")
    assert backend.resolve_auto_black_point(low, consensus=(1, 2, 3)) == ((1, 2, 3), "roll")
    monkeypatch.setattr(backend, "black_point_bgr", (9, 8, 7))
    assert backend.resolve_auto_black_point(low, consensus=(1, 2, 3)) == ((9, 8, 7), "sampled")


def test_white_point_scaled_by_the_light_change(backend, monkeypatch):
    assert backend.auto_white_point_for(tuple(BASE)) is None
    monkeypatch.setattr(backend, "black_point_bgr", (100.0, 100.0, 100.0))
    monkeypatch.setattr(backend, "white_point_bgr", (10.0, 20.0, 30.0))
    assert backend.auto_white_point_for((100.0, 200.0, 50.0)) == pytest.approx((10, 40, 15))


class _Img:
    def __init__(self, raw):
        self.resized_raw = raw
        self.converted = False
        self.fine_rotation_angle = 0
        self.conversion_inputs = None
        self.file_path = "x.RAF"
        self.adjustment_settings = {}

    def reload_image(self):
        raise AssertionError("raw is fresh: no reload expected")

    def update_thumbnail_and_preview(self):
        pass


def test_convert_all_uses_each_frames_own_base(backend, monkeypatch):
    import core.ccr_backend as mod
    drift = BASE * np.array([1.0, 1.0, 1.7])
    imgs = [_Img(frame(seed=1)), _Img(frame(base=drift, seed=2)),
            _Img(frame(edges=(), seed=3)), _Img(frame(seed=4))]
    calls = []

    def fake(img, black, white, density=False, slopes_bgr=None):
        calls.append((black, white))
        return img.resized_raw

    monkeypatch.setattr(mod, "ccr_normalize_with_bwpoint", fake)
    monkeypatch.setattr(backend, "images", imgs)
    monkeypatch.setattr(backend, "auto_black_point", True)
    monkeypatch.setattr(backend, "black_point_bgr", tuple(BASE * 1.1))   # sampled fallback
    backend.apply_bwpoint_to_all_images()
    np.testing.assert_allclose(imgs[0].conversion_inputs["bw"][0], BASE, rtol=0.03)
    np.testing.assert_allclose(imgs[1].conversion_inputs["bw"][0], drift, rtol=0.03)
    assert imgs[2].conversion_inputs["auto_bp"]["source"] == "sampled"
    np.testing.assert_allclose(imgs[2].conversion_inputs["bw"][0], BASE * 1.1)
    assert imgs[0].conversion_inputs["auto_bp"]["source"] == "auto"
    s = backend.last_auto_bp_summary
    assert s["high"] == 3 and s["fallback"] == 1 and s["skipped"] == 0


def test_convert_all_skips_a_frame_with_no_border_and_no_fallback(backend, monkeypatch):
    import core.ccr_backend as mod
    imgs = [_Img(frame(edges=(), seed=5))]
    monkeypatch.setattr(mod, "ccr_normalize_with_bwpoint",
                        lambda img, b, w, density=False, slopes_bgr=None: img.resized_raw)
    monkeypatch.setattr(backend, "images", imgs)
    monkeypatch.setattr(backend, "auto_black_point", True)
    backend.apply_bwpoint_to_all_images()
    assert imgs[0].converted is False
    assert backend.last_auto_bp_summary["skipped"] == 1


# --------------------------------------------------------------------------- #
# panel
# --------------------------------------------------------------------------- #

def test_panel_checkbox_label_and_warning(monkeypatch, backend):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel
    panel = SlidersPanel()
    saved = {}
    monkeypatch.setattr(panel._settings, "setValue", lambda k, v: saved.__setitem__(k, v))
    panel.auto_bp_checkbox.setChecked(False)
    panel.auto_bp_checkbox.setChecked(True)
    assert backend.auto_black_point is True
    assert saved["convert/auto_black_point"] is True
    assert "auto black point per frame" in panel.bwp_mode_label.text()
    # no sampled black point, but auto is on: no "convert unanchored?" prompt
    assert panel._confirm_no_anchor_convert() is True
    panel.auto_bp_checkbox.setChecked(False)
    assert backend.auto_black_point is False
