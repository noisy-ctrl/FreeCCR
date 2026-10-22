#!/usr/bin/env python3
"""Shadow Neutralise (spec/shadow-neutralise.md), including the image-quality
properties every finishing tool must keep: an exact no-op at 0, untouched
pixels returned bit-for-bit, lightness kept, no clipping, monotone and
smooth along gradients, bounded chroma stretch, CPU == GPU."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core import shadow_neutral as sn  # noqa: E402
from core.oklab import to_oklab, from_oklab  # noqa: E402
from core.ccr_processor import adjust_image, adjust_image_opencl, encode_window  # noqa: E402
from test_x_tones import _Img, _wire, _Stub, _img, _capture  # noqa: E402

TEAL_CAST = np.array([0.55, 1.0, 0.92], np.float32)   # C ~0.02 at L 0.22


def _lab(rgb):
    return to_oklab(np.asarray(rgb, np.float32).reshape(-1, 3))


def _px(*rgb):
    return np.array([[rgb]], np.float32)


def _chroma(lab):
    return np.hypot(lab[:, 1], lab[:, 2])


def _kept(rgb, amount=100, shape=None):
    """Fraction of its chroma a colour keeps, and its lightness before/after."""
    p = _px(*rgb)
    a, b = _lab(p)[0], _lab(sn.apply_shadow_neutralise(p, amount, shape))[0]
    return float(np.hypot(*b[1:]) / np.hypot(*a[1:])), a, b


# --------------------------------------------------------------------------- #
# the maths
# --------------------------------------------------------------------------- #

def test_zero_is_an_exact_no_op():
    img = _img()
    a = adjust_image(img, contrast=10, saturation=5)
    np.testing.assert_array_equal(a, adjust_image(img, contrast=10, saturation=5,
                                                  shadow_neutralise=0,
                                                  shadow_shape={"depth": 60}))
    np.testing.assert_array_equal(adjust_image_opencl(img, contrast=10),
                                  adjust_image_opencl(img, contrast=10, shadow_neutralise=0))
    x = np.random.default_rng(0).random((20, 30, 3)).astype(np.float32)
    assert sn.apply_shadow_neutralise(x, 0) is x


def test_tones_above_the_range_are_returned_exactly():
    rng = np.random.default_rng(1)
    x = rng.random((60, 70, 3)).astype(np.float32)
    out = sn.apply_shadow_neutralise(x, 100, {"depth": 80})
    L = _lab(x)[:, 0].reshape(x.shape[:2])
    above = L >= 0.50
    assert above.mean() > 0.3
    np.testing.assert_array_equal(out[above], x[above])            # bit-for-bit


def test_a_cast_in_the_deep_shadows_goes_neutral_at_the_same_lightness():
    rgb = tuple(0.12 * TEAL_CAST)
    kept, a, b = _kept(rgb)
    assert _chroma(a[None])[0] > 0.015                             # a real cast
    assert kept < 0.4                                               # mostly gone
    assert b[0] == pytest.approx(a[0], abs=1e-4)                    # lightness kept
    hue_a, hue_b = np.arctan2(a[2], a[1]), np.arctan2(b[2], b[1])
    assert abs(hue_a - hue_b) < 1e-3                                # same hue, less of it


@pytest.mark.parametrize("name,rgb,least", [
    ("dark red", (0.30, 0.04, 0.04), 0.95), ("navy", (0.04, 0.06, 0.25), 0.95),
    ("dark plum", (0.20, 0.05, 0.18), 0.9),
    # dark colours have little OKLab chroma (deep foliage ~0.058 at L 0.25)
    ("deep foliage", (0.08, 0.16, 0.04), 0.8),
])
def test_dark_colours_keep_their_colour(name, rgb, least):
    kept, _a, _b = _kept(rgb)
    assert kept > least, (name, kept)


def test_skin_in_shade_keeps_more_than_a_cast_does():
    skin, _a, _b = _kept((0.30, 0.20, 0.15))
    cast, _a, _b = _kept(tuple(0.12 * TEAL_CAST))
    assert skin > cast + 0.25


def test_reach_widens_what_counts_as_a_cast():
    rgb = (0.08, 0.16, 0.04)                 # deep foliage
    low, _a, _b = _kept(rgb, shape={"reach": 20})
    high, _a, _b = _kept(rgb, shape={"reach": 100})
    assert low > 0.95 and high < 0.6


def test_range_moves_where_it_ends():
    rgb = (0.32, 0.40, 0.384)                # a mild cast in the lower midtones (L ~0.5)
    short, _a, _b = _kept(rgb, shape={"range": 30})
    long_, _a, _b = _kept(rgb, shape={"range": 70})
    assert short == 1.0 and long_ < 0.8


def test_depth_deepens_the_darkest_tones_monotonically():
    x = np.linspace(0, 1, 4001, dtype=np.float32)
    ramp = np.repeat(x[:, None], 3, 1)[None]
    out = sn.apply_shadow_neutralise(ramp, 100, {"depth": 100})
    L0, L1 = _lab(ramp)[:, 0], _lab(out)[:, 0]
    assert out[0, 0].tolist() == [0.0, 0.0, 0.0]                    # black stays black
    assert np.all(np.diff(L1) > 0)                                   # order kept
    assert (L1[1:] - L1[:-1]).min() >= 0.74 * (L0[1:] - L0[:-1]).min()  # slope >= 0.75
    deep = (L0 > 0.02) & (L0 < 0.15)
    assert np.all(L1[deep] < L0[deep] - 1e-3)                       # deeper
    above = L0 >= 0.52                                               # past the boundary
    np.testing.assert_array_equal(out[0][above], ramp[0][above])    # rest untouched


def test_stays_in_range_without_clipping():
    rng = np.random.default_rng(3)
    img = rng.random((80, 90, 3)).astype(np.float32) * 0.5
    out = sn.apply_shadow_neutralise(img, 100, {"reach": 100, "range": 70, "depth": 100})
    assert out.min() >= 0.0 and out.max() <= 1.0
    noD = sn.apply_shadow_neutralise(img, 100, {"reach": 100, "range": 70})
    dL = np.abs(_lab(noD)[:, 0] - _lab(img)[:, 0])
    assert dL.max() < 2e-4, float(dL.max())                         # lightness kept


@pytest.mark.parametrize("tint", [(1.0, 1.0, 1.0), tuple(TEAL_CAST), (1.0, 0.86, 0.95),
                                  (1.0, 0.75, 0.55)])
def test_a_fine_ramp_stays_smooth_and_in_order(tint):
    """A 16-bit-fine ramp from black into the midtones, with a cast: L keeps
    its order, chroma along it changes smoothly (no steps, so no banding)."""
    x = np.arange(0, 0.6, 1 / 65535, dtype=np.float32)
    ramp = (x[:, None] * np.array(tint, np.float32))[None]
    for shape in (None, {"depth": 100}, {"reach": 100, "range": 70}):
        lab = _lab(sn.apply_shadow_neutralise(ramp, 100, shape))
        assert np.all(np.diff(lab[:, 0]) >= -1e-6)
        c = _chroma(lab)
        assert np.abs(np.diff(c)).max() < 2e-4


def _chroma_sweep(h, L=0.15, top=0.12, n=1001, cols=8):
    """Rows of constant colour, chroma rising down the image at hue h (the
    in-gamut part only), so the decision view sees a smooth gradient."""
    C = np.linspace(0, top, n, dtype=np.float32)
    lab = np.stack([np.full_like(C, L), C * np.cos(h), C * np.sin(h)], 1)
    rgb = from_oklab(lab)                    # (clamps at 0: check the round trip)
    bad = np.flatnonzero((rgb > 1).any(1) | (np.abs(to_oklab(rgb) - lab) > 1e-4).any(1))
    rgb = rgb[: bad[0]] if len(bad) else rgb
    assert len(rgb) > 200
    return np.repeat(rgb[:, None, :], cols, axis=1)


def test_chroma_stretch_is_bounded():
    """Along a smooth gradient chroma never reverses (monotone) and is
    stretched at most ~1.45x where the gate hands colour back (the
    Gaussian's bound)."""
    for h in np.radians([0, 60, 140, 200, 280]):
        img = _chroma_sweep(h)
        Cin = _chroma(_lab(img[:, 3]))
        Cout = _chroma(_lab(sn.apply_shadow_neutralise(img, 100)[:, 3]))
        dc_in, dc_out = np.diff(Cin)[10:-10], np.diff(Cout)[10:-10]   # away from the ends
        assert np.all(dc_out >= -1e-6)
        sel = dc_in > 1e-6
        assert (dc_out[sel] / dc_in[sel]).max() < 1.5


def test_a_big_render_decides_like_the_preview():
    """A render bigger than the decision resolution is decided at it and the
    strength map scaled back up: the same strengths as a per-pixel reading of
    the smooth gradient (to 0.002), and over any 25 rows still monotone with
    a bounded stretch (the resampling wiggle is below a 16-bit code)."""
    for h in np.radians([0, 140, 200]):
        img = _chroma_sweep(h, n=2401)
        lab = _lab(img[:, 3])
        shape = dict(sn.SHADOW_SHAPE_DEFAULT)
        per_pixel = sn.colour_gate(lab, shape) * sn.tone_weight(lab[:, 0], shape)
        m = sn.strength_map(img, 1.0, shape)[:, 3]
        assert np.abs(m - per_pixel)[10:-10].max() < 0.002
        Cin = _chroma(lab)
        Cout = _chroma(_lab(sn.apply_shadow_neutralise(img, 100)[:, 3]))
        dc_in, dc_out = (Cin[35::25] - Cin[10:-25:25]), (Cout[35::25] - Cout[10:-25:25])
        assert np.all(dc_out > 0) and (dc_out / dc_in).max() < 1.5


@pytest.mark.parametrize("c_frac", [0.5, 1.2, 2.0])
def test_grain_is_never_stretched(c_frac):
    """The decision is made on the denoised view, so a pixel's colour grain is
    only scaled down (and its luma grain kept exactly), even at the chroma
    where a per-pixel gate would stretch it most (~1.2 x Reach)."""
    rng = np.random.default_rng(7)
    h = np.radians(200)
    c = c_frac * sn.SHADOW_SHAPE_DEFAULT["reach"] * sn.REACH_UNIT
    lab = np.zeros((120, 160, 3), np.float32)
    lab[..., 0] = 0.18 + rng.normal(0, 0.01, (120, 160))
    lab[..., 1] = c * np.cos(h) + rng.normal(0, 0.004, (120, 160))
    lab[..., 2] = c * np.sin(h) + rng.normal(0, 0.004, (120, 160))
    img = np.clip(from_oklab(lab.reshape(-1, 3)), 0, 1).reshape(lab.shape)
    out = sn.apply_shadow_neutralise(img, 100)
    a, b = _lab(img).reshape(lab.shape), _lab(out).reshape(lab.shape)
    for ch in (1, 2):
        assert (b[..., ch] - b[..., ch].mean()).std() <= (a[..., ch] - a[..., ch].mean()).std() * 1.001
    np.testing.assert_allclose(b[..., 0], a[..., 0], atol=2e-4)


def test_options_are_normalised():
    assert sn.normalize_shadow_shape(dict(sn.SHADOW_SHAPE_DEFAULT)) is None
    assert sn.normalize_shadow_shape({"range": 200}) == dict(sn.SHADOW_SHAPE_DEFAULT, range=70)
    assert sn.normalize_shadow_shape("junk") is None


def test_weight_map_matches_what_it_does():
    img = np.array([[tuple(0.12 * TEAL_CAST), (0.30, 0.04, 0.04), (0.8, 0.8, 0.8)]],
                   np.float32)
    w = sn.weight_map(img)
    assert w[0, 0] > 0.6 and w[0, 1] < 0.1 and w[0, 2] == 0.0
    t = sn.tone_weight(_lab(img[0, 1])[:, 0], dict(sn.SHADOW_SHAPE_DEFAULT))[0]
    assert sn.weight_map(img, {"depth": 100})[0, 1] == pytest.approx(t)  # Depth acts on it


@pytest.mark.parametrize("ws", [False, True])
def test_gpu_matches_cpu(ws):
    rng = np.random.default_rng(1)
    img = (encode_window(rng.uniform(-0.1, 1.1, (30, 40, 3)).astype(np.float32))
           if ws else _img(2, (30, 40, 3)))
    kw = dict(shadow_neutralise=80, shadow_shape={"reach": 70, "depth": 40},
              golden_highlights=50, contrast=10, saturation=20, highlights_x=20)
    a = adjust_image(img, ws_windowed=ws, **kw)
    b = adjust_image_opencl(img, ws_windowed=ws, **kw)
    np.testing.assert_allclose(a.astype(np.int64), b.astype(np.int64), atol=3)


def test_runs_after_golden_highlights():
    img = _img(4)
    both = adjust_image(img, golden_highlights=60, shadow_neutralise=70)
    from core.golden import apply_golden
    gold = adjust_image(img, golden_highlights=60).astype(np.float32) / 65535
    manual = sn.apply_shadow_neutralise(gold, 70)
    np.testing.assert_allclose(both.astype(np.float64) / 65535, manual, atol=2.5 / 65535)
    assert apply_golden  # imported where the processor imports it


def test_image_passes_the_setting(monkeypatch):
    from core.ccr_image import CCRImage
    seen = _capture(monkeypatch)
    CCRImage.apply_adjustments(_Stub({"shadow_neutralise": 55, "shadow_shape": {"reach": 30}}),
                               _img())
    assert seen["shadow_neutralise"] == 55 and seen["shadow_shape"] == {"reach": 30}
    seen.clear()
    CCRImage._adjust_for_area(_Stub({}), _img(), {"shadow_neutralise": 20})
    assert seen["shadow_neutralise"] == 20


# --------------------------------------------------------------------------- #
# panel
# --------------------------------------------------------------------------- #

@pytest.fixture
def panel():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel
    return SlidersPanel()


def test_slider_sits_right_under_golden_highlights(panel):
    from PySide6.QtCore import Qt
    keys = panel.adjustment_keys
    i = keys.index("shadow_neutralise")
    assert keys[i - 1] == "golden_highlights" and keys[i + 1] == "colour_organics"
    assert panel.sliders[i].value_range() == (0, 100) and panel.sliders[i].value() == 0
    lbl = panel._shadow_label
    assert lbl.text() == "Shadow Neutralise"
    assert lbl.contextMenuPolicy() == Qt.CustomContextMenu and "Right-click" in lbl.toolTip()
    from widgets.sliders_panel import SYNC_GROUPS
    assert "shadow_neutralise" in dict((g, k) for g, _l, k in SYNC_GROUPS)["sat"]


def test_popup_writes_options_with_one_undo(panel, monkeypatch):
    img = _Img()
    _wire(panel, monkeypatch, img)
    panel._open_shadow_popup(panel.mapToGlobal(panel.rect().center()))
    pop = panel._shadow_popup
    pop.sliders["reach"].setValue(30)
    pop.sliders["depth"].setValue(40)
    assert img.adjustment_settings["shadow_shape"] == dict(sn.SHADOW_SHAPE_DEFAULT,
                                                           reach=30, depth=40)
    assert panel._shadow_label.font().underline()
    pop.close()
    assert img.undo == 1
    panel.sliders[panel.adjustment_keys.index("saturation")].setValue(7)
    assert img.adjustment_settings["shadow_shape"]["depth"] == 40
    panel._open_shadow_popup(panel.mapToGlobal(panel.rect().center()))
    panel._shadow_popup.reset()
    assert "shadow_shape" not in img.adjustment_settings
    panel._shadow_popup.close()


def test_paste_copy_and_sync(panel, monkeypatch):
    from widgets.sliders_panel import paste_options
    clip = {"adjustments": {}, "shadow_shape": {"reach": 30}}
    row = [r for r in paste_options(clip, lambda k: 0) if r[1] == "shadow_shape"]
    assert row and "reach 30" in row[0][2]
    img = _Img({"contrast": 5})
    _wire(panel, monkeypatch, img)
    panel.current_idx = 1
    panel.clipboard = dict(clip, curves=None, cineon_log=False, look_lut=None,
                           flags={}, profile="color", crop=(None, 0.0), rotation=0,
                           flip_h=False, flip_v=False, fine_rotation=0)
    panel._apply_paste(0, {"shadow_shape"})
    assert img.adjustment_settings["shadow_shape"]["reach"] == 30
    src = _Img({"shadow_neutralise": 40, "shadow_shape": {"depth": 10}})
    _wire(panel, monkeypatch, src)
    panel.current_idx = 5
    panel.copy_settings_from_index(0)
    assert panel.clipboard["shadow_shape"]["depth"] == 10
    assert panel.clipboard["adjustments"]["shadow_neutralise"] == 40

    from core.ccr_backend import ccr_backend
    from widgets.sliders_panel import SYNC_GROUPS
    dst = _Img({"shadow_neutralise": 0})
    for im in (src, dst):
        im.crop_rect, im.crop_angle, im.color_profile = None, 0.0, "color"
    _wire(panel, monkeypatch, src)
    monkeypatch.setattr(ccr_backend, "images", [src, dst])
    monkeypatch.setattr(ccr_backend, "get_image_by_index", lambda i: [src, dst][i])
    panel.current_idx = 0
    panel._sync_group_selection = {gid: gid == "sat" for gid, _l, _k in SYNC_GROUPS}
    panel._perform_sync_to_all()
    assert dst.adjustment_settings["shadow_neutralise"] == 40
    assert dst.adjustment_settings["shadow_shape"]["depth"] == 10


def test_reset_clears_the_options(panel, monkeypatch):
    img = _Img({"shadow_neutralise": 30, "shadow_shape": {"depth": 50}})
    _wire(panel, monkeypatch, img)
    panel._shadow_shape = {"depth": 50}
    panel._update_shadow_label()
    assert panel._shadow_label.font().underline()
    panel.on_reset_clicked()
    assert panel._shadow_shape is None
    assert not panel._shadow_label.font().underline()


def test_show_where_it_acts_follows_the_popup(panel, monkeypatch):
    img = _Img()
    _wire(panel, monkeypatch, img)
    calls = []
    ip = type("IP", (), {"update_preview": lambda s, i: None,
                         "set_shadow_mask": lambda s, shape: calls.append(shape)})()
    mw = type("MW", (), {"image_preview": ip})()
    monkeypatch.setattr(panel, "parent", lambda: type("P", (), {"parent": lambda s: mw})())
    panel._open_shadow_popup(panel.mapToGlobal(panel.rect().center()))
    pop = panel._shadow_popup
    pop.mask_check.setChecked(True)
    assert calls[-1] == {}                                   # default options
    pop.sliders["reach"].setValue(80)
    assert calls[-1]["reach"] == 80                          # follows the options
    pop.close()
    assert calls[-1] is None                                 # gone with the popup


def test_popup_strip_shows_the_cast_going(panel):
    from widgets.shadow_popup import ShadowPopup, _ramp_pixmap
    from PySide6.QtGui import QImage

    def arr(pm):
        q = pm.toImage().convertToFormat(QImage.Format_RGB888)
        w, h = q.width(), q.height()
        return np.frombuffer(q.constBits(), np.uint8).reshape(h, q.bytesPerLine())[:, :w * 3] \
            .reshape(h, w, 3).astype(int)
    before, after = arr(_ramp_pixmap(0, None)), arr(_ramp_pixmap(100, None))
    spread = lambda a: (a.max(axis=2) - a.min(axis=2))[:, 10:60].mean()   # the deep end
    assert spread(after) < 0.5 * spread(before)
    pop = ShadowPopup(None, 0)
    assert pop.values() == sn.SHADOW_SHAPE_DEFAULT
    pop.close()


def test_canvas_layer_shows_where_it_acts(tmp_path, monkeypatch):
    from test_false_colour import _canvas, _HOSTS  # noqa: F401
    from PySide6.QtGui import QImage
    ip, _img_ = _canvas(tmp_path, monkeypatch)
    ip.set_shadow_mask({})
    item = ip._gm_item
    assert item is not None and item.isVisible() and item.parentItem() is ip.pixmap_item
    q = item.pixmap().toImage().convertToFormat(QImage.Format_RGBA8888)
    w, h = q.width(), q.height()
    a = np.frombuffer(q.constBits(), np.uint8).reshape(h, q.bytesPerLine())[:, :w * 4]
    px = a.reshape(h, w, 4)
    alpha = px[..., 3]
    assert alpha[:, :3].max() > 150 and alpha[:, w // 2:].max() == 0   # dark end only
    assert tuple(px[0, 0, :3]) == (255, 40, 200)                      # magenta, not the gold cyan
    ip.set_gold_mask(None)                       # the other tool's popup closing
    assert ip._gm_item.isVisible()               # ... doesn't hide this map
    ip.set_shadow_mask(None)
    assert not ip._gm_item.isVisible()
