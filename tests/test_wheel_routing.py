#!/usr/bin/env python3
"""Scrolls go to the widget under the pointer, even when Qt has latched the
wheel onto another widget (spec/wheel-routing.md).

Qt's latch can't be produced from a test (it needs spontaneous, phased
events from a trackpad), so these hand the router the two events Qt would:
the window's event (where the pointer is) and the misrouted delivery."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import Qt, QPoint, QPointF  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import (QApplication, QWidget, QHBoxLayout, QVBoxLayout,  # noqa: E402
                               QScrollArea, QLabel)

_app = QApplication.instance() or QApplication([])

from ui.wheel_routing import WheelRouter, install_wheel_router  # noqa: E402

P = Qt.ScrollPhase


class Zoomer(QWidget):
    """Stands in for the picture: takes every wheel event (it zooms)."""
    def __init__(self):
        super().__init__()
        self.zooms = 0

    def wheelEvent(self, e):
        self.zooms += 1
        e.accept()


@pytest.fixture
def win():
    w = QWidget()
    lay = QHBoxLayout(w)
    area = QScrollArea()
    content = QWidget()
    v = QVBoxLayout(content)
    for i in range(60):
        v.addWidget(QLabel(f"row {i}"))
    area.setWidget(content)
    area.setWidgetResizable(True)
    zoom = Zoomer()
    lay.addWidget(area)
    lay.addWidget(zoom)
    w.setGeometry(0, 0, 600, 400)
    w.show()
    _app.processEvents()
    w.area, w.zoom = area, zoom
    yield w
    w.close()


def _ev(gpos, phase=P.NoScrollPhase, stamp=1000, dy=-120):
    e = QWheelEvent(QPointF(0, 0), QPointF(gpos), QPoint(0, 0), QPoint(0, dy),
                    Qt.NoButton, Qt.NoModifier, phase, False)
    e.setTimestamp(stamp)
    return e


def _over(widget, fy=0.5):
    vp = widget.viewport() if hasattr(widget, "viewport") else widget
    return vp.mapToGlobal(QPoint(vp.width() // 3, int(vp.height() * fy)))


def _send(router, win, gpos, to, phase=P.NoScrollPhase, stamp=1000):
    """What Qt does: the window's event, then a delivery to `to`. Returns
    True when the router took the delivery over."""
    router.eventFilter(win.windowHandle(), _ev(gpos, phase, stamp))
    return router.eventFilter(to, _ev(gpos, phase, stamp))


def test_a_latched_wheel_scrolls_what_is_under_the_pointer(win):
    r = WheelRouter()
    sb = win.area.verticalScrollBar()
    assert sb.maximum() > 0 and sb.value() == 0
    g = _over(win.area)
    assert _send(r, win, g, win.zoom) is True          # misrouted to the picture
    assert sb.value() > 0 and win.zoom.zooms == 0      # ... the list scrolled instead
    assert r.redirected == 1


def test_correct_routing_is_left_alone(win):
    r = WheelRouter()
    g = _over(win.area)
    under = QApplication.widgetAt(g)
    assert win.area.isAncestorOf(under)
    assert _send(r, win, g, under) is False            # Qt's own delivery
    assert _send(r, win, _over(win.zoom), win.zoom, stamp=1001) is False
    assert r.redirected == 0


def test_only_the_first_delivery_of_an_event_is_judged(win):
    """Qt's propagation and a scroll area's own forwarding to its scroll bar
    are later deliveries of the same event: never touched."""
    r = WheelRouter()
    g = _over(win.area)
    r.eventFilter(win.windowHandle(), _ev(g))
    assert r.eventFilter(QApplication.widgetAt(g), _ev(g)) is False
    assert r.eventFilter(win.area.verticalScrollBar(), _ev(g)) is False
    assert r.eventFilter(win.zoom, _ev(g, stamp=999)) is False      # another event


def test_a_trackpad_gesture_keeps_its_own_lock(win):
    """The rest of a phased sequence goes where it began, even if the pointer
    drifts onto the picture; after its end, the picture zooms normally."""
    r = WheelRouter()
    sb = win.area.verticalScrollBar()
    over_list, over_pic = _over(win.area), _over(win.zoom)
    assert _send(r, win, over_list, win.zoom, P.ScrollBegin, 1) is True
    v1 = sb.value()
    assert _send(r, win, over_pic, win.zoom, P.ScrollUpdate, 2) is True
    assert sb.value() > v1 and win.zoom.zooms == 0
    assert _send(r, win, over_pic, win.zoom, P.ScrollEnd, 3) is True
    assert _send(r, win, over_pic, win.zoom, P.NoScrollPhase, 4) is False   # free again


def test_install_is_once_per_app():
    a = install_wheel_router(_app)
    assert install_wheel_router(_app) is a


@pytest.mark.parametrize("side", ["right", "left"])
def test_in_the_real_window_a_misrouted_wheel_scrolls_the_panels(side, monkeypatch):
    """The real MainWindow: a wheel Qt hands to the picture while the pointer
    is over the right-hand panel or the thumbnails scrolls those and doesn't
    zoom. (The window is wider than the offscreen screen, so "under the
    pointer" is asked of the window itself.)"""
    import glob
    import numpy as np
    import cv2
    import ui.wheel_routing as wr
    from ui import theme
    theme.apply_theme(_app)
    from ui.main_window import MainWindow
    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    win = MainWindow()
    win.setGeometry(0, 0, 1600, 900)
    win.show()
    _app.processEvents()
    monkeypatch.setattr(wr, "widget_at", lambda g: win.childAt(win.mapFromGlobal(g)))
    if side == "left":
        import tempfile
        d = tempfile.mkdtemp()
        for i in range(14):
            cv2.imwrite(os.path.join(d, f"f{i:02d}.png"),
                        (np.random.default_rng(i).random((90, 120, 3)) * 255).astype(np.uint8))
        monkeypatch.setattr(ccr_backend, "images",
                            [CCRImage(f) for f in sorted(glob.glob(os.path.join(d, "*.png")))])
        win.thumbnail_list.load_thumbnails()
        _app.processEvents()
        area = win.thumbnail_list.thumbnail_list
    else:
        area = win.sliders_panel.findChild(QScrollArea)
    r = WheelRouter()
    _app.installEventFilter(r)
    try:
        zoomed = []
        monkeypatch.setattr(win.image_preview, "zoom_by", lambda *a, **k: zoomed.append(a))
        sb = area.verticalScrollBar()
        sb.setValue(0)
        assert sb.maximum() > 0
        g = area.viewport().mapToGlobal(QPoint(6, area.viewport().height() // 2))
        r.eventFilter(win.windowHandle(), _ev(g, stamp=5000))
        QApplication.sendEvent(win.image_preview.view.viewport(), _ev(g, stamp=5000))
        assert sb.value() > 0 and not zoomed
        assert r.redirected == 1
    finally:
        _app.removeEventFilter(r)
