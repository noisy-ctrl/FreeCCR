#!/usr/bin/env python3
"""The camera's magnifier over USB (spec/live-magnifier.md): a Fujifilm's
ThroughImageZoom (d01b) as Whole / 2.5x / 4x / 8x / Max."""

import os
import sys
import types

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import camera_backends as cb  # noqa: E402
from test_capture_routes import fake_gp, _Widget, GPhoto2Error, Clock  # noqa: E402

FUJI = (("Fuji Fujifilm GFX100", "usb:001,004"),)


def _fuji(tmp_path, zoom="1"):
    cfg = _Widget({"shutterspeed": _Widget(value="1/8"), "iso": _Widget(value="100"),
                   "d01b": _Widget(value=zoom)})
    gp = fake_gp(cameras=FUJI, config=cfg)
    st = gp.state
    base = gp.Camera

    class Camera(base):
        def init(self):
            super().init()
            st.in_live = False                     # a fresh connection ends live view

        def get_single_config(self, key):
            return st.config.get_child_by_name(key)

        def set_single_config(self, key, w):
            if st.in_live:                         # every setting refused in live view
                raise GPhoto2Error(gp.GP_ERROR_CAMERA_BUSY)
            st.config.children[key].value = w.value
    gp.Camera = Camera
    b = cb.Gphoto2Backend(gp_module=gp, clock=Clock(), sleep=lambda s: None)
    b.list_cameras()
    b.open("usb:001,004", str(tmp_path))
    return b, gp


def test_levels_only_on_a_fujifilm_with_the_setting(tmp_path):
    b, gp = _fuji(tmp_path)
    assert b.zoom_levels() == ["1", "2.5", "4", "8", "16"]
    canon = cb.Gphoto2Backend(gp_module=fake_gp(), clock=Clock())
    canon.list_cameras()
    canon.open("usb:001,004", str(tmp_path))
    assert canon.zoom_levels() == ["1", "5", "10"]            # Canon's eoszoom, as before
    gp2 = fake_gp(cameras=FUJI)                               # a Fujifilm without d01b
    nod = cb.Gphoto2Backend(gp_module=gp2, clock=Clock())
    nod.list_cameras()
    nod.open("usb:001,004", str(tmp_path))
    assert nod.zoom_levels() == [] and nod.zoom_now() is None


def test_setting_in_live_view_reconnects_once_and_reads_back(tmp_path):
    b, gp = _fuji(tmp_path)
    b.preview()                                               # live view running
    assert gp.state.in_live
    n = b.reconnects
    assert b.set_zoom("8") == "8"
    assert gp.state.config.children["d01b"].value == "5"
    assert b.reconnects == n + 1
    assert b.set_zoom("2.5") == "2.5"                         # not in live view: direct
    assert b.reconnects == n + 1
    assert b.set_zoom("3x") is None


def test_the_cameras_own_values_map_to_labels(tmp_path):
    assert [cb.Gphoto2Backend.fuji_zoom_label(v) for v in
            ("1", "2", "3", "4", "5", "6", "9", "11", "x")] == \
        ["1", "2.5", "2.5", "4", "8", "16", "16", "16", None]
    b, gp = _fuji(tmp_path, zoom="9")
    assert b.zoom_now() == "16"


def test_the_worker_reports_sets_and_the_cameras_own_changes(tmp_path):
    from core.usb_camera import UsbCamera
    from test_capture_routes import _wait
    b, gp = _fuji(tmp_path)
    b.close()
    usb = UsbCamera(backends=[b])
    got = []
    usb.zoom_changed.connect(got.append)
    states = []
    usb.state.connect(lambda k, t: states.append(k))
    usb.start()
    try:
        usb.open("usb:gphoto2:usb:001,004")
        assert _wait(lambda: "open" in states, 5)
        usb.set_zoom("8")
        assert _wait(lambda: "8" in got, 5)
        gp.state.config.children["d01b"].value = "1"         # the camera's own button
        usb._note_zoom(None)
        assert got[-1] == "1"
    finally:
        usb.stop()
        usb.wait(3000)


