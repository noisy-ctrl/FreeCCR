#!/usr/bin/env python3
"""The contact sheet as strips of film (spec/contact-sheet-strips.md): the
format's geometry, which way the strips run, frames lying along the film
(turned top to the left), the gate, the film's edge colour, white sprocket
holes on 35 mm only, edge print, live redraw of one piece, and Layout…."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
sys.path.insert(0, HERE)

from PySide6.QtCore import QPoint, QPointF, QRectF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core import film_formats  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402
from widgets import contact_sheet as cs  # noqa: E402
from test_contact_sheet import Img, _near, _rgb  # noqa: E402

_KEEP = []


@pytest.fixture(autouse=True)
def _no_settings(monkeypatch):
    store = {}
    monkeypatch.setattr(QSettings, "setValue", lambda self, k, v: store.__setitem__(k, v))
    return store


def _roll(monkeypatch, n=8, fmt="35mm", w=600, h=400, colour=(128, 128, 128)):
    imgs = []
    for i in range(n):
        im = Img(f"f{i}.raf", w, h, colour)
        im.film_format = fmt
        imgs.append(im)
    monkeypatch.setattr(ccr_backend, "images", imgs)
    return imgs


def _sheet(**kw):
    s = cs.ContactSheet()
    s.layout_mode, s.strip_dir, s.gate = "strips", "auto", "square"
    s.film_edge, s.holes_white, s.overlay = "black", True, False
    s.text_colour, s.text_info = "edge", ("number",)
    for k, v in kw.items():
        setattr(s, k, v)
    s.update(force=True)
    return s


def _two_tone(w, h, top=(220, 30, 30), bottom=(30, 30, 220)):
    """A picture whose top half is red and bottom half blue."""
    pm = QPixmap(w, h)
    pm.fill(QColor(*bottom))
    p = QPainter(pm)
    p.fillRect(0, 0, w, h // 2, QColor(*top))
    p.end()
    return pm


# --- the film's geometry --------------------------------------------------------

def test_formats_know_their_strips():
    g = film_formats.strip_geometry("35mm")
    assert (g.along, g.across, g.film, g.holes, g.direction, g.per_strip) == \
        (36.0, 24.0, 35.0, True, "across", 6)
    g = film_formats.strip_geometry("645")
    assert (g.along, g.across, g.holes, g.direction, g.per_strip) == (41.5, 56.0, False, "down", 4)
    assert film_formats.strip_geometry("6x7").direction == "across"
    assert film_formats.strip_geometry("4x5") is None             # sheet film: no strips
    assert film_formats.strip_geometry("35mm 3:2").key == "35mm"   # auto crop's names


def test_strip_format_from_the_frames_then_the_roll(monkeypatch):
    imgs = _roll(monkeypatch, 3, fmt=None)
    assert cs.strip_format(imgs) == "35mm"                        # nothing to go on
    assert cs.strip_format(imgs, "645") == "645"                  # the roll's
    imgs[0].film_format = imgs[1].film_format = "6x7"
    assert cs.strip_format(imgs, "645") == "6x7"                  # the frames' first
    imgs[0].film_format = imgs[1].film_format = "4x5"
    assert cs.strip_format(imgs) is None                          # sheet film


# --- the layout ---------------------------------------------------------------------

def test_35mm_strips_run_across_six_a_strip(monkeypatch):
    _roll(monkeypatch, 8)
    lay = _sheet().layout()
    assert isinstance(lay, cs.StripLayout) and lay.direction == "across" and lay.per == 6
    c0, c1, c6 = lay.cell(0), lay.cell(1), lay.cell(6)
    assert c1.left() == c0.right() and c1.top() == c0.top()      # pieces meet exactly
    assert c6.top() > c0.bottom() and c6.left() == c0.left()     # the next strip below
    r = lay.image_rect(0)
    assert r.width() > r.height() and c0.contains(r)
    assert abs(r.width() / r.height() - 36 / 24) < 0.01
    assert lay.frame_at(lay.image_rect(7).center().x(), lay.image_rect(7).center().y()) == 7
    assert lay.frame_at(1, 1) is None


def test_645_strips_run_down_with_landscape_slots(monkeypatch):
    _roll(monkeypatch, 6, fmt="645", w=400, h=560)
    lay = _sheet().layout()
    assert lay.direction == "down" and lay.per == 4
    c0, c1 = lay.cell(0), lay.cell(1)
    assert c1.top() == c0.bottom() and c1.left() == c0.left()
    r = lay.image_rect(0)
    assert abs(r.width() / r.height() - 56 / 41.5) < 0.01        # landscape on screen
    assert lay.cell(4).left() > c0.right()                       # the next strip beside


def test_direction_can_be_chosen(monkeypatch):
    _roll(monkeypatch, 6, fmt="645")
    assert _sheet(strip_dir="across").layout().direction == "across"
    _roll(monkeypatch, 6, fmt="35mm")
    assert _sheet(strip_dir="down").layout().direction == "down"


def test_sheet_film_falls_back_to_the_grid(monkeypatch):
    _roll(monkeypatch, 4, fmt="4x5")
    assert isinstance(_sheet().layout(), cs.SheetLayout)


# --- the frames on the film ---------------------------------------------------------

def test_a_frame_of_the_other_shape_lies_along_the_film_top_to_the_left(monkeypatch):
    imgs = _roll(monkeypatch, 2)
    imgs[0].resized_preview = _two_tone(300, 450)                # portrait: top red
    sheet = _sheet()
    r = sheet.layout().image_rect(0)                             # a landscape slot
    left = _rgb(sheet.image(), r.left() + r.width() * 0.3, r.center().y())
    right = _rgb(sheet.image(), r.left() + r.width() * 0.7, r.center().y())
    assert _near(left, (220, 30, 30)) and _near(right, (30, 30, 220))
    assert cs.lie_along(300, 450, QRectF(0, 0, 36, 24)) == -90
    assert cs.lie_along(450, 300, QRectF(0, 0, 36, 24)) == 0
    assert cs.lie_along(100, 100, QRectF(0, 0, 36, 24)) == 0     # square: as it is


def test_a_turned_frame_in_a_down_strip_reads_upright(monkeypatch):
    """645 down: landscape slots, so a landscape picture stays upright."""
    imgs = _roll(monkeypatch, 2, fmt="645")
    imgs[0].resized_preview = _two_tone(560, 415)
    sheet = _sheet()
    r = sheet.layout().image_rect(0)
    assert _near(_rgb(sheet.image(), r.center().x(), r.top() + r.height() * 0.25), (220, 30, 30))
    assert _near(_rgb(sheet.image(), r.center().x(), r.top() + r.height() * 0.75), (30, 30, 220))


def test_strips_show_the_crop_and_the_camera_gate_a_little_past_it(monkeypatch):
    imgs = _roll(monkeypatch, 2)
    pm = QPixmap(600, 400)
    pm.fill(QColor(200, 200, 40))                                 # past the crop: yellow
    p = QPainter(pm)
    p.fillRect(60, 40, 480, 320, QColor(40, 160, 40))              # the crop: green
    p.end()
    imgs[1].resized_preview = pm
    imgs[1].crop_rect = (0.1, 0.1, 0.9, 0.9)
    sheet = _sheet()
    lay = sheet.layout()
    r = lay.image_rect(1)   # inside the strip
    edge = cs.FILM_EDGES[0][2]
    assert _near(_rgb(sheet.image(), r.left() + 3, r.center().y()), (40, 160, 40), 6)
    assert _near(_rgb(sheet.image(), r.left() - 4, r.center().y()), edge, 6)    # square
    sheet.set_gate("camera")
    _band, bleed = cs.ContactSheet._bands(lay.geo)
    assert 0 < bleed <= 0.4 * lay.geo.gap
    assert _near(_rgb(sheet.image(), r.left() - 4, r.center().y()), (200, 200, 40), 6)
    past = r.left() - bleed * lay.ppm - 3
    assert _near(_rgb(sheet.image(), past, r.center().y()), edge, 6)  # no further
    gap_mid = lay.cell(1).left()
    assert _near(_rgb(sheet.image(), gap_mid, r.center().y()), edge, 6)  # a sliver of gap


def test_the_film_edge_colour_and_white_holes(monkeypatch):
    _roll(monkeypatch, 2)
    sheet = _sheet()
    lay = sheet.layout()
    T = lay.strip_transform(0)
    ppm = lay.ppm
    hole = T.map(QPointF(0.5 * cs.HOLE_PITCH * ppm, (cs.HOLE_EDGE + cs.HOLE_ACROSS / 2) * ppm))
    rebate = T.map(QPointF(0.5 * cs.HOLE_PITCH * ppm, (cs.HOLE_EDGE + cs.HOLE_ACROSS + 0.3) * ppm))
    assert _near(_rgb(sheet.image(), hole.x(), hole.y()), cs.HOLE_WHITE, 8)
    assert _near(_rgb(sheet.image(), rebate.x(), rebate.y()), (6, 6, 6), 4)
    sheet.set_holes_white(False)
    assert _near(_rgb(sheet.image(), hole.x(), hole.y()), cs.BG, 4)
    sheet.set_film_edge("grey")
    assert _near(_rgb(sheet.image(), rebate.x(), rebate.y()), (40, 40, 40), 4)
    sheet.set_film_edge("#503020")
    assert sheet.film_edge == "#503020"
    assert _near(_rgb(sheet.image(), rebate.x(), rebate.y()), (0x50, 0x30, 0x20), 4)
    sheet.set_film_edge("nonsense")
    assert sheet.film_edge == "black"


def test_120_strips_have_no_holes(monkeypatch):
    _roll(monkeypatch, 3, fmt="6x7", w=690, h=560)
    sheet = _sheet()
    lay = sheet.layout()
    assert not lay.geo.holes
    T = lay.strip_transform(0)
    ppm = lay.ppm
    # where a 35 mm hole would be, and all along the rebate band: film edge only
    for x_mm in (2.4, 7.1, 11.9, 30.0):
        pt = T.map(QPointF(x_mm * ppm, (lay.geo.rebate - 0.3) * ppm))
        assert _near(_rgb(sheet.image(), pt.x(), pt.y()), (6, 6, 6), 6)


def test_edge_print_number_and_film(monkeypatch, _no_settings):
    _roll(monkeypatch, 7)
    sheet = _sheet(text_info=("number", "film"), roll_film="XP2 V1")
    lay = sheet.layout()

    def lit(i, top):
        """Edge-print-coloured pixels in frame i's number (or name) band."""
        band_mm, _b = cs.ContactSheet._bands(lay.geo)
        F = lay.film_px
        y0, y1 = (0, band_mm * lay.ppm) if top else (F - band_mm * lay.ppm, F)
        k, piece = lay.piece(i)
        T = lay.strip_transform(k)
        img = sheet.image()
        want = cs.EDGE_TEXT if not top else cs.NAME_TEXT
        n = 0
        for x in range(int(piece.left()) + 1, int(piece.right()) - 1, 2):
            for y in range(int(y0) + 1, int(y1) - 1):
                pt = T.map(QPointF(x, y))
                n += _near(_rgb(img, pt.x(), pt.y()), want, 60)
        return n

    assert lit(0, False) > 10 and lit(1, False) > 10              # numbers on every frame
    assert lit(0, True) > 10 and lit(1, True) == 0                # the film at the strip's start
    assert lit(6, True) > 10                                      # ...of each strip
    sheet.set_text_info(set())
    assert lit(0, False) == 0 and lit(0, True) == 0


