#!/usr/bin/env python3
"""Right-click Set Black Point / Set White Point: Auto and Reset
(spec/bw-point-menu.md)."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.ccr_backend import ccr_backend  # noqa: E402
from test_auto_black_point import frame, BASE  # noqa: E402
from test_tether_start_window import mw  # noqa: E402,F401


@pytest.fixture
def panel(mw, monkeypatch):
    w, asked, answer, saves, open_images, roll = mw
    open_images(1)
    w.thumbnail_list.thumbnail_list.setCurrentRow(0)
    _app.processEvents()
    img = ccr_backend.images[0]
    p = w.sliders_panel
    hints = []
    monkeypatch.setattr(p, "set_temporary_hint", lambda m, duration=0: hints.append(m))
    removed, stored = [], {}
    monkeypatch.setattr(w._settings, "remove", lambda k: removed.append(k))
    monkeypatch.setattr(w._settings, "setValue", lambda k, v: stored.__setitem__(k, v))
    converts = []
    monkeypatch.setattr(p, "_on_convert_current_bwpoint", lambda: converts.append(1))
    ccr_backend.black_point_bgr = None
    yield p, img, hints, removed, stored, converts, monkeypatch
    ccr_backend.black_point_bgr = None


def test_auto_black_point_from_a_bordered_frame(panel):
    p, img, hints, removed, stored, converts, monkeypatch = panel
    monkeypatch.setattr(ccr_backend, "raw_preview", lambda im: frame())
    res = p._auto_black_point_here()
    assert res.confidence == "high"
    assert np.allclose(ccr_backend.black_point_bgr, BASE, rtol=0.03)
    assert "convert/black_point_bgr" in stored
    assert "border" in hints[-1] and not converts           # the conversion is left alone


def test_no_border_sets_nothing(panel):
    p, img, hints, removed, stored, converts, monkeypatch = panel
    ccr_backend.black_point_bgr = (1.0, 2.0, 3.0)
    monkeypatch.setattr(ccr_backend, "raw_preview", lambda im: frame(edges=()))
    p._auto_black_point_here()
    assert ccr_backend.black_point_bgr == (1.0, 2.0, 3.0)
    assert hints[-1].startswith("No Black Point set")


def test_reset_black_point_sticks(panel):
    p, img, hints, removed, stored, converts, monkeypatch = panel
    assert not p._bw_point_menu_actions("black")[1][2]      # nothing to reset
    ccr_backend.black_point_bgr = (1.0, 2.0, 3.0)
    assert p._bw_point_menu_actions("black")[1][2]
    p._reset_black_point()
    assert ccr_backend.black_point_bgr is None
    assert "convert/black_point_bgr" in removed


def test_white_point_auto_and_reset(panel):
    p, img, hints, removed, stored, converts, monkeypatch = panel
    assert not p._bw_point_menu_actions("white")[1][2]      # nothing of its own
    img.wp_sample, img.wp_sample_rect, img.wp_choice = (1.0, 1.0, 1.0), (0, 0, 1, 1), "sampled"
    assert p._bw_point_menu_actions("white")[1][2]
    p._reset_white_point()
    assert img.wp_sample is None and img.wp_choice is None
    assert not converts and "when you convert" in hints[-1]  # not converted yet
    img.converted = True
    p._auto_white_point_here()
    assert img.wp_choice == "frame" and converts == [1]       # converted: again now
    img.converted = False
