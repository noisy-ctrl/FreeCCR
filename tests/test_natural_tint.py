#!/usr/bin/env python3
"""Natural tint (spec/natural-tint.md)."""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import natural_tint as nt  # noqa: E402
from core.oklab import to_oklab  # noqa: E402


def _toned_ramp(H=60, W=800, warm=0.04, cool=0.03):
    """A colour ramp: cool (bluish) shadows, warm highlights, as a toned B&W
    film scan; and its grey."""
    from core.oklab import from_oklab
    L = np.linspace(0.05, 0.98, W, dtype=np.float32)
    lab = np.zeros((H, W, 3), np.float32)
    lab[..., 0] = L
    t = (L - 0.5)
    lab[..., 1] = np.where(t > 0, 0.4 * warm * t * 2, -0.2 * cool * (-t) * 2)
    lab[..., 2] = np.where(t > 0, warm * t * 2, -cool * (-t) * 2)
    colour = np.clip(from_oklab(lab), 0, 1).astype(np.float32)
    g = (colour @ np.array([0.299, 0.587, 0.114], np.float32))[..., None]
    grey = np.repeat(g, 3, axis=2).astype(np.float32)
    return grey, colour


def test_zero_is_the_grey_itself():
    grey, colour = _toned_ramp()
    assert nt.apply(grey, colour, 0) is grey


def test_the_tint_comes_back_by_tone_and_lightness_stays():
    grey, colour = _toned_ramp()
    half = nt.apply(grey, colour, 50)
    full = nt.apply(grey, colour, 100)
    lab_g, lab_h, lab_f = (to_oklab(x[30]) for x in (grey, half, full))
    hi, lo = 700, 120
    assert lab_f[hi, 2] > 0.01 and lab_f[lo, 2] < -0.005       # warm top, cool bottom
    assert abs(lab_f[hi, 2]) > abs(lab_h[hi, 2]) > 0
    assert np.abs(lab_f[:, 0] - lab_g[:, 0]).max() < 2e-3        # lightness kept
    assert full.min() >= 0 and full.max() <= 1
    # pure black and white stay neutral
    ends = np.zeros((2, 4, 3), np.float32)
    ends[1] = 1.0
    out = nt.apply(ends, colour, 100)
    assert np.abs(out - ends).max() < 1e-4
    # a ramp stays monotone in lightness
    assert np.all(np.diff(lab_f[:, 0]) > -1e-5)


def test_a_small_coloured_object_hardly_moves_it():
    grey, colour = _toned_ramp()
    base = nt.tint_curve(colour)
    c2 = colour.copy()
    c2[20:40, 400:440] = (0.9, 0.05, 0.05)                    # a red sign
    after = nt.tint_curve(c2)
    assert np.abs(after[1] - base[1]).max() < 0.01


def test_grain_colour_does_not_come_back():
    rng = np.random.default_rng(3)
    grey, colour = _toned_ramp()
    noisy = np.clip(colour + rng.normal(0, 0.03, colour.shape).astype(np.float32), 0, 1)
    g = (noisy @ np.array([0.299, 0.587, 0.114], np.float32))[..., None]
    noisy_grey = np.repeat(g, 3, axis=2)
    out = nt.apply(noisy_grey, noisy, 100)
    lab_in, lab_out = to_oklab(noisy), to_oklab(out)
    chroma_in = np.hypot(lab_in[..., 1] - lab_in[..., 1].mean(0),
                         lab_in[..., 2] - lab_in[..., 2].mean(0)).std()
    chroma_out = np.hypot(lab_out[..., 1] - lab_out[..., 1].mean(0),
                          lab_out[..., 2] - lab_out[..., 2].mean(0)).std()
    assert chroma_out < 0.2 * chroma_in


def test_export_keeps_colour_and_the_render_uses_it(tmp_path):
    import cv2
    from core.ccr_image import CCRImage
    from core.ccr_processor import renders_monochrome
    p = tmp_path / "a.png"
    grey, colour = _toned_ramp(H=120, W=300)
    cv2.imwrite(str(p), (colour[..., ::-1] * 255).astype(np.uint8))
    img = CCRImage(str(p))
    img.color_profile = "bw"
    assert renders_monochrome(img)
    base = img.resized_raw.copy()
    a = img.apply_adjustments(base.copy())
    assert np.all(a[..., 0] == a[..., 1])                     # plain B&W
    img.adjustment_settings["natural_tint"] = 100
    assert not renders_monochrome(img)
    b = img.apply_adjustments(base.copy())
    assert np.abs(b[..., 0].astype(int) - b[..., 2].astype(int)).max() > 200
    img.decoded_mono = True                                    # a mono decode: none
    assert renders_monochrome(img)


def test_panel_swaps_the_rows_and_stores_it(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import cv2
    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    from widgets.sliders_panel import SlidersPanel, SYNC_GROUPS
    p = tmp_path / "a.png"
    cv2.imwrite(str(p), np.full((40, 60, 3), 120, np.uint8))
    img = CCRImage(str(p))
    monkeypatch.setattr(ccr_backend, "images", [img])
    panel = SlidersPanel()
    panel.show()
    panel.set_current_idx(0)
    org = panel.colour_organics_slider_layout.itemAt(0).widget()
    tint = panel.natural_tint_slider_layout.itemAt(0).widget()
    assert org.isVisible() and not tint.isVisible()
    panel.color_profile_combo.setCurrentIndex(1)              # B&W
    assert img.color_profile == "bw"
    assert tint.isVisible() and not org.isVisible()
    i = panel.adjustment_keys.index("natural_tint")
    assert panel.adjustment_keys[i - 1] == "colour_organics"
    assert panel.sliders[i].value() == 0                       # off by default
    panel.sliders[i].setValue(40)
    assert img.adjustment_settings["natural_tint"] == 40
    panel.color_profile_combo.setCurrentIndex(0)
    assert org.isVisible() and not tint.isVisible()
    assert "natural_tint" in dict((g, k) for g, _l, k in SYNC_GROUPS)["sat"]
