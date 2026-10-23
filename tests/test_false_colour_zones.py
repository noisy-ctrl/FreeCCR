#!/usr/bin/env python3
"""False colour's stop-zone scale (spec/false-colour.md, 2026-10-23): 15
zones in stops from mid grey, read from the negative's density or from the
finished picture; picked on the button's right-click menu or in Settings,
remembered."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core import false_colour as fc  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402


@pytest.fixture
def store(monkeypatch):
    """QSettings writes land here, not in the real preferences."""
    from PySide6.QtCore import QSettings
    saved = {}
    monkeypatch.setattr(QSettings, "setValue", lambda self, k, v: saved.__setitem__(k, v))
    return saved


def test_fifteen_zones_whole_stops_and_halves_round_grey():
    assert [z[0] for z in fc.ZONES] == ["−6", "−5", "−4", "−3",
                                        "−2", "−1", "−½", "0",
                                        "+½", "+1", "+2", "+3", "+4", "+5", "+6"]
    st = np.array([-9, -5.6, -5.5, -3.0, -1.0, -0.5, -0.1, 0.0, 0.1, 0.5,
                   1.0, 2.0, 3.0, 4.0, 5.0, 5.5, 9.0], np.float32)
    names = [fc.ZONES[i][0] for i in fc.zone_index(st)]
    assert names == ["−6", "−6", "−5", "−3", "−1", "−½",
                     "0", "0", "0", "+½", "+1", "+2", "+3", "+4", "+5", "+6", "+6"]
    rgb = fc.zones_rgb(fc.zone_index(st))
    assert tuple(rgb[7]) == (128, 128, 128)                 # grey is grey
    assert tuple(rgb[0]) == (0, 0, 0) and tuple(rgb[-1]) == (255, 255, 255)
    assert len({tuple(z[3]) for z in fc.ZONES}) == 15       # all distinct


def test_the_negative_reads_density_above_the_base():
    base = (52000.0, 60000.0, 40000.0)                      # BGR, green 60000
    g = np.array([60000.0, 60000.0 / 10 ** 0.70, 60000.0 / 10 ** (0.70 + 2 * fc.STOP_DENSITY),
                  60000.0 / 10 ** (0.70 - fc.STOP_DENSITY)], np.float32)
    raw = np.stack([np.full_like(g, 1.0), g, np.full_like(g, 1.0)], -1)[None]
    s = fc.negative_stops(raw, base)[0]
    assert s[0] == pytest.approx(-0.70 / fc.STOP_DENSITY, abs=1e-3)   # the base: about -3.9
    assert s[1] == pytest.approx(0.0, abs=1e-3)              # mid grey at 0.70
    assert s[2] == pytest.approx(2.0, abs=1e-3)              # 2 stops over
    assert s[3] == pytest.approx(-1.0, abs=1e-3)
    assert fc.STOP_DENSITY == pytest.approx(0.6 * np.log10(2))


def test_the_picture_reads_linear_light_against_18_percent():
    px = np.array([[[118, 118, 118], [255, 255, 255], [13, 13, 13]]], np.uint8)
    s = fc.picture_stops(px)[0]
    assert abs(s[0]) < 0.05                                  # display 118 = 18 % grey
    assert s[1] == pytest.approx(np.log2(1 / 0.18), abs=0.01)   # white: +2.47
    assert -5.6 < s[2] < -5.3
    assert fc.zone_index(fc.picture_stops(px))[0, 1] == 10   # white is zone +2


def test_film_base_estimate_is_the_clearest_film():
    raw = np.full((100, 100, 3), 1000.0, np.float32)
    raw[:5] = (50000.0, 61000.0, 42000.0)                   # a clear rebate band
    b = fc.film_base(raw)
    assert b[1] == pytest.approx(61000.0)


# --------------------------------------------------------------------------- #
# canvas
# --------------------------------------------------------------------------- #

def _np(pm):
    from PySide6.QtGui import QImage
    q = pm.toImage().convertToFormat(QImage.Format_RGB888)
    w, h = q.width(), q.height()
    return np.frombuffer(q.constBits(), np.uint8).reshape(
        h, q.bytesPerLine())[:, :w * 3].reshape(h, w, 3).copy()


def test_picture_zones_on_the_canvas(tmp_path, monkeypatch, store):
    from test_false_colour import _canvas, _HOSTS  # noqa: F401
    ip, _img = _canvas(tmp_path, monkeypatch)        # positive mode: reads the picture
    ip.set_fc_scale("zones", "negative")
    ip.set_false_colour(True)
    shown = ip.pixmap_item.pixmap()
    layer = ip._fc_item.pixmap()
    assert np.array_equal(_np(layer), fc.stop_zones(_np(shown)))
    assert store["view/false_colour_scale"] == "zones"
    legend = ip._fc_legend
    assert legend.isVisible() and "picture" in legend.text() and "+6" in legend.text()
    ip.set_fc_scale("bands")
    assert np.array_equal(_np(ip._fc_item.pixmap()), fc.false_colour(_np(shown)))
    assert "black clipping" in ip._fc_legend.text()
    ip.set_false_colour(False)


def test_negative_zones_read_the_scan(tmp_path, monkeypatch, store):
    from test_false_colour import _canvas, _HOSTS  # noqa: F401
    ip, img = _canvas(tmp_path, monkeypatch)
    monkeypatch.setattr(ccr_backend, "positive_mode", False)
    monkeypatch.setattr(ccr_backend, "black_point_bgr", None, raising=False)
    img.converted = False
    reads = []
    real = ccr_backend.raw_preview
    monkeypatch.setattr(ccr_backend, "raw_preview",
                        lambda im: reads.append(1) or real(im))
    ip.set_fc_scale("zones", "negative")
    ip.set_false_colour(True)
    layer = _np(ip._fc_item.pixmap())
    # the scan is a ramp: its clear end is the film base (about -3.9 stops,
    # zone -4), its dense end far over (+6)
    z = {tuple(c): i for i, c in enumerate(fc._ZONE_RGB.tolist())}
    mid = layer.shape[0] // 2
    assert z[tuple(layer[mid, -2])] == 2                     # -4
    assert z[tuple(layer[mid, 1])] == 14                     # +6
    cols = [z[tuple(c)] for c in layer[mid, ::-1]]
    assert all(b >= a for a, b in zip(cols, cols[1:]))      # denser = more stops
    assert "negative" in ip._fc_legend.text() and "clearest film" in ip._fc_legend.text()
    n = len(reads)
    ip._fc_cache = None
    ip._refresh_fc_overlay()
    assert len(reads) == n                                   # cached on the image
    # a black point sampled: that is the base (a new reading)
    monkeypatch.setattr(ccr_backend, "black_point_bgr", (30000.0, 30000.0, 30000.0),
                        raising=False)
    ip._fc_cache = None
    ip._refresh_fc_overlay()
    assert len(reads) == n + 1 and "(black point)" in ip._fc_legend.text()
    ip.set_fc_scale(source="picture")
    assert "finished picture" in ip._fc_legend.text()
    ip.set_false_colour(False)


def test_right_click_menu_picks_and_turns_it_on(tmp_path, monkeypatch, store):
    from test_false_colour import _canvas, _HOSTS  # noqa: F401
    ip, _img = _canvas(tmp_path, monkeypatch)
    ip.set_fc_scale("bands", "negative")
    ip._show_fc_menu(ip.mapToGlobal(ip.rect().center()))
    menu = ip._fc_menu
    acts = {a.text().strip(): a for a in menu.actions() if a.text()}
    assert acts["Exposure bands (signal level)"].isChecked()
    acts["Stop zones (stops from mid grey)"].trigger()
    assert ip.fc_scale() == ("zones", "negative") and ip.fc_action.isChecked()
    acts["The finished picture"].trigger()
    assert ip.fc_scale() == ("zones", "picture")
    assert store["view/false_colour_zone_source"] == "picture"
    menu.close()
    ip.set_false_colour(False)
    ip.set_fc_scale("bands", "negative")


def test_remembered_between_launches(monkeypatch):
    from PySide6.QtCore import QSettings
    from widgets.image_preview import ImagePreview
    vals = {"view/false_colour_scale": "zones", "view/false_colour_zone_source": "picture"}
    monkeypatch.setattr(QSettings, "value",
                        lambda self, k, d=None, type=None: vals.get(k, d))
    assert ImagePreview._stored_fc_scale() == ("zones", "picture")
    vals["view/false_colour_scale"] = "nonsense"
    assert ImagePreview._stored_fc_scale()[0] == "bands"


def test_settings_page_stages_the_scale(monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import test_settings_dialog as tsd
    d = tsd._dialog()
    calls = []
    ip = type("IP", (), {"fc_scale": lambda s: ("bands", "negative"),
                         "set_fc_scale": lambda s, a, b: calls.append((a, b))})()
    d._mw.image_preview = ip
    d._init_toggles()
    assert d._combo_fc_scale.currentData() == "bands"
    d._combo_fc_scale.setCurrentIndex(d._combo_fc_scale.findData("zones"))
    d._apply_pending()
    assert calls == [("zones", "negative")]
