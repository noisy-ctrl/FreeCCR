#!/usr/bin/env python3
"""Hovering the histogram's clip corners shows what is clipping on the
picture (spec/histogram-clip-overlay.md)."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core import clip_overlay as co  # noqa: E402


def test_layer_is_solid_where_all_channels_clip_half_where_some_do():
    px = np.array([[[255, 255, 255], [255, 120, 30], [254, 254, 254],
                    [0, 0, 0], [0, 40, 90], [1, 1, 1]]], np.uint8)
    w = co.clip_rgba(px, "white")
    assert tuple(w[0, 0]) == (*co.COLOURS["white"], co.ALPHA_ALL)
    assert w[0, 1, 3] == co.ALPHA_SOME
    assert w[0, 2, 3] == 0 and w[0, 3, 3] == 0
    b = co.clip_rgba(px, "black")
    assert tuple(b[0, 3]) == (*co.COLOURS["black"], co.ALPHA_ALL)
    assert b[0, 4, 3] == co.ALPHA_SOME and b[0, 5, 3] == 0 and b[0, 0, 3] == 0
    # red for white, blue for black
    assert co.COLOURS["white"][0] > 200 and co.COLOURS["black"][2] > 200


def test_a_lone_clipped_pixel_survives_the_reduction():
    px = np.full((5000, 3000, 3), 128, np.uint8)
    px[4321, 1234] = 255
    layer = co.clip_rgba(px, "white")
    assert max(layer.shape[:2]) <= co.MAX_LONG
    assert (layer[..., 3] > 0).sum() == 1
    k = -(-5000 // co.MAX_LONG)
    assert layer[4321 // k, 1234 // k, 3] == co.ALPHA_ALL


def test_fractions():
    px = np.zeros((10, 10, 3), np.uint8)
    px[0] = 255
    px[1, :, 0] = 255
    assert co.clipped_fraction(px, "white") == (0.1, 0.2)


# --------------------------------------------------------------------------- #
# the widget
# --------------------------------------------------------------------------- #

@pytest.fixture
def hist():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.histogram_widget import HistogramWidget
    h = HistogramWidget()
    h.resize(300, 150)
    data = np.ones((3, 256), np.float32)
    data[:, 0] = 40
    data[2, 255] = 25
    h.set_data(data)
    return h


def test_hovering_each_corner_says_which(hist):
    from PySide6.QtCore import QPointF
    seen = []
    hist.clip_hover.connect(seen.append)
    assert hist.clip_corner_at(QPointF(10, 140)) == "black"
    assert hist.clip_corner_at(QPointF(290, 140)) == "white"
    assert hist.clip_corner_at(QPointF(150, 140)) == ""
    assert hist.clip_corner_at(QPointF(10, 20)) == ""
    hist._set_hover("black")
    assert "Black clipping" in hist.toolTip() and "blue" in hist.toolTip()
    hist._set_hover("white")
    assert "B 8.5 %" in hist.toolTip() and "R 0.3 %" in hist.toolTip()   # 25 / 295, 1 / 295
    hist._set_hover("")
    assert seen == ["black", "white", ""] and hist.toolTip() == ""


def test_mouse_moves_and_leaving_drive_it(hist):
    from PySide6.QtCore import QPointF, Qt, QEvent
    from PySide6.QtGui import QMouseEvent
    seen = []
    hist.clip_hover.connect(seen.append)

    def move(x, y):
        ev = QMouseEvent(QEvent.MouseMove, QPointF(x, y), QPointF(x, y),
                         Qt.NoButton, Qt.NoButton, Qt.NoModifier)
        hist.mouseMoveEvent(ev)
    move(8, 142)
    move(9, 141)                     # no repeat while it stays
    move(150, 80)
    move(292, 142)
    hist.leaveEvent(QEvent(QEvent.Leave))
    assert seen == ["black", "", "white", ""]


def test_no_data_no_corners(hist):
    from PySide6.QtCore import QPointF
    seen = []
    hist.clip_hover.connect(seen.append)
    hist._set_hover("white")
    hist.set_data(None)
    assert seen == ["white", ""]
    assert hist.clip_corner_at(QPointF(290, 140)) == ""


def test_paints_with_the_hover_outline(hist):
    from PySide6.QtGui import QPixmap
    hist._set_hover("white")
    pm = QPixmap(hist.size())
    hist.render(pm)                  # no exceptions, outline drawn
    assert not pm.isNull()


# --------------------------------------------------------------------------- #
# canvas + panel
# --------------------------------------------------------------------------- #

def test_canvas_shows_red_where_white_and_blue_where_black(tmp_path, monkeypatch):
    from test_false_colour import _canvas, _HOSTS  # noqa: F401
    from PySide6.QtGui import QImage
    ip, _img = _canvas(tmp_path, monkeypatch)      # a 0..255 ramp, left to right

    def layer():
        q = ip._clip_item.pixmap().toImage().convertToFormat(QImage.Format_RGBA8888)
        w, h = q.width(), q.height()
        return np.frombuffer(q.constBits(), np.uint8).reshape(
            h, q.bytesPerLine())[:, :w * 4].reshape(h, w, 4)
    ip.set_clip_overlay("white")
    assert ip._clip_item is not None and ip._clip_item.isVisible()
    px = layer()
    assert px[45, -1, 3] > 200 and px[45, -1, 0] > 200            # the white end, red
    assert px[45, : px.shape[1] // 2, 3].max() == 0               # nothing else
    ip.set_clip_overlay("black")
    px = layer()
    assert px[45, 0, 3] > 200 and px[45, 0, 2] > 200              # the black end, blue
    assert px[45, px.shape[1] // 2:, 3].max() == 0
    ip.set_clip_overlay(None)
    assert not ip._clip_item.isVisible()


def test_panel_forwards_the_hover(monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel
    panel = SlidersPanel()
    calls = []
    ip = type("IP", (), {"set_clip_overlay": lambda s, k: calls.append(k)})()
    monkeypatch.setattr(panel, "_golden_preview", lambda: ip)
    panel.histogram.clip_hover.emit("white")
    panel.histogram.clip_hover.emit("")
    assert calls == ["white", None]
