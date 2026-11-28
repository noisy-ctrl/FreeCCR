#!/usr/bin/env python3
"""Levels from the picture: the default conversion
(spec/picture-levels-conversion.md)."""

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

from core import picture_levels as pl  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402
from core.print_curve_data import KNOTS  # noqa: E402

BASE = np.array([30000.0, 24000.0, 26000.0])
SPAN = np.array([1.2, 1.3, 1.5])


def _scan(h=300, w=400, holder=True):
    """A negative in array order: holder outside, a clear rebate, then a
    picture of mid grey with a clear (x 0) and a dense (x 1) block, each
    well over the percentiles' share of the picture."""
    def val(x):
        return BASE * 10.0 ** (-SPAN * np.asarray(x, float))
    raw = np.empty((h, w, 3))
    raw[:] = 15.0 if holder else val(0.0)
    raw[10:-10, 10:-10] = val(0.0)                       # rebate
    raw[30:-30, 30:-30] = val(0.5)                       # picture
    raw[60:120, 60:160] = val(0.0)
    raw[150:210, 200:300] = val(1.0)
    raw[60:120, 200:300] = val(0.25)
    return raw.astype(np.uint16)


PIC = (30 / 400, 30 / 300, 370 / 400, 270 / 300)


# --------------------------------------------------------------------------- #
# Measuring
# --------------------------------------------------------------------------- #

def test_black_and_white_land_on_the_picture_extremes():
    raw = _scan()
    lv = pl.measure(raw, PIC)
    assert lv is not None
    v0 = np.round(BASE).astype(int)
    v1 = np.round(BASE * 10 ** -SPAN).astype(int)
    assert np.allclose(lv.black, v0, atol=1)
    assert np.allclose(lv.white, v1, atol=1)
    assert np.allclose(lv.record["range"], SPAN, atol=1e-3)
    assert lv.record["from"] == "picture" and lv.record["rect"] == list(PIC)


def test_the_area_is_the_picture_inset():
    raw = _scan()
    # a clearer-than-black stripe right at the picture's edge: inside the
    # rect, but within its 3 % inset
    raw[31:269, 31:33] = (60000, 60000, 60000)
    lv = pl.measure(raw, PIC)
    assert np.allclose(lv.black, np.round(BASE), atol=1)
    r0, r1, c0, c1 = pl.measure_area(raw.shape, PIC)
    assert c0 > 33 and r0 > 31


def test_without_a_picture_area_the_middle_is_measured():
    raw = _scan()
    assert pl.measured_rect(None) == pytest.approx([0.07, 0.07, 0.93, 0.93])
    lv = pl.measure(raw, None)
    assert lv is not None and lv.record["rect"] is None


def test_a_holder_strip_is_never_the_white():
    raw = _scan()
    raw[100:140, 40:360] = 15                            # opaque, 3.3+ above black
    lv = pl.measure(raw, PIC)
    assert np.allclose(lv.white, np.round(BASE * 10 ** -SPAN), atol=1)


def test_clipped_pixels_are_left_out():
    raw = _scan()
    raw[220:260, 40:200] = 65535
    lv = pl.measure(raw, PIC)
    assert np.allclose(lv.black, np.round(BASE), atol=1)


def test_a_flat_channel_gets_the_minimum_range():
    raw = _scan()
    raw[..., 1] = 20000                                  # nothing in green
    lv = pl.measure(raw, PIC)
    assert lv.record["range"][1] == pytest.approx(pl.MIN_RANGE)
    assert lv.white[1] == pytest.approx(20000 / 10 ** pl.MIN_RANGE)


def test_no_light_gives_nothing():
    assert pl.measure(np.full((300, 400, 3), 20, np.uint16), PIC) is None
    assert pl.measure(np.full((20, 20, 3), 30000, np.uint16), None) is None


# --------------------------------------------------------------------------- #
# The curve and the decode
# --------------------------------------------------------------------------- #

