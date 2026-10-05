#!/usr/bin/env python3
"""Tests for Chroma Noise Reduction (spec/chroma-noise-reduction.md): a
luma-guided filter on the colour-difference channels only."""

import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_processor import (apply_chroma_denoise,  # noqa: E402
                                chroma_nr_radius_px, CHROMA_NR_RADIUS_DEFAULT)


def _luma(img):
    x = img.astype(np.float64)
    return 0.299 * x[..., 0] + 0.587 * x[..., 1] + 0.114 * x[..., 2]


def _chroma_std(img):
    x = img.astype(np.float64)
    y = _luma(img)
    return float(np.std(x[..., 2] - y) + np.std(x[..., 0] - y))


def _noisy(h=400, w=600, seed=0, sigma=2500):
    """Mid-grey with per-channel (i.e. colour) noise."""
    rng = np.random.default_rng(seed)
    base = np.full((h, w, 3), 30000.0)
    return np.clip(base + rng.normal(0, sigma, (h, w, 3)), 0, 65535).astype(np.uint16)


class TestIdentity:
    def test_zero_amount_returns_input(self):
        img = _noisy()
        assert apply_chroma_denoise(img, 0, 50) is img

    def test_tiny_image_radius_below_a_pixel_is_identity(self):
        img = _noisy(20, 30)
        assert chroma_nr_radius_px(0, 30) < 1
        assert apply_chroma_denoise(img, 100, 0) is img

    def test_shape_dtype_and_extra_channel_kept(self):
        img = _noisy()
        rgba = np.concatenate([img, np.full(img.shape[:2] + (1,), 7, np.uint16)], -1)
        out = apply_chroma_denoise(rgba, 60, 50)
        assert out.dtype == np.uint16 and out.shape == rgba.shape
        assert np.all(out[..., 3] == 7)


class TestEffect:
    def test_reduces_colour_noise(self):
        img = _noisy()
        out = apply_chroma_denoise(img, 100, 60)
        assert _chroma_std(out) < 0.5 * _chroma_std(img)

    def test_luma_is_preserved(self):
        img = _noisy()
        out = apply_chroma_denoise(img, 100, 60)
        assert np.max(np.abs(_luma(out) - _luma(img))) < 2.0   # rounding only

    def test_amount_scales_the_effect(self):
        img = _noisy()
        half = _chroma_std(apply_chroma_denoise(img, 40, 60))
        full = _chroma_std(apply_chroma_denoise(img, 100, 60))
        assert full < half < _chroma_std(img)

    def test_colour_edge_on_a_luma_edge_is_kept(self):
        # Left: dark blue, right: bright orange. The colour boundary sits on a
        # strong luma edge, so the guided filter must not bleed colour across.
        img = np.zeros((300, 400, 3), np.uint16)
        img[:, :200] = (5000, 8000, 20000)
        img[:, 200:] = (50000, 35000, 15000)
        out = apply_chroma_denoise(img, 100, 100).astype(np.int64)
        assert np.max(np.abs(out[:, 195] - img[:, 195])) < 1500
        assert np.max(np.abs(out[:, 205] - img[:, 205])) < 1500

    def test_no_bleed_across_a_colour_edge_of_similar_lightness(self):
        # Red vs teal of near-equal luma: invisible to the luma guide, so only
        # the noise-adaptive edge protection stops the colour smearing over.
        rng = np.random.default_rng(1)
        img = np.zeros((300, 400, 3), np.float64)
        img[:, :200] = (0.85, 0.25, 0.15)          # red,  luma ~0.42
        img[:, 200:] = (0.20, 0.50, 0.45)          # teal, luma ~0.40
        noise = rng.normal(0, 0.03, img.shape)
        noisy = (np.clip(img + noise, 0, 1) * 65535).astype(np.uint16)
        out = apply_chroma_denoise(noisy, 100, 100).astype(np.float64) / 65535
        # Mean colour just either side of the edge stays on its own side.
        left, right = out[:, 192:198].mean((0, 1)), out[:, 202:208].mean((0, 1))
        assert np.max(np.abs(left - img[0, 0])) < 0.04
        assert np.max(np.abs(right - img[0, 399])) < 0.04

    def test_clean_image_is_left_alone(self):
        img = np.zeros((300, 400, 3), np.uint16)
        img[:, :200] = (40000, 12000, 9000)
        img[:, 200:] = (12000, 30000, 28000)
        out = apply_chroma_denoise(img, 100, 100).astype(np.int64)
        assert np.max(np.abs(out - img)) <= 2

    def test_resolution_independent_radius(self):
        assert chroma_nr_radius_px(50, 6000) == 2 * chroma_nr_radius_px(50, 3000)
        assert chroma_nr_radius_px(100, 1000) > chroma_nr_radius_px(0, 1000)


