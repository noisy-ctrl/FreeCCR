#!/usr/bin/env python3
"""
Tests for the per-color-band "Subtractive Saturations" feature: band weight
windows, the four per-band operations (subsat / sat / brightness / hue),
band isolation, the gray gate, and the UI/key wiring.
"""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402
from core.ccr_processor import (  # noqa: E402
    apply_color_band_adjustments, _band_weights,
    COLOR_BANDS, BAND_PARAMS, BAND_ADJUSTMENT_KEYS, BAND_HUE_CENTERS)


def _patch(rgb, size=8):
    """Constant uint16 patch of the given float RGB (0..1)."""
    img = np.empty((size, size, 3), dtype=np.uint16)
    img[..., 0], img[..., 1], img[..., 2] = [int(c * 65535) for c in rgb]
    return img


def _hsv_of(img16):
    rgb = img16.astype(np.float32) / 65535.0
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)


RED = (0.8, 0.1, 0.1)        # hue ~0
ORANGE = (0.8, 0.45, 0.18)   # hue ~26 — skin range
GREEN = (0.1, 0.8, 0.1)      # hue 120
BLUE = (0.1, 0.1, 0.8)       # hue 240
GRAY = (0.5, 0.5, 0.5)       # sat 0


class TestBandWeights:
    def test_weight_one_at_center(self):
        for name, center in BAND_HUE_CENTERS:
            hue = np.array([center], dtype=np.float32)
            w = _band_weights(hue, needed={name})
            assert w[name][0] == pytest.approx(1.0)

    def test_weights_sum_to_one_everywhere(self):
        hue = np.linspace(0.0, 359.9, 720, dtype=np.float32)
        weights = _band_weights(hue, needed=set(COLOR_BANDS))
        total = sum(weights[name] for name in COLOR_BANDS)
        np.testing.assert_allclose(total, 1.0, atol=1e-5)

    def test_wrap_sector_blends_purple_to_red(self):
        # Hue 330 is halfway through the purple(300)→red(360) sector
        hue = np.array([330.0], dtype=np.float32)
        w = _band_weights(hue, needed={"purple", "red"})
        assert w["purple"][0] == pytest.approx(0.5, abs=1e-6)
        assert w["red"][0] == pytest.approx(0.5, abs=1e-6)

    def test_unneeded_bands_not_materialized(self):
        hue = np.array([120.0], dtype=np.float32)
        w = _band_weights(hue, needed={"green"})
        assert set(w) == {"green"}