def test_the_curve_goes_through_its_knots_and_is_monotone():
    u = np.linspace(0, 1, 11)
    assert np.allclose(pl.F(u), KNOTS, atol=1e-6)
    x = np.linspace(-0.3, 1.3, 400001)
    f = pl.F(x)
    slope = np.diff(f) / np.diff(x)
    assert slope.min() > 0.05
    # straight lines outside [0, 1] at the end slopes
    assert pl.F(np.array([-0.1]))[0] == pytest.approx(KNOTS[0] - 0.1 * pl.SLOPE_BLACK)
    assert pl.F(np.array([1.1]))[0] == pytest.approx(KNOTS[-1] + 0.1 * pl.SLOPE_WHITE)
    # C1 at the ends: the slope just inside meets the line
    e = 1e-6
    assert (pl.F(np.array([e]))[0] - pl.F(np.array([0.0]))[0]) / e == pytest.approx(
        pl.SLOPE_BLACK, rel=1e-3)


def test_the_decode_is_transmission_levels_then_the_curve():
    pc = pl.PrintCurve([1.2, 1.3, 1.5])
    x = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [0.4, 0.5, 0.6]], np.float32)
    out = pc.decode(x)
    assert np.allclose(out[0], KNOTS[0], atol=1e-6)
    assert np.allclose(out[1], KNOTS[-1], atol=1e-5)
    D = np.array([1.2, 1.3, 1.5])
    u = (1 - 10 ** (-D * x[2])) / (1 - 10 ** -D)
    assert np.allclose(out[2], pl.F(u), atol=1e-5)


def test_a_16_bit_fine_ramp_stays_smooth_and_in_range():
    pc = pl.PrintCurve([1.2, 1.3, 1.5])
    x = np.linspace(0, 1, 65536, dtype=np.float32)
    out = pc.decode(np.stack([x, x, x], axis=1))
    assert np.all(np.diff(out, axis=0) > 0)
    assert out.min() >= 0 and out.max() <= 1.0
    # no kinks: the second difference is tiny everywhere
    assert np.abs(np.diff(out, 2, axis=0)).max() < 1e-5


def test_only_picture_levels_frames_get_a_print_curve():
    ci = {"mode": "bw", "bw": (tuple(BASE), tuple(BASE / 10)), "density": True,
          "levels": {"from": "picture"}}
    assert isinstance(pl.print_curve(ci), pl.PrintCurve)
    assert np.allclose(pl.print_curve(ci).ranges, 1.0)
    for bad in ({**ci, "levels": None}, {**ci, "density": False}, {**ci, "mode": "ref"},
                {**ci, "bw": (tuple(BASE), None)}, None):
        assert pl.print_curve(bad) is None


# --------------------------------------------------------------------------- #
# Converting and rendering
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
    cv2.imwrite(str(path), _scan()[..., ::-1])           # written BGR
    img = CCRImage(str(path))
    img.crop_rect = PIC                                  # the picture area
    ccr_backend.images.append(img)
    ccr_backend.file_paths.append(img.file_path)
    yield img
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()


def _convert(img):
    plan = ccr_backend.plan_frame_conversion(img)
    raw0 = img.resized_raw.copy()
    ccr_backend.apply_frame_conversion(img, plan)
    return plan, raw0


def _patch(a, r0, r1, c0, c1):
    return a[r0 + 5:r1 - 5, c0 + 5:c1 - 5].reshape(-1, a.shape[2]).mean(axis=0)


def test_convert_measures_and_records_the_levels(frame, monkeypatch):
    monkeypatch.setattr(ccr_backend, "density_bwpoint", False)
    plan, raw0 = _convert(frame)
    ci = frame.conversion_inputs
    assert plan["levels"] is not None and ci["levels"]["from"] == "picture"
    assert ci["density"] is True                         # whatever the toggle says
    assert ci["mode"] == "bw" and ci["bw"][1] is not None
    assert "auto_tone" not in ci and "clip_guard" not in ci and "auto_bp" not in ci
    from core.catalog import _ci_from_json, _ci_to_json
    import json
    back = _ci_from_json(json.loads(json.dumps(_ci_to_json(ci))))
    assert back["levels"] == ci["levels"] and back["bw"] == ci["bw"]


