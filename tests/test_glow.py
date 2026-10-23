#!/usr/bin/env python3
"""Glow (spec/highlight-glow.md): a soft highlight glow in Lab Finish, held
to the finishing tools' quality rules: an exact no-op at 0, untouched pixels
returned bit-for-bit, never darker, nothing clips, a ramp stays monotone,
grain is never stretched, no steps at export size, the same at every render
size, CPU == GPU."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

import cv2  # noqa: E402

from core import glow as gl  # noqa: E402
from core.oklab import to_oklab, from_oklab  # noqa: E402
from core.ccr_processor import adjust_image, adjust_image_opencl, encode_window  # noqa: E402
from test_x_tones import _Img, _wire, _Stub, _img, _capture  # noqa: E402


def _lab(rgb):
    return to_oklab(np.asarray(rgb, np.float32).reshape(-1, 3))


def _lamp(H=540, W=1080, ground=0.06, lamp=(1.0, 0.92, 0.78), r=20):
    """A bright disc on a dark ground, centred (on the pixel grid's own
    centre, so a downsampled copy lines up with a smaller render)."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.hypot(yy - (H - 1) / 2, xx - (W - 1) / 2)
    a = np.clip(r + 0.5 - d, 0, 1)[..., None]
    return (a * np.array(lamp, np.float32) + (1 - a) * ground).astype(np.float32)


def test_zero_is_an_exact_no_op():
    img = _img()
    a = adjust_image(img, contrast=10, saturation=5)
    np.testing.assert_array_equal(a, adjust_image(img, contrast=10, saturation=5,
                                                  glow=0, glow_shape={"size": 90}))
    np.testing.assert_array_equal(adjust_image_opencl(img, contrast=10),
                                  adjust_image_opencl(img, contrast=10, glow=0))
    x = np.random.default_rng(0).random((20, 30, 3)).astype(np.float32)
    assert gl.apply_glow(x, 0) is x


def test_a_light_glows_into_the_dark_falling_off_with_distance():
    img = _lamp()
    out = gl.apply_glow(img, 100)
    row = out[270, 540:, :].mean(axis=1) - img[270, 540:, :].mean(axis=1)
    near, mid, far = row[30], row[60], row[200]
    assert near > 0.02 and near > mid > far >= 0
    assert np.all(np.diff(row[25:400]) <= 1e-6)                    # falls off smoothly
    # far from any highlight the picture comes back exactly
    np.testing.assert_array_equal(out[:3, :3], img[:3, :3])


def test_the_glow_keeps_the_light_s_colour():
    img = _lamp(lamp=(1.0, 0.85, 0.6))                             # a warm lamp
    out = gl.apply_glow(img, 100)
    d = out[270, 540 + 35] - img[270, 540 + 35]
    assert d[0] > d[1] > d[2] > 0                                  # warm spill


def test_never_darker_never_clipped():
    rng = np.random.default_rng(3)
    img = rng.random((200, 300, 3)).astype(np.float32)
    img[60:120, 100:180] = 1.0
    out = gl.apply_glow(img, 100, {"size": 100, "start": 50})
    assert out.min() >= 0.0 and out.max() <= 1.0
    assert np.all(out >= img - 1e-6)


def test_a_big_highlight_keeps_its_own_detail():
    """Texture inside a big bright area changes little (it takes little of
    its own glow), while the dark round it is lifted."""
    H, W = 540, 1080
    img = np.full((H, W, 3), 0.08, np.float32)
    rng = np.random.default_rng(1)
    tex = (0.86 + 0.06 * np.sin(np.arange(400) / 3.0))[None, :, None] + rng.normal(0, 0.01, (300, 400, 1))
    img[120:420, 340:740] = np.clip(tex, 0, 1)
    out = gl.apply_glow(img, 100)
    inside = (out - img)[200:340, 420:660].mean(axis=2)
    outside = (out - img)[270, 760:780].mean()
    assert inside.mean() < 0.15 * outside

    def hp(x):
        return (x - cv2.GaussianBlur(x, (0, 0), 6))[200:340, 420:660]
    assert hp(out).std() > 0.93 * hp(img).std()                    # detail kept


