#!/usr/bin/env python3
"""Turning the crop box in Crop mode keeps it inside the picture, and only
the box's own grid shows (spec/crop-rotate-fit.md)."""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import straighten as st  # noqa: E402

_KEEP = []


@pytest.fixture
def cropping(tmp_path, monkeypatch):
    import cv2
    import numpy as np
    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    from ui.main_window import MainWindow
    w = MainWindow()
    _KEEP.append(w)
    p = tmp_path / "a.png"
    cv2.imwrite(str(p), np.full((300, 450, 3), 120, np.uint8))
    img = CCRImage(str(p))
    img.converted = True
    monkeypatch.setattr(ccr_backend, "images", [img])
    monkeypatch.setattr(ccr_backend, "file_paths", [img.file_path])
    monkeypatch.setattr(ccr_backend, "positive_mode", False)
    ip = w.image_preview
    img.crop_rect, img.crop_angle = (0.05, 0.05, 0.95, 0.95), 0.0
    ip.update_preview(0)
    assert ip.enter_crop_mode() is not False
    yield ip, img
    if ip.crop_mode:
        ip.cancel_crop_mode()


def _norm(ip):
    sel = ip._pending_crop_local
    w, h = ip.current_pixmap.width(), ip.current_pixmap.height()
    return (sel.left() / w, sel.top() / h, sel.right() / w, sel.bottom() / h), w, h


def _inside(ip):
    rect, w, h = _norm(ip)
    return all(-1e-6 <= x <= w + 1e-6 and -1e-6 <= y <= h + 1e-6
               for x, y in st.corners(rect, ip._pending_crop_angle, w, h))


def test_turning_shrinks_to_fit_and_turning_back_restores(cropping):
    ip, _img = cropping
    start = QRectF(ip._pending_crop_local)
    ip.set_pending_straighten(10.0)
    sel = ip._pending_crop_local
    assert _inside(ip)
    assert sel.width() < start.width()
    assert sel.center().x() == pytest.approx(start.center().x())
    assert sel.center().y() == pytest.approx(start.center().y())
    assert sel.width() / sel.height() == pytest.approx(start.width() / start.height())
    ip.set_pending_straighten(-6.0)                   # through and past 0
    assert _inside(ip)
    ip.set_pending_straighten(0.0)
    back = ip._pending_crop_local
    for a, b in ((back.x(), start.x()), (back.y(), start.y()),
                 (back.width(), start.width()), (back.height(), start.height())):
        assert a == pytest.approx(b, abs=1e-6)


def test_the_slider_and_the_knob_fit_too(cropping):
    ip, _img = cropping
    ip.rotation_slider.setValue(-800)                 # the red slider: 8 degrees
    assert ip._pending_crop_angle == pytest.approx(8.0) and _inside(ip)
    ip.set_pending_straighten(0.0)
    start = QRectF(ip._pending_crop_local)
    # the knob: drag round the box's centre from straight up
    c = start.center()
    ip._crop_drag = {"mode": "rotate", "start": QPointF(c.x(), c.y() - 100),
                     "box0": QRectF(start), "angle0": 0.0}
    import math
    a = math.radians(12.0)
    ip._drag_rotate_box(ip._crop_drag, QPointF(c.x() + 100 * math.sin(a),
                                               c.y() - 100 * math.cos(a)))
    assert ip._pending_crop_angle == pytest.approx(12.0, abs=0.01)
    assert _inside(ip) and ip._pending_crop_local.width() < start.width()
    # turning back in the same drag grows it back to the box it started from
    ip._drag_rotate_box(ip._crop_drag, QPointF(c.x(), c.y() - 100))
    assert ip._pending_crop_local.width() == pytest.approx(start.width(), abs=1e-6)
    ip._crop_drag = None


def test_a_moved_box_turns_from_where_it_is(cropping):
    ip, _img = cropping
    ip.set_pending_straighten(5.0)
    small = QRectF(ip._pending_crop_local)
    moved = QRectF(small)
    moved.moveCenter(QPointF(small.center().x() - 3, small.center().y()))
    ip._pending_crop_local = moved                     # a move: a new start
    ip.set_pending_straighten(0.0)
    assert ip._pending_crop_local.width() == pytest.approx(moved.width(), abs=1e-6)


def test_done_never_commits_a_corner_outside(cropping):
    ip, img = cropping
    ip.set_pending_straighten(15.0)
    sel = QRectF(ip._pending_crop_local)
    sel.moveLeft(0.0)                                  # pushed against the left edge
    ip._pending_crop_local = sel
    w, h = ip.current_pixmap.width(), ip.current_pixmap.height()
    ip.confirm_crop()
    assert img.crop_angle == pytest.approx(15.0)
    assert all(-1e-6 <= x <= w + 1e-6 and -1e-6 <= y <= h + 1e-6
               for x, y in st.corners(img.crop_rect, img.crop_angle, w, h))


def test_one_grid_in_crop_mode(cropping):
    ip, _img = cropping
    ip.show_grid = True                                # the slider is held
    assert not ip.view._grid_shown()                         # the box's own grid only
    ip.cancel_crop_mode()
    assert ip.view._grid_shown()                             # outside Crop mode it shows
    ip.show_grid = False