# --------------------------------------------------------------------------- #
# where the magnifier looked (core/zoom_spot.py)
# --------------------------------------------------------------------------- #
def _texture(h=768, w=1024, seed=3):
    import cv2
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), np.uint8)
    for _ in range(400):
        c = tuple(int(x) for x in rng.integers(0, 255, 3))
        cv2.circle(img, (int(rng.integers(0, w)), int(rng.integers(0, h))),
                   int(rng.integers(4, 40)), c, -1)
    return img


def test_the_magnified_spot_is_found():
    import cv2
    from core import zoom_spot
    whole = _texture()
    s, cx, cy = 2.5, 619.0, 276.0                              # as on the GFX
    M = np.float32([[s, 0, 512 - s * cx], [0, s, 384 - s * cy]])
    zoomed = cv2.warpAffine(whole, M, (1024, 768), flags=cv2.INTER_CUBIC)
    spot = zoom_spot.locate(whole, zoomed, 2.5)
    assert spot is not None
    assert spot[0] == pytest.approx(cx / 1024, abs=0.01)
    assert spot[1] == pytest.approx(cy / 768, abs=0.01)
    assert zoom_spot.locate(whole, _texture(seed=9), 2.5) is None      # another picture
    assert zoom_spot.locate(whole, zoomed, 8.0) is None                # wrong scale


# --------------------------------------------------------------------------- #
# the monitor
# --------------------------------------------------------------------------- #
from test_film_base_shoot import film_mon  # noqa: E402,F401


def test_the_monitor_steps_hides_whole_frame_overlays_and_refuses_picks(film_mon):
    mon, usb, light, lm, path = film_mon
    import numpy as np
    mon._on_zoom_levels(["1", "2.5", "4", "8", "16"])
    combo = mon.cam_zoom_combo
    assert [combo.itemText(i) for i in range(combo.count())] == \
        ["Whole", "2.5×", "4×", "8×", "Max"]
    mon.feed.full = np.zeros((480, 640, 3), np.uint8)
    mon._step_cam_zoom()
    assert usb.log[-1] == ("zoom", "2.5")
    assert mon._zoom_whole is not None                      # kept to find the spot
    best = mon._reset_best
    mon._on_cam_zoom("8")                                   # the camera reports it
    assert mon.magnified() and combo.currentText() == "8×"
    assert mon.canvas.magnified == "8×" and mon._reset_best == best + 1
    mon._step_cam_zoom()
    assert usb.log[-1] == ("zoom", "16")
    # whole-frame tools wait for the whole frame
    mon._neg_base_pos = (0.5, 0.05, 640, 480)
    assert mon.start_measure("x") is False
    assert "whole frame first" in mon.capture_caption.text()
    mon._on_neg_button("base")
    assert mon.canvas.mode is None
    # back to the whole frame: the spot it looked at is outlined
    mon._zoom_spot = (0.6, 0.36, 8.0)
    mon._on_cam_zoom("1")
    assert not mon.magnified() and mon.canvas.magnified is None
    assert mon.canvas.zoom_spot == (0.6, 0.36, 8.0)
    assert mon.start_measure("x") is True
    assert _wait_for(lambda: mon._measure is None, 20)


def _wait_for(cond, secs):
    from test_capture_routes import _wait
    return _wait(cond, secs)


def test_the_canvas_draws_the_badge_and_hides_the_guide():
    from widgets.live_canvas import LiveCanvas
    from test_live_canvas import _canvas
    c, render = _canvas()
    c.guide_on = True
    before = c.grab().toImage()
    c.set_magnified("8×")
    after = c.grab().toImage()
    assert before != after
    c.set_magnified(None)
    assert c.grab().toImage() == before
