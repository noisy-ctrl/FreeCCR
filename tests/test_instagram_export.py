#!/usr/bin/env python3
"""Export → Preset → Instagram (spec/instagram-export.md)."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core import export_canvas as ec  # noqa: E402
from test_export_canvas import _dialog, settings_guard  # noqa: E402,F401


# --------------------------------------------------------------------------- #
# the canvas
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("src", [(6000, 4000), (4000, 6000), (4000, 4000)])
def test_a_border_width_keeps_every_side_clear_and_fills_the_box(src):
    c = ec.Canvas(1080, 1350, 1350, margin=32)
    w, h = ec.placed_size(*src, c)
    assert w <= 1080 - 64 and h <= 1350 - 64
    assert w == 1080 - 64 or h == 1350 - 64              # as large as fits


def test_no_margin_and_no_sharpen_are_the_old_canvas():
    old, new = ec.Canvas(1080, 1350, 1300), ec.Canvas(1080, 1350, 1300, margin=0, sharpen=(0, 0))
    assert old == new
    assert ec.placed_size(6000, 4000, new) == (1080, 720)
    rng = np.random.default_rng(0)
    src = (rng.random((400, 600, 3)) * 65535).astype(np.uint16)
    a, _ = ec.compose(src, old)
    b, _ = ec.compose(src, new)
    np.testing.assert_array_equal(a, b)


def test_screen_sharpening_touches_the_frame_but_not_the_border():
    rng = np.random.default_rng(1)
    base = (rng.random((60, 90)) * 40000 + 10000).astype(np.float32)
    import cv2
    src = np.repeat(cv2.resize(base, (900, 600), interpolation=cv2.INTER_NEAREST)[..., None], 3, 2)
    src = src.astype(np.uint16)
    plain, _ = ec.compose(src, ec.Canvas(400, 500, 500, (255, 255, 255), margin=20))
    sharp, _ = ec.compose(src, ec.Canvas(400, 500, 500, (255, 255, 255), margin=20,
                                         sharpen=(25, 35)))
    w, h = ec.placed_size(900, 600, ec.Canvas(400, 500, 500, margin=20))
    y0, x0 = (500 - h) // 2, (400 - w) // 2
    border = np.ones(sharp.shape[:2], bool)
    border[y0:y0 + h, x0:x0 + w] = False
    assert (sharp[border] == 65535).all()                # no halo on the border
    f = lambda im: im[y0:y0 + h, x0:x0 + w, 1].astype(float)
    hp = lambda im: (im - cv2.GaussianBlur(im, (0, 0), 1)).std()
    assert hp(f(sharp)) > hp(f(plain)) * 1.05


# --------------------------------------------------------------------------- #
# the Export window
# --------------------------------------------------------------------------- #
def test_a_preset_sets_and_locks_and_builds_the_plan(tmp_path, monkeypatch, settings_guard):
    from widgets import export_dialog as ed
    dlg = _dialog(tmp_path, monkeypatch)
    assert dlg.preset_combo.currentData() == "" and not dlg.preset_hint.isVisible()
    dlg.preset_combo.setCurrentIndex(dlg.preset_combo.findData("instagram_45"))
    assert dlg.format_combo.currentData() == "jpeg" and not dlg.format_combo.isEnabled()
    assert dlg.colorspace_combo.currentData() == "srgb" and not dlg.colorspace_combo.isEnabled()
    assert dlg.quality_slider.value() == 95 and not dlg.quality_slider.isEnabled()
    assert dlg.canvas_check.isChecked() and not dlg.canvas_w_spin.isEnabled()
    # the long edge is yours, starting at INSTAGRAM_EDGE_PCT % of the side
    # it runs along; there is no separate border width
    assert dlg.canvas_colour_btn.isEnabled() and dlg.canvas_edge_unit.isEnabled()
    assert dlg.canvas_edge_unit.currentData() == "%"
    assert dlg.canvas_edge_pct_spin.value() == pytest.approx(ed.INSTAGRAM_EDGE_PCT)
    assert not hasattr(dlg, "canvas_margin_spin")
    dlg.dest_edit.setText(str(tmp_path / "out"))
    dlg._on_export_clicked()
    p = dlg.plan
    assert p.jpg_output and p.jpg_quality == 95 and p.output_colorspace == "srgb"
    assert p.hdr_headroom is None and p.max_long_side is None
    assert p.canvas.long_edge_pct == pytest.approx(ed.INSTAGRAM_EDGE_PCT)
    assert p.canvas.margin == 0 and p.canvas.sharpen == ed.INSTAGRAM_SHARPEN
    assert (p.canvas.width, p.canvas.height) == (1080, 1350)
    assert ec.placed_size(6000, 4000, p.canvas) == (1015, 677)   # 94% of 1080
    # remembered, and your own settings were not overwritten
    s = settings_guard
    assert s.value("export/preset") == "instagram_45"
    assert s.value("export/format") in (None, "tiff")
    assert not s.value("export/canvas_on", False, type=bool)
    again = ed.ExportSettingsDialog(current_idx=0)
    assert again.preset_combo.currentData() == "instagram_45"
    assert again.canvas_h_spin.value() == 1350


def test_none_puts_your_own_settings_back(tmp_path, monkeypatch, settings_guard):
    dlg = _dialog(tmp_path, monkeypatch)
    dlg.quality_slider.setValue(80)
    dlg.format_combo.setCurrentIndex(dlg.format_combo.findData("jpeg"))
    dlg.dest_edit.setText(str(tmp_path / "out"))
    dlg._on_export_clicked()                              # saves your own: JPEG 80
    dlg.preset_combo.setCurrentIndex(dlg.preset_combo.findData("instagram_34"))
    assert dlg.canvas_h_spin.value() == 1440 and dlg.quality_slider.value() == 95
    dlg.preset_combo.setCurrentIndex(0)
    assert dlg.quality_slider.value() == 80 and dlg.quality_slider.isEnabled()
    assert dlg.format_combo.currentData() == "jpeg" and dlg.format_combo.isEnabled()
    assert not dlg.canvas_check.isChecked() and dlg.colorspace_combo.isEnabled()
    dlg._on_export_clicked()
    assert dlg.plan.canvas is None and dlg.plan.jpg_quality == 80
