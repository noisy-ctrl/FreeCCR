#!/usr/bin/env python3
"""The scope sample circle (spec/scope-sample-circle.md)."""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

_KEEP = []


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
    a[:] = (200, 40, 20)                          # blue (BGR)
    cv2.circle(a, (225, 150), 40, (20, 30, 220), -1)   # a red disc in the middle
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


def _centre_scene(ip):
    br = ip._picture_scene_rect()
    return br, br.center()


def test_the_toggle_sits_after_false_colour_and_notes_the_scopes(ip):
    acts = ip.toolbar.actions()
    i = acts.index(ip.sample_action)
    assert acts[i - 1] is ip.fc_action
    assert ip.sample_action.toolTip().startswith("Sample circle")
    assert not ip.sample_action.icon().isNull()
    ip.sample_action.trigger()
    assert ip.sample_mode
    assert "sample circle (drag to draw)" in ip.scopes_panel._toggle_btn.text()
    ip.sample_action.trigger()
    assert not ip.sample_mode
    assert "sample" not in ip.scopes_panel._toggle_btn.text()


def test_draw_move_resize_and_click(ip):
    ip.set_sample_mode(True)
    br, c = _centre_scene(ip)
    s = 1.0 / ip._view_scale()
    ip.sample_press(c)
    ip.sample_move(QPointF(c.x() + 60 * s, c.y()))
    ip.sample_release(QPointF(c.x() + 60 * s, c.y()))
    (cx, cy), r = ip._sample_circle_scene()
    assert (cx, cy) == pytest.approx((c.x(), c.y()), abs=1e-6)
    assert r == pytest.approx(60 * s, rel=1e-6)
    assert ip.scopes_panel._toggle_btn.text().endswith("Scopes  ·  sample circle")
    # inside: move
    ip.sample_press(QPointF(c.x() + 10 * s, c.y()))
    ip.sample_release(QPointF(c.x() + 40 * s, c.y() + 20 * s))
    (cx, cy), r2 = ip._sample_circle_scene()
    assert (cx, cy) == pytest.approx((c.x() + 30 * s, c.y() + 20 * s), abs=1e-6)
    assert r2 == pytest.approx(r)
    # on the rim: resize
    ip.sample_press(QPointF(cx + r, cy))
    ip.sample_release(QPointF(cx + r + 30 * s, cy))
    assert ip._sample_circle_scene()[1] == pytest.approx(r + 30 * s, rel=1e-6)
    # a click outside: the centre goes there, the size stays
    ip.sample_press(QPointF(br.left() + 20 * s, br.top() + 20 * s))
    ip.sample_release(QPointF(br.left() + 21 * s, br.top() + 20 * s))
    (cx, cy), r3 = ip._sample_circle_scene()
    assert (cx, cy) == pytest.approx((br.left() + 20 * s, br.top() + 20 * s), abs=1e-6)
    assert r3 == pytest.approx(r + 30 * s, rel=1e-6)


def test_the_scopes_and_histogram_read_only_the_circle(ip):
    from core.ccr_backend import ccr_backend
    hist = {}
    panel = ip.parent().parent().sliders_panel
    orig = panel.set_histogram
    panel.set_histogram = lambda h: (hist.__setitem__("h", h), orig(h))
    ip.set_sample_mode(True)
    br, c = _centre_scene(ip)
    disc_r = 40 / 450 * br.width()                 # the red disc, in scene units
    ip.sample_press(c)
    ip.sample_release(QPointF(c.x() + disc_r * 0.8, c.y()))
    region = ip._reference_sample_region()
    rgb, mask = ip._capture_display_image(region=region, circle=True)
    assert not mask[0, 0] and not mask[-1, -1] and mask[mask.shape[0] // 2, mask.shape[1] // 2]
    px = rgb[mask].astype(int)
    assert (px[:, 0] > px[:, 2]).mean() > 0.99            # all red: no blue inside
    ip._push_histogram(0)
    h = np.asarray(hist["h"])
    assert h[2, 150:].sum() < 0.01 * h.sum() / 3          # little bright blue
    assert panel.histogram._caption == "Sample"
    # off: the whole picture again
    ip.set_sample_mode(False)
    assert ip._reference_sample_region() is None
    ip._push_histogram(0)
    h = np.asarray(hist["h"])
    assert h[2, 150:].sum() > 0.3 * h[2].sum()            # mostly blue picture
    # on again: the same circle
    ip.set_sample_mode(True)
    assert ip._sample_circle_scene()[0] == pytest.approx((c.x(), c.y()), abs=1e-6)
    _ = ccr_backend


def test_the_circle_is_drawn_over_the_picture_not_in_it(ip):
    ip.set_sample_mode(True)
    br, c = _centre_scene(ip)
    ip.sample_press(c)
    ip.sample_release(QPointF(c.x() + 30, c.y()))
    items = ip._sample_item
    assert items is not None and all(it.isVisible() for it in items)
    ip.update_preview(0)                       # a redraw keeps it
    assert ip._sample_item is not None and all(it.isVisible() for it in ip._sample_item)
    ip.set_sample_mode(False)
    assert not any(it.isVisible() for it in ip._sample_item)
