#!/usr/bin/env python3
"""The Live Capture Monitor's Negative preview (spec/live-capture-monitor.md
§6): two- and three-point tables measured on synthetic wedges rendered the
way the feed is, plus the tables' own guarantees."""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402

from core import live_negative as ln  # noqa: E402

DMIN = np.array([0.20, 0.55, 0.85])          # R, G, B film base densities
FILMS = {"mild": [0.60, 0.65, 0.72], "crossed": [0.55, 0.65, 0.80], "equal": [0.65, 0.65, 0.65]}


def _enc(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * x ** (1 / 2.4) - 0.055)


def _wedge(gammas, seed, frames=8, sigma=1.5, px=400):
    """A neutral 15-step wedge as the feed would show it: the negative's
    transmission, sRGB-encoded to 8 bits, noise sigma 1.5 levels, 8 frames
    averaged (Reduce noise), 400 px per step. Returns (patches BGR float,
    clean BGR 8-bit) with steps along axis 0."""
    rng = np.random.default_rng(seed)
    stops = np.linspace(0, 7, 15)
    gam = np.array(gammas)
    d = DMIN + gam * (stops[:, None] * np.log10(2))
    rgb = _enc(0.92 * 10 ** (-(d - DMIN.min()))) * 255          # (15, 3) R, G, B
    bgr = rgb[:, ::-1]
    clean = np.round(bgr)
    noisy = np.mean([np.clip(np.round(bgr[:, None] + rng.normal(0, sigma, (15, px, 3))), 0, 255)
                     for _ in range(frames)], 0)
    return noisy, clean


def _measure(lut, patches):
    """(cast, noise): the largest spread between channel means over the
    inner steps, and the largest per-channel standard deviation."""
    idx = np.clip(np.rint(patches), 0, 255).astype(np.uint8)
    out = np.stack([lut[idx[..., c], 0, c] for c in range(3)], -1).astype(np.float64)
    mean = out.mean(1)
    cast = (mean.max(1) - mean.min(1))[1:-1].max()
    noise = out.std(1)[1:-1].max()
    return cast, noise


@pytest.mark.parametrize("film,two_max,three_max", [
    ("mild", 7.5, 5.5), ("crossed", 17.0, 10.5), ("equal", 2.0, 2.0)])
def test_wedges_at_the_measured_casts(film, two_max, three_max):
    patches, clean = _wedge(FILMS[film], seed=2)
    base, hi, mid = clean[0], clean[-1], clean[7]
    two = _measure(ln.tables(base, hi), patches)
    three = _measure(ln.tables(base, hi, mid), patches)
    assert two[0] <= two_max and three[0] <= three_max
    assert three[1] <= two[1] + 0.3                     # the third point adds no noise
    if film == "crossed":
        assert three[0] < two[0] - 4                    # and it does pull the cast in


def test_a_sampled_mid_grey_comes_out_neutral():
    _patches, clean = _wedge(FILMS["crossed"], seed=3)
    base, hi, mid = clean[0], clean[-1], clean[7]
    lut = ln.tables(base, hi, mid)
    vals = [int(lut[int(mid[c]), 0, c]) for c in range(3)]
    assert max(vals) - min(vals) <= 1


def test_base_is_black_and_highlight_white():
    base, hi = (60, 120, 210), (20, 40, 90)
    lut = ln.tables(base, hi)
    for c in range(3):
        assert lut[base[c], 0, c] == 0 and lut[hi[c], 0, c] == 255
    for b in (-100, -40, 60, 100):
        lb = ln.tables(base, hi, brightness=b)
        for c in range(3):
            assert lb[base[c], 0, c] == 0 and lb[hi[c], 0, c] == 255


def test_every_table_is_monotone():
    for kw in ({}, {"mid": (40, 80, 150)}, {"temperature": 70, "tint": -40, "brightness": 30}):
        lut = ln.tables((60, 120, 210), (20, 40, 90), **kw).astype(int)
        assert (np.diff(lut[:, 0, :], axis=0) <= 0).all()      # brighter feed = darker positive


def test_temperature_and_tint_never_add_clipping():
    plain = ln.tables((60, 120, 210), (20, 40, 90)).astype(int)
    for t in (-100, -30, 30, 100):
        for n in (-100, 0, 100):
            lut = ln.tables((60, 120, 210), (20, 40, 90), temperature=t, tint=n).astype(int)
            assert (lut <= plain).all()
    r, g, b = ln.gains(50, 0)
    assert r == 1.0 and b < g < r
    r, g, b = ln.gains(0, 50)
    assert g < r == b == 1.0


def test_auto_points_find_the_border_and_skip_bare_light():
    rng = np.random.default_rng(4)
    frame = np.empty((540, 960, 3), np.float32)
    frame[:] = (62, 128, 214)                                   # the base, in BGR
    frame[60:480, 80:880] = (25, 55, 110)                       # the picture
    frame[60:480, 400:560] = (14, 30, 70)                       # its densest part
    for x in range(100, 880, 70):                               # sprocket holes, bare light
        frame[10:40, x:x + 30] = 255
    frame += rng.normal(0, 1.0, frame.shape)
    frame = np.clip(frame, 0, 255).astype(np.uint8)
    base, hi, note = ln.auto_points(frame)
    assert base == pytest.approx((62, 128, 214), abs=3)
    assert hi[0] < 20 and hi[2] < 80
    assert note
    assert ln.usable(base, hi) and not ln.usable(base, base)


def test_tables_apply_with_cv2_lut():
    lut = ln.tables((62, 128, 214), (14, 30, 70))
    frame = np.full((4, 4, 3), (62, 128, 214), np.uint8)
    assert cv2.LUT(frame, lut).max() == 0


def test_fallback_base_is_not_pushed_up_by_noise_or_holes():
    """No clean border band (holder edges, holes in the rebate): the base is
    the clearest film's level, not a high percentile of noisy pixels (which
    put it ~6 levels too bright and tinted the rebate blue)."""
    rng = np.random.default_rng(5)
    h, w = 540, 960
    frame = np.empty((h, w, 3), np.float32)
    frame[:] = (62, 128, 214)
    frame[100:440, 150:810] = (30, 60, 120)
    for x in range(160, 810, 70):
        frame[45:75, x:x + 34] = 252
        frame[465:495, x:x + 34] = 252
    frame[:20] = 18
    frame[-20:] = 18
    frame += rng.normal(0, 2.2, frame.shape)
    frame = np.clip(frame, 0, 255).astype(np.uint8)
    base, hi, note = ln.auto_points(frame)
    assert base == pytest.approx((62, 128, 214), abs=1.5)
