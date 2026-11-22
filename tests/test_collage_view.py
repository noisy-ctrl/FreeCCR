#!/usr/bin/env python3
"""The collage maker's view and drawing (spec/collage-maker.md): members
(opening, Add / Remove, the strip's reorder), settings remembered, the
preview drawn from each frame's picture, Export (one file per post, JPEG sRGB
4:4:4 at 95, each frame what Export writes for it), the toolbar button and
the main window's showing and leaving."""

import os
import sys

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
sys.path.insert(0, HERE)

from PySide6.QtCore import QRectF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core import collage  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402
from widgets import collage_render as cr  # noqa: E402
from widgets.collage_view import CollageView  # noqa: E402
from test_contact_sheet import Img, _near, _rgb  # noqa: E402

COLOURS = [(220, 40, 40), (40, 200, 60), (40, 60, 220), (230, 200, 40),
           (200, 40, 200), (40, 200, 200), (120, 120, 120), (250, 140, 20)]


class _Store(QSettings):
    """QSettings in memory."""

    def __init__(self):
        super().__init__()
        self._d = {}

    def value(self, k, default=None, type=None):
        return self._d.get(k, default)

    def setValue(self, k, v):
        self._d[k] = v


@pytest.fixture
def roll(monkeypatch):
    imgs = []
    for i, c in enumerate(COLOURS[:6]):
        im = Img(f"f{i}.raf", 600 if i % 3 else 400, 400 if i % 3 else 600, c)
        im.film_format = "35mm"
        imgs.append(im)
    monkeypatch.setattr(ccr_backend, "images", imgs)
    return imgs


def _view(**kw):
    v = CollageView(settings=_Store())
    v._export_threaded = False
    v.resize(1200, 900)
    for k, val in kw.items():
        setattr(v, k, val)
    return v


def _pick(combo, data):
    combo.setCurrentIndex(combo.findData(data))


# --- members -----------------------------------------------------------------

def test_opening_takes_a_new_selection_else_keeps_or_takes_every_frame(roll):
    v = _view()
    v.activate([roll[0]])                              # one selected: every frame
    assert v.members() == roll
    v.activate([roll[1], roll[3]])                     # a new selection
    assert v.members() == [roll[1], roll[3]]
    v.activate([roll[1], roll[3]])                     # the same one, still selected
    assert v.members() == [roll[1], roll[3]]
    v.add_members([roll[4], roll[5]])                  # from the list's menu
    v.activate([roll[4], roll[5]])                     # that selection, still selected
    assert v.members() == [roll[1], roll[3], roll[4], roll[5]]
    v.activate([])                                     # nothing: the collage it had
    assert v.members() == [roll[1], roll[3], roll[4], roll[5]]
    v.activate([roll[0], roll[2]])                     # a new selection again
    assert v.members() == [roll[0], roll[2]]


def test_add_remove_and_frames_that_closed_drop_out(roll, monkeypatch):
    v = _view()
    v.set_members(roll[:2])
    v.add_members([roll[1], roll[2]])                  # no doubles
    assert v.members() == roll[:3]
    assert v.has_member(roll[2]) and not v.has_member(roll[5])
    v.remove_members([roll[0]])
    assert v.members() == roll[1:3]
    assert v.strip.count() == 2 and v.count_label.text() == "2 frames"
    monkeypatch.setattr(ccr_backend, "images", roll[2:])  # roll[1] was closed
    assert v.members() == [roll[2]]


def test_the_strip_reorders_the_collage(roll):
    v = _view()
    v.set_members(roll[:3])
    item = v.strip.takeItem(0)
    v.strip.insertItem(2, item)
    v._order_from_strip()
    assert v.members() == [roll[1], roll[2], roll[0]]


def test_the_list_menu_adds_and_removes(roll):
    from widgets import thumbnail_list as tl
    v = _view()
    v.set_members([roll[0]])
    assert tl.collage_menu_texts(v, [roll[0], roll[1]]) == ("Add to Collage",
                                                            "Remove from Collage")
    assert tl.collage_menu_texts(v, [roll[0]]) == (None, "Remove from Collage")
    assert tl.collage_menu_texts(v, [roll[2], roll[3]]) == ("Add to Collage (2)", None)
    v.add_members([roll[1]])
    assert tl.collage_menu_texts(v, [roll[0], roll[1]]) == (None, "Remove from Collage (2)")


# --- settings -----------------------------------------------------------------

