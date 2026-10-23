#!/usr/bin/env python3
"""Shadow Lift (spec/shadow-lift.md): opens the shadows, black stays black,
with an option to lift black itself. Held to the finishing tools' quality
rules: an exact no-op at 0, tones above the Range returned bit-for-bit, a
monotone tone curve, colour kept, nothing clips, CPU == GPU."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core import shadow_lift as sl  # noqa: E402
from core.oklab import to_oklab, from_oklab  # noqa: E402
from core.ccr_processor import adjust_image, adjust_image_opencl, encode_window  # noqa: E402
from test_x_tones import _Img, _wire, _Stub, _img, _capture  # noqa: E402


def _lab(rgb):
    return to_oklab(np.asarray(rgb, np.float32).reshape(-1, 3))


def _grey_ramp(n=4096):
    v = np.linspace(0.0, 1.0, n, dtype=np.float32)
    return np.repeat(v[None, :, None], 3, axis=2)


def _colours(seed=0, n=200_000):
    return np.random.default_rng(seed).random((1, n, 3)).astype(np.float32)


# --------------------------------------------------------------------------- #
# the tone curve
# --------------------------------------------------------------------------- #

def test_zero_is_an_exact_no_op():
    img = _img()
    a = adjust_image(img, contrast=10, saturation=5)
    np.testing.assert_array_equal(a, adjust_image(img, contrast=10, saturation=5,
                                                  shadow_lift=0,
                                                  shadow_lift_shape={"black": 80}))
    np.testing.assert_array_equal(adjust_image_opencl(img, contrast=10),
                                  adjust_image_opencl(img, contrast=10, shadow_lift=0))
    x = _colours()
    assert sl.apply_shadow_lift(x, 0) is x


@pytest.mark.parametrize("shape", [None, {"range": 25}, {"range": 65}])
def test_tones_above_the_range_come_back_exactly(shape):
    x = _colours(1)
    out = sl.apply_shadow_lift(x, 100, shape)
    s = sl.normalize_lift_shape(shape) or sl.LIFT_SHAPE_DEFAULT
    above = _lab(x)[:, 0] >= s["range"] / 100.0
    assert above.mean() > 0.2
    np.testing.assert_array_equal(out.reshape(-1, 3)[above], x.reshape(-1, 3)[above])


def test_black_stays_black_and_the_shadows_open():
    ramp = _grey_ramp()
    out = sl.apply_shadow_lift(ramp, 100)
    assert np.all(out[0, 0] == 0.0)
    L0, L1 = _lab(ramp)[:, 0], _lab(out)[:, 0]
    lift = L1 - L0
    # the most at about a third of the Range (0.45 / 3 = 0.15), a·R·4/27
    k = int(np.argmax(lift))
    assert 0.13 < L0[k] < 0.17 and abs(lift[k] - 0.45 * 4 / 27) < 2e-3
    assert np.all(lift >= -1e-6)                                  # never darker


def test_lift_black_raises_black_to_a_matte():
    ramp = _grey_ramp()
    out = sl.apply_shadow_lift(ramp, 100, {"black": 100})
    L = _lab(out)[:, 0]
    assert abs(L[0] - sl.FADE_MAX) < 1e-3                         # black is now L 0.16
    assert 10 <= out[0, 0, 0] * 255 <= 20                         # ~15/255 displayed
    half = sl.apply_shadow_lift(ramp, 100, {"black": 50})
    assert abs(_lab(half)[0, 0] - sl.FADE_MAX / 2) < 1e-3
    # neutral light: black stays grey
    assert np.ptp(out[0, 0]) < 1e-4


@pytest.mark.parametrize("shape", [None, {"range": 25}, {"range": 65},
                                   {"black": 100}, {"range": 25, "black": 100},
                                   {"range": 65, "black": 100}])
@pytest.mark.parametrize("amount", [30, 100])
def test_the_grey_axis_is_monotone_and_smooth(shape, amount):
    xs = np.linspace(0.0, 1.0, 20001, dtype=np.float64)
    ys = sl.grey_curve(xs, amount, shape).astype(np.float64)
    slope = np.diff(ys) / np.diff(xs)
    s = sl.normalize_lift_shape(shape) or sl.LIFT_SHAPE_DEFAULT
    floor = 0.5 if s["range"] >= 45 else 0.38                    # a strong matte at worst
    assert slope.min() >= floor, slope.min()                      # never flattens
    assert slope.max() <= 1.0 + amount / 100.0 + 1e-3             # steepest at black
    # C1: no kink anywhere (the slope changes gently between samples)
    assert np.abs(np.diff(slope)).max() < 0.01
    # the measured curve through the display, too: a grey ramp stays monotone
    ramp = _grey_ramp()
    out = sl.apply_shadow_lift(ramp, amount, shape)[0, :, 0]
    assert np.all(np.diff(out) >= 0)


def test_the_opened_shadows_keep_their_colour():
    """C / L is kept (the colour scales with the lightness), and the hue
    doesn't move."""
    shadows = np.array([[[0.16, 0.09, 0.06], [0.05, 0.08, 0.12], [0.10, 0.12, 0.05]]],
                       np.float32)
    out = sl.apply_shadow_lift(shadows, 100)
    a, b = _lab(shadows), _lab(out)
    ca, cb = np.hypot(a[:, 1], a[:, 2]), np.hypot(b[:, 1], b[:, 2])
    assert np.all(b[:, 0] > a[:, 0] + 0.02)
    np.testing.assert_allclose(cb / b[:, 0], ca / a[:, 0], rtol=1e-4)
    np.testing.assert_allclose(np.arctan2(b[:, 2], b[:, 1]),
                               np.arctan2(a[:, 2], a[:, 1]), atol=1e-4)


