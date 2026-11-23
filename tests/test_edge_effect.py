#!/usr/bin/env python3
"""Developer edge effect (spec/developer-edge-effect.md): the border a
developer leaves where dense meets thin, on the negative's density, between
the grain's size and the developer's reach (mm on the film), cored so grain
and plain areas are left alone, soft-limited. Held to the finishing tools'
rules: an exact no-op at 0, untouched tones back exactly, a smooth ramp stays
monotone, grain doesn't grow, resolution independent."""

import os
import sys
import types

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core import edge_effect as ee  # noqa: E402
from core.ccr_processor import WS_B, WS_W  # noqa: E402
from test_x_tones import _Img, _wire, _Stub, _capture  # noqa: E402

WS = (WS_B, WS_W)
SPAN = WS_W - WS_B


def _base(v):
    """A windowed uint16 base from display values `v` (H, W[, 3])."""
    v = np.asarray(v, np.float64)
    if v.ndim == 2:
        v = np.repeat(v[..., None], 3, axis=2)
    return np.clip(np.rint(v * SPAN + WS_B), 0, 65535).astype(np.uint16)


def _v(base):
    return (base.astype(np.float64) - WS_B) / SPAN


def _step(px_mm, mm=(4.0, 2.0), lo=0.3, hi=0.6, at=0.5):
    """A vertical step from `lo` to `hi` at `at` of the width."""
    w, h = int(mm[0] * px_mm), int(mm[1] * px_mm)
    x = np.full((h, w), lo, np.float64)
    x[:, int(at * w):] = hi
    return x


def _grain(shape, sigma, seed=0, size_px=1.2):
    g = np.random.default_rng(seed).normal(0.0, 1.0, shape).astype(np.float32)
    g = cv2.GaussianBlur(g, (0, 0), size_px)
    return g / g.std() * sigma


# --------------------------------------------------------------------------- #
# the maths
# --------------------------------------------------------------------------- #

def test_zero_is_an_exact_no_op():
    b = _base(_step(40))
    assert ee.apply_edge_effect(b, 0, 40.0, ws=WS) is b
    assert ee.apply_edge_effect(b, -5, 40.0, ws=WS) is b


def test_options_are_normalised():
    assert ee.normalize_edge_shape(None) is None
    assert ee.normalize_edge_shape({}) is None
    assert ee.normalize_edge_shape({"reach": 10, "layers": 0}) is None
    assert ee.normalize_edge_shape({"reach": 99}) == {"reach": 50, "layers": 0}
    assert ee.normalize_edge_shape({"reach": 1, "layers": 5}) == {"reach": 5, "layers": 1}
    assert ee.normalize_edge_shape({"reach": "x", "layers": 1}) == {"reach": 10, "layers": 1}


