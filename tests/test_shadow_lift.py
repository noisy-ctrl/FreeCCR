#!/usr/bin/env python3
"""Shadow Lift (spec/shadow-lift.md revision 2): lifts black a little, the
deepest shadows moving up with their grain and the lift rolling off smoothly.
Held to the finishing tools' quality rules: an exact no-op at 0, tones above
the roll-off returned bit-for-bit, a monotone C1 tone curve, never darker,
nothing clips, CPU == GPU."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core import shadow_lift as sl  # noqa: E402
from core.ccr_processor import adjust_image, adjust_image_opencl, encode_window  # noqa: E402
from test_x_tones import _Img, _wire, _Stub, _img, _capture  # noqa: E402


def _grey_ramp(n=4096):
    v = np.linspace(0.0, 1.0, n, dtype=np.float32)
    return np.repeat(v[None, :, None], 3, axis=2)


def _colours(seed=0, n=200_000):
    return np.random.default_rng(seed).random((1, n, 3)).astype(np.float32)


def _luma(x):
    return np.asarray(x, np.float32)[..., :3] @ sl.LUMA


# --------------------------------------------------------------------------- #
# the tone curve
# --------------------------------------------------------------------------- #

def test_zero_is_an_exact_no_op():
    img = _img()
    a = adjust_image(img, contrast=10, saturation=5)
    np.testing.assert_array_equal(a, adjust_image(img, contrast=10, saturation=5,
                                                  shadow_lift=0,
                                                  shadow_lift_shape={"rolloff": 70}))
    np.testing.assert_array_equal(adjust_image_opencl(img, contrast=10),
                                  adjust_image_opencl(img, contrast=10, shadow_lift=0))
    x = _colours()
    assert sl.apply_shadow_lift(x, 0) is x


@pytest.mark.parametrize("shape", [None, {"rolloff": 35}, {"rolloff": 80}])
def test_tones_above_the_roll_off_come_back_exactly(shape):
    x = _colours(1)
    out = sl.apply_shadow_lift(x, 100, shape)
    s = sl.normalize_lift_shape(shape) or sl.LIFT_SHAPE_DEFAULT
    above = _luma(x).reshape(-1) >= s["rolloff"] / 100.0
    assert above.sum() > 1000
    np.testing.assert_array_equal(out.reshape(-1, 3)[above], x.reshape(-1, 3)[above])


@pytest.mark.parametrize("amount,black", [(100, 25.5), (40, 10.2), (10, 2.55)])
def test_black_lifts_to_a_matte_black(amount, black):
    ramp = _grey_ramp()
    out = sl.apply_shadow_lift(ramp, amount)
    assert out[0, 0] * 255 == pytest.approx([black] * 3, abs=1e-3)   # neutral, as asked
    assert np.all(out >= ramp - 1e-7)                                # never darker


@pytest.mark.parametrize("shape", [None, {"rolloff": 35}, {"rolloff": 80}])
@pytest.mark.parametrize("amount", [30, 100])
def test_the_grey_axis_is_monotone_and_smooth(shape, amount):
    """Nothing sudden: slope 1 at black (the deepest shadows move up as they
    are), never below 1 - 1.5 v0 / Vf, 1 again where the lift ends, no kink."""
    xs = np.linspace(0.0, 1.0, 20001, dtype=np.float64)
    ys = sl.grey_curve(xs, amount, shape).astype(np.float64)
    slope = np.diff(ys) / np.diff(xs)
    v0, Vf = sl.params(amount, shape)
    assert slope[0] > 0.9995                                         # flat lift at black
    assert slope.min() >= 1.0 - 1.5 * float(v0) / float(Vf) - 2e-3
    assert slope.max() <= 1.0 + 2e-3
    assert np.abs(np.diff(slope)).max() < 0.005                      # C1
    if shape is None and amount == 100:
        assert slope.min() > 0.69                                    # 0.70 at full, default
    out = sl.apply_shadow_lift(_grey_ramp(), amount, shape)[0, :, 0]
    assert np.all(np.diff(out) >= 0)


def test_the_grain_in_the_deepest_shadows_comes_up_with_them():
    """Black's lift is flat at black, so a noisy patch near black is moved,
    not squashed: its grain is the same after (in display values)."""
    rng = np.random.default_rng(4)
    patch = np.clip(0.02 + rng.normal(0, 0.006, (200, 200, 3)), 0, 1).astype(np.float32)
    out = sl.apply_shadow_lift(patch, 100)
    assert out.mean() > patch.mean() + 0.09
    assert (out - out.mean()).std() == pytest.approx((patch - patch.mean()).std(), rel=0.02)


def test_a_colour_at_the_top_takes_only_what_fits():
    x = np.array([[[0.0, 0.0, 1.0], [0.05, 0.0, 0.97], [0.1, 0.1, 0.1]]], np.float32)
    out = sl.apply_shadow_lift(x, 100)
    assert out.max() <= 1.0
    np.testing.assert_array_equal(out[0, 0], x[0, 0])                # no room: untouched
    assert out[0, 1, 2] == pytest.approx(1.0) and out[0, 1, 0] > 0.05
    x = _colours(2)
    out = sl.apply_shadow_lift(x, 100, {"rolloff": 80})
    assert out.min() >= 0.0 and out.max() <= 1.0 and np.all(out >= x - 1e-7)


def test_options_are_normalised():
    assert sl.normalize_lift_shape(dict(sl.LIFT_SHAPE_DEFAULT)) is None
    assert sl.normalize_lift_shape({"rolloff": 500}) == {"rolloff": 80}
    assert sl.normalize_lift_shape({"rolloff": 1}) == {"rolloff": 35}
    # revision 1's options (range, black) mean the default now
    assert sl.normalize_lift_shape({"range": 30, "black": 80}) is None
    assert sl.normalize_lift_shape("junk") is None


def test_weight_map_shows_where_it_acts():
    ramp = _grey_ramp(1000)
    w = sl.weight_map(ramp)[0]
    assert w[0] == 1.0 and w[-1] == 0.0 and np.all(np.diff(w) <= 1e-7)
    assert w[int(0.5 * 999) + 1] == 0.0                              # gone by the roll-off
    longer = sl.weight_map(ramp, {"rolloff": 80})[0]
    assert np.all(longer >= w - 1e-7)


def test_a_longer_roll_off_eases_out_further_up():
    ramp = _grey_ramp()
    short = sl.apply_shadow_lift(ramp, 100, {"rolloff": 35})[0, :, 0] - ramp[0, :, 0]
    long_ = sl.apply_shadow_lift(ramp, 100, {"rolloff": 80})[0, :, 0] - ramp[0, :, 0]
    at = int(0.4 * 4095)
    assert short[at] == 0.0 and long_[at] > 0.02


# --------------------------------------------------------------------------- #
# in the render
# --------------------------------------------------------------------------- #

def test_runs_after_shadow_neutralise_before_glow():
    from core.shadow_neutral import apply_shadow_neutralise
    img = _img(4)
    both = adjust_image(img, shadow_neutralise=70, shadow_lift=60).astype(np.float64) / 65535
    first = adjust_image(img, shadow_neutralise=70).astype(np.float32) / 65535
    np.testing.assert_allclose(both, sl.apply_shadow_lift(first, 60), atol=2.5 / 65535)
    assert apply_shadow_neutralise is not None


@pytest.mark.parametrize("ws", [False, True])
def test_gpu_matches_cpu(ws):
    rng = np.random.default_rng(1)
    img = (encode_window(rng.uniform(-0.1, 1.1, (30, 40, 3)).astype(np.float32))
           if ws else _img(2, (30, 40, 3)))
    kw = dict(shadow_lift=80, shadow_lift_shape={"rolloff": 70}, shadow_neutralise=40,
              glow=30, contrast=10, saturation=20)
    a = adjust_image(img, ws_windowed=ws, **kw).astype(np.int64)
    b = adjust_image_opencl(img, ws_windowed=ws, **kw).astype(np.int64)
    diff = np.abs(a - b)
    assert np.mean(diff <= 3) > 0.995 and diff.max() <= 24


def test_gpu_finish_matches_numpy(monkeypatch):
    """The OpenCL kernel against the numpy reference on a whole picture and
    on random colours: within a 16-bit code (it is one smoothstep and an add)."""
    from core import ccr_processor as cp
    from core import finish_gpu as fg
    from test_finish_gpu import _gpu, _scene
    monkeypatch.setenv("FREECCR_GPU_FINISH", "1")
    ctx, q = _gpu()
    for shape in (None, {"rolloff": 35}, {"rolloff": 80}):
        for x in (_scene(), _colours(5, 300_000).reshape(300, 1000, 3)):
            cpu = sl.apply_shadow_lift(x, 100, shape)
            gpu = fg.finish_numpy_image(ctx, q, x, shadow_lift=100, shadow_lift_shape=shape)
            d = np.abs(cpu.astype(np.float64) - gpu.astype(np.float64)) * 65535
            assert d.max() <= 1.0, (shape, d.max())
    assert cp is not None


def test_gpu_leaves_untouched_tones_exact(monkeypatch):
    import pyopencl.array as cla
    from core import finish_gpu as fg
    from test_finish_gpu import _gpu
    monkeypatch.setenv("FREECCR_GPU_FINISH", "1")
    ctx, q = _gpu()
    g = fg.for_queue(ctx, q)
    grey = np.full((200, 300, 3), 0.55, np.float32) * np.float32(65535.0)   # above the roll-off
    buf = cla.to_device(q, grey.reshape(-1, 3))
    g.finish(buf, 200, 300, shadow_lift=100.0)
    np.testing.assert_array_equal(buf.get(), grey.reshape(-1, 3))


def test_image_passes_the_setting(monkeypatch):
    from core.ccr_image import CCRImage
    seen = _capture(monkeypatch)
    CCRImage.apply_adjustments(_Stub({"shadow_lift": 55,
                                      "shadow_lift_shape": {"rolloff": 40}}), _img())
    assert seen["shadow_lift"] == 55 and seen["shadow_lift_shape"] == {"rolloff": 40}
    seen.clear()
    CCRImage._adjust_for_area(_Stub({}), _img(), {"shadow_lift": 20})
    assert seen["shadow_lift"] == 20


# --------------------------------------------------------------------------- #
# panel
# --------------------------------------------------------------------------- #

@pytest.fixture
def panel():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel
    return SlidersPanel()


def test_slider_sits_in_lab_finish_between_shadow_neutralise_and_glow(panel):
    from PySide6.QtCore import Qt
    keys = panel.adjustment_keys
    i = keys.index("shadow_lift")
    assert keys[i - 1] == "shadow_neutralise" and keys[i + 1] == "glow"
    assert panel.sliders[i].value_range() == (0, 100) and panel.sliders[i].value() == 0
    sec = panel.lab_finish_section
    order = [sec._content_layout.indexOf(lay) for lay in (
        panel.golden_slider_layout, panel.shadow_neutralise_slider_layout,
        panel.shadow_lift_slider_layout, panel.glow_slider_layout)]
    assert order == [0, 1, 2, 3]
    lbl = panel._lift_label
    assert lbl.text() == "Shadow Lift"
    assert lbl.contextMenuPolicy() == Qt.CustomContextMenu and "Right-click" in lbl.toolTip()
    from widgets.sliders_panel import SYNC_GROUPS
    assert "shadow_lift" in dict((g, k) for g, _l, k in SYNC_GROUPS)["sat"]


def test_popup_writes_options_with_one_undo(panel, monkeypatch):
    img = _Img()
    _wire(panel, monkeypatch, img)
    panel._open_lift_popup(panel.mapToGlobal(panel.rect().center()))
    pop = panel._lift_popup
    pop.sliders["rolloff"].setValue(40)
    pop.sliders["rolloff"].setValue(65)
    assert img.adjustment_settings["shadow_lift_shape"] == {"rolloff": 65}
    assert panel._lift_label.font().underline()
    pop.close()
    assert img.undo == 1
    panel.sliders[panel.adjustment_keys.index("saturation")].setValue(7)
    assert img.adjustment_settings["shadow_lift_shape"]["rolloff"] == 65
    panel._open_lift_popup(panel.mapToGlobal(panel.rect().center()))
    panel._lift_popup.reset()
    assert "shadow_lift_shape" not in img.adjustment_settings
    panel._lift_popup.close()


def test_paste_copy_and_sync(panel, monkeypatch):
    from widgets.sliders_panel import paste_options
    clip = {"adjustments": {}, "shadow_lift_shape": {"rolloff": 70}}
    row = [r for r in paste_options(clip, lambda k: 0) if r[1] == "shadow_lift_shape"]
    assert row and "rolloff 70 %" in row[0][2]
    img = _Img({"contrast": 5})
    _wire(panel, monkeypatch, img)
    panel.current_idx = 1
    panel.clipboard = dict(clip, curves=None, cineon_log=False, look_lut=None,
                           flags={}, profile="color", crop=(None, 0.0), rotation=0,
                           flip_h=False, flip_v=False, fine_rotation=0)
    panel._apply_paste(0, {"shadow_lift_shape"})
    assert img.adjustment_settings["shadow_lift_shape"]["rolloff"] == 70
    src = _Img({"shadow_lift": 40, "shadow_lift_shape": {"rolloff": 60}})
    _wire(panel, monkeypatch, src)
    panel.current_idx = 5
    panel.copy_settings_from_index(0)
    assert panel.clipboard["shadow_lift_shape"]["rolloff"] == 60
    assert panel.clipboard["adjustments"]["shadow_lift"] == 40

    from core.ccr_backend import ccr_backend
    from widgets.sliders_panel import SYNC_GROUPS
    dst = _Img({"shadow_lift": 0})
    for im in (src, dst):
        im.crop_rect, im.crop_angle, im.color_profile = None, 0.0, "color"
    _wire(panel, monkeypatch, src)
    monkeypatch.setattr(ccr_backend, "images", [src, dst])
    monkeypatch.setattr(ccr_backend, "get_image_by_index", lambda i: [src, dst][i])
    panel.current_idx = 0
    panel._sync_group_selection = {gid: gid == "sat" for gid, _l, _k in SYNC_GROUPS}
    panel._perform_sync_to_all()
    assert dst.adjustment_settings["shadow_lift"] == 40
    assert dst.adjustment_settings["shadow_lift_shape"]["rolloff"] == 60


def test_reset_clears_the_options(panel, monkeypatch):
    img = _Img({"shadow_lift": 30, "shadow_lift_shape": {"rolloff": 60}})
    _wire(panel, monkeypatch, img)
    panel._lift_shape = {"rolloff": 60}
    panel._update_lift_label()
    assert panel._lift_label.font().underline()
    panel.on_reset_clicked()
    assert panel._lift_shape is None and not panel._lift_label.font().underline()


def test_show_where_it_acts_follows_the_popup(panel, monkeypatch):
    img = _Img()
    _wire(panel, monkeypatch, img)
    calls = []
    ip = type("IP", (), {"update_preview": lambda s, i: None,
                         "set_lift_mask": lambda s, shape: calls.append(shape)})()
    mw = type("MW", (), {"image_preview": ip})()
    monkeypatch.setattr(panel, "parent", lambda: type("P", (), {"parent": lambda s: mw})())
    panel._open_lift_popup(panel.mapToGlobal(panel.rect().center()))
    pop = panel._lift_popup
    pop.mask_check.setChecked(True)
    assert calls[-1] == {}
    pop.sliders["rolloff"].setValue(60)
    assert calls[-1]["rolloff"] == 60
    pop.close()
    assert calls[-1] is None


def test_popup_strip_shows_black_lifting():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.shadow_lift_popup import _spot_pixmap, ShadowLiftPopup, ROWS
    from PySide6.QtGui import QImage

    def arr(pm):
        q = pm.toImage().convertToFormat(QImage.Format_RGB888)
        w, h = q.width(), q.height()
        return np.frombuffer(q.constBits(), np.uint8).reshape(h, q.bytesPerLine())[:, :w * 3] \
            .reshape(h, w, 3).astype(int)
    before, after = arr(_spot_pixmap(0, None)), arr(_spot_pixmap(100, None))
    assert before[5, 0].sum() == 0 and after[5, 0].sum() > 60      # black lifts (~25/255)
    assert after[5, -1].sum() == before[5, -1].sum()              # gone by the roll-off
    assert {k for k, *_ in ROWS} == set(sl.LIFT_SHAPE_DEFAULT)
    pop = ShadowLiftPopup(None, 0)
    assert pop.values() == sl.LIFT_SHAPE_DEFAULT
    pop.close()


def test_canvas_layer_is_green_where_it_acts(tmp_path, monkeypatch):
    from test_false_colour import _canvas, _HOSTS  # noqa: F401
    from PySide6.QtGui import QImage
    ip, _img_ = _canvas(tmp_path, monkeypatch)
    ip.set_lift_mask({})
    item = ip._gm_item
    assert item is not None and item.isVisible()
    q = item.pixmap().toImage().convertToFormat(QImage.Format_RGBA8888)
    w, h = q.width(), q.height()
    px = np.frombuffer(q.constBits(), np.uint8).reshape(h, q.bytesPerLine())[:, :w * 4].reshape(h, w, 4)
    alpha = px[..., 3].astype(int)
    assert alpha.max() > 10
    y, x = np.unravel_index(alpha.argmax(), alpha.shape)
    assert np.abs(px[y, x, :3].astype(int) - (90, 220, 120)).max() <= 255 // alpha.max() + 1
    ip.set_glow_mask(None)                         # another tool's popup closing
    assert ip._gm_item.isVisible()
    ip.set_lift_mask(None)
    assert not ip._gm_item.isVisible()
