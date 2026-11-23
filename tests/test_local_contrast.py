#!/usr/bin/env python3
"""Local Contrast (spec/local-contrast.md): the Details list's methods,
Lighting & Detail, Local Laplacian and Detail Bands (Edge Effect has its own
tests), held to the finishing tools' rules: an exact no-op at 0, a smooth
ramp stays monotone, grain doesn't grow, little or no halo, nothing clips,
colour ratios kept, the same in shares of the frame at every size."""

import os
import sys

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core import local_contrast as lc  # noqa: E402
from core.oklab import _lin  # noqa: E402
from test_x_tones import _Img, _wire, _Stub  # noqa: E402

FULL = {"light": {"lc_lighting": 0, "lc_detail": 100},
        "llf": {"lc_lighting": 0, "lc_detail": 100},
        "bands": {"lc_texture": 100, "lc_structure": 100, "lc_shape": 100}}
FLAT = {"light": {"lc_lighting": -100}, "llf": {"lc_lighting": -100}}
PXMM = 1600 / 56.0


def _u16(x):
    x = np.asarray(x, np.float32)
    if x.ndim == 2:
        x = np.repeat(x[..., None], 3, axis=2)
    return (np.clip(x, 0, 1) * 65535 + 0.5).astype(np.uint16)


def _run(img, m, s, pxmm=PXMM, crop=None):
    return lc.apply_local_contrast(img, dict(s, local_contrast=m), pxmm, crop)


