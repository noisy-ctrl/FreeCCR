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

TEAL_CAST = np.array([0.55, 1.0, 0.92], np.float32)   # a teal cast: C ~0.014 at L 0.18
DEEP = 0.08                                          # x TEAL_CAST: a cast deep in the shadows


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
    rgb = tuple(DEEP * TEAL_CAST)
    kept, a, b = _kept(rgb)
    assert _chroma(a[None])[0] > 0.012                             # a real cast
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
    rgb = (0.04, 0.08, 0.02)                 # foliage deep in shade (L 0.18, C 0.034)
    low, _a, _b = _kept(rgb, shape={"reach": 20})
    high, _a, _b = _kept(rgb, shape={"reach": 100})
    assert low > 0.9 and high < 0.3


def test_range_moves_where_it_ends():
    rgb = (0.15, 0.19, 0.18)                 # a mild cast at L 0.30 (display ~44/255)
    short, _a, _b = _kept(rgb, shape={"range": 20})
    long_, _a, _b = _kept(rgb, shape={"range": 45})
    assert short == 1.0 and long_ < 0.5


def test_it_is_the_lower_end_of_the_shadows_by_default():
    """Default Range 30: fully on near black, gone by display ~44/255, so the
    upper shadows and everything above keep their colour."""
    keep_44, _a, _b = _kept((0.15, 0.19, 0.18))           # L 0.30
    keep_low, _a, _b = _kept(tuple(DEEP * TEAL_CAST))     # L 0.18
    assert keep_44 == 1.0 and keep_low < 0.3
    assert sn.SHADOW_LIMITS["range"] == (15, 45)


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
    assert sn.normalize_shadow_shape({"range": 200}) == dict(sn.SHADOW_SHAPE_DEFAULT, range=45)
    # an old three-option shape (no Smoothing) keeps the default smoothing
    assert sn.normalize_shadow_shape({"range": 30, "reach": 45, "depth": 20}) == \
        dict(sn.SHADOW_SHAPE_DEFAULT, depth=20)
    assert sn.normalize_shadow_shape("junk") is None


def test_weight_map_matches_what_it_does():
    img = np.array([[tuple(DEEP * TEAL_CAST), (0.30, 0.04, 0.04), (0.8, 0.8, 0.8)]],
                   np.float32)
    w = sn.weight_map(img)
    assert w[0, 0] > 0.6 and w[0, 1] < 0.1 and w[0, 2] == 0.0
    td = sn.depth_weight(_lab(img[0, 1])[:, 0], dict(sn.SHADOW_SHAPE_DEFAULT))[0]
    assert sn.weight_map(img, {"depth": 100})[0, 1] == pytest.approx(td, abs=1e-6)


@pytest.mark.parametrize("ws", [False, True])
def test_gpu_matches_cpu(ws):
    rng = np.random.default_rng(1)
    img = (encode_window(rng.uniform(-0.1, 1.1, (30, 40, 3)).astype(np.float32))
           if ws else _img(2, (30, 40, 3)))
    kw = dict(shadow_neutralise=80, shadow_shape={"reach": 70, "depth": 40},
              golden_highlights=50, contrast=10, saturation=20, highlights_x=20)
    a = adjust_image(img, ws_windowed=ws, **kw).astype(np.int64)
    b = adjust_image_opencl(img, ws_windowed=ws, **kw).astype(np.int64)
    # The decision reads neighbours, so the kernel's last-code differences
    # elsewhere can nudge a pixel's strength; on a fully saturated pixel
    # (chroma ~0.3) that shows as a few codes more. Still well under one
    # 8-bit level (257 codes).
    diff = np.abs(a - b)
    assert np.mean(diff <= 3) > 0.995 and diff.max() <= 16


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


# --------------------------------------------------------------------------- #
# the matte: no blocks, gradations kept (spec/shadow-neutralise.md, revision 4)
# --------------------------------------------------------------------------- #