@pytest.mark.parametrize("tint", [(1.0, 1.0, 1.0), (1.0, 0.92, 0.8)])
def test_a_ramp_stays_monotone_and_smooth(tint):
    W = 2160
    x = np.linspace(0, 1, W, dtype=np.float32)
    img = np.repeat((x[:, None] * np.array(tint, np.float32))[None], 40, axis=0)
    out = gl.apply_glow(img, 100)
    L = _lab(out[20]).reshape(-1, 3)[:, 0]
    assert np.all(np.diff(L) > -1e-5)
    d = (out - img)[20].mean(axis=1)
    assert np.abs(np.diff(d, 2)).max() < 2e-4                       # no kinks


@pytest.mark.parametrize("ground", [0.06, 0.25, 0.5])
def test_grain_is_never_stretched(ground):
    """The glow is a smooth field screened on: grain round a light comes
    through at most as strong as it went in (the field doesn't follow it)."""
    rng = np.random.default_rng(7)
    clean = _lamp(ground=ground, r=60)
    noise = rng.normal(0, 0.01, clean.shape).astype(np.float32)
    noisy = np.clip(clean + noise, 0, 1)
    a, b = gl.apply_glow(clean, 100), gl.apply_glow(noisy, 100)
    yy, xx = np.mgrid[0:540, 0:1080]
    d = np.hypot(yy - 269.5, xx - 539.5)
    ring = (d > 64) & (d < 160)                                     # where it glows
    assert (a - clean)[ring].mean() > 0.004
    grain_in = (noisy - clean)[ring].std()
    grain_out = (b - a)[ring].std()
    assert grain_out <= grain_in * 1.001


def test_no_steps_at_export_size(monkeypatch):
    """At export size the field is spread on a reduced copy and scaled back
    up; that must match spreading it at full size (no steps, no blocks)."""
    img = _lamp(H=240, W=2160, r=40)
    fast = gl.apply_glow(img, 100)
    monkeypatch.setattr(gl, "MIN_SIGMA_PX", 1e9)                 # no reduction
    exact = gl.apply_glow(img, 100)
    assert np.abs(fast - exact).max() < 2e-3                      # < 0.5 of an 8-bit code
    assert np.abs(fast - exact).mean() < 1e-4
    d = (fast - img)[120, 1080 + 60:1080 + 900].mean(axis=1)      # beyond the edge
    assert d.max() > 0.05
    assert np.all(np.diff(d) <= 1e-6)                             # falls off, never ripples


def test_the_same_glow_at_preview_and_export_size():
    small = _lamp(H=540, W=1080, r=20)
    big = _lamp(H=2160, W=4320, r=80)
    a = gl.apply_glow(small, 100) - small
    b = cv2.resize(gl.apply_glow(big, 100) - big, (1080, 540), interpolation=cv2.INTER_AREA)
    ring = np.zeros((540, 1080), bool)
    yy, xx = np.mgrid[0:540, 0:1080]
    d = np.hypot(yy - 270, xx - 540)
    ring[(d > 26) & (d < 120)] = True
    assert np.abs(a[ring] - b[ring]).max() < 0.01


def test_size_widens_it_and_start_moves_where_it_begins():
    img = _lamp()
    tight = (gl.apply_glow(img, 100, {"size": 10}) - img)[270, 540 + 80].mean()
    wide = (gl.apply_glow(img, 100, {"size": 90}) - img)[270, 540 + 80].mean()
    assert wide > 3 * tight
    grey = _lamp(lamp=(0.7, 0.7, 0.7))                               # a mid-bright patch
    low = (gl.apply_glow(grey, 100, {"start": 50}) - grey)[270, 540 + 40].mean()
    high = (gl.apply_glow(grey, 100, {"start": 95}) - grey)[270, 540 + 40].mean()
    assert low > 0.005 and high < 0.1 * low


def test_options_are_normalised():
    assert gl.normalize_glow_shape(dict(gl.GLOW_SHAPE_DEFAULT)) is None
    assert gl.normalize_glow_shape({"size": 500}) == dict(gl.GLOW_SHAPE_DEFAULT, size=100)
    assert gl.normalize_glow_shape("junk") is None


def test_weight_map_shows_where_it_lands():
    w = gl.weight_map(_lamp())
    assert w[270, 540 + 30] > 0.3 and w[5, 5] < 0.01