def test_the_render_is_the_print_curve_of_transmission_levels(frame):
    plan, raw0 = _convert(frame)
    frame.adjustment_settings = {"sharpen_amount": 0}
    out = frame.apply_adjustments(frame.resized_raw).astype(np.float64) / 65535.0
    b, w = np.array(plan["black"]), np.array(plan["white"])
    for (r0, r1, c0, c1) in ((60, 120, 60, 160), (150, 210, 200, 300),
                             (60, 120, 200, 300), (220, 260, 40, 360)):
        v = _patch(raw0.astype(np.float64), r0, r1, c0, c1)
        want = pl.F((b - v) / (b - w))
        got = _patch(out, r0, r1, c0, c1)
        assert np.allclose(got, want, atol=1.0 / 255), (r0, c0, got * 255, want * 255)


def test_the_old_default_additions_are_off(frame):
    _convert(frame)
    assert frame._auto_gain_for_base(frame.resized_raw) == 0.0
    assert ccr_backend.guard_clipping(frame) is None
    # the -8 baseline is not applied: an empty settings dict renders exactly
    # what the bare curve gives
    frame.adjustment_settings = {}
    a = frame.apply_adjustments(frame.resized_raw)
    from core.ccr_processor import _apply_working_space_recovery
    b = _apply_working_space_recovery(frame.resized_raw, 0.0,
                                      look_lut=pl.print_curve(frame.conversion_inputs))
    assert np.abs(a.astype(int) - b.astype(int)).max() <= 1


def test_cineon_takes_the_slot(frame):
    _convert(frame)
    frame.adjustment_settings = {"cineon_log": True, "sharpen_amount": 0}
    with_curve = frame.apply_adjustments(frame.resized_raw)
    ci = dict(frame.conversion_inputs)
    frame.conversion_inputs = {k: v for k, v in ci.items() if k != "levels"}
    plain = frame.apply_adjustments(frame.resized_raw)
    frame.conversion_inputs = ci
    assert np.array_equal(with_curve, plain)


def test_master_shift_moves_density_before_the_curve(frame):
    from core.ccr_processor import (WS_B, _WS_INV_WIDTH, _apply_channel_levels,
                                    _apply_working_space_recovery)
    _convert(frame)
    pc = pl.print_curve(frame.conversion_inputs)
    got = _apply_working_space_recovery(frame.resized_raw, 0.0, ch_master_shift=12.0,
                                        look_lut=pc)
    d = (frame.resized_raw.astype(np.float32) - np.float32(WS_B)) * np.float32(_WS_INV_WIDTH)
    _apply_channel_levels(d, 0.0, 12.0, 0.0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                          clamp=False, include_master_gain=False)
    want = np.clip(pc.decode(d), 0, 1) * 65535
    assert np.abs(got.astype(np.float64) - want).max() <= 1.0


def test_the_film_base_method_is_unchanged(frame, monkeypatch):
    monkeypatch.setattr(ccr_backend, "conversion_levels", "base")
    monkeypatch.setattr(ccr_backend, "black_point_bgr", tuple(float(v) for v in BASE))
    plan = ccr_backend.plan_frame_conversion(frame)
    assert "levels" not in plan and plan.get("density") is None
    ccr_backend.apply_frame_conversion(frame, plan)
    assert "levels" not in frame.conversion_inputs
    assert pl.print_curve(frame.conversion_inputs) is None


def test_too_little_picture_falls_back_to_the_film_base(frame, monkeypatch):
    monkeypatch.setattr(pl, "measure", lambda raw, rect=None: None)
    plan = ccr_backend.plan_frame_conversion(frame)
    assert plan.get("levels") is None


