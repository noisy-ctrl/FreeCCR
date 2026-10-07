#!/usr/bin/env python3
"""Tests for "Prevent channel clipping" camera profiles (spec/profile-no-clip.md):
a flagged profile scales its output down by a fixed per-profile factor so no
channel clips after its white balance, and the no-anchor conversion
compensates, so every conversion mode renders as before wherever nothing
clipped."""

import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import color_management as cm  # noqa: E402
from core import dcp_profile  # noqa: E402
from core import it8_profile as it8  # noqa: E402
from core.ccr_processor import (apply_bwpoint_normalization,  # noqa: E402
                                compute_reference_norm_params,
                                apply_reference_normalization)


# A trichrome-like setup: the light is weak in red, so the profile's white
# balance boosts red ~2.8x (the case that clipped).
NEUTRAL = np.array([0.356, 1.0, 1.75])
M = np.array([[0.40, 0.52, 0.04],          # balanced device -> XYZ D50
              [0.21, 0.85, -0.06],
              [0.01, 0.03, 0.79]])
M = np.diag(np.array(cm.D50_XYZ) / M.sum(1)) @ M   # pin (1,1,1) -> D50


def _fit():
    return SimpleNamespace(matrix=M, wb_mult=1.0 / NEUTRAL)


def _clut_xyz(grid=9):
    """A cLUT = the 3x3 base plus a small smooth residual that fades to zero
    (value AND slope) towards the grid's edges, like the wizard's, which fades
    its correction out beyond the chart's patches."""
    ax = np.linspace(0, 1, grid)
    nodes = np.stack(np.meshgrid(ax, ax, ax, indexing="ij"), -1)
    base = nodes @ M.T
    bump = 0.02 * (np.sin(np.pi * nodes[..., :1]) * np.sin(np.pi * nodes[..., 1:2])) ** 2
    return base + bump * np.array([1.0, 0.5, -0.5])


def _matrix_icc(no_clip):
    return it8.build_camera_icc(_fit(), "t", trichrome=True, no_clip=no_clip)


def _clut_icc(no_clip, grid=9):
    return cm.build_clut_icc("t", _clut_xyz(grid), grid, neutral=NEUTRAL,
                             trichrome=True, no_clip=no_clip)


def _negative(h=60, w=80, seed=0):
    """Raw camera-native negative: a film base whose red goes past the point
    the 2.8x balance clips (raw red > 0.36), plus darker content."""
    rng = np.random.default_rng(seed)
    t = rng.uniform(0.02, 0.95, (h, w, 1))
    base = np.array([1.0, 0.82, 0.87])
    img = t * base * rng.uniform(0.9, 1.1, (h, w, 3))
    return np.clip(img * 65535, 0, 65535).astype(np.uint16)


def _unclipped(old_out, new_out, k):
    """Pixels where the classic profile did NOT clip: new*k reproduces old."""
    return np.all(np.abs(new_out.astype(np.float64) * k - old_out) <= k / 2 + 1, axis=-1)


# --- flag round trip -------------------------------------------------------------

class TestFlag:
    @pytest.mark.parametrize("builder", [_matrix_icc, _clut_icc])
    def test_icc_round_trip(self, builder):
        off = cm.InputProfile.from_bytes(builder(False))
        on = cm.InputProfile.from_bytes(builder(True))
        assert off.no_clip is False and off.headroom == 1.0
        assert on.no_clip is True and on.headroom > 1.0
        assert on.is_trichrome and on.calibration_neutral is not None

    def test_dcp_round_trip(self):
        off = dcp_profile.parse_dcp_bytes(dcp_profile.build_camera_dcp(_fit(), "t"))
        on = dcp_profile.parse_dcp_bytes(
            dcp_profile.build_camera_dcp(_fit(), "t", no_clip=True))
        assert off.no_clip is False and off.headroom == 1.0
        assert on.no_clip is True and on.headroom > 1.0

    def test_inert_without_a_calibration_neutral(self):
        blob = cm.build_clut_icc("t", _clut_xyz(), 9, neutral=None, no_clip=True)
        p = cm.InputProfile.from_bytes(blob)
        assert p.no_clip is True and p.headroom == 1.0

    def test_no_scale_when_nothing_could_clip(self):
        assert cm.headroom_bound(np.eye(3) * 0.9, [1.0, 1.0, 1.0]) == 1.0

    def test_headroom_bound_is_tight_for_a_matrix(self):
        A = np.array([[0.66, 0.50, -0.16], [0.01, 1.12, -0.13], [-0.01, -0.07, 1.08]])
        g = np.array([2.8, 1.0, 0.57])
        assert cm.headroom_bound(A, g) == pytest.approx(0.66 * 2.8 + 0.50 * 1.0)