def test_a_step_gets_a_developer_border_within_the_limit():
    """Dense meets thin: the bright (thin) side brightens and the dark side
    darkens beside the edge, by no more than amount x LIMIT, gone a few
    reaches away; the step itself is kept."""
    pxmm = 60.0
    x = _step(pxmm)
    out = _v(ee.apply_edge_effect(_base(x), 100, pxmm, ws=WS))
    d = out - _v(_base(x))
    row = d[d.shape[0] // 2, :, 0]
    e = int(0.5 * x.shape[1])
    near = int(0.1 * pxmm)                       # 0.1 mm either side
    assert row[e:e + near].max() > 0.01          # thin side brighter
    assert row[e - near:e].min() < -0.01         # dense side darker
    assert np.abs(d).max() <= ee.LIMIT + 1.0 / SPAN
    far = int(0.6 * pxmm)                        # 6 reaches away: back exactly
    assert np.all(row[:e - far] == 0) and np.all(row[e + far:] == 0)
    assert out[0, e + 2 * near, 0] > out[0, e - 2 * near, 0]


def test_a_smooth_ramp_stays_monotone_and_almost_untouched():
    pxmm = 60.0
    w = int(4 * pxmm)
    ramp = np.tile(np.linspace(0.1, 0.9, w), (40, 1))
    b = _base(ramp)
    out = ee.apply_edge_effect(b, 100, pxmm, ws=WS)
    row = out[20, :, 0].astype(np.int64)
    assert np.all(np.diff(row) >= 0)
    mid = slice(w // 4, 3 * w // 4)
    assert np.abs(row[mid] - b[20, mid, 0].astype(np.int64)).max() <= 1


def test_grain_on_a_plain_area_barely_grows():
    """Plain sky with grain: the coring leaves it (x <= 1.05), where a plain
    unsharp mask at the same reach would grow it a good deal."""
    pxmm = 60.0
    shape = (int(3 * pxmm), int(4 * pxmm))
    x = 0.5 + _grain(shape, 0.01)
    b = _base(x)
    out = _v(ee.apply_edge_effect(b, 100, pxmm, ws=WS))[..., 0]
    v = _v(b)[..., 0]
    hp = lambda a: a - cv2.GaussianBlur(a.astype(np.float32), (0, 0), 0.1 * pxmm)
    g0 = hp(v)[20:-20, 20:-20].std()
    g1 = hp(out)[20:-20, 20:-20].std()
    usm = v + ee.LIMIT * np.tanh(hp(v) / ee.LIMIT)
    g2 = hp(usm)[20:-20, 20:-20].std()
    assert g1 / g0 <= 1.05
    assert g2 / g0 >= 1.5


def test_mid_scale_shapes_gain_contrast_through_the_grain():
    """A soft shape the size of the reach, under grain: its form (the band
    between grain and reach) grows; the grain beside it doesn't."""
    pxmm = 60.0
    h, w = int(3 * pxmm), int(4 * pxmm)
    x = np.full((h, w), 0.45, np.float32)
    for k, cx in enumerate(np.linspace(0.15, 0.85, 7)):
        cv2.circle(x, (int(cx * w), h // 2), int(0.12 * pxmm), 0.6 if k % 2 else 0.3, -1)
    x = cv2.GaussianBlur(x, (0, 0), 0.03 * pxmm)
    x = x + _grain(x.shape, 0.006)
    b = _base(x)
    out = _v(ee.apply_edge_effect(b, 100, pxmm, ws=WS))[..., 0]
    v = _v(b)[..., 0]

    def band(a):
        a = a.astype(np.float32)
        return cv2.GaussianBlur(a, (0, 0), 0.03 * pxmm) - cv2.GaussianBlur(a, (0, 0), 0.2 * pxmm)
    rows = slice(h // 2 - int(0.3 * pxmm), h // 2 + int(0.3 * pxmm))
    assert band(out)[rows].std() / band(v)[rows].std() >= 1.1


@pytest.mark.parametrize("opts", [None, {"reach": 25}])
def test_the_same_in_mm_at_every_size(opts):
    """The same frame at 30 and 90 px/mm: the border across an edge has the
    same size in mm and the same strength."""
    profiles = []
    for pxmm in (30.0, 90.0):
        x = _step(pxmm)
        d = _v(ee.apply_edge_effect(_base(x), 80, pxmm, opts, ws=WS)) - _v(_base(x))
        row = d[d.shape[0] // 2, :, 0]
        mm = (np.arange(row.size) + 0.5) / pxmm
        profiles.append((mm, row))
    grid = np.linspace(1.0, 3.0, 400)
    a = np.interp(grid, *profiles[0])
    b = np.interp(grid, *profiles[1])
    assert abs(a.max() - b.max()) <= 0.15 * b.max()
    assert abs(a.min() - b.min()) <= 0.15 * abs(b.min())
    assert abs(grid[a.argmax()] - grid[b.argmax()]) <= 0.04


def test_together_adds_no_colour_at_a_coloured_edge():
    pxmm = 60.0
    x = _step(pxmm)
    v = np.stack([x, x * 0.6, x * 0.3], axis=2)
    b = _base(v)
    d = _v(ee.apply_edge_effect(b, 100, pxmm, ws=WS)) - _v(b)
    assert np.abs(d[..., 0] - d[..., 1]).max() <= 1.0 / SPAN
    assert np.abs(d[..., 0] - d[..., 2]).max() <= 1.0 / SPAN
    each = _v(ee.apply_edge_effect(b, 100, pxmm, {"layers": 1}, ws=WS)) - _v(b)
    assert np.abs(each[..., 0] - each[..., 2]).max() > 0.005     # each its own


def test_each_layer_equals_together_on_a_neutral_edge():
    pxmm = 60.0
    b = _base(_step(pxmm))
    a = ee.apply_edge_effect(b, 70, pxmm, ws=WS).astype(np.int64)
    c = ee.apply_edge_effect(b, 70, pxmm, {"layers": 1}, ws=WS).astype(np.int64)
    assert np.abs(a - c).max() <= 1


def test_a_plain_base_works_in_full_range():
    pxmm = 60.0
    x = _step(pxmm)
    b = np.clip(np.rint(np.repeat(x[..., None], 3, 2) * 65535), 0, 65535).astype(np.uint16)
    out = ee.apply_edge_effect(b, 100, pxmm, ws=None)
    d = (out.astype(np.float64) - b) / 65535.0
    assert d.max() > 0.01 and d.min() < -0.01 and np.abs(d).max() <= ee.LIMIT + 1e-4


def test_reduced_blur_matches_a_full_one():
    x = np.random.default_rng(3).random((300, 400)).astype(np.float32)
    x = cv2.GaussianBlur(x, (0, 0), 1.5)
    full = cv2.GaussianBlur(x, (0, 0), 22.0, borderType=cv2.BORDER_REFLECT)
    assert np.abs(ee._blur(x, 22.0) - full)[30:-30, 30:-30].max() < 5e-4


def test_film_size_comes_from_the_frame():
    img = types.SimpleNamespace(film_format="645", capture_guide=None,
                                auto_crop_last=None, crop_rect=None)
    assert ee.frame_long_mm(img) == 56.0
    img = types.SimpleNamespace(film_format=None, capture_guide={"format": "6x7"},
                                auto_crop_last=None, crop_rect=None)
    assert ee.frame_long_mm(img) == 69.0
    img = types.SimpleNamespace(film_format=None, capture_guide=None,
                                auto_crop_last=None, crop_rect=None)
    assert ee.frame_long_mm(img) == ee.DEFAULT_LONG_MM
    # the crop's long side is the frame
    assert ee.px_per_mm((1000, 1500, 3), 36.0, (0.1, 0.1, 0.9, 0.9)) == pytest.approx(1200 / 36.0)
    assert ee.px_per_mm((1000, 1500, 3), 36.0) == pytest.approx(1500 / 36.0)


# --------------------------------------------------------------------------- #
# the image
# --------------------------------------------------------------------------- #

class _EdgeStub(_Stub):
    from core.ccr_image import CCRImage as _C
    EDGE_FIELD_CACHE = _C.EDGE_FIELD_CACHE
    EDGE_CACHE_MAX_LONG = _C.EDGE_CACHE_MAX_LONG
    _apply_edge_effect = _C._apply_edge_effect
    crop_rect = None
    film_format = "35mm"


def test_image_runs_it_on_the_base_and_keeps_the_field(monkeypatch):
    from core.ccr_image import CCRImage
    seen = {}

    def cap(image, *a, **kw):
        seen["image"] = image
        return image
    import core.ccr_image as mod
    monkeypatch.setattr(mod, "adjust_image_opencl", cap)
    stub = _EdgeStub({"edge_effect": 60})
    stub._ws_windowed = True
    base = _base(_step(30.0, mm=(36.0, 24.0)))
    CCRImage.apply_adjustments(stub, base)
    first = seen["image"]
    assert first is not base and np.any(first != base)
    assert len(stub._edge_field_cache) == 1
    CCRImage.apply_adjustments(stub, base)                 # kept field: same picture
    np.testing.assert_array_equal(seen["image"], first)
    assert len(stub._edge_field_cache) == 1
    stub.adjustment_settings = {"edge_effect": 60, "edge_shape": {"reach": 20}}
    CCRImage.apply_adjustments(stub, base)
    stub.adjustment_settings = {"edge_effect": 60, "edge_shape": {"reach": 30}}
    CCRImage.apply_adjustments(stub, base)
    assert len(stub._edge_field_cache) == stub.EDGE_FIELD_CACHE
    # a fresh field (no cache, as an export) gives the same picture
    stub.adjustment_settings = {"edge_effect": 60}
    fresh = ee.apply_edge_effect(base, 60, ee.px_per_mm(base.shape, 36.0), ws=WS)
    np.testing.assert_array_equal(first, fresh)
    # off, and on a detached patch: the base passes untouched
    stub.adjustment_settings = {"edge_effect": 0, "contrast": 1}
    CCRImage.apply_adjustments(stub, base)
    assert seen["image"] is base
    stub.adjustment_settings = {"edge_effect": 60}
    CCRImage.apply_adjustments(stub, base, skip_dust=True)
    assert seen["image"] is base


# --------------------------------------------------------------------------- #
# panel
# --------------------------------------------------------------------------- #

@pytest.fixture
def panel():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel
    return SlidersPanel()


def test_slider_sits_under_masking_in_details(panel):
    from PySide6.QtCore import Qt
    from widgets.sliders_panel import SYNC_GROUPS
    keys = panel.adjustment_keys
    i = keys.index("edge_effect")
    assert keys[i - 1] == "sharpen_masking" and i == len(keys) - 1
    assert panel.sliders[i].value_range() == (0, 100) and panel.sliders[i].value() == 0
    lbl = panel._edge_label
    assert lbl.text() == "Edge Effect"
    assert lbl.contextMenuPolicy() == Qt.CustomContextMenu and "Right-click" in lbl.toolTip()
    assert "edge_effect" in dict((g, k) for g, _l, k in SYNC_GROUPS)["details"]
    # laid out under sharpening's "Bypass until export", which isn't its own
    lay = panel.details_section._content_layout
    assert lay.indexOf(panel.edge_effect_row) > lay.indexOf(panel.sharpen_bypass_checkbox)


def test_popup_writes_options_with_one_undo(panel, monkeypatch):
    img = _Img()
    _wire(panel, monkeypatch, img)
    panel._open_edge_popup(panel.mapToGlobal(panel.rect().center()))
    pop = panel._edge_popup
    assert pop.reach_lbl.text() == "0.10 mm"
    pop.reach.setValue(15)
    pop.reach.setValue(20)
    pop.layers.setChecked(True)
    assert img.adjustment_settings["edge_shape"] == {"reach": 20, "layers": 1}
    assert panel._edge_label.font().underline()
    pop.close()
    assert img.undo == 1
    panel.sliders[panel.adjustment_keys.index("saturation")].setValue(7)
    assert img.adjustment_settings["edge_shape"]["reach"] == 20
    panel._open_edge_popup(panel.mapToGlobal(panel.rect().center()))
    panel._edge_popup.reset()
    assert "edge_shape" not in img.adjustment_settings
    panel._edge_popup.close()


def test_paste_copy_and_sync(panel, monkeypatch):
    from widgets.sliders_panel import paste_options, SYNC_GROUPS
    clip = {"adjustments": {"edge_effect": 40}, "edge_shape": {"reach": 25}}
    rows = paste_options(clip, lambda k: 0)
    row = [r for r in rows if r[1] == "edge"]
    assert row and row[0][0] == "Detail" and "40" in row[0][2] and "0.25 mm" in row[0][2]
    assert not [r for r in rows if r[1] == "adj:edge_effect"]
    img = _Img({"contrast": 5})
    _wire(panel, monkeypatch, img)
    panel.current_idx = 1
    panel.clipboard = dict(clip, curves=None, cineon_log=False, look_lut=None,
                           flags={}, profile="color", crop=(None, 0.0), rotation=0,
                           flip_h=False, flip_v=False, fine_rotation=0)
    panel._apply_paste(0, {"edge"})
    assert img.adjustment_settings["edge_effect"] == 40
    assert img.adjustment_settings["edge_shape"]["reach"] == 25
    assert img.adjustment_settings["contrast"] == 5

    src = _Img({"edge_effect": 30, "edge_shape": {"reach": 15, "layers": 1}})
    _wire(panel, monkeypatch, src)
    panel.current_idx = 5
    panel.copy_settings_from_index(0)
    assert panel.clipboard["edge_shape"] == {"reach": 15, "layers": 1}
    assert panel.clipboard["adjustments"]["edge_effect"] == 30

    from core.ccr_backend import ccr_backend
    dst = _Img({"edge_effect": 0})
    for im in (src, dst):
        im.crop_rect, im.crop_angle, im.color_profile = None, 0.0, "color"
    _wire(panel, monkeypatch, src)
    monkeypatch.setattr(ccr_backend, "images", [src, dst])
    monkeypatch.setattr(ccr_backend, "get_image_by_index", lambda i: [src, dst][i])
    panel.current_idx = 0
    panel._sync_group_selection = {gid: gid == "details" for gid, _l, _k in SYNC_GROUPS}
    panel._perform_sync_to_all()
    assert dst.adjustment_settings["edge_effect"] == 30
    assert dst.adjustment_settings["edge_shape"] == {"reach": 15, "layers": 1}


def test_reset_clears_the_options(panel, monkeypatch):
    img = _Img({"edge_effect": 30, "edge_shape": {"reach": 20}})
    _wire(panel, monkeypatch, img)
    panel._edge_shape = {"reach": 20, "layers": 0}
    panel._update_edge_label()
    assert panel._edge_label.font().underline()
    panel.on_reset_clicked()
    assert panel._edge_shape is None and not panel._edge_label.font().underline()
