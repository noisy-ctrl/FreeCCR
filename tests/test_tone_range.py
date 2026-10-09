#!/usr/bin/env python3
"""Highlights / Shadows range and keep-endpoint options
(spec/highlights-shadows-range.md)."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_processor import (adjust_image, adjust_image_opencl,  # noqa: E402
                                tone_region_curve, normalize_tone_shape,
                                TONE_SHAPE_DEFAULT, encode_window, keep_region)

X = np.linspace(0.0, 1.0, 2001)


def _old_formula(x, h, s):
    P, S = 0.10546875, 0.30
    y = x + (h / 100) * S * x ** 3 * (1 - x) / P + (s / 100) * S * x * (1 - x) ** 3 / P
    return np.clip(y, 0, 1)


def _img(seed=0, shape=(40, 50, 3)):
    return (np.random.default_rng(seed).random(shape) * 65535).astype(np.uint16)


# --------------------------------------------------------------------------- #
# defaults and the classic family
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("shape", [None, {}, dict(TONE_SHAPE_DEFAULT), "junk",
                                   {"hl_range": 50.0, "sh_keep": 0}])
def test_default_shape_is_byte_identical(shape):
    img = _img()
    a = adjust_image(img, highlights=45, shadows=-35, contrast=10)
    b = adjust_image(img, highlights=45, shadows=-35, contrast=10, tone_shape=shape)
    np.testing.assert_array_equal(a, b)
    g = adjust_image_opencl(img, highlights=45, shadows=-35, contrast=10, tone_shape=shape)
    np.testing.assert_array_equal(
        g, adjust_image_opencl(img, highlights=45, shadows=-35, contrast=10))


def test_normalize():
    assert normalize_tone_shape(None) is None
    assert normalize_tone_shape(dict(TONE_SHAPE_DEFAULT)) is None
    n = normalize_tone_shape({"hl_keep": 1, "sh_range": 140})
    assert n == {"hl_keep": True, "hl_range": 50, "sh_keep": False, "sh_range": 100}


@pytest.mark.parametrize("h,s", [(100, 0), (-100, 0), (0, 100), (0, -100), (60, -40)])
def test_classic_middle_range_is_todays_curve(h, s):
    np.testing.assert_allclose(tone_region_curve(X, h, s, None), _old_formula(X, h, s),
                               atol=1e-12)


def test_range_controls_midtone_reach():
    mid = np.array([0.5])
    narrow = tone_region_curve(mid, 100, 0, {"hl_range": 0})[0] - 0.5
    default = tone_region_curve(mid, 100, 0, None)[0] - 0.5
    wide = tone_region_curve(mid, 100, 0, {"hl_range": 100})[0] - 0.5
    assert 0 <= narrow < default < wide


# --------------------------------------------------------------------------- #
# keep-endpoint mode
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("tool", ["hl", "sh"])
@pytest.mark.parametrize("v", [100, -100, 37])
@pytest.mark.parametrize("r", [0, 25, 50, 75, 100])
def test_keep_mode_is_monotone_and_holds_the_endpoints(tool, v, r):
    shape = {f"{tool}_keep": True, f"{tool}_range": r}
    h, s = (v, 0) if tool == "hl" else (0, v)
    y = tone_region_curve(X, h, s, shape)
    assert y[0] == 0.0 and y[-1] == 1.0
    assert np.all(np.diff(y) > 0), "curve must stay strictly increasing"
    # nothing new reaches black or white
    inner = (X > 0) & (X < 1)
    assert y[inner].min() > 0 and y[inner].max() < 1
    # tones within 1 % of the protected endpoint barely move
    near = X >= 0.99 if tool == "hl" else X <= 0.01
    assert np.abs(y[near] - X[near]).max() < 0.005
    # tones outside the region are untouched
    lo, hi = keep_region(tool, r)
    outside = (X <= lo) if tool == "hl" else (X >= hi)
    np.testing.assert_allclose(y[outside], X[outside], atol=1e-12)


def test_keep_mode_both_tools_overlapping_stay_monotone():
    shape = {"hl_keep": True, "hl_range": 100, "sh_keep": True, "sh_range": 100}
    for h, s in ((100, -100), (-100, 100), (100, 100), (-100, -100)):
        y = tone_region_curve(X, h, s, shape)
        assert np.all(np.diff(y) >= 0)
        assert y[0] == 0.0 and y[-1] == 1.0


def test_keep_mode_darkens_highlights_but_white_stays_white():
    img = np.zeros((1, 3, 3), np.uint16)
    img[0, 0] = 65535
    img[0, 1] = int(0.85 * 65535)
    img[0, 2] = int(0.30 * 65535)
    out = adjust_image(img, highlights=-100, tone_shape={"hl_keep": True})
    assert out[0, 0].min() == 65535                        # white stays white
    assert out[0, 1].max() < int(0.80 * 65535)             # highlights darkened
    np.testing.assert_array_equal(out[0, 2], img[0, 2])    # midtone untouched
    classic = adjust_image(img, highlights=-100)
    assert classic[0, 2].max() < img[0, 2].max()           # classic reaches the midtones


# --------------------------------------------------------------------------- #
# GPU / CPU parity and the image paths
# --------------------------------------------------------------------------- #

SHAPE = {"hl_keep": True, "hl_range": 30, "sh_keep": False, "sh_range": 70}


@pytest.mark.parametrize("ws", [False, True])
@pytest.mark.parametrize("kw", [
    dict(highlights=60, shadows=-40),
    dict(highlights=-80, shadows=50, exposure=30, brightness=-16, ch_r_shift=12,
         ch_master_gain=10),
    dict(highlights=40, contrast=20, saturation=15, blackpoint=10, ch_g_gain=8),
    dict(shadows=70, balance_r=20, cineon_log=True),
])
def test_gpu_matches_cpu_with_a_custom_shape(ws, kw):
    rng = np.random.default_rng(1)
    img = (encode_window(rng.uniform(-0.1, 1.1, (30, 40, 3)).astype(np.float32))
           if ws else _img(2, (30, 40, 3)))
    a = adjust_image(img, ws_windowed=ws, tone_shape=SHAPE, **kw)
    b = adjust_image_opencl(img, ws_windowed=ws, tone_shape=SHAPE, **kw)
    np.testing.assert_allclose(a.astype(np.int64), b.astype(np.int64), atol=2)


def test_custom_curve_applied_accurately():
    img = _img(3)
    out = adjust_image(img, highlights=70, tone_shape={"hl_keep": True, "hl_range": 20})
    want = tone_region_curve(img.astype(np.float64).ravel() / 65535, 70, 0,
                             {"hl_keep": True, "hl_range": 20})
    np.testing.assert_allclose(out.astype(np.float64).ravel() / 65535, want, atol=2 / 65535)


class _Stub:
    def __init__(self, settings, areas=()):
        self._ws_windowed = False
        self.converted = False
        self.adjustment_settings = settings
        self.contrast_base = 0
        self.temperature_base = 0
        self.brightness_base = 0
        self.exposure_base = 0.0
        self.color_profile = "color"
        self.area_layers = list(areas)
        self.tint_balance_factor = 1.0

    def _apply_dust_removal(self, image, ws_windowed=False):
        return image

    def _to_grayscale(self, image):
        return image


def test_apply_adjustments_passes_the_shape(monkeypatch):
    from core.ccr_image import CCRImage
    import core.ccr_image as mod
    seen = {}

    def cap(image, *a, **kw):
        seen.update(kw)
        return image

    monkeypatch.setattr(mod, "adjust_image_opencl", cap)
    CCRImage.apply_adjustments(_Stub({"highlights": 20, "tone_shape": SHAPE}), _img())
    assert seen["tone_shape"] == SHAPE


def test_area_layer_passes_its_own_shape(monkeypatch):
    from core.ccr_image import CCRImage
    import core.ccr_image as mod
    seen = {}

    def cap(image, *a, **kw):
        seen.update(kw)
        return image

    monkeypatch.setattr(mod, "adjust_image_opencl", cap)
    CCRImage._adjust_for_area(_Stub({}), _img(), {"shadows": 30, "tone_shape": SHAPE})
    assert seen["tone_shape"] == SHAPE


# --------------------------------------------------------------------------- #
# panel
# --------------------------------------------------------------------------- #

@pytest.fixture
def panel():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel
    return SlidersPanel()


def test_labels_offer_a_context_menu(panel):
    from PySide6.QtCore import Qt
    for tool in ("hl", "sh"):
        lbl = panel._tone_labels[tool]
        assert lbl.contextMenuPolicy() == Qt.CustomContextMenu
        assert "Right-click" in lbl.toolTip()
    assert panel._tone_labels["hl"].text() == "Highlights"
    assert panel._tone_labels["sh"].text() == "Shadows"


def _wire(panel, monkeypatch, img):
    from core.ccr_backend import ccr_backend
    monkeypatch.setattr(ccr_backend, "get_image_by_index", lambda i: img)
    monkeypatch.setattr(ccr_backend, "images", [img])
    monkeypatch.setattr(ccr_backend, "set_active_settings_by_index",
                        lambda i, s, reprocess=True: setattr(img, "adjustment_settings", s))
    monkeypatch.setattr(ccr_backend, "get_active_settings_by_index",
                        lambda i: img.adjustment_settings)
    mw = type("MW", (), {"image_preview": type("IP", (), {
        "update_preview": lambda s, i: None})()})()
    monkeypatch.setattr(panel, "parent", lambda: type("P", (), {"parent": lambda s: mw})())
    panel.current_idx = 0


class _Img:
    active_area_id = None
    converted = True
    crop_rect = None

    def __init__(self, settings=None):
        self.adjustment_settings = settings or {}
        self.undo = 0
        self.area_layers = []

    def push_undo_state(self):
        self.undo += 1

    def get_area(self, _id):
        return None

    def update_thumbnail_and_preview(self):
        pass


def test_popup_edit_writes_shape_and_survives_a_slider_move(panel, monkeypatch):
    img = _Img()
    _wire(panel, monkeypatch, img)
    panel._open_tone_popup("hl", panel.mapToGlobal(panel.rect().center()))
    pop = panel._tone_popup
    pop.keep_check.setChecked(True)
    pop.range_slider.setValue(30)
    assert img.adjustment_settings["tone_shape"] == {
        "hl_keep": True, "hl_range": 30, "sh_keep": False, "sh_range": 50}
    assert panel._tone_labels["hl"].font().underline()
    assert not panel._tone_labels["sh"].font().underline()
    pop.close()
    assert img.undo == 1                                    # one undo step
    # an ordinary slider move rebuilds the dict - the shape must ride along
    panel.sliders[panel.adjustment_keys.index("contrast")].setValue(12)
    assert img.adjustment_settings["tone_shape"]["hl_keep"] is True
    # reset removes it
    panel._open_tone_popup("hl", panel.mapToGlobal(panel.rect().center()))
    panel._tone_popup.reset()
    assert "tone_shape" not in img.adjustment_settings
    panel._tone_popup.close()


def test_paste_row_and_apply(panel, monkeypatch):
    from widgets.sliders_panel import paste_options
    clip = {"adjustments": {}, "tone_shape": {"hl_keep": True, "hl_range": 30,
                                              "sh_keep": False, "sh_range": 50}}
    rows = paste_options(clip, lambda k: 0)
    row = [r for r in rows if r[1] == "tone_shape"]
    assert row and "keep white" in row[0][2] and "range 30" in row[0][2]
    img = _Img({"contrast": 5})
    _wire(panel, monkeypatch, img)
    panel.current_idx = 1                                   # paste onto a non-current image
    panel.clipboard = dict(clip, curves=None, cineon_log=False, look_lut=None,
                           flags={}, profile="color", crop=(None, 0.0), rotation=0,
                           flip_h=False, flip_v=False, fine_rotation=0)
    panel._apply_paste(0, {"tone_shape"})
    assert img.adjustment_settings["tone_shape"]["hl_keep"] is True
    assert img.adjustment_settings["contrast"] == 5


def test_copy_carries_the_shape(panel, monkeypatch):
    img = _Img({"highlights": 10, "tone_shape": {"sh_keep": True}})
    _wire(panel, monkeypatch, img)
    panel.current_idx = 5                                   # copy from a non-current image
    panel.copy_settings_from_index(0)
    assert panel.clipboard["tone_shape"]["sh_keep"] is True


def _sync(panel, monkeypatch, src, dst, tone):
    from core.ccr_backend import ccr_backend
    from widgets.sliders_panel import SYNC_GROUPS
    for im in (src, dst):
        im.crop_rect = None
        im.crop_angle = 0.0
        im.color_profile = "color"
    _wire(panel, monkeypatch, src)
    monkeypatch.setattr(ccr_backend, "images", [src, dst])
    monkeypatch.setattr(ccr_backend, "get_image_by_index", lambda i: [src, dst][i])
    panel.current_idx = 0
    panel._sync_group_selection = {gid: (gid == "tone") == tone or gid == "sat"
                                   for gid, _l, _k in SYNC_GROUPS}
    panel._sync_group_selection["tone"] = tone
    panel._perform_sync_to_all()


def test_sync_copies_shape_with_tone_group(panel, monkeypatch):
    src = _Img({"highlights": 30, "tone_shape": {"hl_keep": True}})
    dst = _Img({"highlights": 30})
    _sync(panel, monkeypatch, src, dst, tone=True)
    assert dst.adjustment_settings["tone_shape"]["hl_keep"] is True


def test_sync_without_tone_group_keeps_target_shape(panel, monkeypatch):
    src = _Img({"saturation": 40})
    dst = _Img({"saturation": 0, "tone_shape": {"sh_keep": True}})
    _sync(panel, monkeypatch, src, dst, tone=False)
    assert dst.adjustment_settings["saturation"] == 40
    assert dst.adjustment_settings["tone_shape"]["sh_keep"] is True


def test_sync_with_tone_group_removes_shape_when_source_has_none(panel, monkeypatch):
    src = _Img({"highlights": 30})
    dst = _Img({"highlights": 30, "tone_shape": {"sh_keep": True}})
    _sync(panel, monkeypatch, src, dst, tone=True)
    assert "tone_shape" not in dst.adjustment_settings
