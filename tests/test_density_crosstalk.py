#!/usr/bin/env python3
"""Density crosstalk correction (spec/density-crosstalk.md): a rows-sum-to-1
mix of the channels' densities on the converted base, so the film base and
every neutral are unchanged and only colour is re-mixed."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core.ccr_processor import (crosstalk_matrix, crosstalk_applies,  # noqa: E402
                                apply_density_crosstalk, CROSSTALK_KEYS,
                                WS_B, WS_W)
import test_chroma_noise_reduction as tcn  # noqa: E402
import test_paste_settings_dialog as tp  # noqa: E402

BW_BLACK_ONLY = {"mode": "bw", "bw": ((30000.0, 28000.0, 26000.0), None)}


def _win(d):
    """Display densities -> windowed codes."""
    return np.rint(np.asarray(d, np.float64) * (WS_W - WS_B) + WS_B).astype(np.uint16)


def _unwin(c):
    return (c.astype(np.float64) - WS_B) / (WS_W - WS_B)


class TestMatrix:
    def test_identity_is_none_and_free(self):
        assert crosstalk_matrix({}) is None
        assert crosstalk_matrix({k: 0 for k, _i, _j in CROSSTALK_KEYS}) is None
        img = _win(np.full((4, 4, 3), 0.3))
        assert apply_density_crosstalk(img, {}) is img

    def test_rows_sum_to_one(self):
        m = crosstalk_matrix({"xt_rg": -30, "xt_rb": 10, "xt_gb": 40, "xt_br": -100})
        np.testing.assert_allclose(m.sum(axis=1), 1.0)
        assert m[0, 1] == pytest.approx(-0.15) and m[2, 0] == pytest.approx(-0.5)

    def test_base_and_neutrals_unchanged(self):
        s = {"xt_rg": -40, "xt_rb": 25, "xt_gr": 60, "xt_gb": -10, "xt_br": 15, "xt_bg": -70}
        d = np.zeros((1, 4, 3))
        d[0, 1] = 0.25
        d[0, 2] = 0.6
        d[0, 3] = 1.4                           # into the highlight headroom
        out = _unwin(apply_density_crosstalk(_win(d), s))
        np.testing.assert_allclose(out, d, atol=2.0 / (WS_W - WS_B))

    def test_sign_convention(self):
        d = np.array([[[0.8, 0.4, 0.2]]])
        unmix = _unwin(apply_density_crosstalk(_win(d), {"xt_rg": -20}))[0, 0]
        mix = _unwin(apply_density_crosstalk(_win(d), {"xt_rg": 20}))[0, 0]
        assert unmix[0] == pytest.approx(0.8 - 0.1 * (0.4 - 0.8), abs=2e-3)   # red pushed away from green
        assert mix[0] == pytest.approx(0.8 + 0.1 * (0.4 - 0.8), abs=2e-3)
        assert unmix[1] == pytest.approx(0.4, abs=2e-3) and unmix[2] == pytest.approx(0.2, abs=2e-3)


class TestApplies:
    @pytest.mark.parametrize("ci,expected", [
        (BW_BLACK_ONLY, True),
        ({"mode": "bw", "bw": ((1, 2, 3), (0, 0, 0)), "density": True}, True),
        ({"mode": "bw", "bw": ((1, 2, 3), (0, 0, 0)), "density": False}, False),
        ({"mode": "bw", "bw": (None, None)}, False),
        ({"mode": "ref", "ref": (0, 0, 10, 10)}, False),
        ({"mode": "ref_params"}, False),
        (None, False),
    ])
    def test_modes(self, ci, expected):
        assert crosstalk_applies(ci) is expected


class TestPipeline:
    def _img(self, ci, settings):
        img = tcn.TestPipeline()._image(settings)
        img.conversion_inputs = ci
        img._ws_windowed = True
        img.converted = True
        return img

    def _base(self):
        d = np.zeros((40, 60, 3))
        d[:, :20] = (0.7, 0.35, 0.2)            # a colour
        d[:, 20:40] = 0.45                      # a neutral
        return _win(d)

    def test_applied_on_a_density_base(self, monkeypatch):
        from core.ccr_backend import ccr_backend
        monkeypatch.setattr(ccr_backend, "auto_gain", False, raising=False)
        base = self._base()
        on = self._img(BW_BLACK_ONLY, {"xt_rg": -40}).apply_adjustments(base.copy())
        off = self._img(BW_BLACK_ONLY, {}).apply_adjustments(base.copy())
        assert not np.array_equal(on[:, :20], off[:, :20])       # colour changed
        np.testing.assert_allclose(on[:, 20:40].astype(float),   # neutral didn't
                                   off[:, 20:40].astype(float), atol=2)
        np.testing.assert_allclose(on[:, 40:].astype(float),     # base didn't
                                   off[:, 40:].astype(float), atol=2)

    def test_skipped_on_other_bases(self, monkeypatch):
        from core.ccr_backend import ccr_backend
        monkeypatch.setattr(ccr_backend, "auto_gain", False, raising=False)
        base = self._base()
        ci = {"mode": "ref", "ref": (0, 0, 10, 10)}
        on = self._img(ci, {"xt_rg": -40}).apply_adjustments(base.copy())
        off = self._img(ci, {}).apply_adjustments(base.copy())
        assert np.array_equal(on, off)


class TestPanel:
    def test_keys_section_and_group(self):
        from widgets.sliders_panel import SlidersPanel, SYNC_GROUPS
        keys = SlidersPanel.ADJUSTMENT_KEYS
        i = keys.index("xt_rg")
        assert keys[i:i + 6] == [k for k, _i, _j in CROSSTALK_KEYS]
        assert keys[i - 1] == "band_feather" and keys[i + 6] == "chroma_nr"
        groups = {gid: k for gid, _l, k in SYNC_GROUPS}
        assert groups["crosstalk"] == tuple(k for k, _i, _j in CROSSTALK_KEYS)

    def test_section_sits_above_channel_levels(self):
        panel = tp._panel()
        lay = panel.crosstalk_section.parentWidget().layout()
        assert lay.indexOf(panel.crosstalk_section) < lay.indexOf(panel.od_section)
        assert panel.crosstalk_section._toggle_btn.text().startswith("+")   # collapsed

    def test_hint_reflects_the_image(self, tmp_path):
        src, _tgt = tp._images(tmp_path)
        panel = tp._panel()
        src.converted = True
        src.conversion_inputs = {"mode": "ref", "ref": (0, 0, 5, 5)}
        panel._update_crosstalk_hint(src)
        assert panel.crosstalk_hint.text() == panel.CROSSTALK_INACTIVE
        src.conversion_inputs = BW_BLACK_ONLY
        panel._update_crosstalk_hint(src)
        assert panel.crosstalk_hint.text() == panel.CROSSTALK_HINT
        src.active_area_id = "a1"
        panel._update_crosstalk_hint(src)
        assert panel.crosstalk_hint.text() == panel.CROSSTALK_AREA

    def test_paste_offers_one_row_and_applies_all_six(self, tmp_path, monkeypatch):
        from widgets.sliders_panel import SlidersPanel, paste_options
        d = lambda k: SlidersPanel.SLIDER_DEFAULTS.get(k, 0)  # noqa: E731
        adj = {k: d(k) for k in SlidersPanel.ADJUSTMENT_KEYS}
        rows = paste_options({"adjustments": dict(adj, xt_rg=-20, xt_bg=10)}, d)
        assert [r[1] for r in rows] == ["crosstalk"]
        assert "R←G -20" in rows[0][2] and "B←G +10" in rows[0][2]
        src, tgt = tp._images(tmp_path)
        panel = tp._panel()
        panel.current_idx = 0
        tp._set(panel, "xt_rg", -20)
        tp._set(panel, "xt_gb", 30)
        panel.copy_adjustment_settings()
        panel.current_idx = 1
        tp._set(panel, "xt_rg", 0)
        tp._set(panel, "xt_gb", 0)
        tp._paste(panel, monkeypatch, chosen={"crosstalk"})
        assert tgt.adjustment_settings["xt_rg"] == -20 and tgt.adjustment_settings["xt_gb"] == 30