# --- apply ------------------------------------------------------------------------

class TestApply:
    @pytest.mark.parametrize("builder", [_matrix_icc, _clut_icc])
    def test_never_clips_and_matches_where_classic_did_not(self, builder):
        img = _negative()
        old = cm.InputProfile.from_bytes(builder(False)).apply(img)
        new_p = cm.InputProfile.from_bytes(builder(True))
        new = new_p.apply(img)
        assert np.all(new < 65535)
        same = _unclipped(old, new, new_p.headroom)
        assert 0.2 < same.mean() < 0.95          # some clipped, most didn't
        # whole-range input can't clip either
        full = np.full((1, 1, 3), 65535, np.uint16)
        assert np.all(new_p.apply(full) < 65535)

    def test_clut_continues_smoothly_past_the_grid(self):
        p = cm.InputProfile.from_bytes(_clut_icc(True))
        t = np.linspace(0.05, 1.0, 300)
        ramp = np.stack([t, t * 0.82, t * 0.87], -1)
        out = p.apply(np.rint(ramp * 65535).astype(np.uint16)[None]).astype(np.float64)[0]
        assert np.all(np.diff(out[:, 0]) >= -1)                # monotonic red
        edge = NEUTRAL[0]                                      # where classic clipped
        i = np.searchsorted(t, edge)
        s_lo = (out[i - 3, 0] - out[i - 20, 0]) / (t[i - 3] - t[i - 20])
        s_hi = (out[i + 20, 0] - out[i + 3, 0]) / (t[i + 20] - t[i + 3])
        assert s_hi == pytest.approx(s_lo, rel=0.08)           # no kink

    def test_base_colour_constant_with_exposure(self):
        p = cm.InputProfile.from_bytes(_matrix_icc(True))
        ratios = []
        for v in (0.2, 0.5, 0.9):
            px = np.rint(np.array([v, v * 0.82, v * 0.87]) * 65535).astype(np.uint16)
            o = p.apply(px[None, None]).astype(np.float64)[0, 0]
            ratios.append(o[0] / o[1])
        assert max(ratios) - min(ratios) < 0.01

    def test_dcp_matches_classic_scaled(self):
        img = _negative()
        old = dcp_profile.apply_dcp(dcp_profile.parse_dcp_bytes(
            dcp_profile.build_camera_dcp(_fit(), "t")), img)
        on = dcp_profile.parse_dcp_bytes(
            dcp_profile.build_camera_dcp(_fit(), "t", no_clip=True))
        new = dcp_profile.apply_dcp(on, img)
        assert np.all(new < 65535)
        assert _unclipped(old, new, on.headroom).mean() > 0.2


# --- conversions --------------------------------------------------------------------

def _pair():
    img = _negative(120, 160, seed=3)
    # top band: thin content only (nothing the classic profile clips there)
    img[:30] = np.clip(img[:30].astype(np.float64) * 0.3, 0, 65535).astype(np.uint16)
    a = cm.InputProfile.from_bytes(_clut_icc(False)).apply(img)
    p = cm.InputProfile.from_bytes(_clut_icc(True))
    b = p.apply(img)
    return a, b, p.headroom, _unclipped(a, b, p.headroom)


def _pts(img, same):
    """Points sampled like the canvas does (window means), from unclipped areas."""
    lum = img.astype(np.float64).sum(-1)
    lum[~same] = np.nan
    yb, xb = np.unravel_index(np.nanargmax(lum), lum.shape)
    yw, xw = np.unravel_index(np.nanargmin(lum), lum.shape)
    m = lambda y, x: tuple(float(v) for v in img[y:y + 1, x:x + 1].reshape(-1, 3).mean(0))  # noqa: E731
    return (yb, xb), (yw, xw), m


