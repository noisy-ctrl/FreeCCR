"""Colour Contrast (spec/colour-contrast.md): areas pushed apart in colour,
lightness, greys, gradations, grain and skin left alone."""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import colour_contrast as cc                  # noqa: E402
from core.oklab import from_oklab, to_oklab             # noqa: E402


def patch(h, w, L, a, b):
    lab = np.dstack([np.full((h, w), L), np.full((h, w), a), np.full((h, w), b)])
    return from_oklab(lab.reshape(-1, 3).astype(np.float32)).reshape(h, w, 3)


def two_areas(W=1200, noise=0.0, seed=0):
    H = W * 2 // 3
    img = patch(H, W, 0.65, -0.05, 0.04)                       # a green ...
    img[:, W // 2:] = patch(H, W - W // 2, 0.65, -0.06, 0.015)  # ... beside a cooler green
    if noise:
        img = np.clip(img + np.random.default_rng(seed).normal(0, noise, img.shape).astype(np.float32), 0, 1)
    return img


def lab(img):
    return to_oklab(img.reshape(-1, 3)).reshape(img.shape)


def test_zero_is_the_input_itself():
    img = two_areas(300)
    assert cc.apply_colour_contrast(img, 0) is img


def test_lightness_kept_and_in_range():
    img = two_areas(600, noise=0.01)
    out = cc.apply_colour_contrast(img, 100)
    assert out.dtype == np.float32 and out.min() >= 0 and out.max() <= 1
    assert np.abs(lab(out)[..., 0] - lab(img)[..., 0]).max() < 1e-4


def test_neighbouring_areas_move_apart():
    img = two_areas(1200)
    W = 1200
    def gap(im):
        o = lab(im)
        return np.linalg.norm(o[50:100, W // 2 - 50:W // 2 - 10, 1:].reshape(-1, 2).mean(0)
                              - o[50:100, W // 2 + 10:W // 2 + 50, 1:].reshape(-1, 2).mean(0))
    g0, g50, g100 = gap(img), gap(cc.apply_colour_contrast(img, 50)), gap(cc.apply_colour_contrast(img, 100))
    assert g0 < g50 < g100 and g100 > 1.3 * g0


def test_greys_stay_grey():
    img = patch(400, 600, 0.6, 0.06, 0.03)
    img[150:250, 250:350] = patch(100, 100, 0.6, 0.0, 0.0)
    out = lab(cc.apply_colour_contrast(img, 100))
    assert np.abs(out[170:230, 270:330, 1:]).max() < 2e-4


def test_a_linear_ramp_is_not_moved_inside_the_frame():
    x = np.linspace(-0.08, 0.08, 600, dtype=np.float32)
    ramp = from_oklab(np.dstack([np.full((60, 600), 0.6), np.tile(x, (60, 1)),
                                 np.full((60, 600), 0.04)]).reshape(-1, 3).astype(np.float32)).reshape(60, 600, 3)
    out = cc.apply_colour_contrast(ramp, 100)
    assert np.abs(out - ramp)[:, 60:540].max() * 255 < 0.5


def test_grain_does_not_grow():
    for sd in (0.01, 0.02):
        img = two_areas(1200, noise=sd)
        out = cc.apply_colour_contrast(img, 100)
        g = []
        for im in (img, out):
            o = lab(im)[..., 1:].astype(np.float32)
            g.append((o - cv2.GaussianBlur(o, (0, 0), 3))[20:780, 20:580].std())
        assert g[1] <= g[0] * 1.005


def test_skin_is_protected():
    # a skin-coloured area on green: protected, it barely moves; unprotected it is pushed
    img = patch(600, 900, 0.65, -0.06, 0.05)
    img[200:400, 350:550] = patch(200, 200, 0.70, 0.035, 0.065)        # OKLab hue ~62 deg
    def face(o):
        return o[260:340, 410:490, 1:].reshape(-1, 2).mean(0)
    f0 = face(lab(img))
    on = face(lab(cc.apply_colour_contrast(img, 100)))
    off = face(lab(cc.apply_colour_contrast(img, 100, protect_skin=False)))
    assert np.linalg.norm(off - f0) > 0.003
    assert np.linalg.norm(on - f0) < 0.25 * np.linalg.norm(off - f0)


def test_resolution_independent():
    big = two_areas(1200)
    small = cv2.resize(big, (600, 400), interpolation=cv2.INTER_AREA)
    a = cv2.resize(cc.apply_colour_contrast(big, 100), (600, 400), interpolation=cv2.INTER_AREA)
    b = cc.apply_colour_contrast(small, 100)
    assert np.abs(a - b)[20:380, 20:580].max() * 255 < 2.0


def test_unmoved_pixels_come_back_exactly():
    img = patch(200, 300, 0.6, 0.05, 0.02)                              # one area: nothing to push
    out = cc.apply_colour_contrast(img, 100)
    assert np.array_equal(out, img)


# --- the options -------------------------------------------------------------------

import pytest                                            # noqa: E402

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)


@pytest.mark.parametrize("value,want", [
    ({"skin": False}, {"skin": False}), ({"skin": 0}, {"skin": False}),
    ({"skin": True}, None), ({}, None), (None, None), ("x", None), ({"other": 1}, None)])
def test_normalize_options(value, want):
    assert cc.normalize_cc_shape(value) == want
    assert cc.protects_skin(value) == (want is None)


def test_stored_options_reach_the_push():
    img = patch(600, 900, 0.65, -0.06, 0.05)
    img[200:400, 350:550] = patch(200, 200, 0.70, 0.035, 0.065)
    assert np.array_equal(cc.apply_colour_contrast(img, 100, {"skin": False}),
                          cc.apply_colour_contrast(img, 100, protect_skin=False))
    assert np.array_equal(cc.apply_colour_contrast(img, 100, None),
                          cc.apply_colour_contrast(img, 100))


# --- the render --------------------------------------------------------------------

def _frame16():
    img = two_areas(240, noise=0.005)
    img[40:120, 60:140] = patch(80, 80, 0.6, 0.08, 0.03)         # a red among the greens
    return (np.clip(img, 0, 1) * 65535).astype(np.uint16)


def test_adjust_image_takes_it_last_and_is_a_no_op_at_zero():
    from core import ccr_processor as cp
    import inspect
    for fn in (cp.adjust_image, cp.adjust_image_opencl):
        assert list(inspect.signature(fn).parameters)[-2:] == ["colour_contrast",
                                                               "colour_contrast_shape"]
    img16 = _frame16()
    base = cp.adjust_image(img16)
    assert np.array_equal(cp.adjust_image(img16, colour_contrast=0,
                                          colour_contrast_shape={"skin": False}), base)
    on = cp.adjust_image(img16, colour_contrast=80)
    assert not np.array_equal(on, base)
    # it is the stage itself, on the rest of the render
    want = cc.apply_colour_contrast(base.astype(np.float32) / 65535.0, 80) * 65535.0
    assert np.abs(on.astype(np.float64) - np.clip(want, 0, 65535)).max() <= 1.0


def test_it_runs_after_clean_lights_and_before_golden():
    from core import ccr_processor as cp
    from core.clean_lights import apply_clean_lights
    from core.golden import apply_golden
    img16 = _frame16()
    base = cp.adjust_image(img16).astype(np.float32) / 65535.0
    x = np.clip(apply_clean_lights(base, 60), 0, 1).astype(np.float32)
    x = np.clip(cc.apply_colour_contrast(x, 70), 0, 1).astype(np.float32)
    x = np.clip(apply_golden(x, 40, None), 0, 1)
    got = cp.adjust_image(img16, clean_lights=60, golden_highlights=40, colour_contrast=70)
    assert np.abs(got.astype(np.float64) - x * 65535.0).max() <= 2.0


def test_opencl_runs_it_in_numpy_like_the_cpu():
    from core import ccr_processor as cp
    if not cp.OPENCL_AVAILABLE or not cp._initialize_opencl():
        pytest.skip("no OpenCL device")
    img16 = _frame16()
    cpu = cp.adjust_image(img16, colour_contrast=70, clean_lights=40)
    gpu = cp.adjust_image_opencl(img16, colour_contrast=70, clean_lights=40)
    plain = cp.adjust_image_opencl(img16, clean_lights=40)
    assert not np.array_equal(gpu, plain)
    assert np.abs(gpu.astype(np.int32) - cpu.astype(np.int32)).max() <= 8


def test_apply_adjustments_passes_it(monkeypatch):
    from core.ccr_image import CCRImage
    import core.ccr_image as mod
    from test_x_tones import _Stub, _img
    seen = {}

    def cap(image, *a, **kw):
        seen.update(kw)
        return image

    monkeypatch.setattr(mod, "adjust_image_opencl", cap)
    CCRImage.apply_adjustments(_Stub({"colour_contrast": 50,
                                      "colour_contrast_shape": {"skin": False}}), _img())
    assert seen["colour_contrast"] == 50 and seen["colour_contrast_shape"] == {"skin": False}
    seen.clear()
    CCRImage._adjust_for_area(_Stub({}), _img(), {"colour_contrast": 30})
    assert seen["colour_contrast"] == 30 and seen["colour_contrast_shape"] is None


# --- the panel ----------------------------------------------------------------------

@pytest.fixture
def panel():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel
    return SlidersPanel()


def _skin(panel, on):
    menu = panel.colour_contrast_menu()
    act = menu.actions()[0]
    if act.isChecked() != on:
        act.trigger()
    return menu


def test_the_slider_sits_under_organic_saturation_and_is_synced(panel):
    from widgets.sliders_panel import SlidersPanel, SYNC_GROUPS, adjustment_label
    assert SlidersPanel.ADJUSTMENT_KEYS[-1] == "colour_contrast"
    s = panel.sliders[panel.adjustment_keys.index("colour_contrast")]
    assert (s.minimum(), s.maximum(), s.value()) == (0, 100, 0)
    lay = panel.colour_contrast_slider_layout
    assert lay.itemAt(0).widget().text() == "Colour Contrast"
    parent = lay.parent()
    k = next(i for i in range(parent.count()) if parent.itemAt(i).layout() is lay)
    assert parent.itemAt(k - 1).layout() is panel.organic_sat_slider_layout
    groups = {gid: keys for gid, _l, keys in SYNC_GROUPS}
    assert "colour_contrast" in groups["sat"]
    assert adjustment_label("colour_contrast") == "Colour Contrast"


def test_hidden_in_black_and_white(panel):
    panel._sync_bw_rows("bw")
    assert panel.colour_contrast_slider_layout.itemAt(0).widget().isHidden()
    panel._sync_bw_rows("color")
    assert not panel.colour_contrast_slider_layout.itemAt(0).widget().isHidden()


def test_the_label_offers_protect_skin(panel, monkeypatch):
    from PySide6.QtCore import Qt
    from test_x_tones import _Img, _wire
    lbl = panel._cc_label
    assert lbl.contextMenuPolicy() == Qt.CustomContextMenu
    tip = lbl.toolTip()
    assert "Right-click: Protect skin" in tip and "Protect skin: on" in tip
    assert not lbl.font().underline()
    _wire(panel, monkeypatch, _Img({"colour_contrast": 50}))
    acts = panel.colour_contrast_menu().actions()
    assert len(acts) == 1 and acts[0].isCheckable() and acts[0].isChecked()
    assert acts[0].text().startswith("Protect skin")


def test_turning_protect_skin_off_stores_it_once_and_on_removes_it(panel, monkeypatch):
    from test_x_tones import _Img, _wire
    img = _Img({"colour_contrast": 50})
    _wire(panel, monkeypatch, img)
    _skin(panel, False)
    assert img.adjustment_settings["colour_contrast_shape"] == {"skin": False}
    assert img.undo == 1                                  # one undo step
    assert panel._cc_label.font().underline()
    assert "Protect skin: off" in panel._cc_label.toolTip()
    assert not panel.colour_contrast_menu().actions()[0].isChecked()
    panel.set_cc_protect_skin(False)                      # no change: no undo step
    assert img.undo == 1
    panel.sliders[panel.adjustment_keys.index("contrast")].setValue(12)
    assert img.adjustment_settings["colour_contrast_shape"] == {"skin": False}
    _skin(panel, True)
    assert "colour_contrast_shape" not in img.adjustment_settings
    assert not panel._cc_label.font().underline()


def test_loading_and_reset(panel, monkeypatch):
    from test_x_tones import _Img, _wire
    img = _Img({"colour_contrast": 40, "colour_contrast_shape": {"skin": False}})
    _wire(panel, monkeypatch, img)
    panel._load_active_layer(0)
    assert panel._cc_shape == {"skin": False}
    _wire(panel, monkeypatch, _Img({"colour_contrast": 40}))
    panel._load_active_layer(0)
    assert panel._cc_shape is None
    _wire(panel, monkeypatch, img)
    panel._load_active_layer(0)
    monkeypatch.setattr(panel, "_show_rendered", lambda *a, **k: None)
    panel.on_reset_clicked()                              # Reset: Protect skin on again
    assert panel._cc_shape is None
    assert "colour_contrast_shape" not in img.adjustment_settings


def _clip(**kw):
    base = dict(adjustments={"colour_contrast": 60}, curves=None,
                cineon_log=False, look_lut=None, flags={}, profile="color",
                crop=(None, 0.0), rotation=0, flip_h=False, flip_v=False, fine_rotation=0)
    base.update(kw)
    return base


def test_paste_row_says_when_skin_is_not_protected():
    from widgets.sliders_panel import paste_options
    rows = {r[1]: r[2] for r in paste_options(_clip(colour_contrast_shape={"skin": False}),
                                              lambda k: 0)}
    assert rows["adj:colour_contrast"] == "Colour Contrast  (+60, skin not protected)"
    rows = {r[1]: r[2] for r in paste_options(_clip(), lambda k: 0)}
    assert rows["adj:colour_contrast"] == "Colour Contrast  (+60)"


@pytest.mark.parametrize("chosen,clip_off,target_off,want", [
    ({"adj:colour_contrast"}, True, False, {"skin": False}),
    ({"adj:colour_contrast"}, False, True, None),     # the clipboard's default replaces it
    ({"adj:contrast"}, True, False, None),            # not its row: the target's kept
    ({"adj:contrast"}, False, True, {"skin": False}),
])
def test_paste_brings_protect_skin_with_its_row(panel, chosen, clip_off, target_off, want):
    target = {"contrast": 0}
    if target_off:
        target["colour_contrast_shape"] = {"skin": False}
    clip = _clip(colour_contrast_shape={"skin": False} if clip_off else None)
    clip["adjustments"]["contrast"] = 7
    merged = panel.paste_into(target, clip, chosen)
    assert merged.get("colour_contrast_shape") == want


def test_copy_snapshot_carries_it(panel, monkeypatch):
    from test_x_tones import _Img, _wire
    src = _Img({"colour_contrast": 50, "colour_contrast_shape": {"skin": False}})
    _wire(panel, monkeypatch, src)
    panel.current_idx = 5                                 # a frame not on screen
    panel.copy_settings_from_index(0)
    assert panel.clipboard["colour_contrast_shape"] == {"skin": False}
    assert panel.clipboard["adjustments"]["colour_contrast"] == 50


def _sync(panel, monkeypatch, src, dst, sat):
    from core.ccr_backend import ccr_backend
    from widgets.sliders_panel import SYNC_GROUPS
    from test_x_tones import _wire
    for im in (src, dst):
        im.crop_rect = None
        im.crop_angle = 0.0
        im.color_profile = "color"
    _wire(panel, monkeypatch, src)
    monkeypatch.setattr(ccr_backend, "images", [src, dst])
    monkeypatch.setattr(ccr_backend, "get_image_by_index", lambda i: [src, dst][i])
    panel.current_idx = 0
    panel._sync_group_selection = {gid: (gid == "sat" and sat) or gid == "tone"
                                   for gid, _l, _k in SYNC_GROUPS}
    panel._perform_sync_to_all()


def test_sync_with_saturation_carries_it(panel, monkeypatch):
    from test_x_tones import _Img
    src = _Img({"colour_contrast": 50, "colour_contrast_shape": {"skin": False}})
    dst = _Img({"colour_contrast": 10})
    _sync(panel, monkeypatch, src, dst, sat=True)
    assert dst.adjustment_settings["colour_contrast"] == 50
    assert dst.adjustment_settings["colour_contrast_shape"] == {"skin": False}
    back = _Img({"colour_contrast": 50})
    _sync(panel, monkeypatch, back, dst, sat=True)
    assert "colour_contrast_shape" not in dst.adjustment_settings


def test_sync_without_saturation_keeps_the_target_option(panel, monkeypatch):
    from test_x_tones import _Img
    src = _Img({"contrast": 20})
    dst = _Img({"contrast": 0, "colour_contrast_shape": {"skin": False}})
    _sync(panel, monkeypatch, src, dst, sat=False)
    assert dst.adjustment_settings["contrast"] == 20
    assert dst.adjustment_settings["colour_contrast_shape"] == {"skin": False}


# --- memory (2026-11-28: a 51 MP export ran out of memory at 8 GB) ------------------

def test_shared_guide_filter_is_the_guided_filter():
    from core.oklab import guided_filter
    rng = np.random.default_rng(5)
    guide = rng.uniform(0, 1, (120, 160)).astype(np.float32)
    ps = [rng.uniform(-0.1, 0.1, (120, 160)).astype(np.float32) for _ in range(3)]
    want = [guided_filter(p.copy(), guide, 7, 0.03 ** 2) for p in ps]
    got = cc._guided([p.copy() for p in ps], guide, 7, 0.03 ** 2)
    for g, w in zip(got, want):
        assert np.array_equal(g, w)


def test_in_place_writes_the_callers_copy_and_the_default_does_not():
    img = two_areas(160, noise=0.004)
    keep = img.copy()
    out = cc.apply_colour_contrast(img, 80)
    assert out is not img and np.array_equal(img, keep)
    mine = img.copy()
    out2 = cc.apply_colour_contrast(mine, 80, inplace=True)
    assert out2 is mine and np.array_equal(out2, out)


def test_bands_change_nothing(monkeypatch):
    img = two_areas(300, noise=0.004)
    whole = cc.apply_colour_contrast(img, 90)
    monkeypatch.setattr(cc, "CHUNK_ROWS", 37)
    assert np.array_equal(cc.apply_colour_contrast(img, 90), whole)