@pytest.mark.parametrize("ws", [False, True])
def test_gpu_matches_cpu(ws):
    rng = np.random.default_rng(1)
    img = (encode_window(rng.uniform(-0.1, 1.1, (30, 40, 3)).astype(np.float32))
           if ws else _img(2, (30, 40, 3)))
    kw = dict(glow=80, glow_shape={"size": 60}, shadow_neutralise=40,
              golden_highlights=50, contrast=10, saturation=20)
    a = adjust_image(img, ws_windowed=ws, **kw).astype(np.int64)
    b = adjust_image_opencl(img, ws_windowed=ws, **kw).astype(np.int64)
    diff = np.abs(a - b)
    assert np.mean(diff <= 3) > 0.995 and diff.max() <= 16


def test_runs_last_after_shadow_neutralise():
    img = _img(4)
    both = adjust_image(img, shadow_neutralise=70, glow=60).astype(np.float64) / 65535
    first = adjust_image(img, shadow_neutralise=70).astype(np.float32) / 65535
    np.testing.assert_allclose(both, gl.apply_glow(first, 60), atol=2.5 / 65535)


def test_image_passes_the_setting(monkeypatch):
    from core.ccr_image import CCRImage
    seen = _capture(monkeypatch)
    CCRImage.apply_adjustments(_Stub({"glow": 55, "glow_shape": {"size": 20}}), _img())
    assert seen["glow"] == 55 and seen["glow_shape"] == {"size": 20}
    seen.clear()
    CCRImage._adjust_for_area(_Stub({}), _img(), {"glow": 20})
    assert seen["glow"] == 20


# --------------------------------------------------------------------------- #
# panel
# --------------------------------------------------------------------------- #

@pytest.fixture
def panel():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel
    return SlidersPanel()


def test_slider_sits_in_lab_finish_under_shadow_neutralise(panel):
    from PySide6.QtCore import Qt
    keys = panel.adjustment_keys
    i = keys.index("glow")
    assert keys[i - 1] == "shadow_neutralise" and keys[i + 1] == "colour_organics"
    assert panel.sliders[i].value_range() == (0, 100) and panel.sliders[i].value() == 0
    sec = panel.lab_finish_section
    labels = [lay.itemAt(0).widget() for lay in (panel.golden_slider_layout,
              panel.shadow_neutralise_slider_layout, panel.glow_slider_layout)]
    assert all(sec.isAncestorOf(lb) for lb in labels)
    assert sec._content_layout.indexOf(panel.glow_slider_layout) == 2
    lbl = panel._glow_label
    assert lbl.text() == "Glow"
    assert lbl.contextMenuPolicy() == Qt.CustomContextMenu and "Right-click" in lbl.toolTip()
    from widgets.sliders_panel import SYNC_GROUPS
    assert "glow" in dict((g, k) for g, _l, k in SYNC_GROUPS)["sat"]


def test_popup_writes_options_with_one_undo(panel, monkeypatch):
    img = _Img()
    _wire(panel, monkeypatch, img)
    panel._open_glow_popup(panel.mapToGlobal(panel.rect().center()))
    pop = panel._glow_popup
    pop.sliders["size"].setValue(70)
    pop.sliders["start"].setValue(85)
    assert img.adjustment_settings["glow_shape"] == {"size": 70, "start": 85}
    assert panel._glow_label.font().underline()
    pop.close()
    assert img.undo == 1
    panel.sliders[panel.adjustment_keys.index("saturation")].setValue(7)
    assert img.adjustment_settings["glow_shape"]["size"] == 70
    panel._open_glow_popup(panel.mapToGlobal(panel.rect().center()))
    panel._glow_popup.reset()
    assert "glow_shape" not in img.adjustment_settings
    panel._glow_popup.close()


