#!/usr/bin/env python3
"""AWB guarded by the roll (spec/awb-roll-guard.md)."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import awb  # noqa: E402


def test_density_conversions_round_trip():
    for v in (-80, -30, -5, 0, 5, 30, 80):
        assert awb.temp_slider(awb.temp_density(v)) == pytest.approx(v, abs=1e-6)
        assert awb.tint_slider(awb.tint_density(v)) == pytest.approx(v, abs=1e-4)
    assert awb.temp_density(17) == pytest.approx(0.06, abs=0.002)   # the cap's size


def test_no_roll_keeps_a_warm_scene_and_removes_a_cool_cast():
    t, n = awb.guard_wb((-60, 0))                 # a sunset: AWB would cool it hard
    assert awb.temp_density(t) == pytest.approx(-awb.TEMP_COOL_CAP, abs=0.003)
    t, n = awb.guard_wb((40, 0))                  # a blue cast: warmed fully
    assert t == 40
    t, n = awb.guard_wb((0, 95))                  # a strong tint: up to 0.15
    assert awb.tint_density(n) == pytest.approx(awb.TINT_ROLL_CAP, abs=0.003)
    assert awb.guard_wb((10, -10)) == (10, -10)   # small ones pass


def test_with_a_roll_the_rolls_correction_passes_and_the_extra_is_capped():
    roll = (30, 10)
    t, n = awb.guard_wb((-40, 10), roll)          # this frame much warmer than the roll
    assert awb.temp_density(t) == pytest.approx(awb.temp_density(30) - awb.TEMP_ROLL_CAP,
                                                abs=0.003)
    assert n == 10
    assert awb.guard_wb((34, 12), roll) == (34, 12)   # close to the roll: as solved
    assert awb.roll_of([(1, 1), (2, 2)]) is None
    assert awb.roll_of([(1, 5), (3, 1), (2, 9), None]) == (2.0, 5.0)


class _Img:
    def __init__(self, full, converted=True):
        self.full = full
        self.converted = converted
        self.awb_full = None
        self.adjustment_settings = {}
        self.conversion_inputs = {}


@pytest.fixture
def backend(monkeypatch):
    from core.ccr_backend import ccr_backend
    imgs = []
    monkeypatch.setattr(ccr_backend, "images", imgs)
    monkeypatch.setattr(awb, "compute_awb_wb", lambda im: im.full)
    return ccr_backend, imgs


def test_awb_for_records_and_fills_the_roll_only_when_asked(backend):
    be, imgs = backend
    frame = _Img((-60, 0))
    others = [_Img((25, 5)), _Img((30, 8)), _Img((35, 2)), _Img((0, 0), converted=False)]
    imgs.extend([frame] + others)
    t, n, note = be.awb_for(frame, fill_roll=False)
    assert frame.awb_full == (-60, 0) and "no roll" in note
    assert all(o.awb_full is None for o in others)
    t, n, note = be.awb_for(frame, fill_roll=True)
    assert [o.awb_full for o in others[:3]] == [(25, 5), (30, 8), (35, 2)]
    assert others[3].awb_full is None                         # not converted: not the roll
    assert "3 frames" in note
    assert awb.temp_density(t) == pytest.approx(awb.temp_density(30) - 0.06, abs=0.003)
    full = be.awb_for(frame, guard=False)
    assert full[:2] == (-60, 0)


def test_after_conversion_awb_is_guarded_and_never_renders_others(backend):
    be, imgs = backend
    frame = _Img((-60, 0))
    other = _Img((25, 5))
    imgs.extend([frame, other])
    be.auto_awb, was = True, be.auto_awb
    try:
        be.maybe_auto_awb(frame)
    finally:
        be.auto_awb = was
    t = frame.adjustment_settings["temperature"]
    assert awb.temp_density(t) == pytest.approx(-0.06, abs=0.003)
    assert frame.awb_auto == (t, frame.adjustment_settings["tint"])
    assert other.awb_full is None