class TestPipeline:
    def _image(self, settings, profile="color"):
        from core.ccr_image import CCRImage
        img = CCRImage.__new__(CCRImage)
        img.adjustment_settings = dict(settings)
        img.contrast_base = img.temperature_base = img.brightness_base = 0
        img.exposure_base = 0
        img.base_curve = 0
        img.color_profile = profile
        img.decoded_mono = False
        img.area_layers = []
        img.converted = False
        img._ws_windowed = False
        img.tint_balance_factor = 1.0
        img.dust_spots = []
        img.file_path = "x.tif"
        return img

    def test_applied_by_apply_adjustments(self):
        src = _noisy(200, 300)
        img = self._image({"chroma_nr": 100, "chroma_nr_radius": 80})
        out = img.apply_adjustments(src.copy())
        assert _chroma_std(out) < 0.6 * _chroma_std(src)

    def test_skipped_for_sample_patches(self):
        src = _noisy(200, 300)
        img = self._image({"chroma_nr": 100, "chroma_nr_radius": 80})
        out = img.apply_adjustments(src.copy(), skip_dust=True, areas_override=[])
        assert _chroma_std(out) > 0.9 * _chroma_std(src)


class TestPanel:
    def test_keys_defaults_and_group(self):
        from widgets.sliders_panel import SlidersPanel, SYNC_GROUPS
        keys = SlidersPanel.ADJUSTMENT_KEYS
        i = keys.index("chroma_nr")
        assert keys[i:i + 2] == ["chroma_nr", "chroma_nr_radius"]
        assert SlidersPanel.SLIDER_DEFAULTS["chroma_nr_radius"] == CHROMA_NR_RADIUS_DEFAULT
        groups = {gid: k for gid, _l, k in SYNC_GROUPS}
        assert groups["noise"] == ("chroma_nr", "chroma_nr_radius")

    def test_sliders_built_with_defaults(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication(sys.argv[:1])
        from widgets.sliders_panel import SlidersPanel
        panel = SlidersPanel()
        i = panel.adjustment_keys.index("chroma_nr_radius")
        assert panel.sliders[i].value() == CHROMA_NR_RADIUS_DEFAULT
        assert panel.sliders[i].reset_value == CHROMA_NR_RADIUS_DEFAULT
        assert panel.sliders[i - 1].value() == 0

    def test_paste_offers_one_grouped_row(self):
        from widgets.sliders_panel import SlidersPanel, paste_options
        adj = {k: SlidersPanel.SLIDER_DEFAULTS.get(k, 0) for k in SlidersPanel.ADJUSTMENT_KEYS}
        d = lambda k: SlidersPanel.SLIDER_DEFAULTS.get(k, 0)  # noqa: E731
        assert paste_options({"adjustments": dict(adj, chroma_nr_radius=90)}, d) == []
        rows = paste_options({"adjustments": dict(adj, chroma_nr=40)}, d)
        assert [r[1] for r in rows] == ["noise"]
        assert rows[0][0] == "Noise Reduction"