def test_paste_copy_and_sync(panel, monkeypatch):
    from widgets.sliders_panel import paste_options
    clip = {"adjustments": {}, "glow_shape": {"size": 70}}
    row = [r for r in paste_options(clip, lambda k: 0) if r[1] == "glow_shape"]
    assert row and "size 70" in row[0][2]
    img = _Img({"contrast": 5})
    _wire(panel, monkeypatch, img)
    panel.current_idx = 1
    panel.clipboard = dict(clip, curves=None, cineon_log=False, look_lut=None,
                           flags={}, profile="color", crop=(None, 0.0), rotation=0,
                           flip_h=False, flip_v=False, fine_rotation=0)
    panel._apply_paste(0, {"glow_shape"})
    assert img.adjustment_settings["glow_shape"]["size"] == 70
    src = _Img({"glow": 40, "glow_shape": {"start": 60}})
    _wire(panel, monkeypatch, src)
    panel.current_idx = 5
    panel.copy_settings_from_index(0)
    assert panel.clipboard["glow_shape"]["start"] == 60
    assert panel.clipboard["adjustments"]["glow"] == 40

    from core.ccr_backend import ccr_backend
    from widgets.sliders_panel import SYNC_GROUPS
    dst = _Img({"glow": 0})
    for im in (src, dst):
        im.crop_rect, im.crop_angle, im.color_profile = None, 0.0, "color"
    _wire(panel, monkeypatch, src)
    monkeypatch.setattr(ccr_backend, "images", [src, dst])
    monkeypatch.setattr(ccr_backend, "get_image_by_index", lambda i: [src, dst][i])
    panel.current_idx = 0
    panel._sync_group_selection = {gid: gid == "sat" for gid, _l, _k in SYNC_GROUPS}
    panel._perform_sync_to_all()
    assert dst.adjustment_settings["glow"] == 40
    assert dst.adjustment_settings["glow_shape"]["start"] == 60


def test_reset_clears_the_options(panel, monkeypatch):
    img = _Img({"glow": 30, "glow_shape": {"size": 90}})
    _wire(panel, monkeypatch, img)
    panel._glow_shape = {"size": 90}
    panel._update_glow_label()
    assert panel._glow_label.font().underline()
    panel.on_reset_clicked()
    assert panel._glow_shape is None and not panel._glow_label.font().underline()


def test_show_where_it_lands_follows_the_popup(panel, monkeypatch):
    img = _Img()
    _wire(panel, monkeypatch, img)
    calls = []
    ip = type("IP", (), {"update_preview": lambda s, i: None,
                         "set_glow_mask": lambda s, shape: calls.append(shape)})()
    mw = type("MW", (), {"image_preview": ip})()
    monkeypatch.setattr(panel, "parent", lambda: type("P", (), {"parent": lambda s: mw})())
    panel._open_glow_popup(panel.mapToGlobal(panel.rect().center()))
    pop = panel._glow_popup
    pop.mask_check.setChecked(True)
    assert calls[-1] == {}
    pop.sliders["size"].setValue(80)
    assert calls[-1]["size"] == 80
    pop.close()
    assert calls[-1] is None


def test_popup_strip_shows_the_light_spreading():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.glow_popup import _spot_pixmap, GlowPopup, ROWS
    from PySide6.QtGui import QImage

    def arr(pm):
        q = pm.toImage().convertToFormat(QImage.Format_RGB888)
        w, h = q.width(), q.height()
        return np.frombuffer(q.constBits(), np.uint8).reshape(h, q.bytesPerLine())[:, :w * 3] \
            .reshape(h, w, 3).astype(int)
    before, after = arr(_spot_pixmap(0, None)), arr(_spot_pixmap(100, None))
    assert after[20, 80].sum() > before[20, 80].sum() + 6          # light beside the window
    assert {k for k, *_ in ROWS} == set(gl.GLOW_SHAPE_DEFAULT)
    pop = GlowPopup(None, 0)
    assert pop.values() == gl.GLOW_SHAPE_DEFAULT
    pop.close()


def test_canvas_layer_is_amber_where_the_glow_lands(tmp_path, monkeypatch):
    from test_false_colour import _canvas, _HOSTS  # noqa: F401
    from PySide6.QtGui import QImage
    ip, _img_ = _canvas(tmp_path, monkeypatch)
    ip.set_glow_mask({})
    item = ip._gm_item
    assert item is not None and item.isVisible()
    q = item.pixmap().toImage().convertToFormat(QImage.Format_RGBA8888)
    w, h = q.width(), q.height()
    px = np.frombuffer(q.constBits(), np.uint8).reshape(h, q.bytesPerLine())[:, :w * 4].reshape(h, w, 4)
    alpha = px[..., 3].astype(int)
    assert alpha.max() > 10                        # a ramp glows only a little
    y, x = np.unravel_index(alpha.argmax(), alpha.shape)
    # amber (a little rounding: the layer is stored premultiplied)
    assert np.abs(px[y, x, :3].astype(int) - (255, 170, 30)).max() <= 255 // alpha.max() + 1
    ip.set_shadow_mask(None)                       # another tool's popup closing
    assert ip._gm_item.isVisible()
    ip.set_glow_mask(None)
    assert not ip._gm_item.isVisible()
