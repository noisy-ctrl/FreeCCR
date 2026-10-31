#!/usr/bin/env python3
"""The Details (sharpening) defaults (spec/details-defaults.md): 30 / 2 / 0,
changeable in Settings; images that never set their own follow them, edited
ones are written down when saved."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import detail_defaults as dd  # noqa: E402


def test_values_are_cleaned_and_round_trip():
    assert dd.clean(None) == dd.FACTORY
    assert dd.clean({"sharpen_amount": 140, "sharpen_radius": "3.4",
                     "sharpen_masking": "x"}) == {"sharpen_amount": 100,
                                                  "sharpen_radius": 3,
                                                  "sharpen_masking": 0}
    assert dd.decode(dd.encode({"sharpen_amount": 40, "sharpen_radius": 5,
                                "sharpen_masking": 10})) == \
        {"sharpen_amount": 40, "sharpen_radius": 5, "sharpen_masking": 10}
    assert dd.decode("") is None and dd.decode("1,2") is None and dd.decode("a,b,c") is None


def test_an_edited_dict_is_written_down_an_untouched_one_is_not():
    before = dd.current()
    try:
        dd.set_current({"sharpen_amount": 45, "sharpen_radius": 4, "sharpen_masking": 0})
        assert dd.written_down({}) == {}
        assert dd.written_down({"tint": 3}) == {"tint": 3, "sharpen_amount": 45,
                                                "sharpen_radius": 4, "sharpen_masking": 0}
        own = {"tint": 3, "sharpen_amount": 0}
        assert dd.written_down(own) == own                # its own sharpening stays
    finally:
        dd.set_current(before)


def test_an_untouched_image_renders_with_the_defaults(tmp_path):
    import numpy as np
    import cv2
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from core.ccr_image import CCRImage
    rng = np.random.default_rng(1)
    p = str(tmp_path / "f.png")
    cv2.imwrite(p, (rng.random((120, 160, 3)) * 255).astype(np.uint8))
    img = CCRImage(p)
    base = img.resized_raw
    before = dd.current()
    try:
        dd.set_current({"sharpen_amount": 0, "sharpen_radius": 25, "sharpen_masking": 0})
        off = img.apply_adjustments(base.copy())
        dd.set_current({"sharpen_amount": 80, "sharpen_radius": 25, "sharpen_masking": 0})
        on = img.apply_adjustments(base.copy())
        assert np.abs(on.astype(int) - off.astype(int)).max() > 0     # follows the default
        img.adjustment_settings = {"sharpen_amount": 0}              # its own: kept
        own = img.apply_adjustments(base.copy())
        assert np.array_equal(own, off)
    finally:
        dd.set_current(before)
