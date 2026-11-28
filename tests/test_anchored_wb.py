#!/usr/bin/env python3
"""Anchored Temperature and Tint (spec/anchored-white-balance.md)."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from core import anchored_wb as aw  # noqa: E402
from core import picture_levels as pl  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402

BASE = np.array([30000.0, 24000.0, 26000.0])
SPAN = np.array([1.2, 1.3, 1.5])
PIC = (30 / 400, 30 / 300, 370 / 400, 270 / 300)
BLACK, WHITE, MID = (60, 120, 60, 160), (150, 210, 200, 300), (220, 260, 40, 360)


def _scan():
    def val(x):
        return BASE * 10.0 ** (-SPAN * np.asarray(x, float))
    raw = np.empty((300, 400, 3))
    raw[:] = 15.0
    raw[10:-10, 10:-10] = val(0.0)
    raw[30:-30, 30:-30] = val(0.5)
    raw[60:120, 60:160] = val(0.0)
    raw[150:210, 200:300] = val(1.0)
    return raw.astype(np.uint16)


def _patch(a, r0, r1, c0, c1):
    return a[r0 + 5:r1 - 5, c0 + 5:c1 - 5].reshape(-1, a.shape[2]).mean(axis=0)


# --------------------------------------------------------------------------- #
# The gammas and the curve
# --------------------------------------------------------------------------- #

def test_the_measured_gammas():
    assert np.allclose(aw.gammas(13.24, 0), (0.980, 1.005, 1.194), atol=1e-3)
    assert np.allclose(aw.gammas(11.47, -2.65), (1.006, 0.975, 1.171), atol=1e-3)
    assert np.array_equal(aw.gammas(0, 0), np.ones(3))


@pytest.mark.parametrize("g", [0.6, 0.8, 0.98, 1.2, 1.6])
def test_the_curve_is_pinned_smooth_and_monotone(g):
    assert abs(float(aw.curve(np.float32(0.0), g))) < 1e-6
    assert float(aw.curve(np.float32(1.0), g)) == pytest.approx(1.0, abs=1e-6)
    v = np.linspace(-0.5, 2.0, 250001, dtype=np.float32)
    f = aw.curve(v, g)
    assert np.all(np.diff(f) > 0)
    # C1 at 0: the line below black has the curve's own slope there
    e = np.float32(1e-4)
    up = (aw.curve(e, g) - aw.curve(np.float32(0), g)) / e
    down = (aw.curve(np.float32(0), g) - aw.curve(-e, g)) / e
    assert up == pytest.approx(down, rel=2e-2)
    assert np.isfinite(up) and up < 4.1


def test_close_to_a_pure_gamma_away_from_black():
    v = np.linspace(0.05, 1.0, 1000)
    for g in (1.0, 1.1, 1.25):
        assert np.abs(aw.curve(v, g) - v ** g).max() * 255 < 1.1


def test_no_move_is_byte_for_byte():
    d = np.random.default_rng(1).random((20, 20, 3)).astype(np.float32)
    before = d.copy()
    assert aw.apply(d, 0, 0) is d and np.array_equal(d, before)


def test_the_guard_units_are_monotone_and_invert():
    t = np.linspace(-100, 100, 401)
    dt = [aw.temp_density(x) for x in t]
    dn = [aw.tint_density(x) for x in t]
    assert np.all(np.diff(dt) > 0) and np.all(np.diff(dn) > 0)
    for x in (-60, -7, 0, 13, 80):
        assert aw.temp_slider(aw.temp_density(x)) == pytest.approx(x, abs=0.05)
        assert aw.tint_slider(aw.tint_density(x)) == pytest.approx(x, abs=0.05)
    assert aw.temp_density(20) > 0 and aw.tint_density(20) > 0       # warm, magenta


def test_the_guard_caps_in_the_anchored_units():
    from core import awb
    t, n = awb.guard_wb((60.0, 0.0), roll=None, anchored=True)
    assert aw.temp_density(t) == pytest.approx(awb.TEMP_WARM_CAP, abs=0.003)
    t, n = awb.guard_wb((0.0, 0.0), roll=(10.0, 4.0), anchored=True)
    assert (t, n) == (0, 0)                       # within the caps of the roll
    # the flat units are left as they were
    assert awb.guard_wb((60.0, 0.0)) == awb.guard_wb((60.0, 0.0), anchored=False)


# --------------------------------------------------------------------------- #
# In the render
# --------------------------------------------------------------------------- #

@pytest.fixture
def frame(tmp_path, monkeypatch):
    from core.ccr_image import CCRImage
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()
    monkeypatch.setattr(ccr_backend, "positive_mode", False, raising=False)
    monkeypatch.setattr(ccr_backend, "black_point_bgr", None, raising=False)
    monkeypatch.setattr(ccr_backend, "white_point_bgr", None, raising=False)
    monkeypatch.setattr(ccr_backend, "crop_after_convert", False, raising=False)
    monkeypatch.setattr(ccr_backend, "conversion_levels", "picture", raising=False)
    path = tmp_path / "neg.tif"
    cv2.imwrite(str(path), _scan()[..., ::-1])
    img = CCRImage(str(path))
    img.crop_rect = PIC
    ccr_backend.images.append(img)
    ccr_backend.file_paths.append(img.file_path)
    plan = ccr_backend.plan_frame_conversion(img)
    ccr_backend.apply_frame_conversion(img, plan)
    yield img
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()


def _render(img, **s):
    img.adjustment_settings = {"sharpen_amount": 0, **s}
    return img.apply_adjustments(img.resized_raw).astype(np.float64) / 65535.0


def test_ends_stay_and_the_mids_move_by_the_gamma(frame):
    assert ccr_backend.anchored_wb(frame)
    a = _render(frame)
    b = _render(frame, temperature=20)
    # pinned at 0 and 1; the picture's black renders at F(0), 1.8/255, so
    # a +20 move takes it to 0.6/255 in blue
    for box in (BLACK, WHITE):
        assert np.abs(_patch(a, *box) - _patch(b, *box)).max() * 255 <= 1.5
    mid_a, mid_b = _patch(a, *MID), _patch(b, *MID)
    want = float(aw.curve(np.float32(mid_a[2]), aw.gammas(20, 0)[2]))
    assert mid_b[2] == pytest.approx(want, abs=1.5 / 255)
    assert mid_b[2] < mid_a[2] - 0.03                 # warmer: blue down in the mids


def test_a_film_base_frame_keeps_the_flat_multiply(frame, monkeypatch):
    monkeypatch.setattr(ccr_backend, "conversion_levels", "base")
    monkeypatch.setattr(ccr_backend, "black_point_bgr", tuple(float(v) for v in BASE))
    plan = ccr_backend.plan_frame_conversion(frame)
    ccr_backend.apply_frame_conversion(frame, plan)
    assert not ccr_backend.anchored_wb(frame)
    from core.ccr_processor import _apply_working_space_recovery
    a = _apply_working_space_recovery(frame.resized_raw, 0.0, 0.0, 20.0, 0.0)
    b = _apply_working_space_recovery(frame.resized_raw, 0.0, 0.0, 20.0, 0.0,
                                      look_lut=None)
    assert np.array_equal(a, b)


def test_cineon_takes_the_flat_multiply_back(frame):
    frame.adjustment_settings = {"cineon_log": True}
    assert not ccr_backend.anchored_wb(frame)


def test_the_flat_and_anchored_differ_only_with_the_curve():
    from core.ccr_processor import _apply_working_space_recovery, WS_B, _WS_INV_WIDTH
    rng = np.random.default_rng(3)
    base = (WS_B + rng.random((40, 40, 3)) * (1.0 / _WS_INV_WIDTH)).astype(np.uint16)
    pc = pl.PrintCurve([1.2, 1.3, 1.5])
    flat = _apply_working_space_recovery(base, 0.0, 0.0, 20.0, 0.0)
    anchored = _apply_working_space_recovery(base, 0.0, 0.0, 20.0, 0.0, look_lut=pc)
    d = (base.astype(np.float32) - np.float32(WS_B)) * np.float32(_WS_INV_WIDTH)
    want = aw.apply(pc.decode(d), 20.0, 0.0)
    assert np.abs(anchored.astype(np.float64) - np.clip(want, 0, 1) * 65535).max() <= 1.0
    assert not np.array_equal(flat, anchored)


def test_cpu_and_opencl_agree(frame):
    from core.ccr_processor import adjust_image, adjust_image_opencl
    pc = pl.print_curve(frame.conversion_inputs)
    a = adjust_image(frame.resized_raw, 15.0, -6.0, ws_windowed=True, look_lut=pc)
    b = adjust_image_opencl(frame.resized_raw, 15.0, -6.0, ws_windowed=True, look_lut=pc)
    assert np.abs(a.astype(int) - b.astype(int)).max() <= 2


def test_the_wb_picker_lands_a_cast_patch_grey(frame):
    # give the frame a cast in the mids with Channel Levels, then solve
    frame.adjustment_settings = {"sharpen_amount": 0, "ch_b_shift": -12.0, "ch_r_shift": 6.0}
    r0, r1, c0, c1 = MID
    res = frame.solve_neutral_wb(frame.resized_raw[r0 + 5:r1 - 5, c0 + 5:c1 - 5])
    t, n = res[0], res[1]
    out = _render(frame, ch_b_shift=-12.0, ch_r_shift=6.0, temperature=t, tint=n)
    m = _patch(out, *MID) * 255
    assert np.ptp(m) < 3.0, m