def test_nothing_leaves_the_range_and_strong_colours_fit():
    x = _colours(2)
    x[0, :5000] = np.array([0.0, 0.0, 0.35], np.float32)          # a deep saturated blue
    out = sl.apply_shadow_lift(x, 100, {"black": 100, "range": 65})
    assert out.min() >= 0.0 and out.max() <= 1.0
    # the deep blue still opens (by the largest part of the move that fits)
    assert _lab(out[0, :1])[0, 0] > _lab(x[0, :1])[0, 0]


def test_options_are_normalised():
    assert sl.normalize_lift_shape(dict(sl.LIFT_SHAPE_DEFAULT)) is None
    assert sl.normalize_lift_shape({"range": 500}) == dict(sl.LIFT_SHAPE_DEFAULT, range=65)
    assert sl.normalize_lift_shape({"black": -3}) is None
    assert sl.normalize_lift_shape("junk") is None


def test_weight_map_shows_where_it_acts():
    ramp = _grey_ramp(1000)
    w = sl.weight_map(ramp)[0]
    assert w[0] == 1.0 and w[-1] == 0.0 and np.all(np.diff(w) <= 1e-7)
    wb = sl.weight_map(ramp, {"black": 100})[0]
    assert np.all(wb >= w - 1e-7)


def test_range_reaches_further_up():
    ramp = _grey_ramp()
    narrow = _lab(sl.apply_shadow_lift(ramp, 100, {"range": 25}))[:, 0]
    wide = _lab(sl.apply_shadow_lift(ramp, 100, {"range": 65}))[:, 0]
    L0 = _lab(ramp)[:, 0]
    at = np.argmin(np.abs(L0 - 0.35))
    assert narrow[at] == pytest.approx(L0[at], abs=1e-6) and wide[at] > L0[at] + 0.02


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
    kw = dict(shadow_lift=80, shadow_lift_shape={"black": 60}, shadow_neutralise=40,
              glow=30, contrast=10, saturation=20)
    a = adjust_image(img, ws_windowed=ws, **kw).astype(np.int64)
    b = adjust_image_opencl(img, ws_windowed=ws, **kw).astype(np.int64)
    diff = np.abs(a - b)
    assert np.mean(diff <= 3) > 0.995 and diff.max() <= 24


