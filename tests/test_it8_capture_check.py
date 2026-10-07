#!/usr/bin/env python3
"""IT8 wizard Step 3 capture check (spec/it8-capture-check.md): per-photo
exposure verdicts and the profile's white-balance boost."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import it8_profile as it8  # noqa: E402
from core.it8_profile import PatchSample, assess_capture  # noqa: E402


class _Ref:
    """Minimal reference: GS0 is the lightest neutral."""
    def __init__(self, ids):
        self.patches = {i: None for i in ids}

    def lab(self, sid):
        return np.array([95.0, 0.0, 0.0]) if sid == "GS0" else np.array([50.0, 30.0, 10.0])

    def xyz(self, sid):
        return np.array([90.0, 95.0, 90.0]) if sid == "GS0" else np.array([20.0, 18.0, 10.0])


def _samples(white, others=((0.1, 0.2, 0.3),), clip=None):
    out = {"GS0": PatchSample(np.array(white) * 65535, True, 100, np.zeros(3))}
    for i, v in enumerate(others):
        out[f"A{i + 1}"] = PatchSample(np.array(v) * 65535, True, 100, np.zeros(3))
    if clip is not None:
        frac = np.zeros(3); frac[clip] = 0.5
        out["B1"] = PatchSample(np.array([0.5, 0.5, 0.5]) * 65535, False, 100, frac)
    return out


def _status(r):
    return [c.status for c in r.channels]


class TestAssess:
    def test_matches_the_measured_trichrome_chart(self):
        r = assess_capture(_samples((0.199, 0.52, 0.968)), _Ref(["GS0", "A1"]))
        assert _status(r) == ["under", "good", "near"]
        assert r.channels[0].stops == pytest.approx(np.log2(0.75 / 0.199))
        assert r.boost_channel == 0 and r.boost == pytest.approx(0.52 / 0.199)
        assert r.clip_level == pytest.approx(0.199 / 0.52)
        assert not r.balanced

    def test_clipped_channel_counted_from_the_window(self):
        r = assess_capture(_samples((0.6, 0.7, 0.8), clip=2), _Ref(["GS0", "A1", "B1"]))
        assert _status(r) == ["good", "good", "clipped"]
        assert r.channels[2].clipped == 1

    def test_low_band_and_balanced_light(self):
        r = assess_capture(_samples((0.30, 0.33, 0.31)), _Ref(["GS0", "A1"]))
        assert _status(r) == ["low", "low", "low"]
        assert r.balanced and r.boost < it8.BALANCED_BOOST

    def test_boost_never_below_one(self):
        r = assess_capture(_samples((0.6, 0.5, 0.6)), _Ref(["GS0", "A1"]))
        assert r.boost == 1.0 and r.clip_level == 1.0     # green weakest: no boost

    def test_no_reference_means_no_balance_line(self):
        r = assess_capture(_samples((0.6, 0.7, 0.8)), None)
        assert r.gains is None and len(r.channels) == 3

    def test_nothing_valid(self):
        s = {"GS0": PatchSample(np.zeros(3), False, 0)}
        assert assess_capture(s, _Ref(["GS0"])) is None

    def test_sample_patches_records_clip_fraction(self):
        img = np.full((200, 300, 3), 20000, np.uint16)
        img[:, 150:, 2] = 65535                          # right half blown in blue
        quad = [(10.0, 10.0), (290.0, 10.0), (290.0, 190.0), (10.0, 190.0)]
        s = it8.sample_patches(img, it8.grid_sample_points(quad), quad)
        fracs = np.array([ps.clip_frac for ps in s.values() if ps.clip_frac is not None])
        assert fracs[:, 2].max() == pytest.approx(1.0) and fracs[:, 0].max() == 0.0


class TestDialog:
    def _dlg(self, white):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication(sys.argv[:1])
        from PySide6.QtCore import QSettings
        from widgets.it8_profile_dialog import IT8ProfileDialog
        self._old = QSettings("FreeCCR", "FreeCCR").value("it8/no_clip", False, type=bool)
        d = IT8ProfileDialog()
        d._target_merge = ["/x/R1.RAF", "/x/G1.RAF", "/x/B1.RAF"]
        d._capture_report = assess_capture(_samples(white), _Ref(["GS0", "A1"]))
        return d

    def teardown_method(self):
        from PySide6.QtCore import QSettings
        if hasattr(self, "_old"):
            QSettings("FreeCCR", "FreeCCR").setValue("it8/no_clip", self._old)

    def test_lines_name_each_photo(self):
        d = self._dlg((0.199, 0.52, 0.968))
        d.no_clip_check.setChecked(False)
        d._render_capture_report()
        t = d.capture_label.text()
        assert "Red photo (R1.RAF): 20%" in t and "underexposed (+1.9 stops)" in t
        assert "Green photo (G1.RAF): 52%" in t and "good" in t
        assert "Blue photo (B1.RAF): 97%" in t and "close to clipping" in t
        assert "boosts red 2.6" in t and "above 38%" in t
        assert "Tick Prevent channel clipping" in t
        assert "same change" in d.capture_label.toolTip()

    def test_ticking_the_box_updates_the_advice(self):
        d = self._dlg((0.199, 0.52, 0.968))
        d.no_clip_check.setChecked(False)
        d.no_clip_check.setChecked(True)               # toggling re-renders
        assert "is ticked, so this is handled" in d.capture_label.text()

    def test_single_photo_wording(self):
        d = self._dlg((0.6, 0.62, 0.61))
        d._target_merge = None
        d._render_capture_report()
        t = d.capture_label.text()
        assert "Red channel" in t and "photo (" not in t
        assert "even" in t

    def test_cleared_without_an_image(self):
        d = self._dlg((0.6, 0.62, 0.61))
        d._capture_report = None
        d._render_capture_report()
        assert d.capture_label.text() == ""