def test_live_one_piece_redraws_without_touching_its_neighbours(monkeypatch):
    imgs = _roll(monkeypatch, 6)
    sheet = _sheet()
    lay = sheet.layout()
    before = sheet.image().copy()
    imgs[2]._render_epoch += 1
    pm = QPixmap(600, 400)
    pm.fill(QColor(250, 140, 20))
    imgs[2].resized_preview = pm
    assert sheet.update() and sheet.redrawn == [2]
    after = sheet.image()
    for i in (1, 3):
        c = lay.cell(i)
        for x in range(int(c.left()), int(c.right()), 7):
            for y in range(int(c.top()), int(c.bottom()), 7):
                assert before.pixelColor(x, y) == after.pixelColor(x, y)
    r = lay.image_rect(2)
    assert _near(_rgb(after, r.center().x(), r.center().y()), (250, 140, 20), 4)


def test_layout_settings_are_remembered(monkeypatch, _no_settings):
    _roll(monkeypatch, 2)
    sheet = _sheet()
    sheet.set_layout_mode("strips")
    sheet.set_strip_dir("down")
    sheet.set_gate("camera")
    sheet.set_holes_white(False)
    sheet.set_film_edge("brown")
    assert {k: _no_settings[k] for k in ("sheet/layout", "sheet/strip_dir", "sheet/gate",
                                         "sheet/holes_white", "sheet/film_edge")} == \
        {"sheet/layout": "strips", "sheet/strip_dir": "down", "sheet/gate": "camera",
         "sheet/holes_white": False, "sheet/film_edge": "brown"}
    sheet.set_layout_mode("nonsense")
    assert sheet.layout_mode == "grid" and isinstance(sheet.layout(), cs.SheetLayout)


