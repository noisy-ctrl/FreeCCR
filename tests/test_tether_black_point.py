#!/usr/bin/env python3
"""Setting the roll's black point while tethering (spec/tether-black-point.md):
the lit check, the prompt waiting for a lit capture, and the Black Point
buttons in the tether banner and the Live Capture Monitor."""

import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from core import lead_frame  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402


def _with(data):
    return types.SimpleNamespace(resized_raw=data)


def test_lit_level_scales_by_type():
    a = np.zeros((40, 40, 3), np.uint16)
    a[:10, :10, 1] = 32768                      # 6 % of the frame, half scale
    assert lead_frame.lit_level(_with(a)) == pytest.approx(0.5, abs=1e-3)
    assert lead_frame.lit_level(_with((a >> 8).astype(np.uint8))) == pytest.approx(128 / 255, abs=1e-3)
    assert lead_frame.lit_level(_with(a.astype(np.float32) / 65535)) == pytest.approx(0.5, abs=1e-3)
    assert lead_frame.lit_level(_with(np.full((20, 20), 655, np.uint16))) == pytest.approx(0.01, abs=1e-4)
    assert lead_frame.lit_level(_with(None)) is None
    assert lead_frame.lit_level(types.SimpleNamespace()) is None
    assert lead_frame.lit_level(_with(np.zeros((0, 0, 3), np.uint16))) is None


def test_is_lit_around_the_threshold():
    assert lead_frame.is_lit(None)              # unmeasured never blocks
    assert lead_frame.is_lit(lead_frame.LIT_MIN)
    assert not lead_frame.is_lit(lead_frame.LIT_MIN * 0.99)
    assert not lead_frame.is_lit(0.0)
    assert lead_frame.is_lit(0.20)              # the dimmest lit RAF measured (load path)


def test_a_dark_frame_with_noise_is_not_lit():
    """Black level plus read noise: a few codes in 16 bits."""
    rng = np.random.RandomState(3)
    dark = np.clip(rng.normal(60, 25, (200, 300, 3)), 0, None).astype(np.uint16)
    assert not lead_frame.is_lit(lead_frame.lit_level(_with(dark)))


def test_worker_measures_before_converting(tmp_path, monkeypatch):
    from core.tether_watcher import TetherWatchWorker
    seen = []

    class Img:
        converted = False

        def __init__(self):
            self.resized_raw = np.full((20, 20, 3), 30000, np.uint16)

    def convert(img, *a, **k):
        seen.append(getattr(img, "lit_level", "unset"))
        img.resized_raw = np.zeros((20, 20, 3), np.uint16)   # what was measured is gone
        img.converted = True

    monkeypatch.setattr(TetherWatchWorker, "_convert", staticmethod(convert))
    monkeypatch.setattr(ccr_backend, "black_point_bgr", (40000, 40000, 40000), raising=False)
    monkeypatch.setattr(ccr_backend, "white_point_bgr", None, raising=False)
    monkeypatch.setattr(ccr_backend, "positive_mode", False, raising=False)
    w = TetherWatchWorker()
    out = []
    w.captured.connect(out.append)
    img = Img()
    w._finish([img])
    assert seen == [pytest.approx(30000 / 65535, abs=1e-4)]
    assert out == [img] and img.lit_level == pytest.approx(30000 / 65535, abs=1e-4)


# --------------------------------------------------------------------------- #
# the main window
# --------------------------------------------------------------------------- #
@pytest.fixture
def mw(tmp_path):
    from ui.main_window import MainWindow
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()
    ccr_backend.black_point_bgr = None
    ccr_backend.white_point_bgr = None
    ccr_backend.positive_mode = False
    w = MainWindow()
    prompts = []
    w._prompt_bwpoint_for_session = lambda: prompts.append(w._tether_lead_img)
    w._tether_active = True
    w._tether_folder = str(tmp_path)
    w._tether_prompted = False
    w._tether_import_existing = set()
    modes = []
    w.image_preview.set_bwpoint_mode = lambda m: modes.append(m)
    hints = []
    real_hint = w.sliders_panel.set_temporary_hint
    w.sliders_panel.set_temporary_hint = lambda text, **k: (hints.append(text), real_hint(text, **k))
    yield w, prompts, modes, hints, tmp_path
    w.stop_tethering()
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()


