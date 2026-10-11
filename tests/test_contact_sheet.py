#!/usr/bin/env python3
"""Contact sheet (spec/contact-sheet.md): crop sources, tile drawing (box
position, colour, rotation), layout and clicks, the live signature, Negatives,
Save as image, the main-window wiring, and the catalog keys."""

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
    monkeypatch.setattr(ccr_backend, "get_image_rotation_by_index",
                        lambda i: ccr_backend.images[i].rotation_angle)
    monkeypatch.setattr(ccr_backend, "get_image_horizontal_flip_by_index",
                        lambda i: ccr_backend.images[i].horizontal_mirrored)
    monkeypatch.setattr(ccr_backend, "get_image_vertical_flip_by_index",
                        lambda i: ccr_backend.images[i].vertical_mirrored)
    return imgs


def _rgb(pm, x, y):
    c = pm.toImage().pixelColor(int(x), int(y))
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


# --- drawing ---------------------------------------------------------------------

def test_tile_box_lands_on_the_crop_in_the_source_colour():
    src = QPixmap(400, 300)
    src.fill(QColor(128, 128, 128))
    pm = cs.compose_tile(src, (0.25, 0.2, 0.75, 0.8), 0.0, "auto", 0, False, False, 200, 200)
    assert (pm.width(), pm.height()) == (200, 150)
    amber = cs.SOURCES["auto"][0]
    assert _near(_rgb(pm, 50, 75), amber)             # left edge of the box
    assert _near(_rgb(pm, 100, 75), (128, 128, 128))  # inside: untouched
    assert sum(_rgb(pm, 20, 75)) < sum((128, 128, 128)) - 60   # outside: dimmed


def test_tile_box_rotates_with_the_crop_and_the_frame():
    src = QPixmap(400, 300)
    src.fill(QColor(128, 128, 128))
    flat = cs.compose_tile(src, (0.25, 0.2, 0.75, 0.8), 0.0, "hand", 0, False, False, 200, 200)
    tilted = cs.compose_tile(src, (0.25, 0.2, 0.75, 0.8), 10.0, "hand", 0, False, False, 200, 200)
    assert flat.toImage() != tilted.toImage()
    pts = cs.crop_polygon((0.25, 0.2, 0.75, 0.8), 90.0, 400, 300)
    xs = sorted(round(p.x()) for p in pts)
    assert xs[0] == 110 and xs[-1] == 290       # a quarter turn: x spans the box's height (180)
    # a portrait frame (rotated 90) is laid out as displayed: taller than wide
    rot = cs.compose_tile(src, (0.25, 0.2, 0.75, 0.8), 0.0, "hand", 90, False, False, 200, 200)
    assert rot.height() > rot.width()


def test_unsure_frame_gets_a_dashed_outline():
    src = QPixmap(400, 300)
    src.fill(QColor(128, 128, 128))
    pm = cs.compose_tile(src, None, 0.0, "unsure", 0, False, False, 200, 200)
    red = cs.SOURCES["unsure"][0]
    edge = [_near(_rgb(pm, x, 1), red, 60) for x in range(10, 190)]
    assert any(edge) and not all(edge)              # dashed


# --- the view --------------------------------------------------------------------

def _view(width=1000):
    v = cs.ContactSheetView()
    v.resize(width, 700)
    v.show()
    _KEEP.append(v)
    QApplication.processEvents()
    return v


def test_grid_reflows_and_clicks_map_to_frames(images):
    v = _view(1000)
    v.refresh(force=True)
    c = v.canvas
    assert len(c.tiles) == 5 and c._cols >= 2
    cols_wide = c._cols
    v.resize(500, 700)
    QApplication.processEvents()
    v.refresh(force=True)
    assert c._cols < cols_wide
    centre = c.cell_rect(3).center().toPoint()
    assert c.tile_at(centre) == 3 and c.tile_at(QPoint(-50, -50)) is None
    got = []
    v.frameActivated.connect(lambda i, crop: got.append((i, crop)))
    c._pending_click = 3
    c._single_click()
    c.activated.emit(2, True)                       # what a double-click emits
    assert got == [(3, False), (2, True)]