def test_the_roll_format_reaches_the_sheet(monkeypatch):
    imgs = _roll(monkeypatch, 2, fmt=None)
    sheet = _sheet()
    assert sheet.layout().fmt == "35mm"
    sheet.set_roll_format("645")
    assert sheet.layout().fmt == "645"


def test_the_export_writes_the_strips(monkeypatch, tmp_path):
    _roll(monkeypatch, 3)
    sheet = _sheet()
    path = str(tmp_path / "sheet.jpg")
    assert sheet.save(path)
    assert QImage(path).size() == sheet.image().size()


# --- Layout… -----------------------------------------------------------------------

def test_layout_menu(monkeypatch):
    _roll(monkeypatch, 3, fmt="645")
    sheet = cs.ContactSheet()
    sheet.layout_mode, sheet.strip_dir, sheet.gate = "grid", "auto", "square"
    sheet.film_edge, sheet.holes_white = "black", True
    sheet.update(force=True)
    view = cs.ContactSheetView(sheet)
    _KEEP.append(view)
    m = view.layout_menu()
    texts = [a.text() for a in m.actions() if not a.isSeparator()]
    assert texts == ["Layout", "Grid", "Respect crop", "Film strips", "Strips run",
                     "By format (down for 6x4.5)", "Across", "Down", "Gate", "Square",
                     "Camera's own", "Film edge", "Black", "Dark grey", "Brown-black",
                     "Custom…", "White sprocket holes"]
    by = {a.text(): a for a in m.actions()}
    assert by["Grid"].box.isChecked()
    # the strips' rows are greyed in the grid, and Respect crop is the grid's
    assert not by["Square"].defaultWidget().isEnabled()
    assert by["Respect crop"].defaultWidget().isEnabled()
    by["Respect crop"].box.click()
    assert sheet.respect_crop is True
    by["Film strips"].box.click()
    assert sheet.layout_mode == "strips" and isinstance(sheet.layout(), cs.StripLayout)
    assert by["Square"].defaultWidget().isEnabled() and by["Down"].defaultWidget().isEnabled()
    assert not by["White sprocket holes"].defaultWidget().isEnabled()   # 120: none
    assert not by["Respect crop"].defaultWidget().isEnabled()
    by["Across"].box.click()
    assert sheet.layout().direction == "across"
    by["Camera's own"].box.click()
    assert sheet.gate == "camera"
    by["Dark grey"].box.click()
    assert sheet.film_edge == "grey"
    monkeypatch.setattr(view, "_choose_colour", lambda cur, title="": QColor("#224466"))
    by["Custom…"].trigger()
    assert sheet.film_edge == "#224466"
    again = {a.text(): a for a in view.layout_menu().actions()}
    assert again["Custom…"].isChecked() and not again["Black"].box.isChecked()


