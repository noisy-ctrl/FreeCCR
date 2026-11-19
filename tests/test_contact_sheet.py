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

def _draw(rect, angle=0.0, source="auto", w=400, h=300, overlay=True):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainter
    src = QPixmap(w, h)
    src.fill(QColor(128, 128, 128))
    out = QImage(200, 150, QImage.Format_RGB888)
    out.fill(QColor(0, 0, 0))
    p = QPainter(out)
    cs.draw_frame(p, QRectF(0, 0, 200, 150), src, rect, angle, source, overlay)
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


def test_without_the_overlay_just_the_frame():
    out = _draw((0.25, 0.2, 0.75, 0.8), overlay=False)
    for x in (20, 50, 100, 180):                         # outside, edge, inside
        assert _near(_rgb(out, x, 75), (128, 128, 128), 3)
    assert _near(_rgb(_draw(None, source="unsure", overlay=False), 100, 1), (128, 128, 128), 3)


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


def test_overlay_toggle_redraws_every_frame_and_is_remembered(images, monkeypatch):
    from PySide6.QtCore import QSettings
    store = {}
    monkeypatch.setattr(QSettings, "setValue", lambda self, k, v: store.__setitem__(k, v))
    sheet = cs.ContactSheet()
    sheet.overlay = True
    images[1].crop_rect = (0.25, 0.2, 0.75, 0.8)
    sheet.update()
    r = sheet.layout().image_rect(1)
    outside = (r.x() + r.width() * 0.08, r.center().y())
    assert sum(_rgb(sheet.image(), *outside)) < 3 * 128 - 60       # dimmed
    sheet.set_overlay(False)
    assert sheet.redrawn == [0, 1, 2, 3, 4] and store["sheet/overlay"] is False
    assert _near(_rgb(sheet.image(), *outside), (128, 128, 128), 3)
    sheet.set_overlay(True)
    assert sum(_rgb(sheet.image(), *outside)) < 3 * 128 - 60


def test_overlay_checkbox_sits_next_to_negatives(images):
    sheet = cs.ContactSheet()
    sheet.overlay = False
    view = cs.ContactSheetView(sheet)
    _KEEP.append(view)
    bar = view.neg_check.parentWidget().layout()
    i = bar.indexOf(view.neg_check)
    assert bar.itemAt(i + 1).widget() is view.overlay_check
    assert view.overlay_check.text() == "Overlay" and not view.overlay_check.isChecked()
    view.overlay_check.setChecked(True)
    assert sheet.overlay is True


def test_overlay_off_hides_the_crop_status_and_the_legend(images, monkeypatch):
    """Overlay off: the frames as they are - no crop status under them (it
    names the overlay's outline colour) and no colour legend in the footer;
    names and formats stay."""
    from PySide6.QtCore import QSettings
    monkeypatch.setattr(QSettings, "setValue", lambda self, k, v: None)
    for im in images:
        im.crop_rect = (0.1, 0.1, 0.9, 0.9)
        im.auto_crop_rect = im.crop_rect
    sheet = cs.ContactSheet()
    sheet.overlay = True
    sheet.update(force=True)
    lay = sheet.layout()

    def arr():
        q = sheet.image().convertToFormat(QImage.Format_RGB888)
        w, h = q.width(), q.height()
        return np.frombuffer(q.constBits(), np.uint8).reshape(
            h, q.bytesPerLine())[:, :w * 3].reshape(h, w, 3).astype(int)

    def coloured(a, rect, rgb, tol=40):
        x0, y0 = int(rect.left()), int(rect.top())
        x1, y1 = int(rect.right()), int(rect.bottom())
        patch = a[y0:y1, x0:x1]
        return int((np.abs(patch - np.array(rgb)).max(axis=-1) < tol).sum())
    from PySide6.QtCore import QRectF
    cell, img_r = lay.cell(0), lay.image_rect(0)
    caption = QRectF(cell.x(), img_r.bottom() + 1, cell.width(), cell.bottom() - img_r.bottom())
    footer = QRectF(lay.width * 0.6, lay.height - cs.MARGIN - lay.foot_h,
                    lay.width * 0.4 - cs.MARGIN, lay.foot_h)
    auto = cs.SOURCES["auto"][0]
    a = arr()
    assert coloured(a, caption, auto) > 5 and coloured(a, footer, auto) > 5
    sheet.set_overlay(False)
    a = arr()
    assert coloured(a, caption, auto) == 0                  # no "Auto crop" status
    assert coloured(a, footer, auto) == 0                   # no legend
    assert np.abs(a[int(footer.top()):int(footer.bottom()),
                    int(footer.left()):int(footer.right())] - np.array(cs.BG)).max() < 3
    sheet.set_overlay(True)
    a = arr()
    assert coloured(a, caption, auto) > 5 and coloured(a, footer, auto) > 5


