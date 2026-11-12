"""Highlights / Shadows reach +-150; White / Black Point are fine +-20
sliders in tenths, Black Point shown reversed (+ lifts the blacks)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import ccr_processor as cp  # noqa: E402


def _ramp():
    x = np.linspace(0.0, 1.0, 4097, dtype=np.float32)
    return np.stack([x, x, x], axis=-1)[None] * 65535.0


def _single_pass(img, h, s):
    """The old one-pass formula, for the byte-identical check."""
    x = img / 65535.0
    om = 1.0 - x
    wh = (x ** 3) * om / cp.HS_PEAK
    ws = x * (om ** 3) / cp.HS_PEAK
    x = x + (h / 100.0) * cp.HS_STRENGTH * wh + (s / 100.0) * cp.HS_STRENGTH * ws
    return np.clip(x, 0.0, 1.0) * 65535.0


@pytest.mark.parametrize("h,s", [(100, 0), (-100, 0), (0, 100), (0, -100),
                                 (37, -62), (-100, 100)])
def test_within_100_is_unchanged(h, s):
    img = _ramp()
    assert np.array_equal(cp._apply_highlights_shadows(img, h, s),
                          _single_pass(img, h, s))


# Opposite-sign pairs (Highlights down + Shadows up) are left out: at full
# strength together they already fold mid-greys at +-100, before this change.
@pytest.mark.parametrize("h,s", [(-150, 0), (150, 0), (0, 150), (0, -150),
                                 (150, 150), (-150, -150)])
def test_150_stays_monotonic(h, s):
    y = cp._apply_highlights_shadows(_ramp(), h, s)[0, :, 0]
    assert np.all(np.diff(y) >= -1e-3), "tone reversal"
    # The single pass at these amounts is NOT monotonic for the cases that
    # used to fold - the reason for the second pass.
    if (h, s) in ((-150, 0), (0, 150)):
        y1 = _single_pass(_ramp(), h, s)[0, :, 0]
        assert np.any(np.diff(y1) < -1.0)


def test_150_goes_further_than_100():
    img = _ramp()
    mid = 2048
    assert (cp._apply_highlights_shadows(img, -150, 0)[0, mid, 0]
            < cp._apply_highlights_shadows(img, -100, 0)[0, mid, 0])
    assert (cp._apply_highlights_shadows(img, 0, 150)[0, 1024, 0]
            > cp._apply_highlights_shadows(img, 0, 100)[0, 1024, 0])


# --- UI ---------------------------------------------------------------------
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt  # noqa: E402
from widgets.sliders_panel import FineSlider, shown_value, paste_options  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _fine(sign):
    sl = FineSlider(Qt.Horizontal, sign=sign)
    sl.setMinimum(-200)
    sl.setMaximum(200)
    return sl


def test_fine_slider_tenths(app):
    sl = _fine(1)
    sl.setValue(3.7)
    assert sl.value() == 3.7 and sl.value_text() == "3.7"
    sl.setValue(5)
    assert sl.value() == 5 and isinstance(sl.value(), int)
    assert sl.value_range() == (-20, 20)


def test_black_point_shows_reversed(app):
    sl = _fine(-1)
    sl.setValue(4.5)                 # stored: crush the blacks
    assert sl.value_text() == "-4.5"
    assert sl.value() == 4.5         # the stored meaning is unchanged
    QtWidgets.QSlider.setValue(sl, 30)   # drag to shown +3: lift the blacks
    assert sl.value() == -3 and sl.value_text() == "3"
    assert shown_value("black_point", 4.5) == -4.5


def test_old_out_of_range_value_kept_until_moved(app):
    sl = _fine(-1)
    sl.setValue(60)                  # from the old +-100 slider
    assert sl.value() == 60          # kept exactly; another slider moving
    QtWidgets.QSlider.setValue(sl, 0)    # can't change it, this one can
    assert sl.value() == 0


def test_paste_dialog_shows_black_point_reversed():
    rows = paste_options({"adjustments": {"black_point": 5}}, lambda k: 0)
    assert rows and rows[0][2].endswith("(-5)")
