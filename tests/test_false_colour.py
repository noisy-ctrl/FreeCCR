#!/usr/bin/env python3
"""False colour and the HDR glow as dots. See spec/false-colour.md."""

import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core import false_colour as fc  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402

BAND = {name: colour for (_lo, _hi, colour, _label, name) in fc.BANDS}


def _grey(v):
    """A 1x1 picture whose signal level is exactly v (0..1) on 8 bits."""
    return np.full((1, 1, 3), int(round(v * 255)), np.uint8)


def _fc_of(v):
    return tuple(int(c) for c in fc.false_colour(_grey(v))[0, 0])


# --- 1. bands ----------------------------------------------------------------

def test_band_edges():
    assert _fc_of(0.0) == BAND["black clipping"]
    assert _fc_of(5 / 255) == BAND["black clipping"]            # 1.96 %
    assert _fc_of(7 / 255) == BAND["just above black clipping"]  # 2.75 %
    assert _fc_of(11 / 255) == (11, 11, 11)                       # 4.3 %: grey
    assert _fc_of(0.40) == BAND["mid grey"]
    assert _fc_of(96 / 255) == (96, 96, 96)                       # 37.6 %: grey
    assert _fc_of(0.54) == BAND["one stop over grey (skin)"]
    assert _fc_of(0.50) == (128, 128, 128)
    assert _fc_of(248 / 255) == BAND["just below white clipping"]  # 97.3 %
    assert _fc_of(253 / 255) == BAND["white clipping"]            # 99.2 %
    assert _fc_of(1.0) == BAND["white clipping"]


def test_level_is_video_luma_and_outside_bands_is_grey():
    pic = np.zeros((1, 3, 3), np.uint8)
    pic[0, 0] = (255, 0, 0)          # luma 0.2126
    pic[0, 1] = (0, 255, 0)          # luma 0.7152
    pic[0, 2] = (0, 0, 255)          # luma 0.0722
    out = fc.false_colour(pic)
    assert tuple(out[0, 0]) == (54, 54, 54)
    assert tuple(out[0, 1]) == (182, 182, 182)
    assert tuple(out[0, 2]) == (18, 18, 18)
    assert out is not pic and pic[0, 0, 0] == 255


# --- 2. dots -------------------------------------------------------------------