def _diagonal(H, W, inside, outside, slope=0.37):
    """`inside` below a slanted line, `outside` above it, antialiased."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = yy - (H * 0.5 + (xx - W * 0.5) * slope)
    a = np.clip(0.5 + d, 0.0, 1.0)[..., None]
    return (a * np.array(inside, np.float32) + (1 - a) * np.array(outside, np.float32)).astype(np.float32)


def _along(m, xs, ys):
    """Bilinear samples of a map at float positions."""
    import cv2
    return cv2.remap(m, xs.astype(np.float32)[None], ys.astype(np.float32)[None],
                     cv2.INTER_LINEAR)[0]


@pytest.mark.parametrize("long_side", [1080, 4320])
def test_the_matte_follows_a_slanted_edge_without_steps(long_side):
    """A cast shadow meeting a dark red along a slanted edge: at any distance
    from the edge the strength is the same all along it (no stair-steps from
    a coarse map scaled up), and it doesn't spill across."""
    H, W = int(long_side * 2 / 3), long_side
    red = (0.18, 0.02, 0.02)
    img = _diagonal(H, W, tuple(DEEP * TEAL_CAST), red)
    shp = dict(sn.SHADOW_SHAPE_DEFAULT)
    m = sn.strength_map(img, 1.0, shp)
    scale = long_side / 1080
    xs = np.arange(int(W * 0.2), int(W * 0.8), dtype=np.float32)
    edge = H * 0.5 + (xs - W * 0.5) * 0.37
    for off in (-12, -4, -1.5, 1.5, 4, 12):          # px from the edge, at preview size
        # a vertical step of off/cos(theta) is a perpendicular distance of off
        vals = _along(m, xs, edge + off * scale / np.cos(np.arctan(0.37)))
        assert vals.std() < 0.001, (off, float(vals.std()))   # a 1080 map scaled up: 0.0018
    red_alone = sn.strength_map(np.full((64, 96, 3), red, np.float32), 1.0, shp)[32, 48]
    far_in = m[int(H * 0.5 + 40 * scale), W // 2]
    far_out = m[int(H * 0.5 - 40 * scale), W // 2]
    assert far_in > 0.6 and abs(far_out - red_alone) < 0.01


def test_a_big_smooth_gradient_stays_smooth():
    """A 4320-px dark ramp whose cast fades in down the frame: the change the
    tool makes is a smooth field - no steps between neighbours, no kinks."""
    H, W = 1440, 4320
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    v = 0.02 + 0.20 * xx / W                                   # black to ~44/255
    cast = (yy / H)[..., None] * (TEAL_CAST - 1.0) + 1.0
    img = (v[..., None] * cast).astype(np.float32)
    out = sn.apply_shadow_neutralise(img, 100)
    a, b = _lab(img).reshape(H, W, 3), _lab(out).reshape(H, W, 3)
    np.testing.assert_allclose(b[..., 0], a[..., 0], atol=2e-4)  # lightness kept
    f = np.hypot(b[..., 1], b[..., 2]) / np.maximum(np.hypot(a[..., 1], a[..., 2]), 1e-6)
    f = f[H // 4:, :]                                           # where there is a cast
    assert np.abs(np.diff(f, axis=1)).max() < 0.01
    assert np.abs(np.diff(f, 2, axis=1)).max() < 0.002          # no kinks either


@pytest.mark.parametrize("c_frac", [0.5, 1.2, 2.0])
def test_grain_is_never_stretched_at_preview_size(c_frac):
    """As test_grain_is_never_stretched, on a preview-sized frame, where the
    decision view is denoised: colour grain only falls, luma grain is kept."""
    rng = np.random.default_rng(11)
    h = np.radians(200)
    c = c_frac * 0.02
    shape = (540, 1080)
    lab = np.zeros(shape + (3,), np.float32)
    lab[..., 0] = 0.15 + rng.normal(0, 0.012, shape)
    lab[..., 1] = c * np.cos(h) + rng.normal(0, 0.004, shape)
    lab[..., 2] = c * np.sin(h) + rng.normal(0, 0.004, shape)
    img = np.clip(from_oklab(lab.reshape(-1, 3)), 0, 1).reshape(lab.shape)
    out = sn.apply_shadow_neutralise(img, 100)
    a, b = _lab(img).reshape(lab.shape), _lab(out).reshape(lab.shape)
    for ch in (1, 2):
        assert (b[..., ch] - b[..., ch].mean()).std() <= (a[..., ch] - a[..., ch].mean()).std() * 1.001
    np.testing.assert_allclose(b[..., 0], a[..., 0], atol=2e-4)


def test_smoothing_softens_specks_but_keeps_edges():
    """Isolated specks of cast in a coloured area (what a hard selection
    would leave as blotches) are taken out less with more Smoothing, while
    the big area's strength is unchanged."""
    rng = np.random.default_rng(3)
    H, W = 540, 1080
    img = np.empty((H, W, 3), np.float32)
    img[:] = (0.18, 0.02, 0.02)                                  # dark red
    img[:, : W // 2] = tuple(DEEP * TEAL_CAST)                  # cast shadow
    ys, xs = rng.integers(20, H - 20, 60), rng.integers(W // 2 + 20, W - 20, 60)
    for y, x in zip(ys, xs):
        img[y - 1:y + 2, x - 1:x + 2] = tuple(DEEP * TEAL_CAST)  # 3 px specks
    shp = dict(sn.SHADOW_SHAPE_DEFAULT)
    crisp = sn.strength_map(img, 1.0, dict(shp, smooth=0))
    soft = sn.strength_map(img, 1.0, dict(shp, smooth=100))
    speck_crisp = np.mean([crisp[y, x] for y, x in zip(ys, xs)])
    speck_soft = np.mean([soft[y, x] for y, x in zip(ys, xs)])
    assert speck_soft < 0.6 * speck_crisp
    assert abs(soft[H // 2, W // 4] - crisp[H // 2, W // 4]) < 0.02


def test_popup_has_less_more_depth_and_smoothing():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.shadow_popup import ROWS, ShadowPopup
    rows = {k: ends for k, _n, ends, _t in ROWS}
    assert rows["depth"] == ("Less", "More")
    assert "smooth" in rows
    pop = ShadowPopup(None, 0)
    assert pop.sliders["range"].minimum() == 15 and pop.sliders["range"].maximum() == 45
    assert pop.values() == sn.SHADOW_SHAPE_DEFAULT
    pop.close()