def test_layout_menu_on_35mm_and_text_menu_in_strips(monkeypatch):
    _roll(monkeypatch, 3)
    sheet = _sheet()
    view = cs.ContactSheetView(sheet)
    _KEEP.append(view)
    by = {a.text(): a for a in view.layout_menu().actions()}
    assert by["By format (across for 35mm)"].box.isChecked()
    holes = by["White sprocket holes"]
    assert holes.defaultWidget().isEnabled() and holes.box.isChecked()
    holes.box.click()
    assert sheet.holes_white is False
    # name / format / status have no place on a strip: greyed in Text…
    t = {a.text(): a for a in view.text_menu().actions()}
    assert t["Frame number"].defaultWidget().isEnabled() and t["Film"].defaultWidget().isEnabled()
    assert not t["File name"].defaultWidget().isEnabled()
    assert not t["Crop status"].defaultWidget().isEnabled()


def test_ticking_in_the_layout_menu_keeps_it_open(monkeypatch):
    from PySide6.QtTest import QTest
    _roll(monkeypatch, 3)
    sheet = _sheet(layout_mode="grid")
    view = cs.ContactSheetView(sheet)
    _KEEP.append(view)
    m = view.layout_menu()
    m.popup(QPoint(100, 100))
    QApplication.processEvents()
    by = {a.text(): a for a in m.actions()}
    for label in ("Film strips", "Down", "Camera's own", "White sprocket holes"):
        box = by[label].box
        QTest.mouseClick(box, Qt.LeftButton, pos=box.rect().center())
        QApplication.processEvents()
        assert m.isVisible(), label
    assert sheet.layout_mode == "strips" and sheet.strip_dir == "down"
    assert sheet.gate == "camera" and sheet.holes_white is False
    m.close()


