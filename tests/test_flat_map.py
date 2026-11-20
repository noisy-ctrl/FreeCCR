#!/usr/bin/env python3
"""The flat field map (spec/flat-field-map.md): what a frame's flat field
correction does, shown as a display layer."""
import os
import sys
import types

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
sys.path.insert(0, HERE)

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core import flat_field as ff  # noqa: E402
from core import flat_map as fm  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ff.set_library([])
    ff.set_active_profile(None)
    ff.set_preset_flats({})
    store = {}
    monkeypatch.setattr(QSettings, "setValue", lambda self, k, v: store.__setitem__(k, v))
    monkeypatch.setattr(QSettings, "value",
                        lambda self, k, d=None, type=None: store.get(k, d))
    yield store
    ff.set_library([])


def _profile(corner_ev=0.5, hot=0.0, h=96, w=128):
    """A flat: 1 at the centre, up to `corner_ev` stops in the corners, and
    `hot` stops brighter than the centre (a lowering) on the right."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = (xx + 0.5) / w - 0.5, (yy + 0.5) / h - 0.5
    rr = (u * u + v * v) / 0.5
    ev = corner_ev * rr
    ev -= hot * np.clip((u - 0.2) / 0.3, 0, 1)
    gain = np.repeat((2.0 ** ev)[..., None], 3, 2).astype(np.float32)
    gain[..., 0] *= 2 ** (0.05 * rr)                   # red lifted a little more
    return ff.FieldProfile(name="t", gain=gain, aspect=w / h,
                           meta={"light": "white", "film_format": "645"})


# --- the maths -------------------------------------------------------------------

def test_ev_and_frame_gain():
    p = _profile()
    g = fm.frame_gain(p, (600, 800), None, (400, 300))
    assert g.shape == (300, 400, 3)
    ev = fm.ev_map(g)
    assert abs(ev[150, 200]) < 0.01 and ev[2, 2] > 0.4          # centre 0, corners lifted
    # a slice of the right half of the frame shows the right half of the map
    full = fm.ev_map(fm.frame_gain(p, (600, 800), None, (800, 600)))
    right = fm.ev_map(fm.frame_gain(p, (600, 800), [(0, (0.5, 0.0, 1.0, 1.0))], (400, 600)))
    assert np.allclose(right[:, 10:-10], full[:, 410:790], atol=0.02)
    mono = fm.frame_gain(p, (600, 800), None, (80, 60), mono=True)
    assert np.allclose(mono[..., 0], mono[..., 2])


def test_the_layer_is_pink_for_lifts_blue_for_lowers():
    p = _profile(corner_ev=0.5, hot=0.3)
    ev = fm.ev_map(fm.frame_gain(p, (600, 800), None, (400, 300)))
    lay = fm.layer_rgba(ev, 1, contours=False)
    assert lay[150, 200, 3] < 3                                 # no change: clear
    assert tuple(lay[5, 5, :3]) == fm.PINK and lay[5, 5, 3] > 100
    assert tuple(lay[150, 395, :3]) == fm.BLUE and lay[150, 395, 3] > 8
    twice = fm.layer_rgba(ev, 2, contours=False)
    y, x = 120, 150                                             # a small lift
    assert abs(int(twice[y, x, 3]) - 2 * int(lay[y, x, 3])) <= 2
    assert twice[5, 5, 3] == round(fm.WASH_ALPHA * 255)         # capped
    assert len(fm.levels(ev, 8)) > len(fm.levels(ev, 1))
    assert 0.0 not in fm.levels(ev, 1)
    lined = fm.layer_rgba(ev, 1, contours=True)
    white = (lined[..., :3] == 255).all(-1) & (lined[..., 3] > 200)
    assert white.sum() > 100                                    # the lines are there
    texts = [t for _x, _y, t in fm.label_points(ev, 1)]
    assert texts[0] == "+0.1" and len(texts) == len(fm.levels(ev, 1))
    deep = fm.ev_map(fm.frame_gain(_profile(hot=0.6), (600, 800), None, (400, 300)))
    assert "-0.1" in [t for _x, _y, t in fm.label_points(deep, 1)]   # a lowering's line
    assert fm.exaggeration("3") == fm.DEFAULT_GAIN and fm.exaggeration(4) == 4


# --- the canvas -------------------------------------------------------------------

def _canvas(tmp_path, monkeypatch):
    from test_false_colour import _canvas as fc_canvas
    return fc_canvas(tmp_path, monkeypatch)


def _shown_np(pm):
    from test_false_colour import _to_np
    return _to_np(pm)


def test_the_map_is_its_own_layer(tmp_path, monkeypatch):
    ip, img = _canvas(tmp_path, monkeypatch)
    p = _profile()
    raw = img.resized_preview
    img._flat_used = (p, (raw.height(), raw.width()), False)
    img.flat_applied = ff.applied_record(p, "moved")
    before = ip._capture_display_image()[0].copy()
    ip.set_flat_map(True)
    item = ip._fm_item
    assert item is not None and item.isVisible() and item.zValue() == 1.4
    assert item.parentItem() is ip.pixmap_item
    shown = ip.pixmap_item.pixmap()
    layer = item.pixmap()
    t = item.transform()
    assert round(layer.width() * t.m11()) == shown.width()
    assert np.array_equal(ip._capture_display_image()[0], before)   # pixels untouched
    legend = ip._fm_legend
    assert legend.isVisible() and "followed the carrier" in legend.text()
    assert "Brightens by up to +0.5" in legend.text() and "Shown ×2" in legend.text()
    assert ip.flat_map_action.isChecked()
    # with False colour on too, the map's legend sits above its legend
    ip.set_false_colour(True)
    assert ip._fm_legend.y() + ip._fm_legend.height() <= ip._fc_legend.y()
    ip.set_false_colour(False)
    ip.set_flat_map(False)
    assert not item.isVisible() and not legend.isVisible()


def test_right_click_exaggerates_and_is_remembered(tmp_path, monkeypatch, _clean):
    ip, img = _canvas(tmp_path, monkeypatch)
    img._flat_used = (_profile(), (90, 150), False)
    menu = ip.flat_map_menu()
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts[0] == "Exaggeration" and texts[1].startswith("×1")
    assert texts[-1] == "Contour lines"
    by = {a.text().split(" ")[0]: a for a in menu.actions()}
    assert by["×2"].isChecked()                                 # the default
    by["×8"].trigger()
    assert ip._fm_gain == 8 and ip._flat_map and _clean["view/flat_map_gain"] == 8
    assert "Shown ×8 · lines every 0.01 EV" in ip._fm_legend.text()
    {a.text(): a for a in ip.flat_map_menu().actions()}["Contour lines"].trigger()
    assert ip._fm_contours is False and _clean["view/flat_map_contours"] is False
    assert ip._stored_flat_map() == (8, False)
    ip.set_flat_map(False)


def test_a_frame_with_no_flat_says_so(tmp_path, monkeypatch):
    ip, img = _canvas(tmp_path, monkeypatch)
    img._flat_used = None
    ip.set_flat_map(True)
    assert ip._fm_item is None or not ip._fm_item.isVisible()
    assert "No flat field on this frame" in ip._fm_legend.text()
    ip.set_flat_map(False)


def test_the_tool_sits_left_of_hdr():
    from ui.main_window import MainWindow
    ip = MainWindow().image_preview
    names = [a.text() for a in ip.toolbar.actions() if a.text()]
    assert names[names.index("HDR") - 1] == "Flat field map"
    assert not ip.flat_map_action.icon().isNull()
    assert ip.flat_map_action.toolTip().startswith("Flat field map:")
