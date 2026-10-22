#!/usr/bin/env python3
"""Golden Highlights (spec/golden-highlights.md)."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core import golden as gd  # noqa: E402
from core.ccr_processor import adjust_image, adjust_image_opencl, encode_window  # noqa: E402
from test_x_tones import _Img, _wire, _Stub, _img, _capture  # noqa: E402


def _lab(rgb):
    return gd.to_oklab(np.asarray(rgb, np.float32).reshape(-1, 3))


def _px(*rgb):
    return np.array([[rgb]], np.float32)


def test_zero_is_an_exact_no_op():
    img = _img()
    a = adjust_image(img, contrast=10, saturation=5)
    np.testing.assert_array_equal(a, adjust_image(img, contrast=10, saturation=5,
                                                  golden_highlights=0,
                                                  golden_shape={"hue": 60}))
    np.testing.assert_array_equal(adjust_image_opencl(img, contrast=10),
                                  adjust_image_opencl(img, contrast=10, golden_highlights=0))


def test_oklab_round_trip():
    x = np.random.default_rng(0).random((500, 3)).astype(np.float32)
    np.testing.assert_allclose(gd.from_oklab(gd.to_oklab(x)), x, atol=2e-5)


CREAM = (0.93, 0.89, 0.80)          # a sunlit cream: bright, a little warm


def _gain(rgb, amount=100, shape=None):
    p = _px(*rgb)
    a, b = _lab(p)[0], _lab(gd.apply_golden(p, amount, shape))[0]
    return float(np.hypot(*(b[1:] - a[1:]))), a, b


def test_sunlit_cream_gets_more_gold_at_the_same_lightness():
    gain, a, b = _gain(CREAM)
    assert gain > 0.012
    assert b[0] == pytest.approx(a[0], abs=0.002)                 # as bright as before
    hue = np.degrees(np.arctan2(b[2], b[1]))
    before = np.degrees(np.arctan2(a[2], a[1]))
    assert abs(hue - 72) < abs(before - 72)                       # towards gold, not yellow
    assert np.hypot(b[1], b[2]) > np.hypot(a[1], a[2])            # more of it


@pytest.mark.parametrize("name,rgb", [
    ("light skin", (0.86, 0.68, 0.58)), ("pale skin", (0.94, 0.84, 0.78)),
    ("tanned skin", (0.72, 0.52, 0.40)), ("pale blue sky", (0.75, 0.82, 0.90)),
    ("deep sky", (0.35, 0.55, 0.85)), ("lit foliage", (0.70, 0.80, 0.40)),
    ("red", (0.85, 0.20, 0.15)), ("yellow", (0.95, 0.85, 0.30)),
])
def test_skin_sky_and_colours_are_left_alone(name, rgb):
    cream, _a, _b = _gain(CREAM)
    other, _a, _b = _gain(rgb)
    assert other < 0.1 * cream, (name, other, cream)


def test_plain_greys_take_only_the_neutral_share():
    grey, _a, _b = _gain((0.85, 0.85, 0.85))
    full, _a, _b = _gain((0.85, 0.85, 0.85), shape={"neutral": 100})
    assert grey == pytest.approx(0.1 * full, rel=0.15)


def test_shadows_and_midtones_are_untouched():
    for rgb in ((0.0, 0.0, 0.0), (0.05, 0.05, 0.04), (0.2, 0.19, 0.17), (0.4, 0.38, 0.34)):
        p = _px(*rgb)
        np.testing.assert_array_equal(gd.apply_golden(p, 100), p)


def test_clean_whites_keeps_white_a_clean_cream():
    """Clean whites 100 eases the brightest tones back; at 0 the gold runs on
    up (as far as fits: near-white can't take colour without darkening, and
    the tool never darkens)."""
    w = (0.95, 0.93, 0.87)
    clean, _a, _b = _gain(w, shape={"clean": 100})
    golden, _a, _b = _gain(w, shape={"clean": 0})
    assert clean < 0.5 * golden


def test_stays_in_range_without_clipping_a_colour():
    """A colour that can't take the full gold gets as much as fits: every
    output is in range and keeps its lightness (a hard-clipped channel would
    darken it and twist its hue)."""
    rng = np.random.default_rng(3)
    img = rng.random((80, 90, 3)).astype(np.float32)
    out = gd.apply_golden(img, 100, {"hue": 50, "neutral": 100})
    assert out.min() >= 0.0 and out.max() <= 1.0
    dL = np.abs(_lab(out)[:, 0] - _lab(img)[:, 0])
    assert dL.max() < 0.003, float(dL.max())


@pytest.mark.parametrize("tint", [(1.0, 1.0, 1.0), (1.0, 0.975, 0.92), (0.95, 0.97, 1.0)])
def test_ramps_are_smooth_and_keep_their_order(tint):
    x = np.linspace(0, 1, 1001, dtype=np.float32)
    ramp = (x[:, None] * np.array(tint, np.float32))[None]
    lab = _lab(gd.apply_golden(ramp, 100, {"neutral": 60}))
    assert np.all(np.diff(lab[:, 0]) > -1e-4)                        # lightness in order
    chroma = np.hypot(lab[:, 1], lab[:, 2])
    assert np.abs(np.diff(chroma)).max() < 0.0015                    # no steps: no banding


def test_options_are_normalised():
    assert gd.normalize_golden_shape(dict(gd.GOLDEN_SHAPE_DEFAULT)) is None
    assert gd.normalize_golden_shape({"hue": 200}) == dict(gd.GOLDEN_SHAPE_DEFAULT, hue=95)
    assert gd.normalize_golden_shape("junk") is None


def test_start_moves_where_it_begins():
    warm = (0.62, 0.60, 0.55)
    lo, _a, _b = _gain(warm, shape={"start": 30})
    hi, _a, _b = _gain(warm, shape={"start": 85})
    assert lo > 0.008 and hi < 0.0005


def test_weight_map_matches_what_it_does():
    img = np.array([[CREAM, (0.86, 0.68, 0.58), (0.1, 0.1, 0.1)]], np.float32)
    w = gd.weight_map(img)
    assert w[0, 0] > 0.2 and w[0, 1] < 0.02 and w[0, 2] == 0.0


@pytest.mark.parametrize("ws", [False, True])
def test_gpu_matches_cpu(ws):
    rng = np.random.default_rng(1)
    img = (encode_window(rng.uniform(-0.1, 1.1, (30, 40, 3)).astype(np.float32))
           if ws else _img(2, (30, 40, 3)))
    kw = dict(golden_highlights=70, golden_shape={"hue": 66, "clean": 30},
              contrast=10, saturation=20, highlights_x=20)
    a = adjust_image(img, ws_windowed=ws, **kw)
    b = adjust_image_opencl(img, ws_windowed=ws, **kw)
    np.testing.assert_allclose(a.astype(np.int64), b.astype(np.int64), atol=3)


def test_image_passes_the_setting(monkeypatch):
    from core.ccr_image import CCRImage
    seen = _capture(monkeypatch)
    CCRImage.apply_adjustments(_Stub({"golden_highlights": 55, "golden_shape": {"hue": 60}}),
                               _img())
    assert seen["golden_highlights"] == 55 and seen["golden_shape"] == {"hue": 60}
    seen.clear()
    CCRImage._adjust_for_area(_Stub({}), _img(), {"golden_highlights": 20})
    assert seen["golden_highlights"] == 20


# --------------------------------------------------------------------------- #
# panel
# --------------------------------------------------------------------------- #

@pytest.fixture
def panel():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel
    return SlidersPanel()


def test_slider_sits_above_shadow_neutralise_and_colour_organics(panel):
    from PySide6.QtCore import Qt
    keys = panel.adjustment_keys
    i = keys.index("golden_highlights")
    assert keys[i + 1:i + 3] == ["shadow_neutralise", "colour_organics"]
    i = keys.index("golden_highlights")
    assert panel.sliders[i].value_range() == (0, 100)
    lbl = panel._golden_label
    assert lbl.text() == "Golden Highlights"
    assert lbl.contextMenuPolicy() == Qt.CustomContextMenu and "Right-click" in lbl.toolTip()
    from widgets.sliders_panel import SYNC_GROUPS
    assert "golden_highlights" in dict((g, k) for g, _l, k in SYNC_GROUPS)["sat"]


def test_popup_writes_options_with_one_undo(panel, monkeypatch):
    img = _Img()
    _wire(panel, monkeypatch, img)
    panel._open_golden_popup(panel.mapToGlobal(panel.rect().center()))
    pop = panel._golden_popup
    pop.sliders["hue"].setValue(60)
    pop.sliders["clean"].setValue(90)
    assert img.adjustment_settings["golden_shape"] == dict(gd.GOLDEN_SHAPE_DEFAULT, hue=60, clean=90)
    assert panel._golden_label.font().underline()
    pop.close()
    assert img.undo == 1
    panel.sliders[panel.adjustment_keys.index("saturation")].setValue(7)
    assert img.adjustment_settings["golden_shape"]["hue"] == 60
    panel._open_golden_popup(panel.mapToGlobal(panel.rect().center()))
    panel._golden_popup.reset()
    assert "golden_shape" not in img.adjustment_settings
    panel._golden_popup.close()


def test_paste_copy_and_sync(panel, monkeypatch):
    from widgets.sliders_panel import paste_options
    clip = {"adjustments": {}, "golden_shape": {"hue": 60}}
    row = [r for r in paste_options(clip, lambda k: 0) if r[1] == "golden_shape"]
    assert row and "hue 60" in row[0][2]
    img = _Img({"contrast": 5})
    _wire(panel, monkeypatch, img)
    panel.current_idx = 1
    panel.clipboard = dict(clip, curves=None, cineon_log=False, look_lut=None,
                           flags={}, profile="color", crop=(None, 0.0), rotation=0,
                           flip_h=False, flip_v=False, fine_rotation=0)
    panel._apply_paste(0, {"golden_shape"})
    assert img.adjustment_settings["golden_shape"]["hue"] == 60
    src = _Img({"golden_highlights": 40, "golden_shape": {"clean": 10}})
    _wire(panel, monkeypatch, src)
    panel.current_idx = 5
    panel.copy_settings_from_index(0)
    assert panel.clipboard["golden_shape"]["clean"] == 10
    assert panel.clipboard["adjustments"]["golden_highlights"] == 40

    from core.ccr_backend import ccr_backend
    from widgets.sliders_panel import SYNC_GROUPS
    dst = _Img({"golden_highlights": 0})
    for im in (src, dst):
        im.crop_rect, im.crop_angle, im.color_profile = None, 0.0, "color"
    _wire(panel, monkeypatch, src)
    monkeypatch.setattr(ccr_backend, "images", [src, dst])
    monkeypatch.setattr(ccr_backend, "get_image_by_index", lambda i: [src, dst][i])
    panel.current_idx = 0
    panel._sync_group_selection = {gid: gid == "sat" for gid, _l, _k in SYNC_GROUPS}
    panel._perform_sync_to_all()
    assert dst.adjustment_settings["golden_highlights"] == 40
    assert dst.adjustment_settings["golden_shape"]["clean"] == 10


def test_show_where_it_acts_follows_the_popup(panel, monkeypatch):
    img = _Img()
    _wire(panel, monkeypatch, img)
    calls = []
    ip = type("IP", (), {"update_preview": lambda s, i: None,
                         "set_gold_mask": lambda s, shape: calls.append(shape)})()
    mw = type("MW", (), {"image_preview": ip})()
    monkeypatch.setattr(panel, "parent", lambda: type("P", (), {"parent": lambda s: mw})())
    panel._open_golden_popup(panel.mapToGlobal(panel.rect().center()))
    pop = panel._golden_popup
    pop.mask_check.setChecked(True)
    assert calls[-1] == {}                                   # default options
    pop.sliders["neutral"].setValue(40)
    assert calls[-1]["neutral"] == 40                        # follows the options
    pop.close()
    assert calls[-1] is None                                 # gone with the popup


def test_canvas_layer_shows_where_it_acts(tmp_path, monkeypatch):
    from test_false_colour import _canvas, _HOSTS  # noqa: F401
    from PySide6.QtGui import QImage
    ip, _img_ = _canvas(tmp_path, monkeypatch)
    ip.set_gold_mask({"neutral": 100})
    item = ip._gm_item
    assert item is not None and item.isVisible() and item.parentItem() is ip.pixmap_item
    q = item.pixmap().toImage().convertToFormat(QImage.Format_RGBA8888)
    w, h = q.width(), q.height()
    a = np.frombuffer(q.constBits(), np.uint8).reshape(h, q.bytesPerLine())[:, :w * 4]
    alpha = a.reshape(h, w, 4)[..., 3]
    assert alpha[:, : w // 10].max() == 0 and alpha[:, w // 2:].max() > 0  # dark end clear
    ip.set_gold_mask(None)
    assert not ip._gm_item.isVisible()
