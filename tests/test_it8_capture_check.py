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


class TestIso:
    def test_equivalent_time(self):
        assert it8.iso_equivalent_time(0.1, 400, 160) == pytest.approx(0.25)
        assert it8.iso_equivalent_time(0.1, 400, 0) == 0.1
        assert it8.iso_equivalent_time(0.1, None, 160) == 0.1
        assert 160 in it8.ISO_THIRDS and 100 in it8.ISO_THIRDS


class TestShutter:
    def _ch(self, peak, status="good"):
        return it8.ChannelExposure("Red", peak, 0, status,
                                   float(np.log2(it8.IDEAL_PEAK / peak)))

    def test_nearest_shutter(self):
        assert it8.nearest_shutter(0.1)[1] == "1/10"
        assert it8.nearest_shutter(0.62)[1] == "0.6"
        assert it8.nearest_shutter(1 / 7.5)[1] == "1/8"

    @pytest.mark.parametrize("peak,expected", [
        (0.199, "0.4"), (0.52, "1/8"), (0.968, "1/13"),     # earlier chart
        (0.12, "0.6"), (0.33, "1/4"), (0.75, "1/10"),       # today's chart
    ])
    def test_suggestions_from_1_10(self, peak, expected):
        s, label = it8.suggest_shutter(0.1, self._ch(peak))
        assert label == expected
        assert peak * s / 0.1 <= it8.IDEAL_PEAK * 2 ** (1 / 6) + 1e-9

    def test_clipped_goes_a_stop_shorter(self):
        assert it8.suggest_shutter(0.1, self._ch(0.99, "clipped"))[1] == "1/20"

    def test_predicted_boost_drops(self):
        r = assess_capture(_samples((0.199, 0.52, 0.968)), _Ref(["GS0", "A1"]))
        ratios = [it8.suggest_shutter(0.1, c)[0] / 0.1 for c in r.channels]
        assert r.boost > 2.5 and it8.predicted_boost(r, ratios) < 1.3

    def test_reads_raf_exif(self):
        p = "/mnt/user-data/uploads/DSCF1517.RAF"
        if not os.path.exists(p):
            pytest.skip("sample RAF not available")
        assert it8.read_shot_exposure(p) == (pytest.approx(0.1), 400)

    def test_unreadable_file(self, tmp_path):
        f = tmp_path / "x.raw"
        f.write_bytes(b"not an image")
        assert it8.read_shot_exposure(str(f)) == (None, None)
        assert it8.read_shot_exposure(str(tmp_path / "missing.raf")) == (None, None)


