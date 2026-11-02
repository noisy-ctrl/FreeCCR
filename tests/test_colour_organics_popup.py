#!/usr/bin/env python3
"""Colour Organics' region amounts, set from a right-click popup on the
slider's label (spec/colour-organics.md, revisions 2026-10-16 and 2026-11-02:
the families popup became per-region amounts of the measured curve)."""

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
                                normalize_organics_shape, organics_hue_map,
                                organics_region_scale, ORGANICS_REGIONS,
                                ORGANICS_SHAPE_DEFAULT)
from test_x_tones import _Img, _wire, _Stub, _img  # noqa: E402

SHAPE = {"warm": 150, "blue": 0}


# --- the shape ---------------------------------------------------------------------

@pytest.mark.parametrize("shape", [None, {}, dict(ORGANICS_SHAPE_DEFAULT), "junk",
                                   {"warm_on": True, "sky_shift": 5, "reach": 40}])
def test_default_shape_is_none(shape):
    """An old families shape (before 2026-11-02) has none of the region keys."""
    assert normalize_organics_shape(shape) is None


def test_normalize_clamps_and_fills():
    out = normalize_organics_shape({"warm": 999, "green": -5, "blue": "x", "reach": 40})
    assert out == {"warm": 200, "green": 0, "blue": 100}


def test_region_weights_sum_to_one_and_put_each_region_where_it_belongs():
    h = np.arange(0.0, 360.0, 0.5)
    one = {k: 100 for k, *_r in ORGANICS_REGIONS}
    np.testing.assert_allclose(organics_region_scale(h, one), 1.0)
    for key, _n, _w, start, end, swatch in ORGANICS_REGIONS:
        double = dict(one, **{key: 200})
        sc = organics_region_scale(h, double)
        assert sc[int(swatch * 2)] == pytest.approx(2.0)        # inside: the region's own
        assert sc.min() >= 1.0 - 1e-12 and sc.max() <= 2.0 + 1e-12
        far = (swatch + 180.0) % 360.0
        assert sc[int(far * 2)] == pytest.approx(1.0)           # opposite: untouched


def test_a_region_at_zero_leaves_its_hues_alone():
    h = np.float32([15.0, 40.0])                       # reds and oranges
    assert np.allclose(organics_hue_map(h, 100, {"warm": 0}), h, atol=1e-4)
    moved = organics_hue_map(np.float32([205.0]), 100, {"warm": 0})
    assert moved == pytest.approx(organics_hue_map(np.float32([205.0]), 100), abs=1e-4)


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
    tip = lbl.toolTip()
    assert "Right-click" in tip and "lab scans" in tip and "region" in tip


def test_popup_edits_write_the_shape_and_survive_a_slider_move(panel, monkeypatch):
    img = _Img()
    _wire(panel, monkeypatch, img)
    panel._open_organics_popup(panel.mapToGlobal(panel.rect().center()))
    pop = panel._organics_popup
    assert set(pop.sliders) == {k for k, *_r in ORGANICS_REGIONS}
    pop.sliders["warm"].setValue(150 // 5)
    pop.sliders["blue"].setValue(0)
    assert pop.values_shown["warm"].text() == "150 %"
    assert normalize_organics_shape(img.adjustment_settings["organics_shape"]) == \
        normalize_organics_shape(SHAPE)
    assert panel._organics_label.font().underline()
    assert "Reds and oranges 150 %" in panel._organics_label.toolTip()
    pop.close()
    assert img.undo == 1                          # the whole popup visit is one undo
    panel.sliders[panel.adjustment_keys.index("contrast")].setValue(12)
    assert img.adjustment_settings["organics_shape"]["blue"] == 0
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
    assert row and "regions" in row[0][2] and "Cyans and blues 0 %" in row[0][2]
    img = _Img({"contrast": 5})
    _wire(panel, monkeypatch, img)
    panel.current_idx = 1
    panel.clipboard = dict(clip, curves=None, cineon_log=False, look_lut=None,
                           tone_shape=None, flags={}, profile="color", crop=(None, 0.0),
                           rotation=0, flip_h=False, flip_v=False, fine_rotation=0)
    panel._apply_paste(0, {"organics_shape"})
    assert img.adjustment_settings["organics_shape"]["warm"] == 150
    assert img.adjustment_settings["contrast"] == 5
    src = _Img({"colour_organics": 10, "organics_shape": dict(SHAPE)})
    _wire(panel, monkeypatch, src)
    panel.current_idx = 5
    panel.copy_settings_from_index(0)
    assert panel.clipboard["organics_shape"]["blue"] == 0


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
    assert dst.adjustment_settings["organics_shape"]["warm"] == 150


def test_sync_without_it_keeps_the_target_shape(panel, monkeypatch):
    src = _Img({"contrast": 20})
    dst = _Img({"contrast": 0, "organics_shape": dict(SHAPE)})
    _sync(panel, monkeypatch, src, dst, sat=False)
    assert dst.adjustment_settings["contrast"] == 20
    assert dst.adjustment_settings["organics_shape"]["blue"] == 0


def test_sync_removes_the_shape_when_the_source_has_none(panel, monkeypatch):
    src = _Img({"colour_organics": 50})
    dst = _Img({"colour_organics": 50, "organics_shape": dict(SHAPE)})
    _sync(panel, monkeypatch, src, dst, sat=True)
    assert "organics_shape" not in dst.adjustment_settings


# --- the slider: shows 0..100, stores 0..200 (revision 2026-10-23) ---------------

def test_slider_stores_to_200(panel):
    from core.ccr_processor import ORGANICS_AMOUNT_MAX
    assert ORGANICS_AMOUNT_MAX == 200            # shown 100 (revision 2026-10-23)
    i = panel.adjustment_keys.index("colour_organics")
    assert panel.sliders[i].value_range() == (0, 200)



def test_slider_shows_half_and_keeps_odd_old_values(panel, monkeypatch):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtCore import Qt
    i = panel.adjustment_keys.index("colour_organics")
    sl = panel.sliders[i]
    sl.setValue(200)
    assert sl.value_text() == "100" and sl.value() == 200
    sl.setValue(75)                                  # an edit from the old 0..150 slider
    assert sl.value() == 75 and sl.value_text() == "37.5"
    j = panel.adjustment_keys.index("saturation")
    panel.sliders[j].setValue(5)                     # another slider moving
    assert sl.value() == 75                          # ... doesn't nudge it
    ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120),
                     Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    sl.wheelEvent(ev)
    assert sl.value_text() == "39" and sl.value() == 78  # one shown step


def test_paste_row_shows_the_shown_number():
    from widgets.sliders_panel import paste_options
    rows = paste_options({"adjustments": {"colour_organics": 120}}, lambda k: 0)
    assert any("Colour Organics" in r[2] and "(+60)" in r[2] for r in rows)
