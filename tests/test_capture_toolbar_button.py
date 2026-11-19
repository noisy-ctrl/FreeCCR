#!/usr/bin/env python3
"""The canvas toolbar's Capture button (spec/capture-toolbar-button.md)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from ui.main_window import MainWindow  # noqa: E402


class _FakeMonitor:
    def __init__(self, minimised=False):
        self.minimised = minimised
        self.calls = []

    def isMinimized(self):
        return self.minimised

    def showNormal(self):
        self.calls.append("showNormal")

    def show(self):
        self.calls.append("show")

    def raise_(self):
        self.calls.append("raise")

    def activateWindow(self):
        self.calls.append("activate")


def test_capture_sits_left_of_auto_crop_as_a_picture():
    win = MainWindow()
    ip = win.image_preview
    acts = ip.toolbar.actions()
    i = acts.index(ip.capture_action)
    assert acts[i + 1] is ip.auto_crop_action
    assert not ip.capture_action.icon().isNull()
    assert ip.capture_action.text() == "Capture"


def test_capture_brings_an_open_monitor_back():
    win = MainWindow()
    mon = _FakeMonitor(minimised=True)
    win._live_monitor = mon
    assert win.image_preview.open_capture() is mon
    assert mon.calls == ["showNormal", "raise", "activate"]
    mon2 = _FakeMonitor()
    win._live_monitor = mon2
    win.image_preview.capture_action.trigger()
    assert mon2.calls == ["show", "raise", "activate"]
    win._live_monitor = None


def test_capture_opens_the_monitor_when_none_is_open(monkeypatch):
    win = MainWindow()
    made = []
    import widgets.live_monitor as lm

    class _Mon(_FakeMonitor):
        def __init__(self, main_window=None):
            super().__init__()
            made.append(self)

            class _Sig:
                def connect(self, f):
                    pass
            self.closed = _Sig()

        def apply_roll_format(self, key):
            pass
    monkeypatch.setattr(lm, "LiveCaptureMonitor", _Mon)
    assert win.image_preview.open_capture() is made[0]
    assert made[0].calls == ["show", "raise", "activate"]
    win._live_monitor = None
