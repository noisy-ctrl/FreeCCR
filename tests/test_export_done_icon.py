#!/usr/bin/env python3
"""The export-finished message shows a drawn tick (or an amber "!" when
something failed or was stopped), not the platform icon, which on a macOS
from-source run appears as a question mark."""

import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import (QApplication, QMessageBox, QVBoxLayout,  # noqa: E402
                               QWidget)

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core.ccr_backend import ccr_backend  # noqa: E402
from ui import theme  # noqa: E402
from widgets.image_preview import ImagePreview  # noqa: E402


def _rgb(pm, fx, fy):
    img = pm.toImage()
    c = img.pixelColor(int(img.width() * fx), int(img.height() * fy))
    return c.red(), c.green(), c.blue(), c.alpha()


def test_tick_and_problem_badges():
    done = theme.status_icon_pixmap("done", 48, 2.0)
    assert done.width() == 96 and done.devicePixelRatio() == 2.0
    r, g, b, a = _rgb(done, 0.5, 0.15)                 # the disc
    assert a == 255 and g > r and g > b                 # green
    assert _rgb(done, 0.44, 0.67)[:3] == (255, 255, 255)  # on the tick stroke
    assert _rgb(done, 0.02, 0.02)[3] == 0               # transparent corner
    prob = theme.status_icon_pixmap("problem", 48, 2.0)
    r, g, b, a = _rgb(prob, 0.25, 0.5)
    assert r > g > b                                    # amber
    assert _rgb(prob, 0.5, 0.74)[:3] == (255, 255, 255)   # the dot of the "!"


class _Hist:
    def set_caption(self, text):
        pass


class _Sliders:
    histogram = _Hist()

    def __getattr__(self, name):
        return lambda *a, **k: None


class _Host(QWidget):
    def __init__(self):
        super().__init__()
        self.sliders_panel = _Sliders()
        self.mid = QWidget(self)


_HOSTS = []


def _finished(monkeypatch, result):
    host = _Host()
    _HOSTS.append(host)
    QVBoxLayout(host).addWidget(host.mid)
    ip = ImagePreview(host.mid)
    seen = {}

    def fake_exec(box):
        seen["pixmap"] = box.iconPixmap()
        seen["title"] = box.windowTitle()
        seen["text"] = box.text()
        return QMessageBox.Ok
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(ccr_backend, "save_catalog", lambda *a, **k: None)
    monkeypatch.setattr(ccr_backend, "images", [])
    plan = SimpleNamespace(skipped=0, destination="/tmp/out", open_folder=False)
    ip._on_export_finished(SimpleNamespace(accept=lambda: None), result, plan)
    return seen


def test_success_shows_the_tick(monkeypatch):
    seen = _finished(monkeypatch, {"exported": 3, "failed": 0, "cancelled": False,
                                   "failures": []})
    assert seen["title"] == "Export Complete" and "Exported: 3" in seen["text"]
    r, g, b, _ = _rgb(seen["pixmap"], 0.5, 0.15)
    assert g > r and g > b


def test_failure_shows_the_amber_badge(monkeypatch):
    seen = _finished(monkeypatch, {"exported": 1, "failed": 1, "cancelled": False,
                                   "failures": [(0, "boom")]})
    r, g, b, _ = _rgb(seen["pixmap"], 0.25, 0.5)
    assert r > g > b
    seen = _finished(monkeypatch, {"exported": 1, "failed": 0, "cancelled": True,
                                   "failures": []})
    r, g, b, _ = _rgb(seen["pixmap"], 0.25, 0.5)
    assert r > g > b