class TestBandOperations:
    def test_all_zero_is_identity_object(self):
        img = _patch(RED)
        out = apply_color_band_adjustments(img, {})
        assert out is img  # early return, no copy

    def test_red_desaturate(self):
        out = apply_color_band_adjustments(_patch(RED), {"band_red_sat": -100})
        hsv = _hsv_of(out)
        assert hsv[..., 1].max() < 0.02  # fully desaturated

    def test_red_saturate_boost(self):
        muted_red = (0.6, 0.35, 0.35)   # S ≈ 0.42, headroom to saturate
        before = _hsv_of(_patch(muted_red))[0, 0, 1]
        out = apply_color_band_adjustments(_patch(muted_red),
                                           {"band_red_sat": 60})
        after = _hsv_of(out)[0, 0, 1]
        assert after == pytest.approx(before * 1.6, rel=0.03)

    def test_red_band_does_not_touch_blue(self):
        img = _patch(BLUE)
        out = apply_color_band_adjustments(
            img, {"band_red_sat": -100, "band_red_hue": 100,
                  "band_red_bright": 100, "band_red_subsat": 100})
        assert np.abs(out.astype(np.int32) - img.astype(np.int32)).max() <= 2

    def test_hue_shift_rotates_red_toward_orange(self):
        out = apply_color_band_adjustments(_patch(RED), {"band_red_hue": 100})
        hue = _hsv_of(out)[0, 0, 0]
        assert 25.0 <= hue <= 32.0  # +30° at full scale, red center = 0

    def test_negative_hue_wraps_below_zero(self):
        out = apply_color_band_adjustments(_patch(RED), {"band_red_hue": -100})
        hue = _hsv_of(out)[0, 0, 0]
        assert 328.0 <= hue <= 335.0  # 0 - 30 wraps to 330

    def test_brightness_doubles_value(self):
        dark_red = (0.4, 0.05, 0.05)
        before = _hsv_of(_patch(dark_red))[0, 0, 2]
        out = apply_color_band_adjustments(_patch(dark_red),
                                           {"band_red_bright": 100})
        after = _hsv_of(out)[0, 0, 2]
        assert after == pytest.approx(before * 2.0, rel=0.02)

    def test_subsat_pins_max_channel_and_darkens(self):
        img = _patch(RED)
        out = apply_color_band_adjustments(img, {"band_red_subsat": 100})
        # Dominant channel unchanged, others pushed down → denser color
        assert abs(int(out[0, 0, 0]) - int(img[0, 0, 0])) <= 2
        assert out[0, 0, 1] < img[0, 0, 1]
        assert out[0, 0, 2] < img[0, 0, 2]
        # More saturated AND less luminous (subtractive, not additive)
        assert _hsv_of(out)[0, 0, 1] > _hsv_of(img)[0, 0, 1]
        lum = lambda a: a.astype(np.float64).mean()
        assert lum(out) < lum(img)

    def test_subsat_matches_global_sub_saturation_on_pure_band(self):
        from core.ccr_processor import adjust_image
        img = _patch(RED)
        per_band = apply_color_band_adjustments(img, {"band_red_subsat": 40})
        global_ = adjust_image(img, sub_saturation=40.0)
        # Same density model — identical on a fully-red, fully-gated patch
        assert np.abs(per_band.astype(np.int32)
                      - global_.astype(np.int32)).max() <= 2

    def test_gray_untouched_by_every_band(self):
        img = _patch(GRAY)
        settings = {k: 100 for k in BAND_ADJUSTMENT_KEYS}
        out = apply_color_band_adjustments(img, settings)
        assert np.abs(out.astype(np.int32) - img.astype(np.int32)).max() <= 2

    def test_skin_band_hits_orange_not_green(self):
        orange_out = apply_color_band_adjustments(_patch(ORANGE),
                                                  {"band_skin_sat": -100})
        green_out = apply_color_band_adjustments(_patch(GREEN),
                                                 {"band_skin_sat": -100})
        assert _hsv_of(orange_out)[0, 0, 1] < 0.1
        green_in_sat = _hsv_of(_patch(GREEN))[0, 0, 1]
        assert _hsv_of(green_out)[0, 0, 1] == pytest.approx(green_in_sat,
                                                            abs=0.01)

    def test_band_selection_uses_original_hue(self):
        # Rotating red +30° must not re-rotate at the shifted hue's band
        out1 = apply_color_band_adjustments(_patch(RED), {"band_red_hue": 100})
        out2 = apply_color_band_adjustments(
            _patch(RED), {"band_red_hue": 100, "band_skin_hue": 0})
        np.testing.assert_array_equal(out1, out2)

    def test_output_dtype_and_shape(self):
        img = _patch(GREEN, size=5)
        out = apply_color_band_adjustments(img, {"band_green_bright": 30})
        assert out.dtype == np.uint16 and out.shape == img.shape

    def test_negative_subsat_keeps_zero_channels_zero(self):
        # The ratio floor only guards log(0): an exactly-zero channel must
        # stay zero under gamma < 1, matching the global model's pow(0, g)
        img = _patch((1.0, 0.0, 0.0))
        out = apply_color_band_adjustments(img, {"band_red_subsat": -100})
        assert out[0, 0, 0] == 65535
        assert out[0, 0, 1] == 0 and out[0, 0, 2] == 0


class TestOpenCLParity:
    """The kernel band block must match the numpy path (both read the same
    720-bin LUT). Skipped when no OpenCL device is available."""

    @pytest.fixture(autouse=True)
    def require_opencl(self):
        from core.ccr_processor import _initialize_opencl
        if not _initialize_opencl():
            pytest.skip("no OpenCL device")

    def _random_img(self, seed=7, size=96):
        rng = np.random.default_rng(seed)
        return rng.integers(0, 65535, (size, size, 3), dtype=np.uint16)

    BAND_SETTINGS = {
        "band_red_subsat": 60, "band_red_hue": -40,
        "band_skin_sat": 35, "band_yellow_bright": -50,
        "band_green_sat": -80, "band_blue_subsat": -70,
        "band_purple_hue": 80, "band_purple_bright": 45,
    }

    def test_bands_only_parity(self):
        from core.ccr_processor import adjust_image, adjust_image_opencl
        img = self._random_img()
        cpu = adjust_image(img, band_settings=self.BAND_SETTINGS)
        gpu = adjust_image_opencl(img, band_settings=self.BAND_SETTINGS)
        diff = np.abs(cpu.astype(np.int32) - gpu.astype(np.int32))
        assert diff.max() <= 4, f"max diff {diff.max()}"

    def test_bands_with_global_sliders_parity(self):
        from core.ccr_processor import adjust_image, adjust_image_opencl
        img = self._random_img(seed=11)
        # Positive subsat only: negative subsat (gamma < 1) has unbounded
        # slope at 0, so the CPU path's inter-stage uint16 quantization
        # (channel lands on 0 vs 0.4) legitimately diverges there. The
        # bands-only test covers negative subsat with identical inputs.
        bands = dict(self.BAND_SETTINGS, band_blue_subsat=70)
        kwargs = dict(exposure=20.0, contrast=15.0, saturation=10.0,
                      sub_saturation=25.0, band_settings=bands)
        cpu = adjust_image(img, **kwargs)
        gpu = adjust_image_opencl(img, **kwargs)
        diff = np.abs(cpu.astype(np.int32) - gpu.astype(np.int32))
        # The CPU path quantizes to uint16 between the main adjustments and
        # the band step; the kernel stays in float. Allow that staging noise
        # on edge pixels but require near-identity overall.
        assert np.percentile(diff, 99.5) <= 4
        assert diff.max() <= 24, f"max diff {diff.max()}"

    def test_inactive_bands_unchanged_vs_old_kernel_path(self):
        from core.ccr_processor import adjust_image, adjust_image_opencl
        img = self._random_img(seed=13)
        cpu = adjust_image(img, exposure=10.0, sub_saturation=20.0)
        gpu = adjust_image_opencl(img, exposure=10.0, sub_saturation=20.0,
                                  band_settings=None)
        diff = np.abs(cpu.astype(np.int32) - gpu.astype(np.int32))
        assert diff.max() <= 2


