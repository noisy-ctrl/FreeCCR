#!/usr/bin/env python3
"""Contact sheet (spec/contact-sheet.md): the whole roll as one image. Crop
sources, the crop overlay, the layout (6 or 4 across) and frame lookup, live
redraw of only the changed frames, Negatives, Export, the main-window wiring
and the catalog keys."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPixmap  # noqa: E402
from PySide6.QtCore import QPoint  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core.auto_crop import FrameCrop  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402
from widgets import contact_sheet as cs  # noqa: E402

_KEEP = []


class Img:
    """Just what the sheet reads from a CCRImage."""

    def __init__(self, name="a.raf", w=400, h=300, colour=(128, 128, 128)):
        self.file_path = f"/scans/{name}"
        self.crop_rect, self.crop_angle = None, 0.0
        self.converted = False
        self._render_epoch = 1
        pm = QPixmap(w, h)
        pm.fill(QColor(*colour))
        self.resized_preview = pm
        self.rotation_angle = 0
        self.horizontal_mirrored = self.vertical_mirrored = False


@pytest.fixture
def images(monkeypatch):
    imgs = [Img(f"f{i}.raf") for i in range(5)]
    monkeypatch.setattr(ccr_backend, "images", imgs)
    return imgs


def _rgb(im, x, y):
    if isinstance(im, QPixmap):
        im = im.toImage()
    c = im.pixelColor(int(x), int(y))
    return c.red(), c.green(), c.blue()


def _near(c, want, tol=40):
    return all(abs(a - b) <= tol for a, b in zip(c, want))


# --- where a crop came from -----------------------------------------------------

def test_crop_source():
    img = Img()
    assert cs.crop_source(img) == ("none", "")
    img.auto_crop_last = FrameCrop(None, reason="no border found")
    assert cs.crop_source(img) == ("unsure", "no border found")
    img.crop_rect = (0.1, 0.1, 0.9, 0.9)
    img.auto_crop_rect = (0.1, 0.1, 0.9, 0.9)
    img.auto_crop_last = FrameCrop((0.1, 0.1, 0.9, 0.9), confidence="medium")
    assert cs.crop_source(img) == ("auto", "medium")
    img.crop_rect = (0.12, 0.1, 0.9, 0.9)
    assert cs.crop_source(img)[0] == "hand"
    img.auto_crop_example = "abc"
    assert cs.crop_source(img)[0] == "confirmed"


# --- the overlay ---------------------------------------------------------------------

def _draw(rect, angle=0.0, source="auto", w=400, h=300):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainter
    src = QPixmap(w, h)
    src.fill(QColor(128, 128, 128))
    out = QImage(200, 150, QImage.Format_RGB888)
    out.fill(QColor(0, 0, 0))
    p = QPainter(out)
    cs.draw_frame(p, QRectF(0, 0, 200, 150), src, rect, angle, source)
    p.end()
    return out


def test_overlay_line_on_the_crop_in_the_source_colour_outside_dimmed():
    out = _draw((0.25, 0.2, 0.75, 0.8))
    assert _near(_rgb(out, 50, 75), cs.SOURCES["auto"][0])      # the crop's left edge
    assert _near(_rgb(out, 100, 75), (128, 128, 128), 3)        # inside: the frame as is
    assert sum(_rgb(out, 20, 75)) < 3 * 128 - 60                # outside: dimmed


def test_overlay_follows_the_straighten():
    assert _draw((0.25, 0.2, 0.75, 0.8), 0.0) != _draw((0.25, 0.2, 0.75, 0.8), 8.0)
    pts = cs.crop_polygon((0.25, 0.2, 0.75, 0.8), 90.0, 400, 300)
    xs = sorted(round(p.x()) for p in pts)
    assert xs[0] == 110 and xs[-1] == 290       # a quarter turn: x spans the box's height (180)


def test_unsure_frame_gets_a_dashed_outline():
    out = _draw(None, source="unsure")
    red = cs.SOURCES["unsure"][0]
    edge = [_near(_rgb(out, x, 1), red, 70) for x in range(10, 190)]
    assert any(edge) and not all(edge)              # dashed


# --- the sheet ------------------------------------------------------------------------

def test_one_image_six_across_for_35mm_four_for_medium_format(images):
    sheet = cs.ContactSheet()
    sheet.update()
    lay = sheet.layout()
    assert lay.cols == 4                                       # 5 frames, nothing known
    assert sheet.image().width() == lay.width and sheet.image().height() == lay.height
    for img in images:
        img.auto_crop_last = FrameCrop((0.1, 0.1, 0.9, 0.9), confidence="high", fmt="35mm 3:2")
    sheet.update()
    assert sheet.layout().cols == 6
    for img in images:
        img.auto_crop_last = FrameCrop((0.1, 0.1, 0.9, 0.9), confidence="high", fmt="645")
    sheet.update()
    assert sheet.layout().cols == 4


def test_frames_sit_in_order_and_double_click_finds_them(images):
    sheet = cs.ContactSheet()
    sheet.update()
    lay = sheet.layout()
    c3 = lay.image_rect(3).center()
    assert sheet.frame_at(c3.x(), c3.y()) == 3
    assert sheet.frame_at(1, 1) is None                         # the margin
    r0, r1 = lay.image_rect(0), lay.image_rect(1)
    assert r1.left() > r0.right() and abs(r1.top() - r0.top()) < 1e-6
    # the crop overlay is drawn into the one image
    images[2].crop_rect = (0.25, 0.2, 0.75, 0.8)
    images[2].auto_crop_rect = images[2].crop_rect
    sheet.update()
    r2 = lay.image_rect(2)
    fw = r2.width()
    fh = fw * 300 / 400
    top = r2.y() + (r2.height() - fh) / 2
    assert _near(_rgb(sheet.image(), r2.x() + 0.25 * fw, top + 0.5 * fh), cs.SOURCES["auto"][0])


def test_live_only_the_changed_frame_is_redrawn(images):
    sheet = cs.ContactSheet()
    got = []
    sheet.changed.connect(lambda: got.append(1))
    sheet.update()
    assert sheet.redrawn == [0, 1, 2, 3, 4] and got
    assert not sheet.update() and sheet.redrawn == []           # nothing changed
    images[2].crop_rect = (0.1, 0.1, 0.9, 0.9)
    assert sheet.update() and sheet.redrawn == [2]
    images[4]._render_epoch += 1                                 # a re-render (slider, convert)
    sheet.update()
    assert sheet.redrawn == [4]


def test_thumbnail_and_empty_roll(images, monkeypatch):
    sheet = cs.ContactSheet()
    sheet.update()
    th = sheet.thumbnail()
    assert th.width() <= 156 and th.height() <= 117 and not th.isNull()
    monkeypatch.setattr(ccr_backend, "images", [])
    assert sheet.update() and sheet.image() is None and sheet.thumbnail().isNull()


def test_negatives_use_the_remembered_scan(images):
    sheet = cs.ContactSheet()
    sheet.negatives = False
    sheet.update()                                   # remembers the unconverted previews
    img = images[0]
    img.converted = True
    pos = QPixmap(400, 300)
    pos.fill(QColor(250, 250, 250))                  # the converted positive
    img.resized_preview = pos
    img._render_epoch += 1
    sheet.set_negatives(True)
    c = sheet.layout().image_rect(0).center()
    assert _near(_rgb(sheet.image(), c.x(), c.y()), (128, 128, 128), 5)
    sheet.set_negatives(False)
    assert _near(_rgb(sheet.image(), c.x(), c.y()), (250, 250, 250), 5)


def test_negatives_are_read_in_the_background_when_not_remembered(images, monkeypatch):
    images[1].converted = True
    monkeypatch.setattr(cs, "decode_negative", lambda im: np.full((60, 80, 3), 40, np.uint8))
    sheet = cs.ContactSheet()
    sheet.negatives = True
    sheet.update()
    sheet._loader._thread.join(timeout=5)
    sheet.update()
    assert sheet.redrawn == [1]
    c = sheet.layout().image_rect(1).center()
    assert _near(_rgb(sheet.image(), c.x(), c.y()), (40, 40, 40), 5)
    sheet.set_negatives(False)


def test_export_jpeg_and_tiff(images, tmp_path):
    sheet = cs.ContactSheet()
    sheet.update()
    for name in ("sheet.jpg", "sheet.tif"):
        p = str(tmp_path / name)
        assert sheet.save(p) and os.path.getsize(p) > 1000
        back = QImage(p)
        assert (back.width(), back.height()) == (sheet.layout().width, sheet.layout().height)


# --- main window ------------------------------------------------------------------

def test_main_window_shows_and_leaves_the_sheet(monkeypatch):
    from ui.main_window import MainWindow
    mw = MainWindow()
    _KEEP.append(mw)
    imgs = [Img(f"g{i}.raf") for i in range(3)]
    monkeypatch.setattr(ccr_backend, "images", imgs)
    mw.contact_sheet_model.update()
    assert not mw.thumbnail_list.sheet_btn.isHidden()           # its thumbnail appeared
    sp = mw.sliders_panel
    assert mw.show_contact_sheet(True)
    assert mw.contact_sheet_shown() and not mw.image_preview.isVisibleTo(mw)
    assert mw.thumbnail_list.sheet_btn.isChecked()
    assert not sp.auto_crop_btn.isEnabled() and not sp.convert_current_bwp_btn.isEnabled()
    assert sp.auto_crop_all_btn.isEnabled()                     # the roll actions stay
    monkeypatch.setattr(mw.image_preview, "update_preview", lambda *a, **k: None)
    monkeypatch.setattr(mw.image_preview, "_update_unconvert_action_state", lambda: None)
    monkeypatch.setattr(sp, "set_current_idx", lambda i: None)
    assert mw.show_contact_sheet(False)
    assert not mw.contact_sheet_shown() and not mw.thumbnail_list.sheet_btn.isChecked()
    assert sp.auto_crop_btn.isEnabled()
    mw.image_preview.crop_mode = True                # refused while cropping
    assert not mw.show_contact_sheet(True) and not mw.contact_sheet_shown()
    mw.image_preview.crop_mode = False
    opened = []
    monkeypatch.setattr(mw, "toggle_crop_panel", lambda on=None: opened.append(on))
    mw.show_contact_sheet(True)
    lay = mw.contact_sheet_model.layout()
    c = lay.image_rect(1).center()
    mw.contact_sheet._on_double_click(c.x(), c.y())             # double-click frame 2
    assert not mw.contact_sheet_shown() and mw.thumbnail_list.thumbnail_list.currentRow() in (-1, 1)
    mw.show_contact_sheet(True)
    mw.open_frame_from_sheet(0, crop=True)
    assert not mw.contact_sheet_shown() and opened == [True]


# --- catalog ----------------------------------------------------------------------

def test_catalog_keeps_where_the_crop_came_from(tmp_path):
    import cv2
    from core import catalog
    from core.ccr_image import CCRImage
    path = str(tmp_path / "scan.png")
    cv2.imwrite(path, np.full((300, 400, 3), 30000, np.uint16))
    cat = str(tmp_path / "catalog.json")
    img = CCRImage(path)
    img.crop_rect = (0.1, 0.2, 0.9, 0.8)
    img.auto_crop_rect = (0.1, 0.2, 0.9, 0.8)
    img.auto_crop_example = "ex123"
    catalog.update_for_images([img], path=cat)
    r = catalog.create_images_for_path(path, path=cat)[0]
    assert r.auto_crop_rect == (0.1, 0.2, 0.9, 0.8) and r.auto_crop_example == "ex123"
    # a record written before these keys existed restores as "unknown"
    data = catalog.load_catalog(cat)
    for record in data["files"].values():
        for state in record["images"]:
            del state["auto_crop_rect"], state["auto_crop_example"]
    catalog.save_catalog(data, cat)
    old = catalog.create_images_for_path(path, path=cat)[0]
    assert old.crop_rect == (0.1, 0.2, 0.9, 0.8)
    assert old.auto_crop_rect is None and old.auto_crop_example is None