def _capture(tmp_path, name, lit):
    from core.ccr_image import CCRImage
    p = tmp_path / name
    v = 200 if lit else 0
    cv2.imwrite(str(p), np.full((40, 40, 3), v, np.uint8))
    img = CCRImage(str(p))
    img.lit_level = 0.5 if lit else 0.001
    return img


def test_a_dark_first_capture_doesnt_take_the_prompt(mw):
    w, prompts, modes, hints, tmp = mw
    from PySide6.QtCore import QTimer
    dark = _capture(tmp, "dark.png", lit=False)
    w._on_capture(dark)
    _app.processEvents()
    assert w._tether_prompted is False and prompts == []
    assert any("too dark" in h for h in hints)
    lit = _capture(tmp, "lead.png", lit=True)
    w._on_capture(lit)
    QTimer.singleShot(0, lambda: None)
    for _ in range(3):
        _app.processEvents()
    assert w._tether_prompted is True and w._tether_lead_img is lit
    assert prompts == [lit]
    w._on_capture(_capture(tmp, "next.png", lit=True))   # once a session
    _app.processEvents()
    assert prompts == [lit]


def test_black_point_picks_the_newest_lit_capture(mw):
    w, prompts, modes, hints, tmp = mw
    lit1 = _capture(tmp, "a.png", lit=True)
    lit2 = _capture(tmp, "b.png", lit=True)
    dark = _capture(tmp, "c.png", lit=False)
    for img in (lit1, lit2, dark):
        w._on_capture(img)
    _app.processEvents()
    assert w.start_roll_black_point() is True
    assert modes == ["black"]
    assert w.thumbnail_list.thumbnail_list.currentRow() == ccr_backend.images.index(lit2)
    # a frame asked for by name wins
    assert w.start_roll_black_point(lit1) is True
    assert w.thumbnail_list.thumbnail_list.currentRow() == ccr_backend.images.index(lit1)


def test_black_point_falls_back_to_the_frame_on_screen_and_hints_with_none(mw):
    w, prompts, modes, hints, tmp = mw
    assert w.start_roll_black_point() is False
    assert modes == [] and any("film lead first" in h for h in hints)
    from core.ccr_image import CCRImage
    p = tmp / "opened.png"
    cv2.imwrite(str(p), np.full((40, 40, 3), 120, np.uint8))
    img = CCRImage(str(p))
    ccr_backend.images.append(img)
    ccr_backend.file_paths.append(img.file_path)
    w.thumbnail_list.append_image_item(0, select=True)
    _app.processEvents()
    assert w.start_roll_black_point() is True and modes == ["black"]


def test_the_banner_button_starts_sampling(mw):
    w, prompts, modes, hints, tmp = mw
    w._on_capture(_capture(tmp, "a.png", lit=True))
    _app.processEvents()
    w.tether_banner._black.click()
    assert modes == ["black"]


def test_the_monitor_button_starts_sampling(tmp_path, monkeypatch):
    from widgets import live_monitor as lm
    from test_live_monitor import FakeFeed, _light
    from PySide6.QtCore import QSettings
    path = str(tmp_path / "live.ini")
    monkeypatch.setattr(lm, "_settings", lambda: QSettings(path, QSettings.IniFormat))
    calls = []

    class MW:
        def start_roll_black_point(self):
            calls.append("start")
            return len(calls) > 1           # the first time: no capture yet

        def tether_folder(self):
            return None

    light, _dev, _made = _light()
    from core.usb_camera import UsbCamera
    mon = lm.LiveCaptureMonitor(main_window=MW(), feed=FakeFeed(), light=light,
                                usb=UsbCamera(backends=[]))
    try:
        assert mon.black_point_btn.isEnabled()
        mon.black_point_btn.click()
        assert calls == ["start"] and "film lead first" in mon.capture_caption.text()
        mon.black_point_btn.click()
        assert calls == ["start", "start"]
    finally:
        mon.close()
        _app.processEvents()
    mon2 = lm.LiveCaptureMonitor(main_window=None, feed=FakeFeed(), light=light,
                                 usb=UsbCamera(backends=[]))
    try:
        assert not mon2.black_point_btn.isEnabled()
    finally:
        mon2.close()
        _app.processEvents()
