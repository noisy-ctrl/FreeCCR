#!/usr/bin/env python3
"""The Live Capture Monitor's canvas (spec/live-capture-monitor.md §3.3,
§5.4): zoom about the pointer, pan, the frame guide's move / resize /
rotate with a film format, the one-shot modes, and painting."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import Qt, QPointF, QPoint  # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import live_feed as lf  # noqa: E402
from widgets.live_canvas import LiveCanvas, clean_guide  # noqa: E402

FRAME = np.full((1080, 1920, 3), 100, np.uint8)


def _canvas():
    c = LiveCanvas()
    c.resize(960, 540)
    c.show()
    _app.processEvents()
    proc = lf.Processor()

    def render():
        w, h = c.device_size()
        c.set_result(proc.process(FRAME, lf.ViewParams(canvas_w=w, canvas_h=h, scale=c.scale,
                                                       cx=c.cx, cy=c.cy, denoise=False)))
    c.view_changed.connect(render)
    render()
    return c, render


def _mouse(kind, pos, button=Qt.LeftButton):
    p = QPointF(*pos)
    buttons = Qt.NoButton if kind == QMouseEvent.Type.MouseButtonRelease else button
    if kind == QMouseEvent.Type.MouseMove:
        buttons = button
    return QMouseEvent(kind, p, p, button, buttons, Qt.NoModifier)


def _press(c, pos):
    c.mousePressEvent(_mouse(QMouseEvent.Type.MouseButtonPress, pos))


def _move(c, pos):
    c.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, pos))


def _release(c, pos):
    c.mouseReleaseEvent(_mouse(QMouseEvent.Type.MouseButtonRelease, pos))


def test_wheel_zooms_about_the_pointer_and_back_to_fit():
    c, render = _canvas()
    g = c.result.geometry
    at = (300.0, 200.0)
    before = g.to_sensor(at[0] * c.dpr(), at[1] * c.dpr())
    ev = QWheelEvent(QPointF(*at), QPointF(*at), QPoint(0, 0), QPoint(0, 240),
                     Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
    c.wheelEvent(ev)
    assert not c.is_fit()
    g2 = c.result.geometry
    after = g2.to_sensor(at[0] * c.dpr(), at[1] * c.dpr())
    assert abs(after[0] - before[0]) < 2 and abs(after[1] - before[1]) < 2
    c.zoom("fit")
    assert c.is_fit() and c.result.geometry.fit


def test_drag_pans_when_zoomed():
    c, render = _canvas()
    c.zoom("2:1")
    g = c.result.geometry
    x0 = g.x0
    _press(c, (400, 300))
    _move(c, (300, 300))           # drag left: the view moves right
    _release(c, (300, 300))
    assert c.result.geometry.x0 > x0


def test_guide_moves_resizes_to_its_format_and_rotates():
    c, render = _canvas()
    c.set_guide({"cx": 0.5, "cy": 0.5, "w": 0.5, "h": 0.5, "format": "35mm"}, on=True)
    seen = []
    c.guide_changed.connect(seen.append)
    g = c.result.geometry
    centre, w, h = c._guide_frame(g)
    _press(c, (centre.x(), centre.y()))
    _move(c, (centre.x() + 48, centre.y()))
    _release(c, (centre.x() + 48, centre.y()))
    assert seen and seen[-1]["cx"] == pytest.approx(0.5 + 48 * c.dpr() / g.sx / 1920, abs=1e-3)
    # resize from the bottom-right corner: the format holds (3:2 in pixels)
    centre, w, h = c._guide_frame(g)
    corner = (centre.x() + w / 2, centre.y() + h / 2)
    _press(c, corner)
    _move(c, (corner[0] + 40, corner[1] + 5))
    _release(c, (corner[0] + 40, corner[1] + 5))
    gd = seen[-1]
    assert (gd["w"] * 1920) / (gd["h"] * 1080) == pytest.approx(1.5, rel=1e-3)
    # rotate from just outside a corner
    centre, w, h = c._guide_frame(g)
    out = (centre.x() + w / 2 + 15, centre.y() - h / 2 - 15)
    _press(c, out)
    _move(c, (out[0] + 30, out[1] + 40))
    _release(c, (out[0] + 30, out[1] + 40))
    assert seen[-1]["angle"] != 0.0 and abs(seen[-1]["angle"]) <= 45


def test_modes_pick_area_and_line():
    c, render = _canvas()
    got = {}
    c.picked.connect(lambda x, y: got.__setitem__("pick", (x, y)))
    c.area_drawn.connect(lambda a: got.__setitem__("area", a))
    c.line_placed.connect(lambda x: got.__setitem__("line", x))
    g = c.result.geometry
    c.set_mode("pick")
    _press(c, (480, 270))
    sx, sy = got["pick"]
    assert sx == pytest.approx(960, abs=3) and sy == pytest.approx(540, abs=3)
    assert c.mode is None
    c.set_mode("area")
    _press(c, (100, 100))
    _move(c, (300, 200))
    _release(c, (300, 200))
    x0, y0, x1, y1 = got["area"]
    assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    c.set_mode("line")
    _press(c, (240, 100))
    assert got["line"] == pytest.approx(0.25, abs=0.01)
    c.set_mode("pick")
    assert c.cancel_mode() and c.mode is None


def test_paints_picture_overlays_and_message():
    c, render = _canvas()
    c.set_guide({}, on=True)
    c.align_on = True
    c.sharp_on = True
    c.sharp_area = (0.4, 0.4, 0.6, 0.6)
    c.result.sharp = (5.0, 10.0)
    img = c.grab().toImage()
    assert not img.isNull()
    c.set_message("No camera found")
    assert c.result is None
    img = c.grab().toImage()
    assert not img.isNull()


def test_guide_values_are_kept_in_range():
    g = clean_guide({"cx": 3, "cy": -1, "w": 0, "h": "x", "angle": 90, "format": 5})
    assert g["cx"] == 1.0 and g["cy"] == 0.0 and g["w"] > 0 and g["angle"] == 45.0
    assert g["format"] is None


def _readout_centre(c):
    c.sharp_on = True
    c.result.sharp = (5.0, 10.0)
    c.grab()                                  # paints, which places the readout
    r = c._readout_rect
    assert r is not None
    return (r.center().x(), r.center().y())


def test_a_click_on_the_sharpness_readout_asks_for_a_new_best():
    """A left click on the meter is the menu's Reset Best; it wins over the
    frame guide and a pan (it is drawn on top), not over a one-shot mode."""
    c, render = _canvas()
    hits = []
    c.meter_clicked.connect(lambda: hits.append(1))
    at = _readout_centre(c)
    _press(c, at)
    _release(c, at)
    assert hits == [1] and c._drag is None
    # with the frame guide placed under it, the click still goes to the meter
    gx = at[0] / c.width()
    gy = at[1] / c.height()
    c.set_guide({"cx": gx, "cy": gy, "w": 0.3, "h": 0.3}, on=True)
    c.grab()
    _press(c, at)
    _release(c, at)
    assert hits == [1, 1] and c.guide["cx"] == pytest.approx(gx)
    # zoomed in: not a pan
    c.scale = 2.0
    c.view_changed.emit()
    assert _readout_centre(c) == at           # the readout stays put
    _press(c, at)
    assert hits == [1, 1, 1] and c._drag is None
    _release(c, at)
    # elsewhere: nothing
    _press(c, (c.width() - 40, 40))
    _release(c, (c.width() - 40, 40))
    assert len(hits) == 3
    # the right button: nothing (that's the toolbar's menu)
    c.mousePressEvent(_mouse(QMouseEvent.Type.MouseButtonPress, at, Qt.RightButton))
    assert len(hits) == 3
    # drawing an area may start over the meter
    c.set_mode("area")
    _press(c, at)
    assert len(hits) == 3 and c._rubber is not None
    c.cancel_mode()
    c._rubber = None


def test_a_hidden_meter_takes_no_clicks():
    c, render = _canvas()
    hits = []
    c.meter_clicked.connect(lambda: hits.append(1))
    at = _readout_centre(c)
    c.sharp_on = False
    c.grab()
    assert c._readout_rect is None
    _press(c, at)
    _release(c, at)
    assert hits == []


def test_the_pointer_shows_the_meter_is_clickable():
    c, render = _canvas()
    at = _readout_centre(c)
    c.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, at, Qt.NoButton))
    assert c.cursor().shape() == Qt.PointingHandCursor
    c.mouseMoveEvent(_mouse(QMouseEvent.Type.MouseMove, (c.width() - 40, 40), Qt.NoButton))
    assert c.cursor().shape() == Qt.ArrowCursor


# --------------------------------------------------------------------------- #
# the film base patch (spec/film-base-exposure.md, revision 5)
# --------------------------------------------------------------------------- #
from core import base_exposure as be  # noqa: E402


def _white_ring(img, r):
    """How many pixels on the outline of logical rect `r` are near-white."""
    hits, total = 0, 0
    for x in range(int(r.left()) + 1, int(r.right())):
        for y in (int(round(r.top())), int(round(r.bottom()))):
            total += 1
            best = max(min(img.pixelColor(x, y + d).red(), img.pixelColor(x, y + d).green(),
                           img.pixelColor(x, y + d).blue()) for d in (-1, 0, 1))
            hits += best > 200
    return hits / max(1, total)


def _brightest(img, x, y):
    cols = [img.pixelColor(x, y + d) for d in (-1, 0, 1)]
    return max(cols, key=lambda c: c.red() + c.green())


def test_the_pick_box_is_the_patch_a_click_reads():
    c, render = _canvas()
    g = c.result.geometry
    assert c.box_at(QPointF(300, 200)) is None                # not picking
    c.set_mode("pick")
    assert c.box_at(QPointF(300, 200)) is None                # a plain pick: no box
    c.set_mode("pick", box=be.PATCH_SIDE, hint="Click the film base")
    sx, sy = g.to_sensor(300 * c.dpr(), 200 * c.dpr())
    want = be.patch_around(sx / g.full_w, sy / g.full_h, g.full_w, g.full_h)
    assert c.box_at(QPointF(300, 200)) == pytest.approx(want)
    # at the edge it stops where the patch stops
    edge = c.box_at(QPointF(1, 1))
    assert edge[0] == pytest.approx(0, abs=1e-9) and edge[1] == pytest.approx(0, abs=1e-9)
    assert (edge[2] - edge[0]) * g.full_w == pytest.approx(be.PATCH_SIDE * 1080)
    assert c.box_at(QPointF(-5, 200)) is None or g.ox > 0     # off the picture: none
    # the pointer draws it
    _move(c, (300, 200))
    r = c._frac_rect(g, want)
    assert 10 < r.width() < 30                                # 32 sensor px at half size
    assert _white_ring(c.grab().toImage(), r) > 0.8
    # a click picks the centre, and the mode (and box) end
    got = []
    c.picked.connect(lambda x, y: got.append((x, y)))
    _press(c, (300, 200))
    assert got and c.mode is None and c.pick_box is None and c._hover is None
    assert _white_ring(c.grab().toImage(), r) < 0.2


def test_the_measured_patch_is_outlined_until_cleared():
    c, render = _canvas()
    g = c.result.geometry
    rect = (0.45, 0.02, 0.48, 0.07)
    c.set_mark(rect, "Measuring the base here")
    r = c._frac_rect(g, rect)
    img = c.grab().toImage()
    col = _brightest(img, int(r.center().x()), int(round(r.top())))
    assert col.red() > 180 and col.green() > 150 and col.blue() < 140     # amber
    render()                                                    # a new frame keeps it
    assert c.mark is not None
    c.set_mark(None)
    col = _brightest(c.grab().toImage(), int(r.center().x()), int(round(r.top())))
    assert abs(col.red() - col.blue()) < 10                     # just the grey picture


def test_the_readout_says_focus_and_a_new_frame():
    c, render = _canvas()
    c.sharp_on = True
    r = c.result
    r.sharp = (0.95, 1.0)
    img1 = c.grab().toImage()
    c.flash_readout_note("New frame: best reset", seconds=5)
    assert c.readout_note() == "New frame: best reset"
    assert c.grab().toImage() != img1
    c.flash_readout_note("x", seconds=-1)
    assert c.readout_note() is None