def test_layout_button_sits_before_text(monkeypatch):
    _roll(monkeypatch, 2)
    view = cs.ContactSheetView(_sheet())
    _KEEP.append(view)
    bar = view.text_btn.parentWidget().layout()
    assert bar.itemAt(bar.indexOf(view.text_btn) - 1).widget() is view.layout_btn
    assert view.layout_btn.text() == "Layout…"


def test_35mm_perforations_are_ks_and_stand_across_the_film():
    """KS perforations, measured on the user's full-width scans (2026-11-20):
    narrower along the film than across it, 2.01 mm from the edge, leaving
    ~0.7 mm of rebate between a hole and the 24 mm picture."""
    assert cs.HOLE_ALONG < cs.HOLE_ACROSS
    assert abs(cs.HOLE_ALONG - 1.98) < 0.01 and abs(cs.HOLE_ACROSS - 2.79) < 0.01
    assert abs(cs.HOLE_EDGE - 2.01) < 0.01 and cs.HOLE_PITCH == 4.75
    from core import film_formats as ff
    g = ff.strip_geometry("35mm")
    inner = g.film - 2 * (cs.HOLE_EDGE + cs.HOLE_ACROSS)     # between the two rows
    assert abs(inner - 25.37) < 0.1 and inner > g.across
    assert 0.6 < g.rebate - cs.HOLE_EDGE - cs.HOLE_ACROSS < 0.8