def test_settings_are_remembered_and_rows_follow_the_layout(roll):
    store = _Store()
    v = CollageView(settings=store)
    _pick(v.layout_combo, "carousel")
    _pick(v.style_combo, "full")
    v.height_spin.setValue(90)
    v.gap_spin.setValue(20)
    assert store._d["collage/layout"] == "carousel"
    assert store._d["collage/height_share"] == 90
    assert v._form.isRowVisible(v.height_spin) and not v._form.isRowVisible(v.posts_spin)
    assert not v._form.isRowVisible(v.frames_spin)
    w = CollageView(settings=store)
    assert w.layout_combo.currentData() == "carousel"
    assert w.settings().height_share == pytest.approx(0.90) and w.settings().gap == 20
    _pick(w.fmt_combo, "custom")
    w.w_spin.setValue(2000)
    w.h_spin.setValue(1000)
    assert w.canvas_size() == (2000, 1000)
    _pick(w.borders_combo, "none")
    _pick(w.layout_combo, "strip")
    assert w.borders_combo.currentData() == "generated"         # shown as it's drawn
    look = w.look()
    assert look.strip and look.borders == "generated"          # a strip is film
    assert look.strip_fmt == "35mm"
    assert w.settings().gap == 20                               # between strips


def test_shuffle_and_its_history(roll):
    v = _view()
    v.set_members(roll)
    first = v.render_preview().cells
    v.shuffle()
    v.render_preview()
    assert v.seed_label.text() == "arrangement 2 of 2"
    assert v.back_btn_seed.isEnabled() and not v.fwd_btn_seed.isEnabled()
    v.history_back()
    again = v.render_preview().cells
    assert [(c.index, c.x, c.y, c.w, c.h) for c in again] == \
        [(c.index, c.x, c.y, c.w, c.h) for c in first]


# --- drawing ------------------------------------------------------------------

def test_the_preview_draws_each_frame_in_its_cell(roll):
    v = _view()
    v.set_members(roll)
    _pick(v.borders_combo, "none")
    arr, mem, look = v.arrangement()
    img = cr.render(arr, mem, look, cr.PreviewSource(), v.history.seed)
    assert (img.width(), img.height()) == (1080, 1350)
    for c in arr.cells:
        cx, cy = c.x + c.w / 2, c.y + c.h / 2
        assert _near(_rgb(img, cx, cy), COLOURS[c.index], 12), c.index
    # and the gap round them is the background colour
    assert _near(_rgb(img, 3, 3), (255, 255, 255), 3)


def test_generated_borders_put_a_dark_film_edge_round_the_frame(roll):
    v = _view()
    v.set_members(roll[:3])
    _pick(v.borders_combo, "generated")
    arr, mem, look = v.arrangement()
    img = cr.render(arr, mem, look, cr.PreviewSource(), v.history.seed)
    c = arr.cells[0]
    # the cell's edge is film, its middle the picture
    assert sum(_rgb(img, c.x + 4, c.y + c.h / 2)) < 60
    assert sum(_rgb(img, c.x + c.w - 5, c.y + c.h / 2)) < 60
    assert _near(_rgb(img, c.x + c.w / 2, c.y + c.h / 2), COLOURS[c.index], 15)


