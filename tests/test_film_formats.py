#!/usr/bin/env python3
"""Film formats (spec/film-formats.md): the table, the Crop panel's Format
list (film formats first, headings not selectable), the format recorded on
a frame at Done, and the contact sheet's labels, footer and strip widths."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
sys.path.insert(0, HERE)

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtCore import QRectF  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core import crop_aspect, film_formats as ff  # noqa: E402
from core.auto_crop import FrameCrop  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402

_KEEP = []


# --- the table -------------------------------------------------------------------

def test_labels_and_names():
    assert ff.label("645") == "120: 6x4.5"
    assert ff.label("35mm 3:2") == "35mm: 36x24"          # auto crop's name
    assert ff.label("half-frame") == "35mm: half frame"
    assert ff.label("xpan") == "35mm: XPan"
    assert ff.label("nonsense") == "" and ff.label(None) == ""
    assert ff.ratio("645") == pytest.approx(56 / 41.5)
    assert [ff.strip_length(k) for k in ("35mm", "645", "6x6", "6x7", "6x9")] == [6, 4, 3, 3, 2]


def test_format_from_a_crops_proportions():
    assert ff.from_ratio(1500, 1000) == "35mm"             # 3:2 reads as 35mm
    assert ff.from_ratio(1000, 1500) == "35mm"             # either way round
    assert ff.from_ratio(1349, 1000) == "645"
    assert ff.from_ratio(1000, 1000) == "6x6"
    assert ff.from_ratio(1232, 1000) == "6x7"
    assert ff.from_ratio(1100, 1000) is None               # no film format: a print crop
    assert ff.from_ratio(0, 10) is None


# --- the Crop panel's Format list ---------------------------------------------------

def test_presets_put_film_formats_first_under_a_heading():
    keys = [k for _l, k, _r in crop_aspect.ASPECT_PRESETS]
    assert keys[:3] == ["free", "original", "#film"]
    film = [k for k, *_ in ff.FILM_FORMATS]
    assert keys[3:3 + len(film)] == film
    assert keys[3 + len(film)] == "#print" and "3:2" in keys and keys[-1] == "custom"
    assert crop_aspect.preset_for_key("#film") is None      # a heading is not a preset
    assert crop_aspect.preset_for_key("645")[2] == pytest.approx(56 / 41.5)
    assert "6x6" in crop_aspect.ORIENTATION_FIXED_KEYS


def _panel():
    import test_crop_panel as tcp
    from widgets.crop_panel import CropPanel
    ip, host = tcp._make_preview()
    panel = CropPanel(host, ip)
    ip.set_crop_panel(panel)
    _KEEP.append((ip, host, panel))
    return ip, panel


def test_panel_headings_are_not_selectable_and_film_formats_are():
    _ip, panel = _panel()
    combo = panel.aspect_combo
    i = combo.findData("#film")
    assert i >= 0 and not combo.model().item(i).isEnabled()
    assert combo.itemText(i) == "FILM FORMATS"
    combo.setCurrentIndex(combo.findData("645"))
    assert panel.active_film_format() == "645"
    assert combo.currentText() == "120: 6x4.5"
    combo.setCurrentIndex(combo.findData("3:2"))
    assert panel.active_film_format() is None


def test_done_records_the_film_format_on_the_frame():
    import test_crop_panel as tcp
    ip, panel = _panel()
    img = tcp._Img(crop_rect=(0.1, 0.1, 0.9, 0.9))
    saved = ccr_backend.images
    ccr_backend.images = [img]
    try:
        ip.update_preview = lambda *a, **k: None
        panel.aspect_combo.setCurrentIndex(panel.aspect_combo.findData("6x7"))
        ip.crop_mode = False
        assert ip.enter_crop_mode()
        ip.confirm_crop()
        assert img.film_format == "6x7"
        panel.aspect_combo.setCurrentIndex(panel.aspect_combo.findData("free"))
        assert ip.enter_crop_mode()
        ip.confirm_crop()
        assert img.film_format == "6x7"                  # Free doesn't unset it
    finally:
        ccr_backend.images = saved


# --- the contact sheet ------------------------------------------------------------

class Img:
    def __init__(self, name, w=400, h=300):
        self.file_path = f"/scans/{name}"
        self.crop_rect, self.crop_angle = None, 0.0
        self.converted = False
        self._render_epoch = 1
        pm = QPixmap(w, h)
        pm.fill(QColor(128, 128, 128))
        self.resized_preview = pm


def test_frame_format_prefers_what_you_picked(monkeypatch):
    from widgets import contact_sheet as cs
    img = Img("a.raf")
    assert cs.frame_format(img) is None
    img.crop_rect = (0.0, 0.0, 0.75, 1.0)                  # 300 x 300: square
    assert cs.frame_format(img) == "6x6"
    img.auto_crop_last = FrameCrop((0.1, 0.1, 0.9, 0.9), confidence="high", fmt="645")
    assert cs.frame_format(img) == "645"
    img.film_format = "6x8"
    assert cs.frame_format(img) == "6x8"


def test_sheet_labels_formats_and_cuts_strips_to_the_format(monkeypatch):
    from widgets import contact_sheet as cs
    imgs = [Img(f"f{i}.raf") for i in range(7)]
    for img in imgs[:5]:
        img.film_format = "6x6"
    imgs[5].auto_crop_last = FrameCrop((0.1, 0.1, 0.9, 0.9), confidence="high", fmt="645")
    monkeypatch.setattr(ccr_backend, "images", imgs)
    sheet = cs.ContactSheet()
    sheet.update()
    assert sheet.layout().cols == 3                         # 6x6 is cut in strips of 3
    assert cs.format_summary(imgs) == "120: 6x6 ×5 · 120: 6x4.5 ×1"
    # a format change redraws that frame (its label) and the footer
    imgs[6].film_format = "6x6"
    sheet.update()
    assert sheet.redrawn == [6] and sheet._footer == "120: 6x6 ×6 · 120: 6x4.5 ×1"
    imgs[5].film_format = "6x6"
    sheet.update()
    assert cs.format_summary(imgs) == "120: 6x6" and sheet._footer == "120: 6x6"


# --- saved with the frame, and what auto crop learns -------------------------------------

def test_catalog_keeps_the_film_format(tmp_path):
    import cv2
    from core import catalog
    from core.ccr_image import CCRImage
    path = str(tmp_path / "scan.png")
    cv2.imwrite(path, np.full((300, 400, 3), 30000, np.uint16))
    cat = str(tmp_path / "catalog.json")
    img = CCRImage(path)
    img.crop_rect = (0.1, 0.2, 0.9, 0.8)
    img.film_format = "6x9"
    catalog.update_for_images([img], path=cat)
    assert catalog.create_images_for_path(path, path=cat)[0].film_format == "6x9"


def test_learning_records_the_picked_format(tmp_path, monkeypatch):
    import test_auto_crop as tac
    from core import crop_memory as cm
    monkeypatch.setattr(cm, "_memory", cm.CropMemory(str(tmp_path / "m.json")))

    class I:
        info = {"camera_make": "T", "camera_model": "C"}
        is_merged = False
        file_path = "x.raf"
        crop_rect, crop_angle = None, 0.0
    img = I()
    fc = ccr_backend.detect_frame_crop(img, raw=tac.scan_645(0.0)[0])
    img.crop_rect, img.crop_angle = fc.rect, fc.angle
    img.film_format = "6x8"
    assert ccr_backend.learn_confirmed_crop(img)
    assert cm.memory().examples(img.auto_crop_key)[-1]["fmt"] == "6x8"