def test_the_density_toggle_leaves_picture_frames_alone(frame, monkeypatch):
    _convert(frame)
    before = frame.resized_raw.copy()
    monkeypatch.setattr(ccr_backend, "density_bwpoint", False)
    ccr_backend.reprocess_all_for_density_bwpoint_change()
    assert frame.conversion_inputs["density"] is True
    assert np.array_equal(frame.resized_raw, before)


def test_cpu_and_opencl_agree(frame):
    from core.ccr_processor import adjust_image, adjust_image_opencl
    _convert(frame)
    pc = pl.print_curve(frame.conversion_inputs)
    a = adjust_image(frame.resized_raw, saturation=10, ws_windowed=True, look_lut=pc)
    b = adjust_image_opencl(frame.resized_raw, saturation=10, ws_windowed=True, look_lut=pc)
    assert np.abs(a.astype(int) - b.astype(int)).max() <= 2


def test_convert_all_uses_levels_from_the_picture(frame, tmp_path):
    from core.ccr_image import CCRImage
    path = tmp_path / "neg2.tif"
    cv2.imwrite(str(path), _scan()[..., ::-1])
    other = CCRImage(str(path))
    ccr_backend.images.append(other)
    ccr_backend.file_paths.append(other.file_path)
    ccr_backend.apply_bwpoint_to_all_images()
    summ = ccr_backend.last_auto_bp_summary
    assert summ["picture"] == 2 and summ["skipped"] == 0
    for img in (frame, other):
        assert img.converted and img.conversion_inputs["levels"]["from"] == "picture"


def test_a_tether_capture_converts_without_a_black_point(frame):
    from core.tether_watcher import TetherWatchWorker
    w = TetherWatchWorker()
    out = []
    w.captured.connect(out.append)
    w._finish([frame])
    assert out == [frame] and frame.converted
    assert frame.conversion_inputs["levels"]["from"] == "picture"


# --------------------------------------------------------------------------- #
# The user interface
# --------------------------------------------------------------------------- #

def test_settings_and_panel(frame, monkeypatch):
    from PySide6.QtCore import QSettings
    from ui.main_window import MainWindow
    st = QSettings("FreeCCR", "FreeCCR")
    old = st.value("convert/levels")
    try:
        w = MainWindow()
        w.on_conversion_levels_changed("base")
        assert ccr_backend.conversion_levels == "base"
        assert st.value("convert/levels") == "base"
        p = w.sliders_panel
        assert p.levels_note.isHidden()
        assert "levels from each picture" not in p.bwp_mode_label.text()
        w.on_conversion_levels_changed("picture")
        assert ccr_backend.conversion_levels == "picture"
        assert not p.levels_note.isHidden()
        assert p.bwp_mode_label.text() == "Anchor source: levels from each picture"
        assert not p.white_point_btn.isEnabled() and not p.film_stock_combo.isEnabled()
        assert not p.wp_combo.isEnabled()
        assert p.black_point_btn.isEnabled()
        # the tether nudge isn't shown: nothing needs a black point
        w._tether_active = True
        w._tether_prompted = True
        w.refresh_tether_banner()
        assert w._tether_note() == ""
        w._tether_active = False
    finally:
        if old is None:
            st.remove("convert/levels")
        else:
            st.setValue("convert/levels", old)


def test_settings_dialog_offers_the_choice(frame, monkeypatch):
    from ui.main_window import MainWindow
    from widgets.settings_dialog import SettingsDialog
    w = MainWindow()
    monkeypatch.setattr(ccr_backend, "conversion_levels", "picture")
    calls = []
    w.on_conversion_levels_changed = calls.append
    d = SettingsDialog(w)
    combo = d._combo_levels
    assert [combo.itemData(i) for i in range(combo.count())] == ["picture", "base"]
    assert combo.currentData() == "picture"
    combo.setCurrentIndex(combo.findData("base"))
    d._apply_pending()
    assert calls == ["base"]