class TestDialog:
    def _dlg(self, white, shutters=(0.1, 0.1, 0.1), iso=400):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication(sys.argv[:1])
        from PySide6.QtCore import QSettings
        from widgets.it8_profile_dialog import IT8ProfileDialog
        self._old = QSettings("FreeCCR", "FreeCCR").value("it8/no_clip", False, type=bool)
        d = IT8ProfileDialog()
        d._target_merge = ["/x/R1.RAF", "/x/G1.RAF", "/x/B1.RAF"]
        d._exposure_cache = {p: (t, iso) for p, t in zip(d._target_merge, shutters)}
        d._capture_report = assess_capture(_samples(white), _Ref(["GS0", "A1"]))
        d._capture_requested = True                      # as if clicked
        self._old_iso = QSettings("FreeCCR", "FreeCCR").value("it8/calc_iso", 0, type=int)
        d.calc_iso_combo.setCurrentIndex(0)              # As shot
        return d

    def teardown_method(self):
        from PySide6.QtCore import QSettings
        if hasattr(self, "_old"):
            QSettings("FreeCCR", "FreeCCR").setValue("it8/no_clip", self._old)
        if hasattr(self, "_old_iso"):
            QSettings("FreeCCR", "FreeCCR").setValue("it8/calc_iso", self._old_iso)

    def test_hidden_until_check_exposure(self):
        d = self._dlg((0.199, 0.52, 0.968))
        d._capture_requested = False
        d._render_capture_report()
        assert d.capture_label.text().startswith("Place the four corners")
        d._update_locate_status = lambda: d._render_capture_report()   # no image here
        d.check_exposure_btn.click()
        assert "<b>Exposure</b>" in d.capture_label.text()

    def test_iso_override(self):
        d = self._dlg((0.199, 0.52, 0.968))
        d.calc_iso_combo.setCurrentIndex(d.calc_iso_combo.findData(160))   # re-renders
        t = d.capture_label.text()
        assert "speeds for ISO 160; shot at ISO 400" in t
        assert "Red photo (R1.RAF): 20% at 1/10 s, underexposed. Suggested: 1 s." in t
        assert "Green photo (G1.RAF): 52% at 1/10 s, good. Suggested: 0.4 s." in t
        assert "Blue photo (B1.RAF): 97% at 1/10 s, close to clipping. Suggested: 1/5 s." in t
        from PySide6.QtCore import QSettings
        assert QSettings("FreeCCR", "FreeCCR").value("it8/calc_iso", 0, type=int) == 160

    def test_iso_override_never_says_keep(self):
        d = self._dlg((0.75, 0.74, 0.76))
        d.calc_iso_combo.setCurrentIndex(d.calc_iso_combo.findData(200))
        t = d.capture_label.text()
        assert "Keep" not in t and t.count("Suggested: 1/5 s.") == 3

    def test_same_iso_as_shot_is_as_shot(self):
        d = self._dlg((0.75, 0.74, 0.76))
        d.calc_iso_combo.setCurrentIndex(d.calc_iso_combo.findData(400))
        t = d.capture_label.text()
        assert t.count("Keep 1/10 s.") == 3 and "(ISO 400)" in t

    def test_new_card_resets_the_check(self, tmp_path, monkeypatch):
        d = self._dlg((0.199, 0.52, 0.968))
        f = tmp_path / "card.tif"
        f.write_bytes(b"x")
        monkeypatch.setattr(d, "_decode_any",
                            lambda p: np.full((60, 80, 3), 20000, np.uint16))
        assert d._capture_requested
        assert d._load_card_image(str(f))
        assert not d._capture_requested
        assert d.capture_label.text().startswith("Place the four corners")

    def test_lines_name_each_photo_with_speeds(self):
        d = self._dlg((0.199, 0.52, 0.968))
        d.no_clip_check.setChecked(False)
        d._render_capture_report()
        t = d.capture_label.text()
        assert "<b>Exposure</b> (ISO 400)" in t
        assert "Red photo (R1.RAF): 20% at 1/10 s, underexposed. Suggested: 0.4 s." in t
        assert "Green photo (G1.RAF): 52% at 1/10 s, good. Suggested: 1/8 s." in t
        assert "Blue photo (B1.RAF): 97% at 1/10 s, close to clipping. Suggested: 1/13 s." in t
        assert "boosts red 2.6" in t and "above 38%" in t
        assert "With the suggested speeds that boost would be about" in t
        assert "If you can't reshoot, Prevent channel clipping (experimental) is a fallback" in t
        assert "same three speeds" in t
        assert "color:" not in t                         # no traffic lights
        assert t.count("<p ") == 2 and "margin:0 0 6px 0" in t

    def test_keep_when_already_right(self):
        d = self._dlg((0.75, 0.74, 0.76))
        d._render_capture_report()
        t = d.capture_label.text()
        assert t.count("Keep 1/10 s.") == 3 and "even" in t

    def test_ticked_is_a_fallback_not_a_success(self):
        d = self._dlg((0.199, 0.52, 0.968))
        d.no_clip_check.setChecked(False)
        d.no_clip_check.setChecked(True)               # toggling re-renders
        t = d.capture_label.text()
        assert "is ticked as a fallback" in t and "color:" not in t

    def test_mixed_iso_shown_per_photo(self):
        d = self._dlg((0.199, 0.52, 0.968))
        d._exposure_cache["/x/R1.RAF"] = (0.1, 200)
        d._render_capture_report()
        t = d.capture_label.text()
        assert "<b>Exposure</b><br>" in t and ", ISO 200." in t

    def test_without_exif_falls_back_to_stops(self):
        d = self._dlg((0.199, 0.52, 0.968), shutters=(None, None, None), iso=None)
        d._render_capture_report()
        t = d.capture_label.text()
        assert "Suggested: +1.9 stops." in t
        assert "With the suggested speeds" not in t

    def test_single_photo_wording(self):
        d = self._dlg((0.6, 0.62, 0.97))
        d._target_merge = None
        d._target_path = "/x/shot.RAF"
        d._exposure_cache = {"/x/shot.RAF": (1 / 60, 100)}
        d._render_capture_report()
        t = d.capture_label.text()
        assert "Red channel: 60%, good" in t and "photo (" not in t
        assert "Whole shot (currently 1/60 s): Suggested: 1/80 s." in t
        assert "same three speeds" not in t

    def test_cleared_without_an_image(self):
        d = self._dlg((0.6, 0.62, 0.61))
        d._capture_report = None
        d._render_capture_report()
        assert d.capture_label.text() == ""
