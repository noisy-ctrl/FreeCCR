#!/usr/bin/env python3
"""Tests for Sharpening (luma unsharp mask) and the "Bypass until export"
flags of Chroma NR and Sharpening. See spec/sharpening.md."""

import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

sys.path.insert(0, os.path.dirname(__file__))

from core.ccr_processor import apply_unsharp_mask, sharpen_sigma_px  # noqa: E402
# Module imports (not `from … import TestX`) so pytest doesn't re-collect
# those test classes here.
import test_chroma_noise_reduction as tcn  # noqa: E402
import test_paste_settings_dialog as tp  # noqa: E402

_noisy, _chroma_std = tcn._noisy, tcn._chroma_std


def _edge(h=200, w=400):
    img = np.zeros((h, w, 3), np.uint16)
    img[:, :w // 2] = (15000, 20000, 25000)
    img[:, w // 2:] = (40000, 42000, 30000)
    return img


def _luma(img):
    x = img.astype(np.float64)
    return 0.299 * x[..., 0] + 0.587 * x[..., 1] + 0.114 * x[..., 2]


class TestUnsharpMask:
    def test_zero_is_identity(self):
        img = _edge()
        assert apply_unsharp_mask(img, 0, 50, 0) is img

    def test_sub_pixel_radius_skipped(self):
        img = _edge(40, 60)
        assert sharpen_sigma_px(100, 60) < 0.3
        assert apply_unsharp_mask(img, 100, 100, 0) is img

    def test_edge_gets_overshoot(self):
        img = _edge(400, 4000)                       # big enough for a real sigma
        out = apply_unsharp_mask(img, 80, 60, 0)
        y_in, y_out = _luma(img)[0], _luma(out)[0]
        mid = 2000
        assert y_out[mid - 2] < y_in[mid - 2] - 200  # dark side darker
        assert y_out[mid + 2] > y_in[mid + 2] + 200  # bright side brighter
        assert abs(y_out[100] - y_in[100]) < 2       # flat areas untouched

    def test_luma_only_no_colour_shift(self):
        img = _edge(400, 4000)
        out = apply_unsharp_mask(img, 80, 60, 0).astype(np.float64)
        x = img.astype(np.float64)
        # colour differences (R−G, B−G) are unchanged away from clipping
        np.testing.assert_allclose(out[..., 0] - out[..., 1], x[..., 0] - x[..., 1], atol=1.5)
        np.testing.assert_allclose(out[..., 2] - out[..., 1], x[..., 2] - x[..., 1], atol=1.5)

    def test_threshold_protects_fine_noise(self):
        rng = np.random.default_rng(0)
        flat = np.full((400, 4000, 3), 30000.0)
        noisy = np.clip(flat + rng.normal(0, 300, (400, 4000, 1)), 0, 65535).astype(np.uint16)
        plain = apply_unsharp_mask(noisy, 80, 60, 0).astype(np.float64)
        held = apply_unsharp_mask(noisy, 80, 60, 60).astype(np.float64)
        assert np.std(held) < 0.5 * (np.std(plain) - np.std(noisy)) + np.std(noisy)


class TestBypassUntilExport:
    def _img(self, **settings):
        return tcn.TestPipeline()._image(settings)

    def test_sharpen_bypassed_in_preview_but_not_export(self):
        src = _edge(400, 4000)
        img = self._img(sharpen_amount=80, sharpen_radius=60, sharpen_export_only=True)
        prev = img.apply_adjustments(src.copy())
        exp = img.apply_adjustments(src.copy(), for_export=True)
        assert np.array_equal(prev, src)
        assert not np.array_equal(exp, src)

    def test_nr_bypassed_in_preview_but_not_export(self):
        src = _noisy(200, 300)
        img = self._img(chroma_nr=100, chroma_nr_radius=80, chroma_nr_export_only=True)
        prev = img.apply_adjustments(src.copy())
        exp = img.apply_adjustments(src.copy(), for_export=True)
        assert _chroma_std(prev) > 0.9 * _chroma_std(src)
        assert _chroma_std(exp) < 0.6 * _chroma_std(src)

    def test_without_flag_preview_is_processed(self):
        src = _edge(400, 4000)
        img = self._img(sharpen_amount=80, sharpen_radius=60)
        assert not np.array_equal(img.apply_adjustments(src.copy()), src)


class TestFlagsInThePanel:
    def test_flag_survives_a_slider_move(self, tmp_path):
        src, _tgt = tp._images(tmp_path)
        panel = tp._panel()
        panel.current_idx = 0
        panel.sharpen_bypass_checkbox.setChecked(True)
        assert src.adjustment_settings.get("sharpen_export_only") is True
        tp._set(panel, "contrast", 12)
        assert src.adjustment_settings.get("sharpen_export_only") is True

    def test_paste_carries_the_flag_with_its_row(self, tmp_path, monkeypatch):
        src, tgt = tp._images(tmp_path)
        src.adjustment_settings = {"sharpen_export_only": True}
        panel = tp._panel()
        panel.current_idx = 0
        tp._set(panel, "sharpen_amount", 40)
        panel.copy_adjustment_settings()
        assert panel.clipboard["flags"]["sharpen_export_only"] is True
        panel.current_idx = 1
        tp._set(panel, "sharpen_amount", 0)
        tp._paste(panel, monkeypatch, chosen={"sharpen"})
        assert tgt.adjustment_settings["sharpen_amount"] == 40
        assert tgt.adjustment_settings.get("sharpen_export_only") is True

    def test_unticked_row_keeps_target_flag(self, tmp_path, monkeypatch):
        src, tgt = tp._images(tmp_path)
        panel = tp._panel()
        panel.current_idx = 0
        tp._set(panel, "contrast", 9)
        panel.copy_adjustment_settings()
        panel.current_idx = 1
        tgt.adjustment_settings = {"chroma_nr_export_only": True}
        tp._paste(panel, monkeypatch, chosen={"adj:contrast"})
        assert tgt.adjustment_settings.get("chroma_nr_export_only") is True

    def test_paste_label_mentions_bypass(self):
        from widgets.sliders_panel import SlidersPanel, paste_options
        d = lambda k: SlidersPanel.SLIDER_DEFAULTS.get(k, 0)  # noqa: E731
        adj = {k: d(k) for k in SlidersPanel.ADJUSTMENT_KEYS}
        rows = paste_options({"adjustments": dict(adj, sharpen_amount=30),
                              "flags": {"sharpen_export_only": True}}, d)
        assert [r[1] for r in rows] == ["sharpen"]
        assert "bypass until export" in rows[0][2]
