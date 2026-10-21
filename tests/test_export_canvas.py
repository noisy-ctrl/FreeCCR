#!/usr/bin/env python3
"""Export on a coloured canvas (border). See spec/export-canvas-border.md."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402
import tifffile  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core import ccr_processor as cp  # noqa: E402
from core import export_canvas as ec  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402
from core.color_management import apply_export_colorspace  # noqa: E402

try:
    import imagecodecs
    _UHDR = bool(getattr(imagecodecs, "ULTRAHDR", None)
                 and imagecodecs.ULTRAHDR.available)
except Exception:          # pragma: no cover
    imagecodecs = None
    _UHDR = False


# --- 1. placed_size -------------------------------------------------------------

def test_long_edge_sets_the_size_for_either_orientation():
    c = ec.Canvas(2000, 2000, 1946)
    assert ec.placed_size(6000, 4000, c) == (1946, 1297)
    assert ec.placed_size(4000, 6000, c) == (1297, 1946)


def test_too_big_for_the_canvas_shrinks_to_fit():
    c = ec.Canvas(1080, 1350, 1300)
    w, h = ec.placed_size(6000, 4000, c)              # landscape 1300 wide > 1080
    assert w == 1080 and h == 720
    w, h = ec.placed_size(4000, 6000, c)              # portrait 1300 tall fits
    assert (w, h) == (867, 1300)
    assert ec.placed_size(1, 5000, ec.Canvas(100, 100, 100)) == (1, 100)


def test_colour_hex_round_trip():
    assert ec.colour_to_hex((255, 128, 0)) == "#ff8000"
    assert ec.colour_from_hex("#ff8000") == (255, 128, 0)
    assert ec.colour_from_hex("bogus") == ec.DEFAULT_COLOUR
    assert ec.Canvas(10, 10, 5, (9, 9, 9)).neutral
    assert not ec.Canvas(10, 10, 5, (9, 9, 10)).neutral


# --- 2. compose -------------------------------------------------------------------

def test_compose_centres_on_the_colour_and_pads_the_map():
    src = np.full((40, 60, 3), 30000, np.uint16)
    over = np.full((40, 60), 3.0, np.float32)
    c = ec.Canvas(101, 81, 50, (10, 20, 30))
    out, omap = ec.compose(src, c, over)
    assert out.shape == (81, 101, 3) and out.dtype == np.uint16
    w, h = ec.placed_size(60, 40, c)                   # 50 x 33
    x0, y0 = (101 - w) // 2, (81 - h) // 2
    assert np.all(out[y0:y0 + h, x0:x0 + w] == 30000)
    bg = np.array([10 * 257, 20 * 257, 30 * 257], np.uint16)
    assert np.all(out[0, 0] == bg) and np.all(out[-1, -1] == bg)
    assert np.all(out[y0 - 1, x0] == bg) and np.all(out[y0, x0 - 1] == bg)
    assert np.all(out[y0 + h, x0] == bg) and np.all(out[y0, x0 + w] == bg)
    # odd pixel of border on the right/bottom
    assert (101 - w - x0) >= x0 and (81 - h - y0) >= y0
    assert omap.shape == (81, 101)
    assert np.allclose(omap[y0:y0 + h, x0:x0 + w], 3.0, atol=1e-5)
    assert omap[0, 0] == 0 and omap[-1, -1] == 0
    assert np.all(src == 30000) and np.all(over == 3.0)   # inputs untouched
    assert ec.compose(src, c)[1] is None


def test_compose_single_channel():
    src = np.full((20, 30), 1000, np.uint16)
    out, _ = ec.compose(src, ec.Canvas(50, 40, 25, (200, 200, 200)))
    assert out.shape == (40, 50) and out[0, 0] == 200 * 257


# --- 3-6. through the export pipeline ------------------------------------------

def _converted_frame(tmp_path, monkeypatch, w=180, h=120):
    neg = np.zeros((h, w, 3), np.float32)
    x = np.linspace(0, 1, w)[None, :]
    neg[..., 2] = 0.80 - 0.79 * x
    neg[..., 1] = 0.55 - 0.545 * x
    neg[..., 0] = 0.35 - 0.347 * x
    path = str(tmp_path / "neg.tif")
    cv2.imwrite(path, (neg * 65535).astype(np.uint16))
    img = CCRImage(path)
    monkeypatch.setattr(ccr_backend, "images", [img])
    monkeypatch.setattr(ccr_backend, "black_point_bgr",
                        (0.35 * 65535, 0.55 * 65535, 0.80 * 65535))
    monkeypatch.setattr(ccr_backend, "white_point_bgr", None)
    if hasattr(ccr_backend, "auto_black_point"):
        monkeypatch.setattr(ccr_backend, "auto_black_point", False)
    ccr_backend.apply_bwpoint_to_all_images()
    assert img.converted
    return img


def test_tiff_on_a_canvas(tmp_path, monkeypatch):
    _converted_frame(tmp_path, monkeypatch)
    c = ec.Canvas(300, 260, 200, (255, 255, 255))
    path = str(tmp_path / "c.tiff")
    assert ccr_backend.export_image_by_index(0, path, canvas=c)
    out = tifffile.imread(path)
    assert out.shape == (260, 300, 3)
    assert np.all(out[0, 0] == 65535) and np.all(out[-1, -1] == 65535)
    w, h = ec.placed_size(180, 120, c)               # 200 x 133
    x0, y0 = (300 - w) // 2, (260 - h) // 2
    ref_path = str(tmp_path / "ref.tiff")
    assert ccr_backend.export_image_by_index(0, ref_path)
    ref = tifffile.imread(ref_path)
    expect = cv2.resize(ref, (w, h), interpolation=cv2.INTER_LANCZOS4)
    assert np.array_equal(out[y0:y0 + h, x0:x0 + w], expect)


def test_jpeg_on_a_coloured_canvas_ignores_resize(tmp_path, monkeypatch):
    _converted_frame(tmp_path, monkeypatch)
    c = ec.Canvas(320, 320, 300, (20, 120, 220))
    path = str(tmp_path / "c.jpg")
    assert ccr_backend.export_image_by_index(0, path, jpg_output=True,
                                             jpg_quality=95, max_long_side=100,
                                             canvas=c)
    out = cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2RGB).astype(int)
    assert out.shape == (320, 320, 3)
    assert np.all(np.abs(out[3, 3] - np.array([20, 120, 220])) <= 3)


def test_prophoto_border_is_converted_with_the_picture(tmp_path, monkeypatch):
    _converted_frame(tmp_path, monkeypatch)
    colour = (200, 60, 40)
    c = ec.Canvas(260, 200, 150, colour)
    path = str(tmp_path / "pp.tiff")
    assert ccr_backend.export_image_by_index(0, path, output_colorspace="prophoto",
                                             canvas=c)
    out = tifffile.imread(path)
    patch = np.empty((2, 2, 3), np.uint16)
    patch[...] = [v * 257 for v in colour]
    want, _ = apply_export_colorspace(patch, "prophoto")
    assert np.array_equal(out[0, 0], want[0, 0])
    assert not np.array_equal(out[0, 0], patch[0, 0])


def test_monochrome_keeps_one_channel_only_on_a_neutral_border(tmp_path, monkeypatch):
    img = _converted_frame(tmp_path, monkeypatch)
    img.color_profile = "bw"
    grey = str(tmp_path / "g.tiff")
    assert ccr_backend.export_image_by_index(0, grey,
                                             canvas=ec.Canvas(240, 200, 150, (255, 255, 255)))
    assert tifffile.imread(grey).ndim == 2
    tinted = str(tmp_path / "t.tiff")
    assert ccr_backend.export_image_by_index(0, tinted,
                                             canvas=ec.Canvas(240, 200, 150, (240, 230, 210)))
    out = tifffile.imread(tinted)
    assert out.ndim == 3 and np.array_equal(out[0, 0], [240 * 257, 230 * 257, 210 * 257])


@pytest.mark.skipif(not _UHDR, reason="imagecodecs without libultrahdr")
def test_hdr_on_a_canvas_glows_only_inside_the_frame(tmp_path, monkeypatch):
    _converted_frame(tmp_path, monkeypatch)
    c = ec.Canvas(400, 400, 360, (255, 255, 255))
    path = str(tmp_path / "h.jpg")
    seen = {}
    real = cp._write_hdr_jpeg

    def spy(rgb_u16, output_path, jpg_quality, output_colorspace, headroom, over):
        seen["over"] = over
        return real(rgb_u16, output_path, jpg_quality, output_colorspace, headroom, over)
    monkeypatch.setattr(cp, "_write_hdr_jpeg", spy)
    assert ccr_backend.export_image_by_index(0, path, jpg_output=True,
                                             hdr_headroom=2.0, canvas=c)
    over = seen["over"]
    assert over.shape == (400, 400)
    w, h = ec.placed_size(180, 120, c)
    y0 = (400 - h) // 2
    assert over[:y0].max() == 0 and over[y0 + h:].max() == 0
    assert over[y0 + h // 2, (400 + w) // 2 - 3] > 1.0          # the bright edge
    data = open(path, "rb").read()
    base = imagecodecs.ultrahdr_decode(data, dtype=np.uint8)[..., :3]
    full = imagecodecs.ultrahdr_decode(data).astype(np.float64)[..., :3]
    assert base.shape[:2] == (400, 400)
    assert np.all(base[5, 5] >= 250)                             # white border
    assert abs(full[5, 5, 1] - 1.0) < 0.02                       # ...at SDR white
    inside = full[y0 + h // 2, (400 + w) // 2 - 3, 1]
    assert inside > 1.05                                         # the frame glows


def test_without_a_canvas_nothing_changes(tmp_path, monkeypatch):
    _converted_frame(tmp_path, monkeypatch)
    a, b = str(tmp_path / "a.tiff"), str(tmp_path / "b.tiff")
    assert ccr_backend.export_image_by_index(0, a)
    assert ccr_backend.export_image_by_index(0, b, canvas=None)
    assert np.array_equal(tifffile.imread(a), tifffile.imread(b))


# --- 7. export window ------------------------------------------------------------

_KEYS = ["export/destination", "export/scope", "export/filename_template",
         "export/format", "export/colorspace", "export/jpeg_quality",
         "export/resize", "export/conflict", "export/open_folder",
         "export/hdr_headroom", "export/canvas_on", "export/canvas_w",
         "export/canvas_h", "export/canvas_long_edge", "export/canvas_colour"]


@pytest.fixture
def settings_guard():
    s = QSettings("FreeCCR", "FreeCCR")
    saved = {k: s.value(k) for k in _KEYS}
    for k in _KEYS:
        s.remove(k)
    yield s
    for k, v in saved.items():
        if v is None:
            s.remove(k)
        else:
            s.setValue(k, v)


def _dialog(tmp_path, monkeypatch):
    from widgets.export_dialog import ExportSettingsDialog
    _converted_frame(tmp_path, monkeypatch)
    return ExportSettingsDialog(current_idx=0)


def test_dialog_border_controls(tmp_path, monkeypatch, settings_guard):
    dlg = _dialog(tmp_path, monkeypatch)
    assert not dlg.canvas_check.isChecked()
    assert not dlg.canvas_w_spin.isEnabled() and dlg.resize_combo.isEnabled()
    assert dlg.canvas_w_spin.value() == 2000 and dlg.canvas_h_spin.value() == 2000
    assert dlg.canvas_edge_spin.value() == 1946
    assert dlg.canvas_colour_btn.text() == "#ffffff"
    dlg.canvas_check.setChecked(True)
    assert dlg.canvas_w_spin.isEnabled() and dlg.canvas_colour_btn.isEnabled()
    assert not dlg.resize_combo.isEnabled()
    dlg.canvas_check.setChecked(False)
    assert dlg.resize_combo.isEnabled()


def test_dialog_plan_and_settings(tmp_path, monkeypatch, settings_guard):
    from PySide6.QtGui import QColor
    from widgets import export_dialog
    dlg = _dialog(tmp_path, monkeypatch)
    dlg.canvas_check.setChecked(True)
    dlg.canvas_w_spin.setValue(1080)
    dlg.canvas_h_spin.setValue(1350)
    dlg.canvas_edge_spin.setValue(1000)
    monkeypatch.setattr(export_dialog.QColorDialog, "getColor",
                        staticmethod(lambda *a, **k: QColor(10, 20, 30)))
    dlg._on_pick_canvas_colour()
    assert dlg.canvas_colour_btn.text() == "#0a141e"
    dlg.resize_combo.setCurrentIndex(dlg.resize_combo.findData(1080))
    dlg.dest_edit.setText(str(tmp_path / "out"))
    dlg._on_export_clicked()
    plan = dlg.plan
    assert plan.canvas == ec.Canvas(1080, 1350, 1000, (10, 20, 30))
    assert plan.max_long_side is None                    # the canvas sets the size
    again = export_dialog.ExportSettingsDialog(current_idx=0)
    assert again.canvas_check.isChecked()
    assert (again.canvas_w_spin.value(), again.canvas_h_spin.value(),
            again.canvas_edge_spin.value()) == (1080, 1350, 1000)
    assert again._canvas_colour == (10, 20, 30)
    again.canvas_check.setChecked(False)
    again._on_export_clicked()
    assert again.plan.canvas is None


def test_cancelled_colour_picker_keeps_the_colour(tmp_path, monkeypatch, settings_guard):
    from PySide6.QtGui import QColor
    from widgets import export_dialog
    dlg = _dialog(tmp_path, monkeypatch)
    monkeypatch.setattr(export_dialog.QColorDialog, "getColor",
                        staticmethod(lambda *a, **k: QColor()))      # invalid = cancelled
    dlg._on_pick_canvas_colour()
    assert dlg._canvas_colour == (255, 255, 255)


def test_worker_passes_the_canvas(monkeypatch):
    from widgets.export_dialog import ExportPlan
    from widgets.image_preview import ExportItemsWorker
    got = {}
    monkeypatch.setattr(ccr_backend, "export_items",
                        lambda items, **kw: got.update(kw) or {"exported": 0, "failed": 0,
                                                               "cancelled": False,
                                                               "failures": []})
    c = ec.Canvas(100, 100, 90)
    ExportItemsWorker(ExportPlan(items=[], canvas=c)).run()
    assert got["canvas"] is c


def test_estimate_uses_the_canvas_size(tmp_path, monkeypatch, settings_guard):
    from core import export_estimator
    dlg = _dialog(tmp_path, monkeypatch)
    dlg.format_combo.setCurrentIndex(dlg.format_combo.findData("tiff"))
    seen = {}
    real = export_estimator.estimate_total_bytes

    def spy(dims_list, fmt, **kw):
        seen["dims"], seen["kw"] = list(dims_list), kw
        return real(dims_list, fmt, **kw)
    monkeypatch.setattr(export_estimator, "estimate_total_bytes", spy)
    dlg.canvas_check.setChecked(True)
    dlg.canvas_w_spin.setValue(500)
    dlg.canvas_h_spin.setValue(400)
    dlg._update_estimate()
    assert seen["dims"] == [(400, 500)] and seen["kw"]["max_long_side"] is None
