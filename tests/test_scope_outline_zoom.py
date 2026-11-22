#!/usr/bin/env python3
"""The scopes outlined red while they read part of the picture, and the
vectorscope's wheel zoom (spec/scope-outline-and-zoom.md)."""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import scopes  # noqa: E402
from widgets.scopes_panel import ScopesPanel, VectorscopeWidget  # noqa: E402

_KEEP = []


def _pixel(cb, cr=0.0, y=128.0):
    """An RGB pixel (1x1 image) with that Cb/Cr."""
    r = y + 1.402 * cr
    g = y - 0.344136 * cb - 0.714136 * cr
    b = y + 1.772 * cb
    return np.array([[[r, g, b]]], np.float32)


def _peak_x(counts):
    ys, xs = np.nonzero(counts == counts.max())
    return int(xs[0]), int(ys[0])


def test_zoom_spreads_the_middle_and_drops_what_falls_off():
    mask = np.ones((1, 1), bool)
    plain = scopes.compute_vectorscope(_pixel(20), mask)
    near = scopes.compute_vectorscope(_pixel(20), mask, zoom=1.5)
    assert _peak_x(plain)[0] == int((20 + 128) * 0.5)
    assert _peak_x(near)[0] == int((30 + 128) * 0.5)          # 20 x 1.5
    far = scopes.compute_vectorscope(_pixel(100), mask, zoom=1.5)
    assert far.sum() == 0                                       # 150: off the face
    grey = scopes.compute_vectorscope(_pixel(0), mask, zoom=8.0)
    assert _peak_x(grey) == (64, 64)                            # grey stays in the middle


def test_zoomed_counts_are_spread_over_the_8_bit_steps_not_lost():
    rgb = np.random.default_rng(1).integers(100, 160, (40, 40, 3)).astype(np.uint8)
    mask = np.ones((40, 40), bool)
    z = scopes.compute_vectorscope(rgb, mask, zoom=4.0)
    assert z.sum() == pytest.approx(1600, rel=0.01)


def _wheel(w, notches):
    pos = QPointF(w.width() / 2, w.height() / 2)
    ev = QWheelEvent(pos, w.mapToGlobal(pos), QPoint(0, 0), QPoint(0, int(120 * notches)),
                     Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    w.wheelEvent(ev)
    return ev


def test_the_wheel_zooms_and_a_double_click_goes_back():
    w = VectorscopeWidget()
    w.resize(200, 200)
    seen = []
    w.zoom_changed.connect(seen.append)
    assert _wheel(w, 1).isAccepted()
    assert w.zoom == pytest.approx(1.25)
    _wheel(w, 3)
    assert w.zoom == pytest.approx(1.25 ** 4)
    _wheel(w, 40)
    assert w.zoom == VectorscopeWidget.ZOOM_MAX
    _wheel(w, -60)
    assert w.zoom == 1.0
    w.set_zoom(3.0)
    from PySide6.QtTest import QTest
    QTest.mouseDClick(w, Qt.LeftButton)
    assert w.zoom == 1.0 and seen[-1] == 1.0
    img = w.grab().toImage()          # paints at any zoom
    w.set_zoom(5.0)
    assert not w.grab().toImage().isNull() and not img.isNull()


def test_the_panel_recomputes_the_trace_at_the_new_zoom():
    sp = ScopesPanel()
    _KEEP.append(sp)
    rgb = np.zeros((10, 10, 3), np.uint8)
    rgb[...] = (150, 120, 100)
    mask = np.ones((10, 10), bool)
    sp.set_frame(rgb, mask)
    v0 = sp.vectorscope._trace_version
    sp.vectorscope.set_zoom(3.0)
    assert sp.vectorscope._trace_version == v0 + 1
    sp.set_frame(rgb, mask)                       # a new frame keeps the zoom
    assert sp.vectorscope.zoom == 3.0
    sp.clear()
    sp.vectorscope.set_zoom(1.0)                  # nothing to recompute: no error


def test_a_region_outlines_both_scopes_red():
    sp = ScopesPanel()
    _KEEP.append(sp)
    sp.resize(600, 240)
    sp._toggle_btn.setChecked(True)
    sp._on_toggle(True)
    sp.show()
    _app.processEvents()

    def edge_red(w):
        img = w.grab().toImage()
        c = img.pixelColor(img.width() // 2, 0)       # the 1 px border's row
        return c.red() > 180 and c.green() < 90

    sp.set_sampling_note("skin", region=True)
    assert sp.parade.region and sp.vectorscope.region
    assert edge_red(sp.parade) and edge_red(sp.vectorscope)
    sp.set_sampling_note("", region=False)
    assert not sp.parade.region and not edge_red(sp.vectorscope)
    sp.set_sampling_note("whatever")              # no region given: left as it is
    assert not sp.parade.region


@pytest.fixture
def ip(tmp_path, monkeypatch):
    import cv2
    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    from ui.main_window import MainWindow
    w = MainWindow()
    _KEEP.append(w)
    w.resize(1400, 900)
    w.show()
    a = np.zeros((300, 450, 3), np.uint8)
    a[:] = (200, 40, 20)
    p = tmp_path / "a.png"
    cv2.imwrite(str(p), a)
    img = CCRImage(str(p))
    img.converted = True
    monkeypatch.setattr(ccr_backend, "images", [img])
    monkeypatch.setattr(ccr_backend, "file_paths", [img.file_path])
    ip = w.image_preview
    ip.update_preview(0)
    ip.scopes_panel._toggle_btn.setChecked(True)
    ip.scopes_panel._on_toggle(True)
    _app.processEvents()
    yield ip
    ip.set_sample_mode(False)


def test_the_sample_circle_outlines_the_scopes(ip):
    sp = ip.scopes_panel
    ip._push_histogram(0)
    assert not sp.vectorscope.region
    ip.set_sample_mode(True)
    assert not sp.vectorscope.region              # no circle drawn yet: the whole picture
    br = ip._picture_scene_rect()
    c = br.center()
    s = 1.0 / ip._view_scale()
    ip.sample_press(c)
    ip.sample_move(QPointF(c.x() + 40 * s, c.y()))
    ip.sample_release(QPointF(c.x() + 40 * s, c.y()))
    ip._push_histogram(0)
    assert sp.vectorscope.region and sp.parade.region
    ip.set_sample_mode(False)
    ip._push_histogram(0)
    assert not sp.vectorscope.region
