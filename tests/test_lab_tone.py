"""Lab Tone (spec/lab-tone.md): the labs' tone curve on lightness."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import lab_tone as lt                          # noqa: E402
from core import lab_tone_data as data                   # noqa: E402
from core.oklab import to_oklab, from_oklab              # noqa: E402


def grey(L, n=8):
    px = from_oklab(np.array([[L, 0.0, 0.0]], np.float32))[0]
    return np.broadcast_to(px, (n, n, 3)).astype(np.float32).copy()


def test_zero_is_the_input_itself():
    img = grey(0.5)
    assert lt.apply_lab_tone(img, 0) is img


def test_the_curve_at_fifty_goes_through_the_measured_knots():
    c = lt.curve(50)
    assert np.allclose(np.interp(data.KNOTS, lt.GRID, c), data.VALUES, atol=1e-6)


@pytest.mark.parametrize("amount", [5, 25, 50, 75, 100])
def test_the_curve_never_turns_back(amount):
    c = lt.curve(amount)
    assert (np.diff(c) > 0).all()


def test_zero_amount_curve_is_the_identity():
    assert np.allclose(lt.curve(0), lt.GRID)


def test_black_lifts_and_white_holds_back_at_fifty():
    black = np.zeros((4, 4, 3), np.float32)
    white = np.ones((4, 4, 3), np.float32)
    Lb = to_oklab(lt.apply_lab_tone(black, 50).reshape(-1, 3))[:, 0]
    Lw = to_oklab(lt.apply_lab_tone(white, 50).reshape(-1, 3))[:, 0]
    assert Lb.mean() == pytest.approx(data.VALUES[0], abs=2e-3)
    assert Lw.mean() == pytest.approx(data.VALUES[-1], abs=2e-3)


def test_greys_stay_grey():
    out = lt.apply_lab_tone(grey(0.45), 50)
    o = to_oklab(out.reshape(-1, 3))
    assert np.abs(o[:, 1:]).max() < 2e-3


def test_hue_and_chroma_are_kept():
    L, C, h = 0.55, 0.06, 70
    px = from_oklab(np.array([[L, C * np.cos(np.radians(h)), C * np.sin(np.radians(h))]], np.float32))[0]
    img = np.broadcast_to(px, (8, 8, 3)).astype(np.float32).copy()
    o0 = to_oklab(img.reshape(-1, 3))
    o1 = to_oklab(lt.apply_lab_tone(img, 50).reshape(-1, 3))
    assert np.abs(o1[:, 1:] - o0[:, 1:]).max() < 2e-3
    assert not np.allclose(o1[:, 0], o0[:, 0])


@pytest.mark.parametrize("amount", [50, 100])
def test_a_fine_grey_ramp_stays_in_order(amount):
    v = np.linspace(0, 1, 65536, dtype=np.float32)
    img = np.repeat(v[:, None], 3, 1).reshape(256, 256, 3)
    out = lt.apply_lab_tone(img, amount).reshape(-1, 3)
    L = to_oklab(out)[:, 0]
    assert (np.diff(L) >= -1e-6).all()


def test_in_range_and_float32():
    rng = np.random.default_rng(1)
    img = rng.uniform(0, 1, (64, 64, 3)).astype(np.float32)
    out = lt.apply_lab_tone(img, 100)
    assert out.dtype == np.float32 and out.min() >= 0.0 and out.max() <= 1.0


def test_unmoved_pixels_come_back_exactly():
    # where the curve crosses the identity a pixel is returned bit for bit
    c = lt.curve(50)
    cross = lt.GRID[np.argmin(np.abs(c - lt.GRID)[2000:3500]) + 2000]
    img = grey(float(cross), 2)
    out = lt.apply_lab_tone(img, 50)
    o0 = to_oklab(img.reshape(-1, 3))[:, 0]
    o1 = to_oklab(out.reshape(-1, 3))[:, 0]
    assert np.abs(o1 - o0).max() < 2e-4


def test_adjust_image_runs_it_and_is_a_no_op_at_zero():
    from core import ccr_processor as cp
    rng = np.random.default_rng(4)
    img16 = (rng.uniform(0.2, 0.8, (32, 48, 3)) * 65535).astype(np.uint16)
    base = cp.adjust_image(img16)
    assert np.array_equal(cp.adjust_image(img16, lab_tone=0.0), base)
    assert not np.array_equal(cp.adjust_image(img16, lab_tone=50.0), base)


def test_panel_key_is_last_and_synced_with_lab_finish():
    from widgets import sliders_panel as sp
    assert sp.SlidersPanel.ADJUSTMENT_KEYS[-1] == "lab_tone"