def _step(h=500, w=800, lo=0.2, hi=0.7):
    x = np.full((h, w), lo, np.float32)
    x[:, w // 2:] = hi
    return x


# --------------------------------------------------------------------------- #
# choosing
# --------------------------------------------------------------------------- #

def test_method_of_reads_the_choice_and_the_old_edge_effect():
    assert lc.method_of({}) == "off"
    assert lc.method_of({"edge_effect": 30}) == "edge"          # before the list
    assert lc.method_of({"edge_effect": 30, "local_contrast": "off"}) == "off"
    assert lc.method_of({"local_contrast": "llf"}) == "llf"
    assert lc.method_of({"local_contrast": "nonsense"}) == "off"
    assert not lc.active({"local_contrast": "light"})
    assert lc.active({"local_contrast": "light", "lc_detail": 5})
    assert not lc.active({"local_contrast": "bands", "lc_detail": 5})   # not its slider


@pytest.mark.parametrize("m", ["light", "llf", "bands"])
def test_zero_and_off_are_exact_no_ops(m):
    img = _u16(_step())
    assert _run(img, m, {}) is img
    assert lc.apply_local_contrast(img, dict(FULL[m], local_contrast="off"), PXMM) is img
    assert lc.apply_local_contrast(img, dict(FULL[m]), PXMM) is img      # no method set


# --------------------------------------------------------------------------- #
# the picture's quality
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("m", ["light", "llf", "bands"])
def test_a_smooth_ramp_stays_monotone_and_almost_untouched(m):
    w = 1600
    ramp = np.tile(np.linspace(0.05, 0.95, w, dtype=np.float32), (400, 1))
    b = _u16(ramp)
    o = _run(b, m, FULL[m])[200, :, 0].astype(np.int64)       # a ramp has no detail
    assert np.all(np.diff(o) >= 0)
    mid = slice(w // 8, -w // 8)
    assert np.abs(o[mid] - b[200, mid, 0].astype(np.int64)).max() <= 0.02 * 65535
    if m in FLAT:                                              # a ramp IS lighting
        o = _run(b, m, FLAT[m])[200, :, 0].astype(np.int64)
        assert np.all(np.diff(o) >= 0)


@pytest.mark.parametrize("m,halo", [("light", 0.01), ("llf", 0.03), ("bands", 0.06)])
def test_a_hard_edge_throws_little_or_no_halo(m, halo):
    """A 3.8-stop step at full strength: how far either side moves past its
    own level (Bands is softly limited and kept out of strong edges)."""
    x = _step()
    o = _run(_u16(x), m, FULL[m])[250, :, 0] / 65535.0
    left, right = o[:398], o[402:]
    assert left.min() >= 0.2 - halo and left.max() <= 0.2 + halo
    assert right.max() <= 0.7 + halo and right.min() >= 0.7 - halo


@pytest.mark.parametrize("m", ["light", "llf", "bands"])
def test_grain_on_a_plain_patch_does_not_grow(m):
    """Film-sized grain on plain sky at full strength: ≤ ×1.06."""
    pxmm = 4000 / 56.0
    g = cv2.GaussianBlur(np.random.default_rng(0).normal(0, 1, (700, 1000)).astype(np.float32),
                         (0, 0), 0.6)
    x = 0.55 + g / g.std() * 0.015
    b = _u16(x)

    def hp(a):
        a = a.astype(np.float32)
        return (a - cv2.GaussianBlur(a, (0, 0), 3.0))[60:-60, 60:-60].std()
    o = _run(b, m, FULL[m], pxmm)[..., 1]
    assert hp(o) / hp(b[..., 1]) <= 1.06


@pytest.mark.parametrize("m", ["light", "llf", "bands"])
def test_forms_gain_contrast(m):
    """Soft shapes under a little grain gain contrast at their own size."""
    h, w = 600, 900
    x = np.full((h, w), 0.45, np.float32)
    for k, cx in enumerate(np.linspace(0.12, 0.88, 7)):
        cv2.circle(x, (int(cx * w), h // 2), 40, 0.6 if k % 2 else 0.32, -1)
    x = cv2.GaussianBlur(x, (0, 0), 6.0)
    x = x + cv2.GaussianBlur(np.random.default_rng(1).normal(0, 0.004, x.shape).astype(np.float32),
                             (0, 0), 0.6)
    b = _u16(x)
    o = _run(b, m, FULL[m])[..., 1].astype(np.float32)
    rows = slice(h // 2 - 60, h // 2 + 60)

    def form(a):
        a = a.astype(np.float32)
        return (cv2.GaussianBlur(a, (0, 0), 3) - cv2.GaussianBlur(a, (0, 0), 60))[rows].std()
    assert form(o) / form(b[..., 1]) >= 1.08


@pytest.mark.parametrize("m", ["light", "llf", "bands"])
def test_nothing_clips_and_colour_ratios_are_kept(m):
    h, w = 400, 700
    rng = np.random.default_rng(2)
    base = cv2.GaussianBlur(rng.random((h, w)).astype(np.float32), (0, 0), 12)
    base = (base - base.min()) / (base.max() - base.min())
    v = np.stack([0.15 + 0.85 * base, 0.1 + 0.6 * base, 0.05 + 0.3 * base], axis=2)
    v[:40, :, :] = 1.0                                   # a band at white
    b = _u16(v)
    for s in (FULL[m], FLAT.get(m, {})):
        if not s:
            continue
        o = _run(b, m, s)
        assert o.dtype == np.uint16
        # no pixel reaches the top that wasn't there already
        assert ((o == 65535).any(axis=2) & ~(b == 65535).any(axis=2)).sum() == 0
        lin_in = _lin(b[60:-60, 60:-60].astype(np.float32) / 65535)
        lin_out = _lin(o[60:-60, 60:-60].astype(np.float32) / 65535)
        r_in = lin_in[..., 0] / np.maximum(lin_in[..., 1], 1e-4)
        r_out = lin_out[..., 0] / np.maximum(lin_out[..., 1], 1e-4)
        assert np.abs(r_out / r_in - 1).max() < 0.01


@pytest.mark.parametrize("m", ["light", "bands"])
def test_the_same_in_shares_of_the_frame_at_every_size(m):
    """A soft scene at 800 and 1600 px wide: the change, compared at the same
    places, agrees."""
    w = 800
    x = np.full((500, w), 0.4, np.float32)
    cv2.circle(x, (300, 250), 90, 0.7, -1)
    cv2.rectangle(x, (520, 120), (700, 380), 0.2, -1)
    x = cv2.GaussianBlur(x, (0, 0), 8)
    small = _u16(x)
    big = _u16(cv2.resize(x, (2 * w, 1000), interpolation=cv2.INTER_LINEAR))
    ds = _run(small, m, FULL[m], 800 / 56.0)[..., 1] / 65535.0 - small[..., 1] / 65535.0
    db = _run(big, m, FULL[m], 1600 / 56.0)[..., 1] / 65535.0 - big[..., 1] / 65535.0
    db = cv2.resize(db.astype(np.float32), (w, 500), interpolation=cv2.INTER_AREA)
    inner = (slice(40, -40), slice(40, -40))
    assert np.abs(ds[inner] - db[inner]).max() <= 0.25 * np.abs(ds[inner]).max() + 2e-3


def test_flattening_the_lighting_opens_shadows_and_keeps_the_brightest():
    x = np.full((400, 800), 0.15, np.float32)
    x[:, 400:] = 0.85
    x = cv2.GaussianBlur(x, (0, 0), 3)
    b = _u16(x)
    for m in ("light", "llf"):
        o = _run(b, m, {"lc_lighting": -60})[200, :, 1] / 65535.0
        assert o[100] > 0.17                              # the shadow side opens
        assert abs(o[700] - 0.85) < 0.03                  # the bright side is kept


def test_remap_never_turns_back():
    i = np.linspace(-6, 6, 4001, dtype=np.float32)
    for c in (0.5, 1.0, 2.0):
        for d in (0.5, 1.0, 2.0):
            r = lc.remap(i, 0.0, c, d)
            assert np.all(np.diff(r) > 0)


# --------------------------------------------------------------------------- #
# the image
# --------------------------------------------------------------------------- #

class _LcStub(_Stub):
    from core.ccr_image import CCRImage as _C
    EDGE_FIELD_CACHE = _C.EDGE_FIELD_CACHE
    EDGE_CACHE_MAX_LONG = _C.EDGE_CACHE_MAX_LONG
    _apply_edge_effect = _C._apply_edge_effect
    _apply_local_contrast = _C._apply_local_contrast
    crop_rect = None
    film_format = "645"


def test_the_image_runs_only_the_chosen_method(monkeypatch):
    from core.ccr_image import CCRImage
    import core.ccr_image as mod
    from core.ccr_processor import WS_B, WS_W
    seen = {}

    def cap(image, *a, **kw):
        seen["base"] = image
        return np.clip(((image.astype(np.float32) - WS_B) / (WS_W - WS_B)) * 65535,
                       0, 65535).astype(np.uint16)
    monkeypatch.setattr(mod, "adjust_image_opencl", cap)
    v = cv2.GaussianBlur(_step(400, 600, 0.3, 0.6), (0, 0), 2)
    base = np.clip(np.rint(np.repeat(v[..., None], 3, 2) * (WS_W - WS_B) + WS_B), 0, 65535).astype(np.uint16)
    stub = _LcStub({"edge_effect": 60, "local_contrast": "light", "lc_detail": 80})
    stub._ws_windowed = True
    out_light = CCRImage.apply_adjustments(stub, base)
    assert seen["base"] is base                          # Edge Effect not chosen: base untouched
    stub.adjustment_settings = {"edge_effect": 60, "local_contrast": "edge", "lc_detail": 80}
    out_edge = CCRImage.apply_adjustments(stub, base)
    assert seen["base"] is not base
    stub.adjustment_settings = {"edge_effect": 60, "local_contrast": "off", "lc_detail": 80}
    out_off = CCRImage.apply_adjustments(stub, base)
    assert seen["base"] is base
    assert not np.array_equal(out_light, out_off) and not np.array_equal(out_edge, out_off)
    stub.adjustment_settings = {"local_contrast": "light", "lc_detail": 80}
    np.testing.assert_array_equal(CCRImage.apply_adjustments(stub, base, skip_dust=True), out_off)


# --------------------------------------------------------------------------- #
# panel
# --------------------------------------------------------------------------- #

@pytest.fixture
def panel():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel
    return SlidersPanel()


def _shown(panel):
    rows = {"edge_effect": panel.edge_effect_row, **panel._lc_rows}
    return {k for k, r in rows.items() if not r.itemAt(0).widget().isHidden()}


def test_the_list_shows_each_methods_sliders(panel):
    from widgets.sliders_panel import SYNC_GROUPS
    combo = panel.lc_combo
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "Off", "Edge Effect", "Lighting & Detail", "Local Laplacian", "Detail Bands"]
    assert _shown(panel) == set()
    expect = {"edge": {"edge_effect"}, "light": {"lc_lighting", "lc_detail"},
              "llf": {"lc_lighting", "lc_detail"},
              "bands": {"lc_texture", "lc_structure", "lc_shape"}, "off": set()}
    for m, keys in expect.items():
        combo.setCurrentIndex(combo.findData(m))
        assert _shown(panel) == keys, m
    lay = panel.details_section._content_layout
    assert (lay.indexOf(panel.sharpen_bypass_checkbox) < lay.indexOf(panel.lc_method_row)
            < lay.indexOf(panel.edge_effect_row) < lay.indexOf(panel._lc_rows["lc_lighting"]))
    details = dict((g, k) for g, _l, k in SYNC_GROUPS)["details"]
    assert set(lc.LC_KEYS) <= set(details)


def test_choosing_stores_the_method_with_one_undo(panel, monkeypatch):
    img = _Img({"contrast": 4})
    _wire(panel, monkeypatch, img)
    combo = panel.lc_combo
    combo.setCurrentIndex(combo.findData("bands"))
    assert img.adjustment_settings["local_contrast"] == "bands"
    assert img.undo == 1
    panel.sliders[panel.adjustment_keys.index("lc_shape")].setValue(30)
    assert img.adjustment_settings["lc_shape"] == 30
    assert img.adjustment_settings["local_contrast"] == "bands"   # kept by a slider move


def test_loading_a_frame_sets_the_list(panel, monkeypatch, tmp_path):
    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    p = tmp_path / "a.png"
    cv2.imwrite(str(p), np.full((40, 60, 3), 120, np.uint8))
    img = CCRImage(str(p))
    monkeypatch.setattr(ccr_backend, "images", [img])
    for settings, method, shown in (
            ({"edge_effect": 40}, "edge", {"edge_effect"}),           # before the list
            ({"local_contrast": "llf", "lc_detail": 20}, "llf", {"lc_lighting", "lc_detail"}),
            ({}, "off", set())):
        img.adjustment_settings = dict(settings)
        panel.set_current_idx(0)
        assert panel.lc_combo.currentData() == method
        assert _shown(panel) == shown


def test_paste_copy_sync_and_reset(panel, monkeypatch):
    from widgets.sliders_panel import paste_options, SYNC_GROUPS
    clip = {"adjustments": {"lc_lighting": -20, "lc_detail": 30}, "local_contrast": "light"}
    rows = [r for r in paste_options(clip, lambda k: 0) if r[1] == "local"]
    assert rows and rows[0][2] == "Local Contrast  (Lighting & Detail: lighting -20, detail +30)"
    rows = [r for r in paste_options({"adjustments": {"lc_texture": 25}, "local_contrast": "bands"},
                                     lambda k: 0) if r[1] == "local"]
    assert rows[0][2] == "Local Contrast  (Detail Bands: texture +25, structure 0, shape 0)"
    img = _Img({"contrast": 5})
    _wire(panel, monkeypatch, img)
    panel.current_idx = 1
    panel.clipboard = dict(clip, curves=None, cineon_log=False, look_lut=None, flags={},
                           profile="color", crop=(None, 0.0), rotation=0, flip_h=False,
                           flip_v=False, fine_rotation=0)
    panel._apply_paste(0, {"local"})
    s = img.adjustment_settings
    assert s["local_contrast"] == "light" and s["lc_detail"] == 30 and s["contrast"] == 5

    src = _Img({"local_contrast": "bands", "lc_texture": 25})
    _wire(panel, monkeypatch, src)
    panel.current_idx = 5
    panel.copy_settings_from_index(0)
    assert panel.clipboard["local_contrast"] == "bands"

    from core.ccr_backend import ccr_backend
    dst = _Img({"local_contrast": "llf"})
    for im in (src, dst):
        im.crop_rect, im.crop_angle, im.color_profile = None, 0.0, "color"
    _wire(panel, monkeypatch, src)
    monkeypatch.setattr(ccr_backend, "images", [src, dst])
    monkeypatch.setattr(ccr_backend, "get_image_by_index", lambda i: [src, dst][i])
    panel.current_idx = 0
    panel._sync_group_selection = {gid: gid == "details" for gid, _l, _k in SYNC_GROUPS}
    panel._perform_sync_to_all()
    assert dst.adjustment_settings["local_contrast"] == "bands"
    assert dst.adjustment_settings["lc_texture"] == 25

    _wire(panel, monkeypatch, src)
    panel.on_reset_clicked()
    assert panel.lc_combo.currentData() == "off"
    assert "local_contrast" not in src.adjustment_settings