def test_live_only_the_changed_tile_is_redrawn(images):
    v = _view()
    v.refresh(force=True)
    before = {k: val[1] for k, val in v._cache.items()}
    images[2].crop_rect = (0.1, 0.1, 0.9, 0.9)
    v.refresh()
    after = {k: val[1] for k, val in v._cache.items()}
    changed = [i for i, img in enumerate(images) if before[id(img)] is not after[id(img)]]
    assert changed == [2]
    assert v.canvas.tiles[2]["source"] == "hand"
    images[2]._render_epoch += 1                     # a re-render (slider, convert)
    v.refresh()
    assert v._cache[id(images[2])][1] is not after[id(images[2])]


def test_negatives_use_the_remembered_scan(images, monkeypatch):
    v = _view()
    v.refresh(force=True)                            # remembers the unconverted previews
    img = images[0]
    img.converted = True
    pos = QPixmap(400, 300)
    pos.fill(QColor(250, 250, 250))                  # the converted positive
    img.resized_preview = pos
    img._render_epoch += 1
    v.neg_check.setChecked(True)
    pm = v._cache[id(img)][1]
    assert _near(_rgb(pm, pm.width() // 2, pm.height() // 2), (128, 128, 128), 5)
    v.neg_check.setChecked(False)
    pm = v._cache[id(img)][1]
    assert _near(_rgb(pm, pm.width() // 2, pm.height() // 2), (250, 250, 250), 5)


def test_negatives_are_decoded_in_the_background_when_not_remembered(images, monkeypatch):
    img = images[1]
    img.converted = True
    monkeypatch.setattr(cs, "decode_negative", lambda im: np.full((60, 80, 3), 40, np.uint8))
    v = _view()
    v.neg_check.setChecked(True)
    for _ in range(200):
        if not v._loader.pending():
            break
        QApplication.processEvents()
    v._loader._thread.join(timeout=5)
    v.refresh()
    pm = v._cache[id(img)][1]
    assert pm is not None and _near(_rgb(pm, pm.width() // 2, pm.height() // 2), (40, 40, 40), 5)
    v.neg_check.setChecked(False)


def test_save_as_image(images, tmp_path):
    v = _view()
    images[0].crop_rect = (0.1, 0.1, 0.9, 0.9)
    out = v.render_image(tile=200)
    assert out.width() == 20 + 3 * 220 and out.height() > 2 * 200 * cs.IMG_ASPECT
    p = str(tmp_path / "sheet.jpg")
    assert out.save(p, "JPG", 92) and os.path.getsize(p) > 1000


# --- main window ------------------------------------------------------------------

def test_main_window_shows_and_leaves_the_sheet(monkeypatch):
    from ui.main_window import MainWindow
    mw = MainWindow()
    _KEEP.append(mw)
    imgs = [Img(f"g{i}.raf") for i in range(3)]
    monkeypatch.setattr(ccr_backend, "images", imgs)
    for name in ("get_image_rotation_by_index", "get_image_horizontal_flip_by_index",
                 "get_image_vertical_flip_by_index"):
        monkeypatch.setattr(ccr_backend, name, lambda i: 0)
    assert mw.show_contact_sheet(True)
    assert mw.contact_sheet_shown() and not mw.image_preview.isVisibleTo(mw)
    assert mw.thumbnail_list.sheet_btn.isChecked()
    assert mw.show_contact_sheet(False)
    assert not mw.contact_sheet_shown() and not mw.thumbnail_list.sheet_btn.isChecked()
    mw.image_preview.crop_mode = True                # refused while cropping
    assert not mw.show_contact_sheet(True) and not mw.contact_sheet_shown()
    mw.image_preview.crop_mode = False
    opened = []
    monkeypatch.setattr(mw, "toggle_crop_panel", lambda on=None: opened.append(on))
    monkeypatch.setattr(mw.image_preview, "update_preview", lambda *a, **k: None)
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