class TestPipelineIntegration:
    def test_apply_adjustments_routes_band_keys(self):
        from core.ccr_image import CCRImage
        img_obj = CCRImage.__new__(CCRImage)
        img_obj.adjustment_settings = {"band_red_sat": -100}
        img_obj.color_profile = "color"
        img_obj.contrast_base = 0
        img_obj.temperature_base = 0
        img_obj.brightness_base = 0
        img_obj.exposure_base = 0
        img_obj.converted = False
        img_obj._ws_windowed = False
        img_obj.tint_balance_factor = 1.0
        out = img_obj.apply_adjustments(_patch(RED))
        assert _hsv_of(out)[..., 1].max() < 0.02

    def test_apply_adjustments_without_band_keys_unchanged_path(self):
        from core.ccr_image import CCRImage
        img_obj = CCRImage.__new__(CCRImage)
        img_obj.adjustment_settings = {"saturation": 0}
        img_obj.color_profile = "color"
        img_obj.contrast_base = 0
        img_obj.temperature_base = 0
        img_obj.brightness_base = 0
        img_obj.exposure_base = 0
        img_obj.converted = False
        img_obj._ws_windowed = False
        img_obj.tint_balance_factor = 1.0
        img = _patch(RED)
        out = img_obj.apply_adjustments(img)
        assert np.abs(out.astype(np.int32) - img.astype(np.int32)).max() <= 2


class TestUIWiring:
    @pytest.fixture(autouse=True)
    def qt_app(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication(sys.argv[:1])

    def test_adjustment_keys_include_bands_in_order(self):
        from widgets.sliders_panel import SlidersPanel
        keys = SlidersPanel.ADJUSTMENT_KEYS
        # The global band feather follows the per-band keys (which appear in
        # order immediately before it); only the Chroma NR keys come after.
        fi = keys.index("band_feather")
        assert keys[fi + 1:] == ["chroma_nr", "chroma_nr_radius"]
        assert keys[fi - len(BAND_ADJUSTMENT_KEYS):fi] == list(BAND_ADJUSTMENT_KEYS)
        assert len(BAND_ADJUSTMENT_KEYS) == len(COLOR_BANDS) * len(BAND_PARAMS)

    def test_panel_slider_count_matches_keys(self):
        from widgets.sliders_panel import SlidersPanel
        panel = SlidersPanel()
        assert len(panel.sliders) == len(SlidersPanel.ADJUSTMENT_KEYS)

    def test_band_page_switching(self):
        from widgets.sliders_panel import SlidersPanel
        panel = SlidersPanel()
        # isHidden() reflects the page's own visibility flag even while the
        # whole section is collapsed
        assert not panel._band_pages["red"].isHidden()
        panel._show_band_page("blue")
        assert not panel._band_pages["blue"].isHidden()
        assert panel._band_pages["red"].isHidden()
        assert panel._band_buttons["blue"].isChecked()
        assert not panel._band_buttons["red"].isChecked()

    def test_get_slider_values_covers_band_keys(self):
        from widgets.sliders_panel import SlidersPanel
        panel = SlidersPanel()
        values = panel.get_slider_values()
        for key in BAND_ADJUSTMENT_KEYS:
            assert key in values and values[key] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
