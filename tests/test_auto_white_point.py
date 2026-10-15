#!/usr/bin/env python3
"""Auto white point (spec/auto-white-point.md): each frame's brightest area
becomes its white point, guarded against neutralising the scene's colour."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
sys.path.insert(0, HERE)

from core import auto_white_point as awp  # noqa: E402

BASE = np.array([30000.0, 26000.0, 21000.0])
GAMMA = np.array([1.0, 1.05, 1.10])          # unequal layer contrast, no tint
RECT = (0.1, 0.1, 0.9, 0.9)


def negative(gamma=GAMMA, patch_level=1.0, h=400, w=600, seed=0, holder=None):
    """A raw negative: clear border, a scene of log-exposures 0..0.8 and a
    bright neutral patch at `patch_level`, each channel's density = gamma x
    the scene value. `holder`: opaque value painted outside the picture."""
    rng = np.random.default_rng(seed)
    s = rng.random((h, w)) * 0.8
    y0, y1, x0, x1 = int(0.1 * h), int(0.9 * h), int(0.1 * w), int(0.9 * w)
    s[y0 + 20:y0 + 50, x0 + 20:x0 + 90] = patch_level   # ~3 % of the picture
    D = s[..., None] * np.asarray(gamma)[None, None, :]
    raw = BASE / 10.0 ** D
    raw[:y0], raw[y1:], raw[:, :x0], raw[:, x1:] = BASE, BASE, BASE, BASE
    if holder is not None:
        raw[:8], raw[-8:] = holder, holder
    return raw.astype(np.uint16)


# --- measuring -------------------------------------------------------------------

def test_finds_the_brightest_areas_density():
    d = awp.highlight_density(negative(), BASE, RECT)
    assert d == pytest.approx(tuple(GAMMA * 1.0), abs=0.01)


def test_the_holder_outside_the_picture_is_ignored():
    a = awp.highlight_density(negative(), BASE, RECT)
    b = awp.highlight_density(negative(holder=60), BASE, RECT)
    assert b == pytest.approx(a, abs=1e-6)


def test_nothing_usable():
    flat = np.full((200, 300, 3), 20000, np.uint16)          # no density at all
    assert awp.highlight_density(flat, BASE, RECT) is None
    assert awp.highlight_density(negative(), None, RECT) is None
    assert awp.highlight_density(None, BASE, RECT) is None


def test_the_white_point_makes_the_patch_neutral():
    from core.ccr_processor import _twopoint_invert
    raw = negative()
    d = awp.highlight_density(raw, BASE, RECT)
    used, info = awp.guarded_dmax(d)                           # no tint, mild cool: all removed
    assert info["ref"] == "default" and abs(info["tint"]) < 0.01
    white = awp.white_point(BASE, used)
    h, w = raw.shape[:2]
    patch = raw[int(0.1 * h) + 25:int(0.1 * h) + 45, int(0.1 * w) + 30:int(0.1 * w) + 80]
    out = _twopoint_invert(patch.astype(np.float32), BASE, white, density=True)
    m = out.reshape(-1, 3).mean(0)
    assert np.ptp(m) / m.mean() < 0.01                         # neutral


# --- keeping the scene's colour ------------------------------------------------------

def _dmax(temp, tint, level=1.0):
    r = np.ones(3) + temp * awp._UT + tint * awp._UG
    return tuple(r * level)


# The white point's ratio is what becomes neutral: using the frame's own ratio
# removes all of its highlight colour, using the reference keeps all of it.
# So used - reference is what is REMOVED, frame - used what is KEPT.

def test_against_the_roll_a_warm_highlight_is_mostly_kept():
    ref = np.ones(3)
    frame = _dmax(0.20, 0.0)
    used, info = awp.guarded_dmax(frame, ref)
    assert info["ref"] == "roll" and info["temp"] == pytest.approx(0.20, abs=1e-3)
    assert (awp.ratio(used) - ref) @ awp._UT == pytest.approx(awp.TEMP_ROLL, abs=1e-6)
    assert (awp.ratio(frame) - awp.ratio(used)) @ awp._UT == pytest.approx(
        0.20 - awp.TEMP_ROLL, abs=1e-6)                       # the sunset stays
    used, _ = awp.guarded_dmax(_dmax(-0.20, 0.0), ref)        # a blue sky, too
    assert (awp.ratio(used) - ref) @ awp._UT == pytest.approx(-awp.TEMP_ROLL, abs=1e-6)


def test_tint_is_removed_up_to_its_cap():
    ref = np.ones(3)
    frame = _dmax(0.0, -0.10)
    used, _ = awp.guarded_dmax(frame, ref)
    assert awp.ratio(used) == pytest.approx(awp.ratio(frame), abs=1e-6)   # magenta gone
    used, _ = awp.guarded_dmax(_dmax(0.0, -0.30), ref)
    assert (awp.ratio(used) - ref) @ awp._UG == pytest.approx(-awp.TINT_MAX, abs=1e-6)


def test_without_a_roll_cool_is_removed_further_than_warm():
    frame = _dmax(-0.15, 0.0)
    used, info = awp.guarded_dmax(frame)
    assert info["ref"] == "default"
    assert awp.ratio(used) == pytest.approx(awp.ratio(frame), abs=1e-6)   # all removed
    used, _ = awp.guarded_dmax(_dmax(0.15, 0.0))
    assert (awp.ratio(used) - 1) @ awp._UT == pytest.approx(awp.TEMP_WARM, abs=1e-6)


def test_the_frames_own_level_is_kept():
    used, _ = awp.guarded_dmax(_dmax(0.1, 0.05, level=1.3), np.ones(3))
    assert np.mean(used) == pytest.approx(1.3, abs=1e-9)


def test_the_roll_needs_three_frames():
    assert awp.roll_reference([_dmax(0, 0), _dmax(0.1, 0)]) is None
    ref = awp.roll_reference([_dmax(0, 0), _dmax(0.1, 0), None, _dmax(0.2, 0)])
    assert (ref - 1) @ awp._UT == pytest.approx(0.1, abs=1e-3)


# --- the backend ----------------------------------------------------------------------

class _Img:
    def __init__(self, raw, name):
        self.resized_raw = raw
        self.converted = False
        self.fine_rotation_angle = 0
        self.conversion_inputs = None
        self.file_path = name
        self.adjustment_settings = {}
        self.crop_rect = RECT          # known picture area: no crop detection

    def reload_image(self):
        raise AssertionError("raw is fresh: no reload expected")

    def update_thumbnail_and_preview(self):
        pass


@pytest.fixture
def backend(monkeypatch):
    import core.ccr_backend as mod
    from core.ccr_backend import ccr_backend
    from core.auto_black_point import RebateResult, HIGH
    for k, v in (("black_point_bgr", None), ("white_point_bgr", None),
                 ("film_stock_slopes", None), ("density_bwpoint", True),
                 ("auto_awb", False), ("auto_black_point", True),
                 ("auto_white_point", True)):
        monkeypatch.setattr(ccr_backend, k, v, raising=False)
    # Every frame's border reads as BASE (the black point is not what this tests).
    monkeypatch.setattr(ccr_backend, "detect_frame_black_point",
                        lambda img, reload=True: RebateResult(tuple(BASE), HIGH, side="top"))
    calls = []

    def fake(img, black, white, density=False, slopes_bgr=None):
        calls.append((img.file_path, black, white, slopes_bgr))
        return img.resized_raw

    monkeypatch.setattr(mod, "ccr_normalize_with_bwpoint", fake)
    ccr_backend._calls = calls
    return ccr_backend


def _roll(backend, monkeypatch, n=4):
    imgs = [_Img(negative(seed=i), f"f{i}.RAF") for i in range(n)]
    monkeypatch.setattr(backend, "images", imgs)
    return imgs


def test_convert_all_gives_every_frame_its_white_point(backend, monkeypatch):
    imgs = _roll(backend, monkeypatch)
    backend.apply_bwpoint_to_all_images()
    for img, (_n, black, white, slopes) in zip(imgs, backend._calls):
        rec = img.conversion_inputs["auto_wp"]
        assert rec["ref"] == "roll"                              # 3 others each
        assert rec["dmax"] == pytest.approx(list(GAMMA), abs=0.01)
        assert white is not None and slopes is None
        assert img.conversion_inputs["bw"][1] == pytest.approx(white)
    assert backend.last_auto_bp_summary["auto_wp"] == 4
    import json
    from core.catalog import _ci_to_json
    json.dumps(_ci_to_json(imgs[0].conversion_inputs))         # catalog-safe


def test_two_frames_have_no_roll_to_compare_with(backend, monkeypatch):
    imgs = _roll(backend, monkeypatch, n=2)
    backend.apply_bwpoint_to_all_images()
    assert all(i.conversion_inputs["auto_wp"]["ref"] == "default" for i in imgs)


@pytest.mark.parametrize("setup", ["sampled white", "film stock", "setting off"])
def test_what_turns_it_off(backend, monkeypatch, setup):
    imgs = _roll(backend, monkeypatch, n=3)
    if setup == "sampled white":
        monkeypatch.setattr(backend, "black_point_bgr", tuple(BASE))
        monkeypatch.setattr(backend, "white_point_bgr", tuple(BASE / 10))
    elif setup == "film stock":
        monkeypatch.setattr(backend, "film_stock_slopes", (0.8, 0.7, 0.6))
    else:
        monkeypatch.setattr(backend, "auto_white_point", False)
    assert not backend.uses_auto_white_point()
    backend.apply_bwpoint_to_all_images()
    assert all("auto_wp" not in i.conversion_inputs for i in imgs)


def test_convert_current_uses_the_other_frames_records(backend, monkeypatch):
    imgs = _roll(backend, monkeypatch)
    backend.apply_bwpoint_to_all_images()
    ref = backend.roll_highlight_reference(imgs[0])
    assert ref == pytest.approx(awp.ratio(GAMMA), abs=0.01)
    # Unconverted frames with a known picture area are measured quickly instead.
    from core.auto_black_point import RebateResult, HIGH
    import core.auto_black_point as abp
    monkeypatch.setattr(abp, "find_rebate", lambda raw: RebateResult(tuple(BASE), HIGH))
    for i in imgs:
        i.converted, i.conversion_inputs = False, None
    assert backend.roll_highlight_reference(imgs[0]) == pytest.approx(ref, abs=1e-6)
    i1 = imgs[1]
    assert i1._awp_quick[0] is i1.resized_raw                  # cached on that raw


# --- the panel ------------------------------------------------------------------------

def test_label_and_setting(monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from core.ccr_backend import ccr_backend
    from widgets.sliders_panel import SlidersPanel
    for k, v in (("black_point_bgr", None), ("white_point_bgr", None),
                 ("film_stock_slopes", None), ("film_stock_name", None)):
        monkeypatch.setattr(ccr_backend, k, v, raising=False)
    panel = SlidersPanel()
    monkeypatch.setattr(ccr_backend, "auto_white_point", True)
    panel._update_bwp_mode_label()
    assert panel.bwp_mode_label.text() == "Anchor source: auto black + white point per frame"
    monkeypatch.setattr(ccr_backend, "auto_white_point", False)
    panel._update_bwp_mode_label()
    assert panel.bwp_mode_label.text() == \
        "Anchor source: auto black point per frame, default slope"
