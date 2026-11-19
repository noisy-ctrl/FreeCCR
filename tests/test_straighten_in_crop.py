#!/usr/bin/env python3
"""The rotate slider straightens inside the crop; crop mode's red slider and
grid (spec/straighten-in-crop.md)."""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import straighten as st  # noqa: E402

_KEEP = []


def _inside(rect, angle, w, h, tol=1e-6):
    return all(-tol <= x <= w + tol and -tol <= y <= h + tol
               for x, y in st.corners(rect, angle, w, h))


@pytest.mark.parametrize("angle", [0.0, 0.7, -3.0, 12.0, -44.0])
def test_fit_box_keeps_the_box_inside_centred_and_its_shape(angle):
    w, h = 1080, 720
    base = (0.05, 0.1, 0.9, 0.95)
    r = st.fit_box(base, angle, w, h)
    assert _inside(r, angle, w, h)
    c0 = ((base[0] + base[2]) / 2, (base[1] + base[3]) / 2)
    assert ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2) == pytest.approx(c0)
    shape = lambda q: (q[2] - q[0]) * w / ((q[3] - q[1]) * h)      # noqa: E731
    assert shape(r) == pytest.approx(shape(base))
    if r == pytest.approx(base):
        assert _inside(base, angle, w, h)          # it fitted as it was
    else:
        # shrunk only as far as needed: a corner touches an edge
        assert (r[2] - r[0]) < (base[2] - base[0])
        xs = [x for x, _y in st.corners(r, angle, w, h)]
        ys = [y for _x, y in st.corners(r, angle, w, h)]
        gaps = [min(xs), w - max(xs), min(ys), h - max(ys)]
        assert min(gaps) == pytest.approx(0.0, abs=1e-6)


def test_a_small_box_in_the_middle_is_left_its_size():
    r = st.fit_box((0.4, 0.4, 0.6, 0.6), 10.0, 1000, 1000)
    assert r == pytest.approx((0.4, 0.4, 0.6, 0.6))


# --------------------------------------------------------------------------- #
# the slider
# --------------------------------------------------------------------------- #
@pytest.fixture
def frame(tmp_path, monkeypatch):
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
    ip.update_preview(0)
    yield ip, img


def _settle(ip):
    ip._straighten_refresh.stop()
    ip._fine_rot_burst_timer.stop()
    ip._end_fine_rot_burst(show=True)


def test_the_slider_turns_the_picture_inside_its_crop(frame):
    ip, img = frame
    w, h = img.resized_preview.width(), img.resized_preview.height()
    img.crop_rect, img.crop_angle = (0.1, 0.1, 0.9, 0.9), 0.0
    ip.update_preview(0)
    assert ip.rotation_slider.value() == 0 and ip.rotation_slider.isEnabled()
    undo0 = len(img.undo_stack)
    for v in (50, 150, 300):
        ip.rotation_slider.setValue(v)
    assert img.crop_angle == pytest.approx(-3.0)
    assert img.fine_rotation_angle == 0
    assert _inside(img.crop_rect, img.crop_angle, w, h)
    assert len(img.undo_stack) == undo0 + 1                  # one step for the drag
    _settle(ip)
    ip.rotation_slider.setValue(0)                           # back: the box comes back
    assert img.crop_rect == pytest.approx((0.1, 0.1, 0.9, 0.9))
    assert img.crop_angle == 0.0
    _settle(ip)


def test_no_crop_crops_and_zero_clears_it_again(frame):
    ip, img = frame
    w, h = img.resized_preview.width(), img.resized_preview.height()
    assert img.crop_rect is None
    ip.rotation_slider.setValue(-200)
    assert img.crop_angle == pytest.approx(2.0)
    assert img.crop_rect is not None and _inside(img.crop_rect, 2.0, w, h)
    ip.rotation_slider.setValue(0)
    assert img.crop_rect is None and img.crop_angle == 0.0
    _settle(ip)


def test_an_old_fine_rotation_is_folded_in_and_switching_never_writes(frame):
    ip, img = frame
    img.fine_rotation_angle = 120
    img.crop_rect, img.crop_angle = (0.1, 0.1, 0.9, 0.9), 1.0
    ip.update_preview(0)
    assert ip.rotation_slider.value() == 120 - 100          # fine − crop × 100
    assert img.fine_rotation_angle == 120 and img.crop_angle == 1.0   # untouched
    ip.rotation_slider.setValue(40)
    assert img.fine_rotation_angle == 0 and img.crop_angle == pytest.approx(-0.4)
    _settle(ip)


def test_an_unconverted_negative_keeps_the_fine_rotation(frame):
    ip, img = frame
    img.converted = False
    try:
        ip.update_preview(0)
        ip.rotation_slider.setValue(250)
        assert img.fine_rotation_angle == 250 and img.crop_rect is None
    finally:
        ip._fine_rot_burst_timer.stop()


def test_crop_mode_slider_is_red_moves_the_box_and_follows_it(frame):
    ip, img = frame
    img.crop_rect, img.crop_angle = (0.1, 0.1, 0.9, 0.9), 0.0
    ip.update_preview(0)
    assert ip.enter_crop_mode() is not False
    sl = ip.rotation_slider
    assert ip.crop_mode and sl.isEnabled() and "QSlider::groove" in sl.styleSheet()
    sl.setValue(-250)
    assert ip._pending_crop_angle == pytest.approx(2.5)
    ip.set_pending_straighten(-1.25)                          # the panel / knob
    assert sl.value() == 125
    grid = [it for it in ip._crop_handle_items if getattr(it, "_crop_grid", False)]
    assert len(grid) == 22                                    # 11 + 11 lines in the box
    ip.cancel_crop_mode()
    assert not ip.crop_mode and sl.styleSheet() == "" and sl.value() == 0
    assert img.crop_angle == 0.0                              # Cancel changed nothing