def test_dots_density_follows_the_glow():
    h, w = 48, 48
    none = fc.glow_dots_rgba(np.zeros((h, w), np.float32))
    assert not none[..., 3].any()
    counts = []
    for s in (0.01, 0.3, 0.6, 1.0):
        rgba = fc.glow_dots_rgba(np.full((h, w), s, np.float32))
        opaque = rgba[..., 3] == 255
        assert np.all(rgba[..., 3][~opaque] == 0)
        colours = {tuple(c) for c in rgba[opaque][:, :3]}
        assert colours <= {(255, 255, 255), (0, 0, 0)}
        counts.append(int(opaque.sum()))
    assert counts == sorted(counts) and counts[0] < counts[-1]
    cells = (h // fc.DOT_SPACING) * (w // fc.DOT_SPACING)
    per_cell = 2 * fc.DOT_SIZE * fc.DOT_SIZE
    assert counts[-1] == cells * per_cell                  # full glow: every cell
    assert counts[0] == cells // 16 * per_cell              # faint: the sparsest layer


def test_dots_only_where_it_glows():
    s = np.zeros((24, 48), np.float32)
    s[:, 24:] = 1.0
    rgba = fc.glow_dots_rgba(s)
    assert not rgba[:, :24, 3].any() and rgba[:, 24:, 3].any()


# --- 3. canvas -----------------------------------------------------------------

class _Hist:
    def set_caption(self, text):
        pass


class _Sliders:
    histogram = _Hist()

    def __getattr__(self, name):
        return lambda *a, **k: None


class _Thumbs:
    def selected_indices(self):
        return []

    def update_thumbnail(self, idx):
        pass


_HOSTS = []


def _canvas(tmp_path, monkeypatch):
    from widgets.image_preview import ImagePreview
    path = str(tmp_path / "pos.png")
    pic = np.zeros((90, 150, 3), np.uint8)
    pic[...] = np.linspace(0, 255, 150).astype(np.uint8)[None, :, None]
    cv2.imwrite(path, pic)
    monkeypatch.setattr(ccr_backend, "positive_mode", True)
    monkeypatch.setattr(ccr_backend, "hdr_tint_headroom", None)
    img = CCRImage(path)
    monkeypatch.setattr(ccr_backend, "images", [img])
    img.update_thumbnail_and_preview()

    class Host(QWidget):
        def __init__(self):
            super().__init__()
            self.sliders_panel = _Sliders()
            self.thumbnail_list = _Thumbs()
            self.mid = QWidget(self)
    host = Host()
    _HOSTS.append(host)
    QVBoxLayout(host).addWidget(host.mid)
    ip = ImagePreview(host.mid)
    QVBoxLayout(host.mid).addWidget(ip)
    host.resize(700, 600)
    host.show()
    _app.processEvents()
    monkeypatch.setattr(ip, "_request_hires_detail", lambda *a, **k: None, raising=False)
    ip.update_preview(0)
    return ip, img


def _to_np(pm):
    q = pm.toImage().convertToFormat(QImage.Format_RGB888)
    w, h = q.width(), q.height()
    buf = np.frombuffer(q.constBits(), np.uint8)
    return buf.reshape(h, q.bytesPerLine())[:, :w * 3].reshape(h, w, 3).copy()


def test_false_colour_is_its_own_layer(tmp_path, monkeypatch):
    ip, img = _canvas(tmp_path, monkeypatch)
    before = ip._capture_display_image()[0].copy()
    ip.set_false_colour(True)
    item = ip._fc_item
    assert item is not None and item.isVisible()
    assert item.parentItem() is ip.pixmap_item
    shown = ip.pixmap_item.pixmap()
    layer = item.pixmap()
    assert (layer.width(), layer.height()) == (shown.width(), shown.height())
    assert np.array_equal(_to_np(layer), fc.false_colour(_to_np(shown)))
    assert np.array_equal(ip._capture_display_image()[0], before)   # scopes untouched
    legend = ip._fc_legend
    vp = ip.view.viewport()
    assert legend is not None and legend.isVisible()
    assert legend.x() == 8 and legend.y() + legend.height() <= vp.height()
    assert ip.fc_action.isChecked()
    ip.set_false_colour(False)
    assert not ip._fc_item.isVisible() and not legend.isVisible()
    assert not ip.fc_action.isChecked()


def test_hdr_dots_sit_above_false_colour(tmp_path, monkeypatch):
    ip, img = _canvas(tmp_path, monkeypatch)
    ip.set_false_colour(True)
    ip.set_hdr_tint(True)
    glow = ip._glow_item
    assert glow is not None and glow.isVisible()
    assert glow.zValue() > ip._fc_item.zValue()
    rgba = glow.pixmap().toImage().convertToFormat(QImage.Format_RGBA8888)
    w, h = rgba.width(), rgba.height()
    a = np.frombuffer(rgba.constBits(), np.uint8).reshape(h, rgba.bytesPerLine())[:, :w * 4]
    a = a.reshape(h, w, 4)
    opaque = a[..., 3] == 255
    assert opaque.any()
    assert {tuple(c) for c in a[opaque][:, :3]} <= {(255, 255, 255), (0, 0, 0)}
    # Only on the bright end of the picture actually shown (positive mode's
    # base curve lifts the ramp, so measure the displayed pixels).
    shown = _to_np(ip.current_pixmap)
    assert shown.shape[:2] == (h, w)
    luma = fc.signal_level(shown)
    assert opaque[:, -10:].any()
    assert not opaque[luma < 0.75].any()
    ip.set_hdr_tint(False)
    ip.set_false_colour(False)
    monkeypatch.setattr(ccr_backend, "hdr_tint_headroom", None)


def test_toolbar_keeps_export_visible():
    """The icon-only False colour button must not push Export off the end of
    the toolbar at a typical laptop window width."""
    from ui.main_window import MainWindow
    w = MainWindow()
    _HOSTS.append(w)
    w.resize(1450, 900)
    w.show()
    _app.processEvents()
    ip = w.image_preview
    assert not ip.fc_action.icon().isNull()
    assert ip.toolbar.widgetForAction(ip.export_action).isVisible()
