#!/usr/bin/env python3
"""Colour Organics families and reach, set from a right-click popup on the
slider's label (spec/colour-organics.md, popup revision)."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
sys.path.insert(0, HERE)

from core.ccr_processor import (adjust_image, adjust_image_opencl,  # noqa: E402
                                normalize_organics_shape, organics_anchors,
                                organics_width, organics_hue_map,
                                ORGANICS_SHAPE_DEFAULT, ORGANICS_STRENGTH)
from test_tone_range import _Img, _wire, _Stub, _img  # noqa: E402

FULL = ORGANICS_STRENGTH
SHAPE = {"warm_shift": -12, "sky_on": False, "reach": 40}


# --- the shape ---------------------------------------------------------------------

@pytest.mark.parametrize("shape", [None, {}, dict(ORGANICS_SHAPE_DEFAULT), "junk"])
def test_default_shape_is_none(shape):
    assert normalize_organics_shape(shape) is None


def test_normalize_clamps_and_fills():
    s = normalize_organics_shape({"warm_shift": 99, "reach": 3, "foliage_on": 0,
                                  "violet_shift": "x"})
    assert s["warm_shift"] == 20 and s["reach"] == 10
    assert s["foliage_on"] is False and s["violet_shift"] == 0
    assert s["sky_on"] is True


def test_anchors_follow_the_families():
    assert organics_anchors(None) == [25.0, 100.0, 200.0, 265.0]
    assert organics_anchors(SHAPE) == [13.0, 100.0, 265.0]
    assert organics_width(SHAPE) == pytest.approx(0.40)
    every = {"warm_shift": 20, "foliage_shift": -20, "sky_shift": 20, "violet_shift": -20}
    a = organics_anchors(every)
    assert a == sorted(a)                         # nudges can never reorder them


def test_a_nudged_family_moves_its_centre():
    a, w = organics_anchors(SHAPE), organics_width(SHAPE)
    assert float(organics_hue_map(13.0, FULL, a, w)) == pytest.approx(13.0, abs=1e-3)
    assert float(organics_hue_map(25.0, FULL, a, w)) < 25.0      # drawn to 13 now


def test_all_families_off_is_the_identity():
    off = {f"{k}_on": False for k in ("warm", "foliage", "sky", "violet")}
    img = _img(4)
    assert np.array_equal(adjust_image(img, colour_organics=100, organics_shape=off), img)


def test_one_family_works_round_the_whole_circle():
    a = organics_anchors({f"{k}_on": False for k in ("warm", "sky", "violet")})
    h = np.linspace(0, 360, 3601)[:-1].astype(np.float32)
    m = organics_hue_map(h, FULL, a, 0.3)
    d = (np.roll(m, -1) - m) % 360
    assert d[:-1].min() > 0                       # still monotone
    assert float(organics_hue_map(100.0, FULL, a, 0.3)) == pytest.approx(100.0, abs=1e-3)


def test_the_shape_reaches_the_render_and_gpu_matches():
    img = _img(5)
    plain = adjust_image(img, colour_organics=80)
    shaped = adjust_image(img, colour_organics=80, organics_shape=SHAPE)
    assert not np.array_equal(plain, shaped)
    gpu = adjust_image_opencl(img, colour_organics=80, organics_shape=SHAPE)
    np.testing.assert_allclose(shaped.astype(np.int64), gpu.astype(np.int64), atol=3)


def test_apply_adjustments_and_area_layers_pass_the_shape(monkeypatch):
    from core.ccr_image import CCRImage
    import core.ccr_image as mod
    seen = {}

    def cap(image, *a, **kw):
        seen.update(kw)
        return image

    monkeypatch.setattr(mod, "adjust_image_opencl", cap)
    CCRImage.apply_adjustments(_Stub({"colour_organics": 50, "organics_shape": SHAPE}), _img())
    assert seen["organics_shape"] == SHAPE
    seen.clear()
    CCRImage._adjust_for_area(_Stub({}), _img(), {"colour_organics": 30, "organics_shape": SHAPE})
    assert seen["organics_shape"] == SHAPE


# --- the popup and the panel ---------------------------------------------------------

@pytest.fixture
def panel():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel
    return SlidersPanel()


def test_label_offers_a_context_menu(panel):
    from PySide6.QtCore import Qt
    lbl = panel._organics_label
    assert lbl.text() == "Colour Organics"
    assert lbl.contextMenuPolicy() == Qt.CustomContextMenu
    assert "Right-click" in lbl.toolTip() and "lab scanners" in lbl.toolTip()


def test_popup_edits_write_the_shape_and_survive_a_slider_move(panel, monkeypatch):
    img = _Img()
    _wire(panel, monkeypatch, img)
    panel._open_organics_popup(panel.mapToGlobal(panel.rect().center()))
    pop = panel._organics_popup
    pop.checks["sky"].setChecked(False)
    assert not pop.shifts["sky"].isEnabled()      # an off family can't be nudged
    pop.shifts["warm"].setValue(-12)
    pop.reach_slider.setValue(40)
    assert normalize_organics_shape(img.adjustment_settings["organics_shape"]) == \
        normalize_organics_shape(SHAPE)
    assert panel._organics_label.font().underline()
    assert "Sky and water off" in panel._organics_label.toolTip()
    pop.close()
    assert img.undo == 1                          # the whole popup visit is one undo
    panel.sliders[panel.adjustment_keys.index("contrast")].setValue(12)
    assert img.adjustment_settings["organics_shape"]["sky_on"] is False
    panel._open_organics_popup(panel.mapToGlobal(panel.rect().center()))
    panel._organics_popup.reset()
    assert "organics_shape" not in img.adjustment_settings
    assert not panel._organics_label.font().underline()
    panel._organics_popup.close()


def test_loading_a_frame_picks_up_its_shape(panel, monkeypatch):
    img = _Img({"colour_organics": 40, "organics_shape": dict(SHAPE)})
    _wire(panel, monkeypatch, img)
    panel._load_active_layer(0)
    assert panel._organics_shape == normalize_organics_shape(SHAPE)
    img2 = _Img({"colour_organics": 40})
    _wire(panel, monkeypatch, img2)
    panel._load_active_layer(0)
    assert panel._organics_shape is None


def test_paste_row_copy_and_apply(panel, monkeypatch):
    from widgets.sliders_panel import paste_options
    clip = {"adjustments": {}, "organics_shape": normalize_organics_shape(SHAPE)}
    row = [r for r in paste_options(clip, lambda k: 0) if r[1] == "organics_shape"]
    assert row and "Sky and water off" in row[0][2] and "reach 40%" in row[0][2]
    img = _Img({"contrast": 5})
    _wire(panel, monkeypatch, img)
    panel.current_idx = 1
    panel.clipboard = dict(clip, curves=None, cineon_log=False, look_lut=None,
                           tone_shape=None, flags={}, profile="color", crop=(None, 0.0),
                           rotation=0, flip_h=False, flip_v=False, fine_rotation=0)
    panel._apply_paste(0, {"organics_shape"})
    assert img.adjustment_settings["organics_shape"]["warm_shift"] == -12
    assert img.adjustment_settings["contrast"] == 5
    src = _Img({"colour_organics": 10, "organics_shape": dict(SHAPE)})
    _wire(panel, monkeypatch, src)
    panel.current_idx = 5
    panel.copy_settings_from_index(0)
    assert panel.clipboard["organics_shape"]["sky_on"] is False


def _sync(panel, monkeypatch, src, dst, sat):
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
    panel._sync_group_selection = {gid: (gid == "sat" and sat) or gid == "tone"
                                   for gid, _l, _k in SYNC_GROUPS}
    panel._perform_sync_to_all()


def test_sync_with_the_saturation_group_carries_the_shape(panel, monkeypatch):
    src = _Img({"colour_organics": 50, "organics_shape": dict(SHAPE)})
    dst = _Img({"colour_organics": 50})
    _sync(panel, monkeypatch, src, dst, sat=True)
    assert dst.adjustment_settings["organics_shape"]["warm_shift"] == -12


def test_sync_without_it_keeps_the_target_shape(panel, monkeypatch):
    src = _Img({"contrast": 20})
    dst = _Img({"contrast": 0, "organics_shape": dict(SHAPE)})
    _sync(panel, monkeypatch, src, dst, sat=False)
    assert dst.adjustment_settings["contrast"] == 20
    assert dst.adjustment_settings["organics_shape"]["sky_on"] is False


def test_sync_removes_the_shape_when_the_source_has_none(panel, monkeypatch):
    src = _Img({"colour_organics": 50})
    dst = _Img({"colour_organics": 50, "organics_shape": dict(SHAPE)})
    _sync(panel, monkeypatch, src, dst, sat=True)
    assert "organics_shape" not in dst.adjustment_settings
