#!/usr/bin/env python3
"""
Tests for the Vibrance slider (spec/vibrance.md): a saturation control
weighted towards muted colours, with skin-tone hues partly protected.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ccr_processor import adjust_image, adjust_image_opencl  # noqa: E402


def _chroma(px):
    px = px.astype(np.float64)
    return px.max() - px.min()


def _test_image():
    img = np.zeros((2, 4, 3), dtype=np.uint16)
    img[0, 0] = (30000, 30000, 30000)   # neutral grey
    img[0, 1] = (30000, 34000, 40000)   # muted blue
    img[0, 2] = (5000, 10000, 60000)    # strongly saturated blue
    img[0, 3] = (50000, 36000, 28000)   # skin-like orange (~22 deg)
    img[1, 0] = (0, 0, 0)               # black
    img[1, 1] = (65535, 65535, 65535)   # white
    img[1, 2] = (28000, 40000, 34000)   # muted green
    img[1, 3] = (34000, 40000, 30000)   # muted yellow-green (same chroma, non-skin)
    return img


class TestVibrance:
    def test_zero_is_identity(self):
        img = _test_image()
        assert np.array_equal(adjust_image(img, vibrance=0), img)

    def test_neutrals_unchanged(self):
        img = _test_image()
        for amount in (-100, -40, 40, 100):
            out = adjust_image(img, vibrance=amount)
            for pos in ((0, 0), (1, 0), (1, 1)):
                np.testing.assert_allclose(
                    out[pos].astype(np.int64), img[pos].astype(np.int64), atol=2)

    def test_muted_colours_gain_more_than_saturated(self):
        img = _test_image()
        out = adjust_image(img, vibrance=60)
        muted_gain = _chroma(out[0, 1]) / _chroma(img[0, 1])
        sat_gain = _chroma(out[0, 2]) / _chroma(img[0, 2])
        assert muted_gain > 1.2
        assert muted_gain > sat_gain + 0.2

    def test_negative_reduces_chroma(self):
        img = _test_image()
        out = adjust_image(img, vibrance=-60)
        assert _chroma(out[0, 1]) < _chroma(img[0, 1])
        assert _chroma(out[1, 2]) < _chroma(img[1, 2])

    def test_skin_tones_protected(self):
        # A skin-like orange should gain noticeably less than a non-skin
        # colour of similar saturation.
        img = np.zeros((1, 2, 3), dtype=np.uint16)
        img[0, 0] = (40000, 33000, 28000)   # skin-ish orange
        img[0, 1] = (28000, 40000, 33000)   # same channel values, green hue
        out = adjust_image(img, vibrance=80)
        skin_gain = _chroma(out[0, 0]) / _chroma(img[0, 0])
        other_gain = _chroma(out[0, 1]) / _chroma(img[0, 1])
        assert skin_gain < other_gain

    def test_output_range_and_dtype(self):
        img = _test_image()
        out = adjust_image(img, vibrance=100, saturation=50)
        assert out.dtype == np.uint16
        assert out.shape == img.shape

    def test_opencl_matches_cpu(self):
        img = _test_image()
        for amount in (-70, 35, 100):
            cpu = adjust_image(img, vibrance=amount)
            gpu = adjust_image_opencl(img, vibrance=amount)
            np.testing.assert_allclose(
                cpu.astype(np.int64), gpu.astype(np.int64), atol=2)

    def test_opencl_matches_cpu_combined(self):
        img = _test_image()
        kw = dict(saturation=20, vibrance=40, sub_saturation=15, contrast=10)
        cpu = adjust_image(img, **kw)
        gpu = adjust_image_opencl(img, **kw)
        np.testing.assert_allclose(
            cpu.astype(np.int64), gpu.astype(np.int64), atol=2)


class TestVibranceUI:
    def test_key_sits_after_saturation(self):
        from widgets.sliders_panel import SlidersPanel
        keys = SlidersPanel.ADJUSTMENT_KEYS
        assert keys.index("vibrance") == keys.index("saturation") + 1

    def test_in_saturation_sync_group(self):
        from widgets.sliders_panel import SYNC_GROUPS
        groups = {gid: k for gid, _l, k in SYNC_GROUPS}
        assert "vibrance" in groups["sat"]