# --- revision 2026-11-19: turned as in the main window, Respect crop, text ---------

def _quad_scan(w=400, h=300):
    """A scan whose left half is red and right half blue, top-left corner green."""
    q = QImage(w, h, QImage.Format_RGB888)
    q.fill(QColor(0, 0, 220))
    from PySide6.QtGui import QPainter
    p = QPainter(q)
    p.fillRect(0, 0, w // 2, h, QColor(220, 0, 0))
    p.fillRect(0, 0, w // 8, h // 8, QColor(0, 220, 0))
    p.end()
    return q


def _draw_oriented(orient, rect=None, respect=False, size=(200, 200), overlay=False):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainter
    out = QImage(*size, QImage.Format_RGB888)
    out.fill(QColor(0, 0, 0))
    p = QPainter(out)
    got = cs.draw_frame(p, QRectF(0, 0, *size), _quad_scan(), rect, 0.0, "hand",
                        overlay, orient=orient, respect_crop=respect)
    p.end()
    return out, got


def test_a_turned_frame_is_drawn_the_way_round_the_main_window_shows_it():
    # 90 degrees clockwise (the canvas's rotate(90)): the scan's left (red)
    # ends up at the top, its top-left (green) at the top-right
    out, got = _draw_oriented((90, 0.0, False, False))
    assert got.width() < got.height()                         # now portrait
    cx = got.center().x()
    assert _near(_rgb(out, cx, got.top() + 10), (220, 0, 0))
    assert _near(_rgb(out, cx, got.bottom() - 10), (0, 0, 220))
    assert _near(_rgb(out, got.right() - 4, got.top() + 4), (0, 220, 0))
    # mirrored left-right: red on the right, green top-right
    out, got = _draw_oriented((0, 0.0, True, False))
    assert _near(_rgb(out, got.right() - 20, got.center().y()), (220, 0, 0))
    assert _near(_rgb(out, got.right() - 4, got.top() + 4), (0, 220, 0))


def test_the_crop_overlay_turns_with_the_frame():
    # a crop on the scan's left half, turned 90: the bright (undimmed) part is on top
    out, got = _draw_oriented((90, 0.0, False, False), rect=(0.0, 0.0, 0.5, 1.0),
                              overlay=True)
    cx = got.center().x()
    top = sum(_rgb(out, cx, got.top() + got.height() * 0.25))
    bottom = sum(_rgb(out, cx, got.top() + got.height() * 0.75))
    assert top > 200 and bottom < 160                          # the outside dimmed


def test_respect_crop_shows_only_what_the_crop_keeps():
    # the crop is the scan's left half (red): only red shows, turned
    out, got = _draw_oriented((0, 0.0, False, False), rect=(0.0, 0.0, 0.5, 1.0),
                              respect=True)
    assert abs(got.width() / got.height() - (200 / 300)) < 0.02   # the crop's shape
    for fx in (0.2, 0.5, 0.8):                                 # no blue: it was cut off
        assert _near(_rgb(out, got.left() + got.width() * fx, got.center().y()),
                     (220, 0, 0))
    out, got = _draw_oriented((90, 0.0, False, False), rect=(0.0, 0.0, 0.5, 1.0),
                              respect=True)
    assert got.width() > got.height()                          # 200x300 turned: landscape
    # no crop: the whole frame
    out, got = _draw_oriented((0, 0.0, False, False), rect=None, respect=True)
    assert abs(got.width() / got.height() - 4 / 3) < 0.02


def test_cells_take_the_turned_frames_shape(images, monkeypatch):
    from PySide6.QtCore import QSettings
    monkeypatch.setattr(QSettings, "setValue", lambda self, k, v: None)
    sheet = cs.ContactSheet()
    sheet.respect_crop = False
    sheet.update(force=True)
    assert sheet.layout().cell_w / sheet.layout().cell_h == pytest.approx(4 / 3, 0.01)
    for im in images:
        im.rotation_angle = 270
    sheet.update(force=True)
    assert sheet.layout().cell_w / sheet.layout().cell_h == pytest.approx(3 / 4, 0.01)
    # turning one frame redraws it alone
    images[2].rotation_angle = 90
    sheet.update()
    assert sheet.redrawn == [2]


def test_text_colour_and_info(images, monkeypatch):
    from PySide6.QtCore import QSettings, QRectF
    store = {}
    monkeypatch.setattr(QSettings, "setValue", lambda self, k, v: store.__setitem__(k, v))
    sheet = cs.ContactSheet()
    sheet.overlay, sheet.respect_crop = True, False
    sheet.text_colour, sheet.text_info = "edge", cs.DEFAULT_INFO
    sheet.update(force=True)
    lay = sheet.layout()

    def caption_px(rgb, tol=30):
        q = sheet.image().convertToFormat(QImage.Format_RGB888)
        a = np.frombuffer(q.constBits(), np.uint8).reshape(
            q.height(), q.bytesPerLine())[:, :q.width() * 3].reshape(q.height(), q.width(), 3)
        cell, img_r = lay.cell(0), lay.image_rect(0)
        patch = a[int(img_r.bottom()) + 1:int(cell.bottom()), int(cell.left()):int(cell.right())]
        return int((np.abs(patch.astype(int) - np.array(rgb)).max(axis=-1) < tol).sum())
    assert caption_px(cs.EDGE_TEXT) > 5                       # the warm number
    sheet.set_text_colour("#ff00ff")
    assert store["sheet/text_colour"] == "#ff00ff"
    assert caption_px((255, 0, 255)) > 20 and caption_px(cs.EDGE_TEXT) == 0
    sheet.set_text_colour("nonsense")
    assert sheet.text_colour == "edge"
    sheet.set_text_colour("white")
    before = caption_px((240, 240, 240))
    sheet.set_text_info({"number"})
    assert store["sheet/text_info"] == "number"
    assert 0 < caption_px((240, 240, 240)) < before           # the name went
    sheet.set_text_info(())
    assert caption_px((240, 240, 240)) == 0
    # the film: the frame's preset, else the roll's
    sheet.set_roll_film("XP2 V1")
    sheet.set_text_info({"film"})
    assert caption_px((240, 240, 240)) > 20


def test_text_menu_and_back_to_frame(images, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QColor as QC
    monkeypatch.setattr(QSettings, "setValue", lambda self, k, v: None)
    sheet = cs.ContactSheet()
    sheet.text_colour, sheet.text_info = "edge", cs.DEFAULT_INFO
    view = cs.ContactSheetView(sheet)
    _KEEP.append(view)
    m = view.text_menu()
    texts = [a.text() for a in m.actions() if not a.isSeparator()]
    assert texts == ["Colour", "Edge print", "White", "Light grey", "Mid grey", "Warm",
                     "Custom…", "Show", "Frame number", "File name", "Film",
                     "Film format", "Crop status"]
    by = {a.text(): a for a in m.actions()}
    assert by["Edge print"].isChecked() and by["File name"].isChecked()
    assert not by["Film"].isChecked()
    by["Warm"].trigger()
    assert sheet.text_colour == "warm"
    by["Film"].trigger()
    assert "film" in sheet.text_info
    monkeypatch.setattr(view, "_choose_colour", lambda cur: QC("#123456"))
    by["Custom…"].trigger()
    assert sheet.text_colour == "#123456"
    assert view.text_menu().actions()[6].isChecked()           # Custom… ticked
    # Back to Frame stands out, and Respect crop sits after Overlay
    assert view.back_btn.text() == "Back to Frame"
    assert "#e6e6e6" in view.back_btn.styleSheet()
    bar = view.neg_check.parentWidget().layout()
    assert bar.itemAt(bar.indexOf(view.overlay_check) + 1).widget() is view.crop_check
    view.crop_check.setChecked(True)
    assert sheet.respect_crop is True

