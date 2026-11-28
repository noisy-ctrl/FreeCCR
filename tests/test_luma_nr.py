"""Luminance noise reduction (spec/luminance-noise-reduction.md)."""
import os
import sys
import time

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from core import luma_nr as L      # noqa: E402

H, W = 600, 900


def _scene(seed=0, sigma=0.012, h=H, w=W):
    """A step, strong texture at the bottom and grain-like noise of a known
    size. Returns (uint16 RGB, clean luma, noise)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.where(xx < w // 2, 0.40, 0.62).astype(np.float32)
    tex = (0.08 * np.sin(xx / 6.0) * np.sin(yy / 7.0) * (yy > h * 0.6)).astype(np.float32)
    clean = base + tex
    noise = cv2.GaussianBlur(rng.normal(0, sigma, (h, w)).astype(np.float32), (0, 0), 0.7) * 1.6
    y = np.clip(clean + noise, 0, 1)
    rgb = np.stack([y * 1.08, y, y * 0.88], -1)
    return np.rint(np.clip(rgb, 0, 1) * 65535).astype(np.uint16), clean, noise


def _luma(img16):
    return (img16.astype(np.float32) / 65535) @ L.LUMA


def _bp(a):
    return a - cv2.GaussianBlur(a, (0, 0), 10)


FLAT = (slice(40, 300), slice(60, 400))
TEX = (slice(400, 580), slice(60, 840))


def test_zero_returns_the_input_object():
    img, _c, _n = _scene()
    assert L.apply_luma_nr(img, 0) is img
    assert L.apply_luma_nr(img, 0, 30) is img


def test_a_clean_picture_is_left_alone():
    yy, xx = np.mgrid[0:H, 0:W]
    y = (0.2 + 0.6 * xx / W + 0.05 * np.sin(yy / 40.0)).astype(np.float32)
    img = np.rint(np.stack([y, y * 0.9, y * 0.8], -1) * 65535).astype(np.uint16)
    out = L.apply_luma_nr(img, 100, 50)
    assert np.abs(out.astype(int) - img.astype(int)).max() <= 1


def test_noise_falls_and_texture_and_edges_stay():
    img, clean, noise = _scene()
    y_in = _luma(img)
    for amount, max_noise, min_kept in ((50, 0.65, 0.88), (100, 0.45, 0.75)):
        out = L.apply_luma_nr(img, amount, 50)
        y = _luma(out)
        factor = (y - clean)[FLAT].std() / (y_in - clean)[FLAT].std()
        ref = _bp(clean)[TEX]
        kept = float((_bp(y)[TEX] * ref).sum() / (ref * ref).sum())
        print(f"amount {amount}: noise x{factor:.2f}, texture kept {kept:.2f}")
        assert factor < max_noise
        assert kept > min_kept
    # the step: its 10-90 % width barely changes (averaged rows, flat part)
    def width(yrow):
        lo, hi = yrow[:20].mean(), yrow[-20:].mean()
        a = np.interp(lo + 0.1 * (hi - lo), yrow, np.arange(yrow.size))
        b = np.interp(lo + 0.9 * (hi - lo), yrow, np.arange(yrow.size))
        return b - a
    cut = (slice(40, 300), slice(W // 2 - 30, W // 2 + 30))
    w_in = width(y_in[cut].mean(0))
    w_out = width(_luma(L.apply_luma_nr(img, 100, 50))[cut].mean(0))
    assert w_out - w_in < 0.5


def test_detail_keeps_more_as_it_rises():
    img, clean, _n = _scene(seed=2)
    left = [(_luma(L.apply_luma_nr(img, 100, d)) - clean)[FLAT].std() for d in (0, 50, 100)]
    assert left[0] < left[1] < left[2]
    # Detail 100 keeps at least the floor of the finest band
    y_in = _luma(img)
    d_in = (y_in - L._smooth(y_in, 1))[FLAT].std()
    y100 = _luma(L.apply_luma_nr(img, 100, 100))
    d_out = (y100 - L._smooth(y100, 1))[FLAT].std()
    assert d_out >= L.FLOOR_MAX * d_in * 0.95


def test_colour_differences_are_untouched():
    img, _c, _n = _scene(seed=3)
    out = L.apply_luma_nr(img, 100, 30)
    f_in, f_out = img.astype(np.float64) / 65535, out.astype(np.float64) / 65535
    y_in, y_out = f_in @ L.LUMA, f_out @ L.LUMA
    for c in (0, 2):
        assert np.abs((f_out[..., c] - y_out) - (f_in[..., c] - y_in)).max() < 2.5 / 65535


def test_nothing_leaves_range_and_no_new_clipping():
    rng = np.random.default_rng(4)
    y = np.clip(0.97 + rng.normal(0, 0.02, (300, 400)), 0, 1).astype(np.float32)
    img = np.rint(np.stack([y, y * 0.7, y * 0.5], -1) * 65535).astype(np.uint16)
    out = L.apply_luma_nr(img, 100, 0)
    assert (out[..., 0] == 65535).sum() <= (img[..., 0] == 65535).sum()
    dark = np.rint(np.stack([1 - y, (1 - y) * 0.7, (1 - y) * 0.5], -1) * 65535).astype(np.uint16)
    out = L.apply_luma_nr(dark, 100, 0)
    assert (out == 0).sum() <= (dark == 0).sum()


def test_a_ramp_keeps_its_order():
    rng = np.random.default_rng(5)
    xx = np.linspace(0.05, 0.95, 1200, dtype=np.float32)
    y = np.clip(xx[None, :] + rng.normal(0, 0.01, (200, 1200)), 0, 1).astype(np.float32)
    img = np.rint(np.stack([y, y, y], -1) * 65535).astype(np.uint16)
    out = _luma(L.apply_luma_nr(img, 100, 0)).mean(0)
    assert np.all(np.diff(cv2.GaussianBlur(out.reshape(1, -1), (0, 0), 4).ravel()) > 0)
    # and a clean 16-bit-fine ramp is untouched
    fine = (np.arange(4096, dtype=np.float32) + 20000)[None, :].repeat(64, 0)
    img = np.stack([fine, fine, fine], -1).astype(np.uint16)
    assert np.abs(L.apply_luma_nr(img, 100, 0).astype(int) - img.astype(int)).max() <= 1


def test_unmoved_pixels_come_back_exactly():
    img, _c, _n = _scene(seed=6)
    img[:240, :240] = (30000, 30000, 30000)      # a flat block wider than the bands reach
    out = L.apply_luma_nr(img, 60, 50)
    assert np.array_equal(out[20:140, 20:140], img[20:140, 20:140])


def test_strips_give_the_same_picture_as_one_piece(monkeypatch):
    img, _c, _n = _scene(seed=7, h=1400, w=500)
    strips = L.apply_luma_nr(img, 80, 40)
    monkeypatch.setattr(L, "STRIP", 100000)
    whole = L.apply_luma_nr(img, 80, 40)
    assert np.abs(strips.astype(int) - whole.astype(int)).max() <= 1


def test_band_weights_follow_the_full_resolution_size():
    assert L.band_weight(1) == L.band_weight(4) == 1.0
    assert abs(L.band_weight(8) - 0.75) < 1e-9 and abs(L.band_weight(16) - 0.5) < 1e-9
    assert L.band_weight(64) == 0.0
    # a preview at a fifth of the scan treats its finest band as a 5 px one
    img, _c, _n = _scene(seed=8)
    full = L.apply_luma_nr(img, 100, 50, scale=1.0)
    small = L.apply_luma_nr(img, 100, 50, scale=0.2)
    ch = lambda o: np.abs(o.astype(int) - img.astype(int)).mean()
    assert ch(small) < ch(full)


def test_the_noise_is_measured_by_tone():
    rng = np.random.default_rng(9)
    xx = np.linspace(0.05, 0.95, 1000, dtype=np.float32)[None, :].repeat(600, 0)
    sigma = np.where(xx < 0.5, 0.02, 0.005)          # noisier shadows
    y = np.clip(xx + rng.normal(0, 1, xx.shape) * sigma, 0, 1).astype(np.float32)
    prof = L.noise_profile(y)
    assert prof[0, 1] > 2.5 * prof[0, 6]


def test_speed_is_reported():
    img, _c, _n = _scene(h=1080, w=1620)
    t = time.time()
    L.apply_luma_nr(img, 50, 50)
    dt = time.time() - t
    print(f"1620 x 1080: {dt * 1000:.0f} ms")
    assert dt < 5.0


# --------------------------------------------------------------------------- #
# Pipeline and panel
# --------------------------------------------------------------------------- #

def _ccr(settings, profile="color"):
    import test_chroma_noise_reduction as tcn
    return tcn.TestPipeline()._image(settings, profile)


def test_the_render_runs_it_bypass_and_bw_included():
    img, _c, _n = _scene(seed=10)
    plain = _ccr({}).apply_adjustments(img.copy())
    on = _ccr({"luma_nr": 70}).apply_adjustments(img.copy())
    assert not np.array_equal(on, plain)
    by = _ccr({"luma_nr": 70, "chroma_nr_export_only": True})
    assert np.array_equal(by.apply_adjustments(img.copy()), plain)
    assert not np.array_equal(by.apply_adjustments(img.copy(), for_export=True), plain)
    bw_plain = _ccr({}, "bw").apply_adjustments(img.copy())
    bw_on = _ccr({"luma_nr": 70}, "bw").apply_adjustments(img.copy())
    assert not np.array_equal(bw_on, bw_plain)


def test_the_panel_section():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel, SYNC_GROUPS, paste_options
    import test_paste_settings_dialog as tp
    panel = tp._panel()
    assert panel.noise_section.isAncestorOf(panel.luma_nr_caption)
    nl = panel.noise_section._content_layout
    order = []
    for k in range(nl.count()):
        it = nl.itemAt(k)
        order.append(it.widget().text() if it.widget() is not None and hasattr(it.widget(), "text")
                     else it.layout().itemAt(0).widget().text())
    assert order[:6] == ["Luminance", "Amount", "Detail", "Colour", "Amount", "Radius"]
    assert "Noise reduction is skipped" in panel.nr_bypass_checkbox.toolTip()
    groups = {gid: (label, keys) for gid, label, keys in SYNC_GROUPS}
    assert groups["noise"][0] == "Noise Reduction"
    # then Colour Contrast (spec/colour-contrast.md)
    assert SlidersPanel.ADJUSTMENT_KEYS[-3:-1] == ["luma_nr", "luma_nr_detail"]
    assert SlidersPanel.SLIDER_DEFAULTS["luma_nr_detail"] == L.DETAIL_DEFAULT
    rows = paste_options({"adjustments": {"luma_nr": 40, "luma_nr_detail": 50, "chroma_nr": 30,
                                          "chroma_nr_radius": 50}, "flags": {}},
                         panel._default_for)
    assert ("Detail", "noise",
            "Noise Reduction  (luminance 40, detail 50; colour 30, radius 50)") in rows
