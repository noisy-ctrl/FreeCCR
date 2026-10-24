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
