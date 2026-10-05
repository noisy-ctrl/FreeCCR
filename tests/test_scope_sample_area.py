#!/usr/bin/env python3
"""Tests for the Scopes 'Ref frame' toggle: the scopes and the histogram
sample the red reference frame instead of the whole image, read-only.
See spec/scope-sample-area.md."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import QRectF, QSettings  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import (QApplication, QGraphicsPixmapItem,  # noqa: E402
                               QGraphicsRectItem, QVBoxLayout, QWidget)

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core.ccr_backend import ccr_backend  # noqa: E402
from widgets.image_preview import ImagePreview  # noqa: E402


class _Hist:
    def __init__(self):
        self.caption = None

    def set_caption(self, text):
        self.caption = text


class _Sliders:
    def __init__(self):
        self.histogram = _Hist()
        self.hist_data = "unset"

    def set_histogram(self, data):
        self.hist_data = data

    def set_sliders_enabled(self, *a):
        pass


class _Host(QWidget):
    def __init__(self):
        super().__init__()
        self.sliders_panel = _Sliders()
        self.mid = QWidget(self)


_HOSTS = []


@pytest.fixture(autouse=True)
def _restore_setting():
    s = QSettings("FreeCCR", "FreeCCR")
    old = s.value("scopes/sample_reference", False, type=bool)
    yield
    s.setValue("scopes/sample_reference", old)


def _preview():
    """ImagePreview showing a 120x80 image: left half red, right half blue."""
    host = _Host()
    _HOSTS.append(host)
    QVBoxLayout(host).addWidget(host.mid)
    ip = ImagePreview(host.mid)
    QVBoxLayout(host.mid).addWidget(ip)
    host.resize(700, 600)
    host.show()
    _app.processEvents()
    pm = QPixmap(120, 80)
    pm.fill(QColor(220, 0, 0))
    p = QPainter(pm)
    p.fillRect(60, 0, 60, 80, QColor(0, 0, 220))
    p.end()
    ip.current_pixmap = pm
    ip.pixmap_item = QGraphicsPixmapItem(pm)
    ip.scene.addItem(ip.pixmap_item)
    ip.current_idx = 0
    ip._fit_view_to_content()
    return ip, host


def _draw_ref(ip, rect):
    item = QGraphicsRectItem(rect)
    ip.scene.addItem(item)
    ip.reference_rect_item = item
    return item


def _set_toggle(ip, on):
    ip.scopes_panel._sample_btn.setChecked(on)
    _app.processEvents()


class TestRegion:
    def test_off_samples_whole_image(self):
        ip, _h = _preview()
        _set_toggle(ip, False)
        _draw_ref(ip, QRectF(70, 10, 40, 60))
        assert ip._reference_sample_region() is None

    def test_on_without_a_frame_falls_back(self):
        ip, _h = _preview()
        _set_toggle(ip, True)
        assert ip._reference_sample_region() is None
        assert ip._sampling_status() == ("", "whole image (no frame)")

    def test_on_with_a_frame_captures_only_the_frame(self):
        ip, _h = _preview()
        _set_toggle(ip, True)
        _draw_ref(ip, QRectF(70, 10, 40, 60))      # inside the blue half
        region = ip._reference_sample_region()
        assert region is not None
        rgb, mask = ip._capture_display_image(region=region)
        px = rgb[mask]
        assert px.size
        assert int(np.median(px[:, 2])) > 200 and int(np.median(px[:, 0])) < 10
        assert ip._sampling_status() == ("Ref frame", "reference frame")

    def test_frame_is_clipped_to_the_image(self):
        ip, _h = _preview()
        _set_toggle(ip, True)
        _draw_ref(ip, QRectF(100, 40, 200, 200))   # runs off the image
        region = ip._reference_sample_region()
        assert region.right() <= 120 + 1e-6 and region.bottom() <= 80 + 1e-6


class TestHistogram:
    def test_histogram_follows_the_frame(self, monkeypatch):
        ip, host = _preview()
        monkeypatch.setattr(ccr_backend, "get_histogram_data_by_index",
                            lambda idx: None)
        _set_toggle(ip, True)
        _draw_ref(ip, QRectF(70, 10, 40, 60))
        ip._push_histogram(0)
        data = host.sliders_panel.hist_data
        assert data.shape == (3, 256)
        assert data[2, 200:].sum() > 0 and data[0, 200:].sum() == 0   # blue only
        assert host.sliders_panel.histogram.caption == "Ref frame"

    def test_toggle_off_uses_the_image_histogram(self, monkeypatch):
        ip, host = _preview()
        sentinel = np.ones((3, 256), np.float32)
        monkeypatch.setattr(ccr_backend, "get_histogram_data_by_index",
                            lambda idx: sentinel)
        _draw_ref(ip, QRectF(70, 10, 40, 60))
        _set_toggle(ip, True)
        _set_toggle(ip, False)                     # flipping re-pushes it
        assert host.sliders_panel.hist_data is sentinel
        assert host.sliders_panel.histogram.caption == ""


class TestReadOnly:
    def test_sampling_never_writes_the_reference_frame(self, monkeypatch):
        ip, _h = _preview()
        monkeypatch.setattr(ccr_backend, "get_histogram_data_by_index",
                            lambda idx: None)

        def _boom(*a, **k):
            raise AssertionError("sampling must not write the reference frame")
        monkeypatch.setattr(ccr_backend, "set_reference_frame_by_index", _boom)
        _draw_ref(ip, QRectF(70, 10, 40, 60))
        _set_toggle(ip, True)
        ip._push_histogram(0)
        ip._update_scopes_now()
        _set_toggle(ip, False)


class TestProbe:
    def test_markers_hidden_outside_the_sample(self):
        ip, _h = _preview()
        ip.scopes_panel.set_probe(10, 20, 30, 0.5, markers=False)
        assert ip.scopes_panel.parade._probe is None
        assert "R  10" in ip.scopes_panel._readout.text()
        ip.scopes_panel.set_probe(10, 20, 30, 0.5)
        assert ip.scopes_panel.parade._probe is not None
