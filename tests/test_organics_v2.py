"""Colour Organics v2 (spec/colour-organics-v2.md): the labs' hue moves and
saturation by tone, held to the finishing tools' quality rules."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import organics_v2 as v2                      # noqa: E402
from core.oklab import to_oklab, from_oklab             # noqa: E402


def lab_patch(L, C, h, size=(48, 64)):
    a = C * np.cos(np.radians(h))
    b = C * np.sin(np.radians(h))
    px = from_oklab(np.array([[L, a, b]], np.float32))[0]
    return np.broadcast_to(px, size + (3,)).astype(np.float32).copy()


def lch(img):
    o = to_oklab(img.reshape(-1, 3))
    return (o[:, 0], np.hypot(o[:, 1], o[:, 2]),
            np.degrees(np.arctan2(o[:, 2], o[:, 1])) % 360)


def mean_turn(img, out):
    _, _, h0 = lch(img)
    _, _, h1 = lch(out)
    return float(np.median((h1 - h0 + 180) % 360 - 180))


def test_zero_is_the_input_itself():
    img = lab_patch(0.6, 0.08, 60)
    assert v2.apply_organics_v2(img, 0, 0) is img


def test_greys_come_back_exactly():
    ramp = np.repeat(np.linspace(0, 1, 256, dtype=np.float32)[None, :, None], 32, 0)
    ramp = np.repeat(ramp, 3, 2)
    out = v2.apply_organics_v2(ramp, 100, 100)
    assert np.array_equal(out, ramp)


def test_lightness_is_kept():
    img = lab_patch(0.55, 0.09, 70)
    out = v2.apply_organics_v2(img, 100, 100)
    L0, _, _ = lch(img)
    L1, _, _ = lch(out)
    assert np.abs(L1 - L0).max() < 2e-3


@pytest.mark.parametrize("L,C,h,sign", [(0.6, 0.08, 60, +1),     # orange -> yellower
                                        (0.5, 0.08, 250, -1),    # blue -> cyan
                                        (0.6, 0.08, 15, +1)])    # red -> orange
def test_directions_match_the_labs(L, C, h, sign):
    img = lab_patch(L, C, h)
    out = v2.apply_organics_v2(img, 50, 0)
    turn = mean_turn(img, out)
    want, _ = v2.table_fields([L], [h])
    assert sign * turn > 1.0
    assert turn == pytest.approx(float(want[0]), abs=1.0)


def test_dark_browns_turn_further_than_mid_oranges():
    # Lab A (the default, spec/organics-v2-labs.md): 7.8 deg dark vs 6.9 mid;
    # the three labs' average had a wider gap.
    dark = lab_patch(0.30, 0.06, 35)
    mid = lab_patch(0.60, 0.06, 35)
    assert mean_turn(dark, v2.apply_organics_v2(dark, 50, 0)) > \
        mean_turn(mid, v2.apply_organics_v2(mid, 50, 0)) + 0.5


def test_saturation_only_ever_takes_colour_out():
    rng = np.random.default_rng(1)
    for _ in range(12):
        L, C, h = rng.uniform(0.15, 0.9), rng.uniform(0.02, 0.12), rng.uniform(0, 360)
        img = lab_patch(L, C, h, (16, 16))
        out = v2.apply_organics_v2(img, 0, 100)
        _, c0, _ = lch(img)
        _, c1, _ = lch(out)
        assert c1.max() <= c0.max() + 1e-4


def test_the_hue_map_never_folds_at_full():
    assert v2.fold_limit() >= v2.AMOUNT_MAX
    for L in (0.15, 0.3, 0.5, 0.7, 0.9):
        h = np.arange(0, 360, 0.25)
        move, _ = v2.table_fields(np.full_like(h, L), h)
        slope = 1 + 2.0 * np.gradient(move, 0.25)
        assert slope.min() >= v2.MIN_SLOPE


def test_a_fine_hue_ramp_stays_in_order():
    # 16-bit-fine steps of hue at constant L and C: never reordered.
    n = 4096
    h = np.linspace(0, 359.9, n)
    L, C = 0.6, 0.07
    lab = np.stack([np.full(n, L), C * np.cos(np.radians(h)), C * np.sin(np.radians(h))], 1)
    img = from_oklab(lab.astype(np.float32)).reshape(1, n, 3)
    img = np.repeat(img, 8, 0)
    out = v2.apply_organics_v2(img, 100, 0)
    _, _, h1 = lch(out[4:5])
    d = (np.diff(np.unwrap(np.radians(h1))))
    assert (d > -1e-6).all()


def test_in_range():
    rng = np.random.default_rng(2)
    img = rng.uniform(0, 1, (64, 64, 3)).astype(np.float32)
    out = v2.apply_organics_v2(img, 100, 100)
    assert out.min() >= 0.0 and out.max() <= 1.0


def test_colour_grain_does_not_grow():
    rng = np.random.default_rng(3)
    img = lab_patch(0.5, 0.08, 250, (128, 128))
    lab = to_oklab(img.reshape(-1, 3)).reshape(img.shape)
    lab[..., 1:] += rng.normal(0, 0.006, lab[..., 1:].shape).astype(np.float32)
    noisy = np.clip(from_oklab(lab.reshape(-1, 3)).reshape(img.shape), 0, 1)
    out = v2.apply_organics_v2(noisy, 100, 100)
    _, c0, _ = lch(noisy)
    _, c1, _ = lch(out)
    o0 = to_oklab(noisy.reshape(-1, 3))
    o1 = to_oklab(out.reshape(-1, 3))
    # colour scatter about the patch's own mean: kept or reduced
    s0 = np.linalg.norm(o0[:, 1:] - o0[:, 1:].mean(0), axis=1).std()
    s1 = np.linalg.norm(o1[:, 1:] - o1[:, 1:].mean(0), axis=1).std()
    assert s1 <= s0 * 1.01


def test_same_turn_at_two_sizes():
    small = lab_patch(0.45, 0.07, 60, (60, 90))
    big = lab_patch(0.45, 0.07, 60, (240, 360))
    t1 = mean_turn(small, v2.apply_organics_v2(small, 50, 50))
    t2 = mean_turn(big, v2.apply_organics_v2(big, 50, 50))
    assert t1 == pytest.approx(t2, abs=0.05)


def test_adjust_image_runs_it_and_is_a_no_op_at_zero():
    from core import ccr_processor as cp
    rng = np.random.default_rng(4)
    img16 = (rng.uniform(0.2, 0.8, (32, 48, 3)) * 65535).astype(np.uint16)
    base = cp.adjust_image(img16)
    assert np.array_equal(cp.adjust_image(img16, organics_v2=0.0, organic_sat=0.0), base)
    moved = cp.adjust_image(img16, organics_v2=60.0, organic_sat=60.0)
    assert not np.array_equal(moved, base)


def test_panel_keys_are_last_and_synced_with_saturation():
    from widgets import sliders_panel as sp
    keys = sp.SlidersPanel.ADJUSTMENT_KEYS
    assert keys[-5:-3] == ["organics_v2", "organic_sat"]   # then lab_tone, luma_nr*
    sat = next(g for g in sp.SYNC_GROUPS if g[0] == "sat") if hasattr(sp, "SYNC_GROUPS") else None
    if sat is not None:
        assert "organics_v2" in sat[2] and "organic_sat" in sat[2]
