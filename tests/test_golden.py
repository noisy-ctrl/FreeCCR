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


def test_bright_greys_turn_gold_at_the_same_lightness():
    g = _px(0.8, 0.8, 0.8)
    out = gd.apply_golden(g, 100)
    lab0, lab1 = _lab(g)[0], _lab(out)[0]
    assert lab1[0] == pytest.approx(lab0[0], abs=0.002)          # as bright as before
    chroma = np.hypot(lab1[1], lab1[2])
    hue = np.degrees(np.arctan2(lab1[2], lab1[1]))
    assert 0.025 < chroma < 0.04 and hue == pytest.approx(72, abs=2)
    r, gg, b = out[0, 0]
    assert r > gg > b                                             # warm, not green/pink


def test_shadows_are_untouched():
    for v in (0.0, 0.05, 0.15, 0.25):
        p = _px(v, v, v)
        np.testing.assert_array_equal(gd.apply_golden(p, 100), p)


def test_a_pale_blue_sky_is_left_mostly_alone():
    grey, sky = _px(0.78, 0.8, 0.82), _px(0.62, 0.74, 0.9)
    gain = {}
    for name, p in (("grey", grey), ("sky", sky)):
        a, b = _lab(p)[0], _lab(gd.apply_golden(p, 100))[0]
        gain[name] = float(np.hypot(*(b[1:] - a[1:])))
    assert gain["sky"] < 0.2 * gain["grey"], gain


def test_clean_whites_keeps_white_neutral():
    """Clean whites 100 eases the brightest tones back to neutral; at 0 the gold
    runs on up (as far as fits: true white can't take colour without
    darkening, and the tool never darkens)."""
    w = _px(0.97, 0.97, 0.97)
    clean = _lab(gd.apply_golden(w, 100, {"clean": 100}))[0]
    golden = _lab(gd.apply_golden(w, 100, {"clean": 0}))[0]
    assert np.hypot(clean[1], clean[2]) < 0.004
    assert np.hypot(golden[1], golden[2]) > 0.012


def test_stays_in_range_without_clipping_a_colour():
    """A colour that can't take the full gold gets as much as fits: every
    output is in range and keeps its lightness (a hard-clipped channel would
    darken it and twist its hue)."""
    rng = np.random.default_rng(3)
    img = rng.random((80, 90, 3)).astype(np.float32)
    out = gd.apply_golden(img, 100, {"hue": 50})
    assert out.min() >= 0.0 and out.max() <= 1.0
    dL = np.abs(_lab(out)[:, 0] - _lab(img)[:, 0])
    assert dL.max() < 0.003, float(dL.max())


def test_grey_ramp_is_smooth_and_keeps_its_order():
    x = np.linspace(0, 1, 1001, dtype=np.float32)
    ramp = np.repeat(x[None, :, None], 3, axis=2)
    lab = _lab(gd.apply_golden(ramp, 100))
    assert np.all(np.diff(lab[:, 0]) > -1e-4)                        # lightness in order
    chroma = np.hypot(lab[:, 1], lab[:, 2])
    assert np.abs(np.diff(chroma)).max() < 0.0015                    # no steps: no banding


def test_options_are_normalised():
    assert gd.normalize_golden_shape({"hue": 72, "start": 40, "clean": 60}) is None
    assert gd.normalize_golden_shape({"hue": 200}) == {"hue": 95, "start": 40, "clean": 60}
    assert gd.normalize_golden_shape("junk") is None


def test_start_moves_where_it_begins():
    p = _px(0.45, 0.45, 0.45)
    lo = _lab(gd.apply_golden(p, 100, {"start": 20}))[0]
    hi = _lab(gd.apply_golden(p, 100, {"start": 70}))[0]
    assert np.hypot(lo[1], lo[2]) > 0.02 and np.hypot(hi[1], hi[2]) < 0.002


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


def test_slider_sits_right_above_colour_organics(panel):
    from PySide6.QtCore import Qt
    keys = panel.adjustment_keys
    assert keys[keys.index("colour_organics") - 1] == "golden_highlights"
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
    assert img.adjustment_settings["golden_shape"] == {"hue": 60, "start": 40, "clean": 90}
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
