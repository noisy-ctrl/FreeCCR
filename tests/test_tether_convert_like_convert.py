#!/usr/bin/env python3
"""Tethered captures convert exactly as Convert Current does, after taking
the previous frame's look (spec/tether-convert-like-convert.md)."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from core.ccr_backend import ccr_backend  # noqa: E402


def _negative(path, seed=1):
    """A colour-negative-looking frame: orange film base round a darker,
    varied picture."""
    rng = np.random.RandomState(seed)
    h, w = 240, 360
    img = np.empty((h, w, 3), np.uint16)
    img[:] = (20000, 36000, 52000)                       # BGR film base (orange)
    pic = (rng.rand(h - 60, w - 60, 3) * 0.5 + 0.2)
    pic = (pic * np.array([16000, 28000, 42000])).astype(np.uint16)
    img[30:-30, 30:-30] = pic
    cv2.imwrite(str(path), img)
    return str(path)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from core.ccr_image import CCRImage
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()
    monkeypatch.setattr(ccr_backend, "positive_mode", False, raising=False)
    monkeypatch.setattr(ccr_backend, "black_point_bgr", (20000.0, 36000.0, 52000.0),
                        raising=False)
    monkeypatch.setattr(ccr_backend, "white_point_bgr", None, raising=False)
    yield CCRImage, tmp_path
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()


def test_a_capture_converts_as_convert_current_would(setup):
    CCRImage, tmp = setup
    from core.tether_watcher import TetherWatchWorker
    a = CCRImage(_negative(tmp / "a.tif"))
    b = CCRImage(_negative(tmp / "b.tif"))
    # Convert Current's way
    plan = ccr_backend.plan_frame_conversion(a)
    ccr_backend.apply_frame_conversion(a, plan)
    # the tether worker's way
    w = TetherWatchWorker()
    out = []
    w.captured.connect(out.append)
    w._finish([b])
    assert out == [b] and b.converted
    ca, cb = a.conversion_inputs, b.conversion_inputs
    assert ca.keys() == cb.keys()
    assert ca["bw"] == cb["bw"] and ca.get("auto_bp") == cb.get("auto_bp")
    assert ca.get("auto_wp") == cb.get("auto_wp") and ca.get("picture") == cb.get("picture")
    assert np.array_equal(a.resized_raw, b.resized_raw)


def test_the_panel_uses_the_same_two_steps(setup, monkeypatch):
    """Convert Current goes through plan_frame_conversion + apply_frame_conversion."""
    from ui.main_window import MainWindow
    CCRImage, tmp = setup
    img = CCRImage(_negative(tmp / "c.tif"))
    ccr_backend.images.append(img)
    ccr_backend.file_paths.append(img.file_path)
    w = MainWindow()
    w.thumbnail_list.load_thumbnails()
    calls = []
    real_plan, real_apply = ccr_backend.plan_frame_conversion, ccr_backend.apply_frame_conversion
    monkeypatch.setattr(ccr_backend, "plan_frame_conversion",
                        lambda i: (calls.append("plan"), real_plan(i))[1])
    monkeypatch.setattr(ccr_backend, "apply_frame_conversion",
                        lambda i, p: (calls.append("apply"), real_apply(i, p))[1])
    w.sliders_panel.current_idx = 0
    w.sliders_panel._on_convert_current_bwpoint()
    assert calls == ["plan", "apply"] and img.converted


def test_the_look_comes_first_and_its_own_awb_stays_behind(setup, monkeypatch):
    """The previous frame's flip and crop reach the capture BEFORE it converts
    (the crop is where its measurements are made); a Temperature/Tint the
    previous frame's auto white balance wrote is not copied."""
    CCRImage, tmp = setup
    from core.tether_watcher import TetherWatchWorker, look_template
    prev = CCRImage(_negative(tmp / "p.tif"))
    prev.horizontal_mirrored = True
    prev.crop_rect = (0.2, 0.2, 0.6, 0.6)
    prev.adjustment_settings = {"temperature": 12, "tint": -3, "contrast": 20}
    prev.awb_auto = (12, -3)                              # written by AWB, untouched
    t = look_template(prev)
    assert t["horizontal_mirrored"] and t["crop_rect"] == (0.2, 0.2, 0.6, 0.6)
    assert t["adjustment_settings"] == {"contrast": 20}
    prev.adjustment_settings["temperature"] = 30          # set by hand now: it's a look
    assert look_template(prev)["adjustment_settings"]["temperature"] == 30

    seen = {}
    real_plan = ccr_backend.plan_frame_conversion

    def plan(img):
        seen["flip"], seen["crop"] = img.horizontal_mirrored, img.crop_rect
        return real_plan(img)
    monkeypatch.setattr(ccr_backend, "plan_frame_conversion", plan)
    new = CCRImage(_negative(tmp / "n.tif", seed=2))
    w = TetherWatchWorker()
    w.template = t
    w._finish([new], t)
    assert seen == {"flip": True, "crop": (0.2, 0.2, 0.6, 0.6)}
    assert new.horizontal_mirrored and new.converted
    assert new._tether_template_applied
    assert new.adjustment_settings.get("contrast") == 20
    t["adjustment_settings"]["contrast"] = 99             # its own copy
    assert new.adjustment_settings["contrast"] == 20


def test_no_black_point_means_no_auto_convert(setup, monkeypatch):
    CCRImage, tmp = setup
    from core.tether_watcher import TetherWatchWorker
    monkeypatch.setattr(ccr_backend, "black_point_bgr", None, raising=False)
    img = CCRImage(_negative(tmp / "d.tif"))
    w = TetherWatchWorker()
    w._finish([img])
    assert not img.converted


def test_the_main_window_hands_the_look_over_and_doesnt_apply_it_twice(tmp_path):
    from ui.main_window import MainWindow
    from core.ccr_image import CCRImage
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()
    w = MainWindow()
    prev = CCRImage(_negative(tmp_path / "p.tif"))
    prev.horizontal_mirrored = True
    ccr_backend.images.append(prev)
    ccr_backend.file_paths.append(prev.file_path)
    w.thumbnail_list.load_thumbnails()

    class Worker:
        template = None
    wk = Worker()
    w._hand_template(wk)
    assert wk.template and wk.template["horizontal_mirrored"]
    # a capture the worker already gave the look keeps its own settings
    w._tether_active = True
    w._tether_folder = str(tmp_path)
    w._tether_prompted = True
    cap = CCRImage(_negative(tmp_path / "c.tif"))
    cap.adjustment_settings = {"temperature": 5, "tint": 1}   # its own AWB
    cap._tether_template_applied = True
    w._on_capture(cap)
    assert cap.adjustment_settings == {"temperature": 5, "tint": 1}
    w.stop_tethering()
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()