class TestConversions:
    @pytest.mark.parametrize("mode", ["linear", "density", "black_only", "none"])
    def test_bwpoint_modes_unchanged_where_unclipped(self, mode):
        a, b, k, same = _pair()
        (yb, xb), (yw, xw), m = _pts(a, same)
        if mode == "none":
            oa = apply_bwpoint_normalization(a, None, None)
            ob = apply_bwpoint_normalization(b, None, None, input_scale=k)
        else:
            wp_a = None if mode == "black_only" else m(yw, xw)
            wp_b = None if mode == "black_only" else tuple(v for v in _pts(b, same)[2](yw, xw))
            dens = mode == "density"
            oa = apply_bwpoint_normalization(a, m(yb, xb), wp_a, density=dens)
            ob = apply_bwpoint_normalization(b, _pts(b, same)[2](yb, xb), wp_b, density=dens)
        d = np.abs(oa.astype(np.float64) - ob)[same]
        assert np.percentile(d, 99) < 40         # LSB of 65535: rounding only

    def test_no_anchor_needs_the_compensation(self):
        a, b, k, same = _pair()
        oa = apply_bwpoint_normalization(a, None, None).astype(np.float64)
        ob = apply_bwpoint_normalization(b, None, None, input_scale=1.0).astype(np.float64)
        assert np.median(np.abs(oa - ob)[same]) > 200

    def test_reference_mode_unchanged_where_unclipped(self):
        a, b, k, same = _pair()
        rect = (0, 0, 160, 30)                  # the frame: the unclipped band
        assert same[:30].all()
        oa = apply_reference_normalization(a, *compute_reference_norm_params(a, rect, 0))
        ob = apply_reference_normalization(b, *compute_reference_norm_params(b, rect, 0))
        d = np.abs(oa.astype(np.float64) - ob)[same]
        assert np.percentile(d, 99) < 60


# --- decode records the applied scale ----------------------------------------------

class TestDecodeRecord:
    def _img(self):
        from core.ccr_image import CCRImage
        im = CCRImage.__new__(CCRImage)
        im.file_path = "x.nef"
        im.is_merged = False
        return im

    def test_records_scale_only_when_applied(self, monkeypatch):
        p = cm.InputProfile.from_bytes(_clut_icc(True))
        monkeypatch.setattr(cm, "_active_input_profile", p)
        monkeypatch.setattr(cm, "_active_dcp_profile", None)
        monkeypatch.setattr(cm, "_input_profile_disabled", False)
        im = self._img()
        im.profile_headroom = 1.0
        im._apply_input_icc(_negative(8, 8))
        assert im.profile_headroom == pytest.approx(p.headroom)
        monkeypatch.setattr(cm, "_input_profile_disabled", True)
        im.profile_headroom = 1.0                 # read_image resets per decode
        im._apply_input_icc(_negative(8, 8))
        assert im.profile_headroom == 1.0

    def test_active_scale_helper(self, monkeypatch):
        p = cm.InputProfile.from_bytes(_matrix_icc(True))
        monkeypatch.setattr(cm, "_active_input_profile", p)
        monkeypatch.setattr(cm, "_active_dcp_profile", None)
        monkeypatch.setattr(cm, "_input_profile_disabled", False)
        assert cm.active_headroom_scale() == pytest.approx(p.headroom)
        monkeypatch.setattr(cm, "_input_profile_disabled", True)
        assert cm.active_headroom_scale() == 1.0


# --- wizard checkbox -----------------------------------------------------------------

class TestWizard:
    def test_checkbox_saves_the_flag(self, tmp_path, monkeypatch):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication(sys.argv[:1])
        from widgets.it8_profile_dialog import IT8ProfileDialog
        s = QSettings("FreeCCR", "FreeCCR")
        old = s.value("it8/no_clip", False, type=bool)
        try:
            dlg = IT8ProfileDialog()
            assert dlg.no_clip_check.text() == "Prevent channel clipping"
            dlg.no_clip_check.setChecked(True)
            assert s.value("it8/no_clip", False, type=bool) is True   # remembered
            dlg._fit = _fit()
            dlg._target_merge = ["r", "g", "b"]
            monkeypatch.setattr(dlg, "_is_dcp", lambda: False)
            dlg.type_combo.setCurrentIndex(0)                          # matrix
            dlg.save_path_edit.setText(str(tmp_path / "p.icc"))
            assert dlg._do_save()
            prof = cm.load_input_profile(str(tmp_path / "p.icc"))
            assert prof.no_clip and prof.is_trichrome and prof.headroom > 1.0
            dlg.no_clip_check.setChecked(False)
            assert dlg._do_save()
            assert not cm.load_input_profile(str(tmp_path / "p.icc")).no_clip
        finally:
            s.setValue("it8/no_clip", old)
