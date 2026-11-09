#!/usr/bin/env python3
"""Clean Lights: less colour in the light tones at unchanged lightness, as
measured on lab scans (spec/clean-lights.md), held to the finishing tools'
quality rules."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core import clean_lights as cl  # noqa: E402
from core import ccr_processor as cp  # noqa: E402
from core.oklab import to_oklab, from_oklab  # noqa: E402


def _patch(L, C, h=np.radians(60), shape=(90, 120), noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    lab = np.zeros(shape + (3,), np.float32)
    lab[..., 0] = L + (rng.normal(0, noise, shape) if noise else 0)
    lab[..., 1] = C * np.cos(h) + (rng.normal(0, noise / 3, shape) if noise else 0)
    lab[..., 2] = C * np.sin(h) + (rng.normal(0, noise / 3, shape) if noise else 0)
    return np.clip(from_oklab(lab.reshape(-1, 3)), 0, 1).reshape(lab.shape).astype(np.float32)


def _lab(x):
    return to_oklab(np.asarray(x, np.float32).reshape(-1, 3))


def _chroma(lab):
    return np.hypot(lab[:, 1], lab[:, 2])


def test_zero_is_the_input_and_darks_come_back_exactly():
    x = _patch(0.5, 0.06)
    assert cl.apply_clean_lights(x, 0) is x
    out = cl.apply_clean_lights(x, 100)
    np.testing.assert_array_equal(out, x)                 # L 0.5: below the lights


@pytest.mark.parametrize("L", [0.80, 0.85, 0.90])
def test_fifty_is_the_labs_share_at_unchanged_lightness_and_hue(L):
    x = _patch(L, 0.04)
    a, b = _lab(x), _lab(cl.apply_clean_lights(x, 50))
    share = np.median(_chroma(b) / _chroma(a))
    assert share == pytest.approx(1 - float(cl.lab_share(np.float32(L))), abs=0.01)
    np.testing.assert_allclose(b[:, 0], a[:, 0], atol=2e-4)
    hue = lambda lab: np.arctan2(lab[:, 2], lab[:, 1])
    np.testing.assert_allclose(hue(b), hue(a), atol=1e-3)


def test_the_measured_curve():
    """0 below L 0.70; ~4 % at 0.83, ~16 % at 0.88, ~38 % at 0.93."""
    s = cl.lab_share(np.array([0.6, 0.7, 0.83, 0.88, 0.93], np.float32))
    assert s[0] == 0 and s[1] == 0
    np.testing.assert_allclose(s[2:], [0.04, 0.16, 0.38], atol=0.03)


def test_hundred_is_fifty_twice_and_never_overshoots():
    L = np.linspace(0, 1, 101, dtype=np.float32)
    m50, m100 = cl.strength(L, 50), cl.strength(L, 100)
    np.testing.assert_allclose(1 - m100, (1 - m50) ** 2, atol=1e-6)
    assert m100.min() >= 0 and m100.max() < 1


@pytest.mark.parametrize("tint", [(1.0, 0.92, 0.80), (0.85, 0.95, 1.0)])
def test_a_fine_ramp_stays_smooth_and_in_order(tint):
    """A 16-bit-fine ramp from the mids to white with a tint: lightness keeps
    its order exactly, chroma along it changes smoothly (no banding)."""
    x = np.arange(0.45, 1.0, 1 / 65535, dtype=np.float32)
    ramp = np.clip(x[:, None] * np.array(tint, np.float32), 0, 1)[None]
    out = cl.apply_clean_lights(ramp, 100)
    lab = _lab(out)
    assert np.all(np.diff(lab[:, 0]) >= -1e-6)
    np.testing.assert_allclose(lab[:, 0], _lab(ramp)[:, 0], atol=2e-4)
    assert np.abs(np.diff(_chroma(lab))).max() < 2e-4
    assert out.min() >= 0 and out.max() <= 1


def test_colour_grain_never_grows_and_luma_grain_is_kept():
    x = _patch(0.88, 0.03, noise=0.012, shape=(540, 720), seed=3)
    a, b = _lab(x).reshape(540, 720, 3), _lab(cl.apply_clean_lights(x, 100)).reshape(540, 720, 3)
    for ch in (1, 2):
        assert (b[..., ch] - b[..., ch].mean()).std() <= (a[..., ch] - a[..., ch].mean()).std() * 1.001
    np.testing.assert_allclose(b[..., 0], a[..., 0], atol=2e-4)


def test_adjust_image_runs_it_before_golden():
    img = (_patch(0.88, 0.05) * 65535).astype(np.uint16)
    plain = cp.adjust_image(img)
    clean = cp.adjust_image(img, clean_lights=60)
    assert _chroma(_lab(clean / 65535.0)).mean() < _chroma(_lab(plain / 65535.0)).mean() * 0.95
    # Golden after it: the gold the user adds is not cleaned away
    both = cp.adjust_image(img, clean_lights=60, golden_highlights=100)
    gold = cp.apply_golden(np.clip(clean / 65535.0, 0, 1).astype(np.float32), 100) * 65535
    np.testing.assert_allclose(both.astype(float), np.clip(gold, 0, 65535).round(), atol=1)


def test_gpu_matches_numpy(monkeypatch):
    from core import finish_gpu as fg
    monkeypatch.setenv("FREECCR_GPU_FINISH", "1")
    if not cp.OPENCL_AVAILABLE or not cp._initialize_opencl():
        pytest.skip("no OpenCL device")
    ctx, q = cp._opencl_cache['ctx'], cp._opencl_cache['queue']
    x = np.concatenate([_patch(0.86, 0.04, noise=0.01), _patch(0.93, 0.02, noise=0.01),
                        _patch(0.4, 0.08)], 1)
    gpu = fg.finish_numpy_image(ctx, q, x, clean_lights=80)
    assert np.abs(gpu - cl.apply_clean_lights(x, 80)).max() * 65535 <= 3


def test_the_panel_has_the_slider_in_lab_finish():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel, SYNC_GROUPS
    keys = SlidersPanel.ADJUSTMENT_KEYS
    assert keys.index("clean_lights") == keys.index("golden_highlights") - 1
    assert "clean_lights" in dict((g[0], g[2]) for g in SYNC_GROUPS)["sat"]
    p = SlidersPanel()
    assert p.clean_lights_slider_layout.itemAt(0).widget().text() == "Clean Lights"
    # the positional zip lands the key on its own slider: move it, read it back
    s = p.sliders[keys.index("clean_lights")]
    s.setValue(37)
    assert dict(zip(p.adjustment_keys, (x.value() for x in p.sliders)))["clean_lights"] == 37
    assert p.sliders[keys.index("golden_highlights")].value() == 0
    assert p.lab_finish_section._content_layout.itemAt(0).layout() is p.clean_lights_slider_layout
