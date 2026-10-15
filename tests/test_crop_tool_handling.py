#!/usr/bin/env python3
"""Crop tool handling (spec/crop-tool-handling.md): the whole inside of the box
moves it, edges are grabbed anywhere along their length, and a resize keeps
the orientation the box already has instead of the one the panel remembered."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
sys.path.insert(0, HERE)

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import QPointF, QRectF  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

import test_crop_panel as tcp  # noqa: E402

_KEEP = []


def _preview(box=QRectF(100, 100, 200, 100)):
    ip, host = tcp._make_preview()          # 400 x 300, view scale 1
    _KEEP.append((ip, host))
    ip._pending_crop_local = QRectF(box)
    ip._pending_crop_angle = 0.0
    return ip


def _with_panel(key, landscape):
    from widgets.crop_panel import CropPanel
    ip, host = tcp._make_preview()
    panel = CropPanel(host, ip)
    ip.set_crop_panel(panel)
    _KEEP.append((ip, host, panel))
    panel.aspect_combo.setCurrentIndex(panel.aspect_combo.findData(key))
    (panel.landscape_radio if landscape else panel.portrait_radio).setChecked(True)
    return ip, panel


def _drag(ip, a, b):
    t = ip._base_transform()
    ip.begin_crop_drag(t.map(QPointF(*a)))
    ip.update_crop_drag(t.map(QPointF(*b)))
    ip.end_crop_drag(t.map(QPointF(*b)))


# --- what is under the pointer ------------------------------------------------------

@pytest.mark.parametrize("pt, want", [
    ((150, 130), "move"),          # inside, well away from the centre glyph
    ((280, 180), "move"),
    ((110, 150), "edge-l"),        # just inside the left edge
    ((90, 150), "edge-l"),         # just outside it
    ((100, 125), "edge-l"),        # near the end of the edge, not the midpoint
    ((270, 100), "edge-t"),
    ((300, 190), "corner-br"),
    ((95, 95), "corner-tl"),
    ((200, 100 - 28), "rotate"),
    ((50, 150), None),             # outside: a new box
    ((200, 260), None),
])
def test_hit_zones(pt, want):
    ip = _preview()
    assert ip._crop_handle_at(QPointF(*pt)) == want


def test_a_small_box_still_moves_from_its_middle():
    ip = _preview(QRectF(190, 140, 20, 20))
    assert ip._crop_handle_at(QPointF(200, 150)) == "move"
    assert ip._crop_handle_at(QPointF(185, 150)) == "edge-l"     # from outside


def test_dragging_inside_moves_the_box_instead_of_drawing_a_new_one():
    ip = _preview()
    _drag(ip, (130, 120), (160, 140))
    r = ip._pending_crop_local
    assert (r.x(), r.y(), r.width(), r.height()) == pytest.approx((130, 120, 200, 100))


# --- orientation follows the box ----------------------------------------------------

def test_resizing_keeps_the_boxs_orientation():
    ip, panel = _with_panel("35mm", landscape=False)     # remembered: portrait
    ip._pending_crop_local = QRectF(50, 50, 300, 200)    # but this box is landscape
    _drag(ip, (350, 250), (330, 240))                    # pull the corner in a bit
    r = ip._pending_crop_local
    assert panel.landscape_radio.isChecked()
    assert r.width() / r.height() == pytest.approx(1.5, abs=1e-3)
    assert r.width() > r.height()


def test_entering_crop_points_the_toggle_the_way_the_box_points():
    ip, panel = _with_panel("645", landscape=True)
    ip._pending_crop_local = QRectF(100, 20, 150, 200)   # portrait box
    ip._crop_box_is_seed = False
    panel.bind_image()
    assert panel.portrait_radio.isChecked()
    assert ip._pending_crop_local == QRectF(100, 20, 150, 200)   # not reshaped


def test_on_screen_orientation_counts_after_a_quarter_turn():
    _ip, panel = _with_panel("35mm", landscape=True)
    assert panel.match_box_orientation(200, 300, rotation=90) is False   # landscape on screen
    assert panel.match_box_orientation(300, 200, rotation=90) is True
    assert panel.portrait_radio.isChecked()


def test_square_boxes_and_orientation_free_presets_leave_the_toggle():
    _ip, panel = _with_panel("35mm", landscape=True)
    assert panel.match_box_orientation(200, 201) is False
    assert panel.landscape_radio.isChecked()
    _ip, panel = _with_panel("free", landscape=True)
    assert panel.match_box_orientation(100, 300) is False
