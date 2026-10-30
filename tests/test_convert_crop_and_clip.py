#!/usr/bin/env python3
"""Convert, then crop; and nothing clips at conversion
(spec/convert-crop-and-clip.md)."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from core.ccr_backend import ccr_backend  # noqa: E402
from core.auto_crop import FrameCrop  # noqa: E402
from core import clip_guard as cg  # noqa: E402

BASE = (40000.0, 40000.0, 40000.0)          # a grey film base: decodes neutral
                                            # through any colour matrix
PICTURE = (0.1, 0.1, 0.9, 0.9)


def _negative(path, seed=3):
    """A negative whose picture runs from near the base (deep
    shadows) to far denser than the auto white point's percentile band
    (highlights the old conversion clips): smooth gradients, grain, a few
    dense blobs."""
    rng = np.random.RandomState(seed)
    h, w = 400, 600
    img = np.empty((h, w, 3), np.float32)
    img[:] = BASE
    yy, xx = np.mgrid[0:h - 80, 0:w - 120].astype(np.float32)
    t = (xx / xx.max()) * 0.75 + (yy / yy.max()) * 0.25        # 0 .. 1 density ramp
    dens = 0.02 + 1.1 * t                                         # above the base
    pic = np.empty(t.shape + (3,), np.float32)
    for c in range(3):
        pic[..., c] = BASE[c] / (10.0 ** dens)
    for (cy, cx, r) in ((60, 400, 18), (200, 450, 10), (250, 300, 14)):
        m = (yy - cy) ** 2 + (xx - cx) ** 2 < r * r
        for c in range(3):
            pic[..., c][m] = BASE[c] / (10.0 ** 1.6)
    pic *= (1.0 + rng.normal(0, 0.01, pic.shape)).astype(np.float32)
    img[40:-40, 60:-60] = pic
    cv2.imwrite(str(path), np.clip(img, 0, 65535).astype(np.uint16))
    return str(path)


@pytest.fixture
def frame(tmp_path, monkeypatch):
    from core.ccr_image import CCRImage
    ccr_backend.images.clear()
    monkeypatch.setattr(ccr_backend, "black_point_bgr", BASE, raising=False)
    monkeypatch.setattr(ccr_backend, "white_point_bgr", None, raising=False)
    monkeypatch.setattr(ccr_backend, "positive_mode", False, raising=False)
    monkeypatch.setattr(ccr_backend, "auto_tone", True, raising=False)
    monkeypatch.setattr(ccr_backend, "crop_after_convert", False, raising=False)
    monkeypatch.setattr(ccr_backend, "clip_mode", "rolloff", raising=False)

    def make(name="n.tif", seed=3):
        img = CCRImage(_negative(tmp_path / name, seed))
        img.crop_rect = PICTURE                      # the picture to measure in
        ccr_backend.images.append(img)
        return img
    yield make
    ccr_backend.images.clear()


def _convert(img):
    plan = ccr_backend.plan_frame_conversion(img)
    ccr_backend.apply_frame_conversion(img, plan)
    return plan


def _clips(img):
    out = img.apply_adjustments(img.resized_raw)
    return cg.clip_counts(out, (img.conversion_inputs or {}).get("picture"))


def _median_luma(img):
    out = img.apply_adjustments(img.resized_raw)
    p8 = cg._area(cv2.convertScaleAbs(out, alpha=255 / 65535), img.conversion_inputs["picture"])
    return float(np.median(p8.mean(axis=2)))


# --------------------------------------------------------------------------- #
# counting
# --------------------------------------------------------------------------- #
def test_a_lone_speck_doesnt_count_a_patch_does():
    u = np.full((40, 40, 3), 30000, np.uint16)
    u[10, 10] = 65535                                  # a speck
    assert cg.clip_counts(u)[1] == 0
    u[20:24, 20:24] = 65535                            # an area
    assert cg.clip_counts(u)[1] > 0
    assert cg.clip_counts(u, (0.0, 0.0, 0.4, 0.4))[1] == 0      # outside the rect
    u[30:34, 30:34, 1] = 0                             # one channel at black
    assert cg.clip_counts(u)[0] > 0


def test_bisect_finds_the_least_that_clears():
    calls = []

    def clips(x):
        calls.append(x)
        return x < 0.37
    x, ok = cg.bisect_clear(clips, 0.0, 1.0, 6)
    assert ok and 0.37 <= x < 0.37 + 1 / 64 + 1e-9 and len(calls) == 6
    x, ok = cg.bisect_clear(lambda x: True, 0.0, 1.0, 3)
    assert x == 1.0 and not ok


# --------------------------------------------------------------------------- #
# the modes
# --------------------------------------------------------------------------- #
def test_legacy_is_the_old_conversion_and_it_clips(frame, monkeypatch):
    monkeypatch.setattr(ccr_backend, "clip_mode", "legacy", raising=False)
    img = frame()
    _convert(img)
    ci = img.conversion_inputs
    assert "clip_guard" not in ci and "clip_gain" not in ci
    b, w, n = _clips(img)
    assert w > 0                                       # what the user saw


def test_rolloff_clips_nothing_and_keeps_the_brightness(frame, monkeypatch):
    monkeypatch.setattr(ccr_backend, "clip_mode", "legacy", raising=False)
    old = frame("old.tif")
    _convert(old)
    before = _median_luma(old)
    monkeypatch.setattr(ccr_backend, "clip_mode", "rolloff", raising=False)
    img = frame("new.tif")
    _convert(img)
    b, w, n = _clips(img)
    assert (b, w) == (0, 0)
    rec = img.conversion_inputs["clip_guard"]
    assert rec["mode"] == "rolloff" and rec["white"] == 0 and rec["black"] == 0
    assert "clip_gain" not in img.conversion_inputs
    assert abs(_median_luma(img) - before) <= 0.02 * 255


def test_brightest_clips_nothing_with_its_own_gain(frame, monkeypatch):
    monkeypatch.setattr(ccr_backend, "clip_mode", "brightest", raising=False)
    img = frame()
    _convert(img)
    ci = img.conversion_inputs
    assert ci.get("clip_gain") and ci["clip_guard"]["mode"] == "brightest"
    assert "knee" not in (ci.get("auto_tone") or {})
    assert _clips(img)[:2] == (0, 0)
    # the gain is the frame's, Auto Gain on or off, and drafts use it too
    v = img._auto_gain_for_base(img.resized_raw)
    from core.ccr_processor import CH_SLIDER_DIV
    assert v == pytest.approx(CH_SLIDER_DIV * (1 - 1 / ci["clip_gain"]))
    on = img.apply_adjustments(img.resized_raw)
    monkeypatch.setattr(ccr_backend, "auto_gain", False, raising=False)
    off = img.apply_adjustments(img.resized_raw)
    assert np.array_equal(on, off)


def test_nothing_is_touched_without_a_film_base_or_auto_tone(frame, monkeypatch):
    img = frame()
    monkeypatch.setattr(ccr_backend, "auto_tone", False, raising=False)
    _convert(img)
    assert "clip_guard" not in img.conversion_inputs
    img2 = frame("b.tif")
    monkeypatch.setattr(ccr_backend, "auto_tone", True, raising=False)
    _convert(img2)
    img2.conversion_inputs["bw"] = (None, None)          # as the no-anchor invert records
    assert ccr_backend.guard_clipping(img2) is None
    assert "clip_guard" not in img2.conversion_inputs


def test_a_frame_with_a_film_look_is_left_alone(frame):
    img = frame()
    img.adjustment_settings = {"look_lut": "some look"}
    _convert(img)
    assert "clip_guard" not in img.conversion_inputs


# --------------------------------------------------------------------------- #
# convert, then crop
# --------------------------------------------------------------------------- #
def _fake_detect(monkeypatch, rect=(0.12, 0.11, 0.88, 0.9), confidence="high", calls=None):
    def detect(img, raw=None, extra_priors=None):
        if calls is not None:
            calls.append(raw is not None)
        fc = FrameCrop(rect, angle=0.3, confidence=confidence)
        img.auto_crop_last = fc
        return fc
    monkeypatch.setattr(ccr_backend, "detect_frame_crop", detect)


def test_convert_crops_a_frame_auto_crop_may_change(frame, monkeypatch):
    monkeypatch.setattr(ccr_backend, "crop_after_convert", True, raising=False)
    calls = []
    _fake_detect(monkeypatch, calls=calls)
    img = frame()
    img.crop_rect = None
    plan = _convert(img)
    assert calls == [True]                             # found on the unconverted scan
    assert plan["rect"] == (0.12, 0.11, 0.88, 0.9)     # ... and measured inside it
    assert img.converted and img.crop_rect == (0.12, 0.11, 0.88, 0.9)
    assert img.crop_angle == pytest.approx(0.3) and img.auto_crop_rect == img.crop_rect
    assert img.conversion_inputs["picture"] == [0.12, 0.11, 0.88, 0.9]
    assert img.pop_undo_state()                        # one step: the crop only
    assert img.crop_rect is None and img.converted


def test_a_hand_or_confirmed_crop_stays_and_the_setting_turns_it_off(frame, monkeypatch):
    monkeypatch.setattr(ccr_backend, "crop_after_convert", True, raising=False)
    _fake_detect(monkeypatch)
    hand = frame("h.tif")
    hand.crop_rect = (0.2, 0.2, 0.7, 0.7)               # drawn by hand
    _convert(hand)
    assert hand.crop_rect == (0.2, 0.2, 0.7, 0.7)
    done = frame("d.tif")
    done.crop_rect = done.auto_crop_rect = (0.2, 0.2, 0.7, 0.7)
    done.auto_crop_example = "ex1"                      # confirmed with Done
    _convert(done)
    assert done.crop_rect == (0.2, 0.2, 0.7, 0.7)
    old = frame("o.tif")
    old.crop_rect = old.auto_crop_rect = (0.2, 0.2, 0.7, 0.7)   # auto crop's: replaced
    _convert(old)
    assert old.crop_rect == (0.12, 0.11, 0.88, 0.9)
    monkeypatch.setattr(ccr_backend, "crop_after_convert", False, raising=False)
    off = frame("x.tif")
    off.crop_rect = None
    _convert(off)
    assert off.crop_rect is None


def test_an_unsure_detection_doesnt_crop(frame, monkeypatch):
    monkeypatch.setattr(ccr_backend, "crop_after_convert", True, raising=False)
    _fake_detect(monkeypatch, confidence="low")
    img = frame()
    img.crop_rect = None
    _convert(img)
    assert img.converted and img.crop_rect is None


def test_convert_all_crops_and_counts(frame, monkeypatch):
    monkeypatch.setattr(ccr_backend, "crop_after_convert", True, raising=False)
    monkeypatch.setattr(ccr_backend, "auto_black_point", True, raising=False)
    _fake_detect(monkeypatch)
    a, b = frame("a.tif"), frame("b.tif", seed=4)
    a.crop_rect = b.crop_rect = None
    b.crop_rect = (0.3, 0.3, 0.6, 0.6)                  # by hand
    ccr_backend.apply_bwpoint_to_all_images()
    assert a.converted and b.converted
    assert a.crop_rect == (0.12, 0.11, 0.88, 0.9)
    assert b.crop_rect == (0.3, 0.3, 0.6, 0.6)
    assert ccr_backend.last_auto_bp_summary["cropped"] == 1


def test_a_tethered_capture_crops_and_an_inherited_hand_crop_stays(frame, monkeypatch):
    from core.tether_watcher import TetherWatchWorker, look_template
    monkeypatch.setattr(ccr_backend, "crop_after_convert", True, raising=False)
    _fake_detect(monkeypatch)
    prev = frame("p.tif")
    prev.crop_rect = prev.auto_crop_rect = (0.2, 0.2, 0.7, 0.7)   # auto crop's
    cap = frame("c.tif", seed=5)
    ccr_backend.images.remove(cap)
    cap.crop_rect = None
    t = look_template(prev)
    assert t["crop_is_auto"]
    w = TetherWatchWorker()
    w._finish([cap], t)
    assert cap.converted and cap.crop_rect == (0.12, 0.11, 0.88, 0.9)
    prev.auto_crop_rect = None                                   # now a hand crop
    cap2 = frame("c2.tif", seed=6)
    ccr_backend.images.remove(cap2)
    t2 = look_template(prev)
    assert not t2["crop_is_auto"]
    w._finish([cap2], t2)
    assert cap2.converted and cap2.crop_rect == (0.2, 0.2, 0.7, 0.7)


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #
def test_settings_round_trip():
    from ui.main_window import MainWindow
    before = (ccr_backend.crop_after_convert, ccr_backend.clip_mode)
    w = MainWindow()
    try:
        w.on_clip_mode_changed("brightest")
        w.on_crop_after_convert_toggled(False)
        w2 = MainWindow()
        assert ccr_backend.clip_mode == "brightest" and not ccr_backend.crop_after_convert
        w.on_clip_mode_changed("nonsense")
        assert ccr_backend.clip_mode == "rolloff"
        from widgets.settings_dialog import SettingsDialog
        dlg = SettingsDialog(w)
        assert dlg._combo_clip_mode.currentData() == "rolloff"
        assert not dlg._cb_crop_after.isChecked()
        w2.hide()
        dlg.close()
    finally:
        w.on_clip_mode_changed(before[1])
        w.on_crop_after_convert_toggled(before[0])
        w.hide()