def test_a_film_strip_lays_each_frame_along_it_top_to_the_left(roll):
    from PySide6.QtGui import QPainter as _P
    for im in roll:                               # a white bar along each picture's top
        w, h = im.resized_preview.width(), im.resized_preview.height()
        p = _P(im.resized_preview)
        p.fillRect(0, 0, w, h // 6, QColor(255, 255, 255))
        p.end()
    v = _view()
    v.set_members(roll)
    _pick(v.layout_combo, "strip")
    _pick(v.fmt_combo, "story")
    v._colour = QColor(0, 0, 0)
    arr, mem, look = v.arrangement()
    assert arr.strip_dir == "down"
    img = cr.render(arr, mem, look, cr.PreviewSource(), v.history.seed)
    for c in arr.cells:
        pc = cr.Piece(mem[c.index], "35mm", True)
        W, H = cr.shown_shape(mem[c.index])
        assert pc.turn == (W > H)                 # landscape frames lie turned on a strip down
        ppm = c.w / pc.w
        ix, iy = c.x + pc.image.x() * ppm, c.y + pc.image.y() * ppm
        iw, ih = pc.image.width() * ppm, pc.image.height() * ppm
        top = _rgb(img, ix + iw / 2, iy + ih * 0.04)
        left = _rgb(img, ix + iw * 0.04, iy + ih / 2)
        if pc.turn:
            assert _near(left, (255, 255, 255), 20) and not _near(top, (255, 255, 255), 60)
        else:
            assert _near(top, (255, 255, 255), 20) and not _near(left, (255, 255, 255), 60)


# --- export -------------------------------------------------------------------

class _Backend:
    """Export stand-in: writes each frame as a 16-bit TIFF of its colour at
    the long side asked, its own shape."""

    def __init__(self, images):
        self.images = images
        self.calls = []

    def export_image_by_index(self, idx, path, jpg_output=False, max_long_side=None,
                              output_colorspace=None, image_obj=None, **kw):
        img = image_obj or self.images[idx]
        self.calls.append((idx, max_long_side, output_colorspace, jpg_output))
        w, h = img.resized_preview.width(), img.resized_preview.height()
        s = max_long_side / max(w, h)
        w, h = max(1, round(w * s)), max(1, round(h * s))
        c = self.images[idx].resized_preview.toImage().pixelColor(0, 0)
        arr = np.empty((h, w, 3), np.uint16)
        arr[...] = (c.blue() * 257, c.green() * 257, c.red() * 257)
        cv2.imwrite(os.path.splitext(path)[0] + ".tiff", arr)
        return True


def test_a_carousel_exports_one_jpeg_a_post_through_export(roll, tmp_path, monkeypatch):
    fake = _Backend(roll)
    monkeypatch.setattr(ccr_backend, "export_image_by_index", fake.export_image_by_index)
    v = _view()
    v.set_members(roll)
    _pick(v.layout_combo, "carousel")
    _pick(v.style_combo, "varied")
    v.posts_spin.setValue(3)
    v._ask_export_path = lambda: str(tmp_path / "trip.jpg")
    written = v.export()
    assert [os.path.basename(p) for p in written] == ["trip_1.jpg", "trip_2.jpg", "trip_3.jpg"]
    arr, mem, _look = v.arrangement()
    for k, p in enumerate(written):
        im = cv2.imread(p)
        assert im.shape[:2] == (1350, 1080)
        raw = open(p, "rb").read()
        assert b"ICC_PROFILE" in raw[:4096]                   # sRGB embedded
    # every frame went through Export, in sRGB, as a 16-bit TIFF
    assert {c[0] for c in fake.calls} == set(range(len(roll)))
    assert all(c[2] == "srgb" and c[3] is False for c in fake.calls)
    # and each cell shows its frame
    whole = np.hstack([cv2.imread(p)[..., ::-1] for p in written])
    for c in arr.cells:
        cx, cy = int(c.x + c.w / 2), int(c.y + c.h / 2)
        got = tuple(int(x) for x in whole[cy, cx])
        assert _near(got, COLOURS[c.index], 14), (c.index, got)


def test_jpegs_are_444_at_95(roll, tmp_path):
    img = QImage(64, 64, QImage.Format_RGB32)
    img.fill(QColor(200, 30, 30))
    p = QPainter(img)
    p.fillRect(QRectF(0, 0, 1, 64), QColor(30, 30, 200))
    p.end()
    path = str(tmp_path / "a.jpg")
    assert cr.save_jpeg(img, path, 95)
    data = open(path, "rb").read()
    sof = data.index(b"\xff\xc0")
    assert data[sof + 9] == 3
    assert data[sof + 11] == data[sof + 14] == data[sof + 17] == 0x11   # no subsampling
    assert b"ICC_PROFILE" in data[:4096]


def test_export_jobs_cover_the_background_at_the_canvas_size(roll):
    v = _view()
    v.set_members(roll[:3])
    _pick(v.bg_combo, "blur")
    arr, mem, look = v.arrangement()
    jobs = CollageView.export_jobs(arr, mem, look)
    first = [j for j in jobs if j[0] is mem[0]]
    assert first and max(j[1] for j in first) >= max(arr.width, arr.height)
    _pick(v.borders_combo, "scan")
    arr, mem, look = v.arrangement()
    jobs = CollageView.export_jobs(arr, mem, look)
    assert any(j[2] > 0 for j in jobs)                          # a bleed render
    assert any(j[0] is mem[0] and j[2] == 0 and j[1] >= max(arr.width, arr.height)
               for j in jobs)                                   # and the cover


# --- the main window ----------------------------------------------------------

def test_the_toolbar_button_shows_the_collage_and_back(roll):
    from ui.main_window import MainWindow
    mw = MainWindow()
    ip = mw.image_preview
    names = [a.text() for a in ip.toolbar.actions() if a.text()]
    assert names.index("Collage") == names.index("Skin") + 1
    ip.collage_action.trigger()
    assert mw.collage_shown() and ip.isHidden() and ip.collage_action.isChecked()
    assert mw.collage_view.members() == roll
    # picking in the list while it shows changes nothing
    tl = mw.thumbnail_list
    assert tl._in_collage()
    mw.show_contact_sheet(True)
    assert not mw.collage_shown() and not ip.collage_action.isChecked()
    mw.show_collage(True)
    assert mw.collage_shown() and not mw.contact_sheet_shown()
    mw.collage_view.closeRequested.emit()
    assert not mw.collage_shown() and not ip.isHidden()
    assert not ip.collage_action.isChecked()


def test_no_frames_no_collage(monkeypatch):
    from ui.main_window import MainWindow
    monkeypatch.setattr(ccr_backend, "images", [])
    mw = MainWindow()
    mw.image_preview.collage_action.trigger()
    assert not mw.collage_shown() and not mw.image_preview.collage_action.isChecked()


@pytest.mark.parametrize("layout,borders", [("strip", "generated"), ("triptych", "scan"),
                                            ("mosaic", "generated")])
def test_other_layouts_export_one_file_with_each_frame_in_place(roll, tmp_path, monkeypatch,
                                                                layout, borders):
    fake = _Backend(roll)
    monkeypatch.setattr(ccr_backend, "export_image_by_index", fake.export_image_by_index)
    v = _view()
    v.set_members(roll)
    _pick(v.layout_combo, layout)
    _pick(v.borders_combo, borders)
    _pick(v.fmt_combo, "story")
    v._ask_export_path = lambda: str(tmp_path / "c.jpg")
    written = v.export()
    assert [os.path.basename(p) for p in written] == ["c.jpg"]
    out = cv2.imread(written[0])[..., ::-1]
    assert out.shape[:2] == (1920, 1080)
    arr, mem, _look = v.arrangement()
    for c in arr.cells:
        got = tuple(int(x) for x in out[int(c.y + c.h / 2), int(c.x + c.w / 2)])
        assert _near(got, COLOURS[c.index], 14), (c.index, got)


def test_a_frame_in_the_export_is_exports_own_file(tmp_path, monkeypatch):
    """ExportSource renders each frame through Export itself: its pixels are
    Export's 16-bit sRGB TIFF of that frame at that size, and with no screen
    sharpening a cell of that size is those pixels."""
    import tifffile
    from test_export_canvas import _converted_frame
    img = _converted_frame(tmp_path, monkeypatch)
    src = cr.ExportSource(ccr_backend, sharpen=None)
    assert src.prepare(img, 150)
    ref_path = str(tmp_path / "ref.tiff")
    assert ccr_backend.export_image_by_index(0, ref_path, jpg_output=False, max_long_side=150,
                                             output_colorspace="srgb")
    ref = tifffile.imread(ref_path)
    got = src._array(img)
    assert got.shape == ref.shape and got.dtype == np.uint16
    assert np.array_equal(got, ref)
    fitted = src._fit(got, got.shape[1], got.shape[0])
    assert np.array_equal(fitted, ref)
    assert not [f for f in os.listdir(tempfile_dir()) if f.startswith("freeccr-collage-")]


def tempfile_dir():
    import tempfile
    return tempfile.gettempdir()


def test_from_the_scan_renders_past_the_crop_and_leaves_the_frame_alone(tmp_path, monkeypatch):
    from test_export_canvas import _converted_frame
    img = _converted_frame(tmp_path, monkeypatch)
    img.crop_rect = (0.25, 0.25, 0.75, 0.75)
    src = cr.ExportSource(ccr_backend, sharpen=None)
    assert src.prepare(img, 120, 0.0) and src.prepare(img, 120, 0.1)
    plain, wide = src._array(img, False), src._array(img, True)
    assert plain.shape[:2] == (60, 90)                 # the crop (Export never enlarges)
    assert wide.shape[1] / plain.shape[1] == pytest.approx(1.2, abs=0.03)
    assert img.crop_rect == (0.25, 0.25, 0.75, 0.75)   # the frame's own crop untouched
