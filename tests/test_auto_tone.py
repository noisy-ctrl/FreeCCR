#!/usr/bin/env python3
"""Auto tone (spec/auto-tone.md): a per-frame toe and shoulder, measured at
conversion, that keep shadow and highlight detail a frame would otherwise
lose at black or white."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
sys.path.insert(0, HERE)

from core import auto_tone as at  # noqa: E402

RECT = (0.0, 0.0, 1.0, 1.0)


def scene(lo=0.15, hi=0.85, n=200_000, seed=0, extra=()):
    """Display values (n, 1, 3): grey levels spread between lo and hi, plus
    (value, share) blocks appended."""
    rng = np.random.default_rng(seed)
    v = list(rng.uniform(lo, hi, n))
    for value, share in extra:
        v += [value] * int(share * n)
    a = np.repeat(np.asarray(v, np.float32)[:, None, None], 3, axis=2)
    return a


# --- measuring -------------------------------------------------------------------

def test_a_frame_that_loses_nothing_is_left_alone():
    assert at.measure(scene(), RECT) is None
    d = scene()
    assert np.array_equal(at.apply(d.copy(), None), d)


def test_content_below_black_is_lifted_white_kept():
    p = at.measure(scene(extra=((-0.05, 0.02),)), RECT)
    assert p["lift"] == pytest.approx(0.05, abs=0.002)
    out = at.apply(np.array([-p["lift"], 1.0], np.float32), {"lift": p["lift"]})
    assert out == pytest.approx([0.0, 1.0], abs=1e-6)
    assert at.measure(scene(extra=((-0.9, 0.05),)), RECT)["lift"] == at.LIFT_MAX


def test_toe_follows_how_much_is_near_black():
    assert "toe" not in (at.measure(scene(extra=((0.03, 0.02),)), RECT) or {})
    weak = at.measure(scene(extra=((0.03, 0.08),)), RECT)["toe"]
    strong = at.measure(scene(extra=((0.03, 0.30),)), RECT)["toe"]
    assert 0 < weak < strong <= at.TOE_MAX


def test_toe_shape():
    x = np.linspace(-0.1, 1.0, 2001, dtype=np.float32)
    y = at.apply(x.copy(), {"toe": 1.0})
    assert at.apply(np.zeros(1, np.float32), {"toe": 1.0})[0] == 0.0     # black stays
    assert np.all(np.diff(y) > 0)                                          # monotone
    slope0 = (at.apply(np.array([1e-4], np.float32), {"toe": 1.0})[0]) / 1e-4
    assert slope0 == pytest.approx(2.0, rel=1e-3)
    above = x >= at.TOE_END
    assert np.allclose(y[above], x[above])                                 # identity above


def test_shoulder_shape():
    p = at.measure(scene(hi=0.95, extra=((1.15, 0.06),)), RECT)
    k, top = p["knee"], p["top"]
    assert at.KNEE_MIN <= k < 1.0 < top
    x = np.linspace(0.0, top, 4001, dtype=np.float32)
    y = at.apply(x.copy(), p)
    assert y[-1] == pytest.approx(1.0, abs=1e-4)                           # top -> white
    assert np.all(np.diff(y) > 0)
    below = x <= k
    assert np.allclose(y[below], x[below])
    e = 1e-3                                                               # no kink
    yk = at.apply(np.array([k - e, k, k + e], np.float32), p)
    assert (yk[2] - yk[1]) / e == pytest.approx((yk[1] - yk[0]) / e, rel=0.02)


def test_shoulder_strength_follows_the_share_above_white():
    few = at.measure(scene(hi=0.95, extra=((1.25, 0.008),)), RECT)
    many = at.measure(scene(hi=0.95, extra=((1.25, 0.08),)), RECT)
    assert few["top"] < many["top"] and few["knee"] > many["knee"]
    assert "knee" not in (at.measure(scene(extra=((1.25, 0.003),)), RECT) or {})


def test_lamps_do_not_drag_the_knee_down():
    p = at.measure(scene(hi=0.95, extra=((4.0, 0.06),)), RECT)
    assert p["top"] <= at.HI_MAX + 1e-6 and p["knee"] >= at.KNEE_MIN


def test_only_the_picture_area_is_measured():
    d = np.full((100, 100, 3), 0.5, np.float32)
    d[:10] = -0.5                      # a rebate band
    d[-10:] = 3.0                      # holder
    assert at.measure(d, (0.0, 0.12, 1.0, 0.88)) is None
    assert at.measure(d, RECT) is not None


# --- conversion and render ---------------------------------------------------------------

class _Img:
    def __init__(self, base):
        self.resized_raw = base
        self._ws_windowed = True
        self.conversion_inputs = {"mode": "bw", "bw": ((1, 1, 1), None)}
        self.crop_rect = None


def _windowed(d):
    from core.ccr_processor import encode_window
    return encode_window(d.astype(np.float32))


@pytest.fixture
def backend(monkeypatch):
    from core.ccr_backend import ccr_backend
    monkeypatch.setattr(ccr_backend, "auto_gain", False)
    monkeypatch.setattr(ccr_backend, "auto_tone", True)
    return ccr_backend


def test_conversion_records_it_with_a_picture_area(backend):
    d = np.full((200, 300, 3), 0.5, np.float32)
    d[50:80, 50:200] = 1.2                         # 7.5 % above white
    img = _Img(_windowed(d))
    assert backend.measure_auto_tone(img) is None   # no picture area: nothing
    assert "auto_tone" not in img.conversion_inputs
    p = backend.measure_auto_tone(img, (0.0, 0.0, 1.0, 1.0))
    assert p and img.conversion_inputs["auto_tone"] == p
    img.conversion_inputs["picture"] = [0.0, 0.0, 1.0, 1.0]
    img.resized_raw = _windowed(np.full((200, 300, 3), 0.5, np.float32))
    assert backend.measure_auto_tone(img) is None   # re-measured: now nothing needed
    assert "auto_tone" not in img.conversion_inputs


def test_render_applies_it_only_while_the_setting_is_on(backend, monkeypatch):
    from core.ccr_processor import _apply_working_space_recovery
    d = np.full((20, 20, 3), 0.5, np.float32)
    d[:5] = 1.2
    base = _windowed(d)
    p = {"knee": 0.8, "top": 1.2, "sigma": at._solve_sigma(2.0)}
    off = _apply_working_space_recovery(base, 0.0)
    on = _apply_working_space_recovery(base, 0.0, auto_tone=p)
    assert off[0, 0, 0] == 65535 and on[0, 0, 0] == pytest.approx(65535, abs=40)   # window quantisation
    assert on[10, 10, 0] == off[10, 10, 0]          # below the knee: untouched
    from core.ccr_image import CCRImage
    img = CCRImage.__new__(CCRImage)
    img.conversion_inputs = {"auto_tone": p}
    img.converted = True
    seen = []
    import core.ccr_image as ci_mod
    real = ci_mod.adjust_image_opencl

    def spy(*a, **k):
        seen.append(k.get("auto_tone"))
        return real(*a, **k)
    monkeypatch.setattr(ci_mod, "adjust_image_opencl", spy)
    for attr, val in (("_ws_windowed", True), ("contrast_base", 0), ("temperature_base", 0),
                      ("brightness_base", -8), ("exposure_base", 0.0), ("base_curve", 0),
                      ("color_profile", "color"), ("area_layers", []),
                      ("adjustment_settings", {}), ("tint_balance_factor", 1.0),
                      ("dust_spots", None), ("decoded_mono", False)):
        setattr(img, attr, val)
    monkeypatch.setattr(img, "_apply_dust_removal", lambda im, ws_windowed=False: im,
                        raising=False)
    img.apply_adjustments(base, skip_dust=True)
    monkeypatch.setattr(backend, "auto_tone", False)
    img.apply_adjustments(base, skip_dust=True)
    assert seen == [p, None]