def test_gpu_finish_matches_numpy(monkeypatch):
    """The OpenCL kernel against the numpy reference on a whole picture: all
    but a few hundredths of a percent within a 16-bit code; the rest (strong
    colours at the edge of the gamut, where the fit-in-range search lands a
    step apart) within 24 codes - under a tenth of an 8-bit level."""
    from core import ccr_processor as cp
    from core import finish_gpu as fg
    from test_finish_gpu import _gpu, _scene
    monkeypatch.setenv("FREECCR_GPU_FINISH", "1")
    ctx, q = _gpu()
    for shape in (None, {"black": 100}, {"range": 65, "black": 40}, {"range": 25}):
        x = _scene()
        cpu = sl.apply_shadow_lift(x, 100, shape)
        gpu = fg.finish_numpy_image(ctx, q, x, shadow_lift=100, shadow_lift_shape=shape)
        d = np.abs(cpu.astype(np.float64) - gpu.astype(np.float64)) * 65535
        assert d.max() <= 24, (shape, d.max())
        assert np.mean(d <= 1.0) >= 0.999, (shape, np.mean(d <= 1.0))
    assert cp is not None


def test_gpu_leaves_untouched_tones_exact(monkeypatch):
    import pyopencl.array as cla
    from core import finish_gpu as fg
    from test_finish_gpu import _gpu
    monkeypatch.setenv("FREECCR_GPU_FINISH", "1")
    ctx, q = _gpu()
    g = fg.for_queue(ctx, q)
    grey = np.full((200, 300, 3), 0.40, np.float32) * np.float32(65535.0)   # L ~0.50
    buf = cla.to_device(q, grey.reshape(-1, 3))
    g.finish(buf, 200, 300, shadow_lift=100.0)
    np.testing.assert_array_equal(buf.get(), grey.reshape(-1, 3))


def test_image_passes_the_setting(monkeypatch):
    from core.ccr_image import CCRImage
    seen = _capture(monkeypatch)
    CCRImage.apply_adjustments(_Stub({"shadow_lift": 55,
                                      "shadow_lift_shape": {"range": 30}}), _img())
    assert seen["shadow_lift"] == 55 and seen["shadow_lift_shape"] == {"range": 30}
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
    pop.sliders["range"].setValue(30)
    pop.sliders["black"].setValue(40)
    assert img.adjustment_settings["shadow_lift_shape"] == {"range": 30, "black": 40}
    assert panel._lift_label.font().underline()
    pop.close()
    assert img.undo == 1
    panel.sliders[panel.adjustment_keys.index("saturation")].setValue(7)
    assert img.adjustment_settings["shadow_lift_shape"]["black"] == 40
    panel._open_lift_popup(panel.mapToGlobal(panel.rect().center()))
    panel._lift_popup.reset()
    assert "shadow_lift_shape" not in img.adjustment_settings
    panel._lift_popup.close()


def test_paste_copy_and_sync(panel, monkeypatch):
    from widgets.sliders_panel import paste_options
    clip = {"adjustments": {}, "shadow_lift_shape": {"black": 70}}
    row = [r for r in paste_options(clip, lambda k: 0) if r[1] == "shadow_lift_shape"]
    assert row and "black 70" in row[0][2]
    img = _Img({"contrast": 5})
    _wire(panel, monkeypatch, img)
    panel.current_idx = 1
    panel.clipboard = dict(clip, curves=None, cineon_log=False, look_lut=None,
                           flags={}, profile="color", crop=(None, 0.0), rotation=0,
                           flip_h=False, flip_v=False, fine_rotation=0)
    panel._apply_paste(0, {"shadow_lift_shape"})
    assert img.adjustment_settings["shadow_lift_shape"]["black"] == 70
    src = _Img({"shadow_lift": 40, "shadow_lift_shape": {"range": 60}})
    _wire(panel, monkeypatch, src)
    panel.current_idx = 5
    panel.copy_settings_from_index(0)
    assert panel.clipboard["shadow_lift_shape"]["range"] == 60
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
    assert dst.adjustment_settings["shadow_lift_shape"]["range"] == 60


def test_reset_clears_the_options(panel, monkeypatch):
    img = _Img({"shadow_lift": 30, "shadow_lift_shape": {"range": 60}})
    _wire(panel, monkeypatch, img)
    panel._lift_shape = {"range": 60}
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
    pop.sliders["range"].setValue(60)
    assert calls[-1]["range"] == 60
    pop.close()
    assert calls[-1] is None


def test_popup_strip_shows_the_shadows_opening():
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
    assert after[5, 40].sum() > before[5, 40].sum() + 6           # the shadows open
    assert after[5, 0].sum() == before[5, 0].sum() == 0           # black stays black
    matte = arr(_spot_pixmap(100, {"black": 100}))
    assert matte[5, 0].sum() > 20                                 # ... unless lifted
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
