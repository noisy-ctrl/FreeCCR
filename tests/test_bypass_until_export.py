#!/usr/bin/env python3
""""Bypass until export" for Chroma Noise Reduction and Details (sharpening):
skipped in the preview, thumbnails and zoom, always applied in an export.
See spec/bypass-until-export.md."""

import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

# Module imports (not `from … import TestX`) so pytest doesn't re-collect
# those test classes here.
import test_chroma_noise_reduction as tcn  # noqa: E402
import test_paste_settings_dialog as tp  # noqa: E402


def _edge(h=400, w=4000):
    img = np.zeros((h, w, 3), np.uint16)
    img[:, :w // 2] = (15000, 20000, 25000)
    img[:, w // 2:] = (40000, 42000, 30000)
    return img


def _img(**settings):
    return tcn.TestPipeline()._image(settings)


class TestRender:
    def test_sharpening_bypassed_in_preview_but_not_export(self):
        src = _edge()
        img = _img(sharpen_amount=80, sharpen_radius=60, sharpen_export_only=True)
        prev = img.apply_adjustments(src.copy())
        exp = img.apply_adjustments(src.copy(), for_export=True)
        assert np.array_equal(prev, src)
        assert not np.array_equal(exp, src)

    def test_sharpening_without_flag_is_applied_in_preview(self):
        src = _edge()
        img = _img(sharpen_amount=80, sharpen_radius=60)
        assert not np.array_equal(img.apply_adjustments(src.copy()), src)

    def test_nr_bypassed_in_preview_but_not_export(self):
        src = tcn._noisy(200, 300)
        img = _img(chroma_nr=100, chroma_nr_radius=80, chroma_nr_export_only=True)
        prev = img.apply_adjustments(src.copy())
        exp = img.apply_adjustments(src.copy(), for_export=True)
        assert tcn._chroma_std(prev) > 0.9 * tcn._chroma_std(src)
        assert tcn._chroma_std(exp) < 0.6 * tcn._chroma_std(src)


class TestPanel:
    def test_checkboxes_live_in_their_sections(self):
        panel = tp._panel()
        assert panel.sharpen_bypass_checkbox.text() == "Bypass until export"
        assert panel.nr_bypass_checkbox.text() == "Bypass until export"
        assert panel.details_section.isAncestorOf(panel.sharpen_bypass_checkbox)
        assert panel.noise_section.isAncestorOf(panel.nr_bypass_checkbox)

    def test_flag_survives_a_slider_move(self, tmp_path):
        src, _tgt = tp._images(tmp_path)
        panel = tp._panel()
        panel.current_idx = 0
        panel.sharpen_bypass_checkbox.setChecked(True)
        assert src.adjustment_settings.get("sharpen_export_only") is True
        tp._set(panel, "contrast", 12)
        assert src.adjustment_settings.get("sharpen_export_only") is True

    def test_flags_ride_their_sync_groups(self):
        from widgets.sliders_panel import SYNC_GROUPS
        groups = {gid: keys for gid, _l, keys in SYNC_GROUPS}
        assert "sharpen_export_only" in groups["details"]
        assert "chroma_nr_export_only" in groups["noise"]

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

    def test_paste_row_is_one_details_row(self):
        from widgets.sliders_panel import SlidersPanel, paste_options
        d = lambda k: SlidersPanel.SLIDER_DEFAULTS.get(k, 0)  # noqa: E731
        adj = {k: d(k) for k in SlidersPanel.ADJUSTMENT_KEYS}
        assert paste_options({"adjustments": adj}, d) == []       # default 25 is not a change
        rows = paste_options({"adjustments": dict(adj, sharpen_amount=40, sharpen_masking=10),
                              "flags": {"sharpen_export_only": True}}, d)
        assert [r[1] for r in rows] == ["sharpen"]
        assert "masking 10" in rows[0][2] and "bypass until export" in rows[0][2]
