#!/usr/bin/env python3
"""Right-click the histogram: scale White / Black Point to clip 0 % or 2 %
(spec/histogram-clip-levels.md)."""
import os
import sys

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import clip_guard as cg  # noqa: E402
from core import clip_levels as cl  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402

_KEEP = []
BASE = np.array([0.30, 0.50, 0.75])            # the film base, BGR


# --- the search ---------------------------------------------------------------

def _step(edge):
    """A non-decreasing share: 0 below `edge`, rising past it."""
    return lambda x: 0.0 if x <= edge else 0.01 * (x - edge) / 0.1


@pytest.mark.parametrize("edge", [-20.0, -7.3, 0.0, 4.4, 19.9, 25.0])
def test_largest_within_finds_the_edge(edge):
    for share in (0.0, 0.02):
        x, where = cl.largest_within(_step(edge), share, -20, 20)
        f = _step(edge)
        assert f(x) <= share or where == "bottom"
        if where == "":
            assert f(round(x + 0.1, 6)) > share
        if edge >= 20:
            assert where == "top" and x == 20


@pytest.mark.parametrize("x0", [-20, -12.3, -3.0, 0.0, 6.6, 20])
def test_refine_gives_the_same_answer_from_any_guess(x0):
    for edge in (-20.0, -9.1, 3.3, 14.8, 25.0):
        for share in (0.0, 0.02):
            calls = []
            f = _step(edge)

            def g(x):
                calls.append(x)
                return f(x)
            assert cl.refine(g, share, x0, -20, 20) == cl.largest_within(f, share, -20, 20)


def test_solve_corrects_white_after_black_lifts_the_highlights():
    """Black Point's remap lifts the highlights a little: White is solved
    again at the new Black Point."""
    def measure(white, black):
        black_share = max(0.0, 0.004 * (black - 3.0))
        white_share = max(0.0, 0.004 * (white + 0.3 * max(black, 0.0) - 8.0))
        return black_share, white_share
    for share in (0.0, 0.02):
        for coarse in (None, measure):
            w, b, info = cl.solve(measure, share, (-20, 20), (-20, 20), coarse=coarse)
            bs, ws = measure(w, b)
            assert bs <= share and ws <= share
            assert measure(round(w + 0.1, 6), b)[1] > share
            assert measure(w, round(b + 0.1, 6))[0] > share


def test_the_words():
    info = {"white_limit": "", "black_limit": "", "white_share": 0.0, "black_share": 0.0}
    assert cl.describe(2.3, 0.8, 0.0, info) == (
        "White Point 2.3 · Black Point 0.8: the picture just reaches white and black.")
    assert cl.describe(10, -1.5, 0.02, info).endswith("2 % clips at each end.")
    info = {"white_limit": "bottom", "black_limit": "", "white_share": 0.004,
            "black_share": 0.0}
    assert "White Point is at the end of its range: 0.4 % still clips." in \
        cl.describe(-20, 0, 0.0, info)
    info = {"white_limit": "top", "black_limit": "", "white_share": 0.0,
            "black_share": 0.0}
    assert "the picture doesn't reach white" in cl.describe(20, 0, 0.0, info)


# --- on a real frame --------------------------------------------------------------

def _converted_frame(tmp_path, monkeypatch):
    from core.ccr_image import CCRImage
    h, w = 160, 240
    rng = np.random.default_rng(3)
    t = np.linspace(0.04, 0.85, w, dtype=np.float32)[None, :, None]
    neg = (BASE * t * (1.0 + 0.03 * rng.standard_normal((h, w, 1)))).astype(np.float32)
    path = str(tmp_path / "neg.tif")
    cv2.imwrite(path, (np.clip(neg, 0, 1) * 65535).astype(np.uint16))
    img = CCRImage(path)
    monkeypatch.setattr(ccr_backend, "images", [img])
    monkeypatch.setattr(ccr_backend, "file_paths", [path])
    monkeypatch.setattr(ccr_backend, "black_point_bgr", tuple(BASE * 65535))
    monkeypatch.setattr(ccr_backend, "white_point_bgr", None)
    if hasattr(ccr_backend, "auto_black_point"):
        monkeypatch.setattr(ccr_backend, "auto_black_point", False)
    ccr_backend.apply_bwpoint_to_all_images()
    assert img.converted
    return img


def _shares(img, white, black):
    st = dict(img.adjustment_settings)
    st["white_point"], st["black_point"] = white, black
    out = img.apply_adjustments(img.resized_raw, settings=st,
                                auto_gain_override=img._auto_gain_for_base(img.resized_raw),
                                skip_dust=True)
    b, w, total = cg.clip_counts(out, img.clip_area())
    return b / total, w / total


def test_on_a_frame_zero_just_reaches_white_and_black(tmp_path, monkeypatch):
    img = _converted_frame(tmp_path, monkeypatch)
    white, black, info = img.solve_clip_levels(0.0, (-20, 20), (-20, 20))
    b, w = _shares(img, white, black)
    assert b == 0 and w == 0
    if info["white_limit"] == "":
        assert _shares(img, round(white + 0.1, 6), black)[1] > 0
    if info["black_limit"] == "":
        assert _shares(img, white, round(black + 0.1, 6))[0] > 0


def test_on_a_frame_two_percent_clips_two_percent(tmp_path, monkeypatch):
    img = _converted_frame(tmp_path, monkeypatch)
    white, black, info = img.solve_clip_levels(0.02, (-20, 20), (-20, 20))
    b, w = _shares(img, white, black)
    assert b <= 0.02 and w <= 0.02
    if info["white_limit"] == "":
        assert w >= 0.01                         # this frame's highlights are smooth
        assert _shares(img, round(white + 0.1, 6), black)[1] > 0.02
    if info["black_limit"] == "":
        assert _shares(img, white, round(black + 0.1, 6))[0] > 0.02


def test_the_area_is_the_crop_turned_inward(tmp_path, monkeypatch):
    img = _converted_frame(tmp_path, monkeypatch)
    img.crop_rect, img.crop_angle = (0.1, 0.2, 0.9, 0.8), 0.0
    assert img.clip_area() == pytest.approx((0.1, 0.2, 0.9, 0.8))
    img.crop_angle = 2.0
    x1, y1, x2, y2 = img.clip_area()
    assert 0.1 < x1 < x2 < 0.9 and 0.2 < y1 < y2 < 0.8
    img.crop_rect = None
    img.conversion_inputs["picture"] = (0.05, 0.05, 0.95, 0.95)
    assert img.clip_area() == pytest.approx((0.05, 0.05, 0.95, 0.95))


# --- the panel ---------------------------------------------------------------------

@pytest.fixture
def win(tmp_path, monkeypatch):
    from ui.main_window import MainWindow
    _converted_frame(tmp_path, monkeypatch)
    w = MainWindow()
    _KEEP.append(w)
    w.resize(1400, 900)
    w.show()
    w.image_preview.update_preview(0)
    w.sliders_panel.set_current_idx(0)
    _app.processEvents()
    hints = []
    monkeypatch.setattr(w.sliders_panel, "set_temporary_hint",
                        lambda t, duration=0: hints.append(t))
    w._hints = hints
    return w


def test_the_menu_sets_both_sliders_as_one_undo_step(win):
    sp = win.sliders_panel
    img = ccr_backend.images[0]
    texts = [a.text() for a in sp.histogram_menu().actions()]
    assert texts == ["Scale Sliders to Clip 0 %", "Scale Sliders to Clip 2 %"]
    undo0 = len(img.undo_stack)
    sp.histogram_menu().actions()[1].trigger()
    _app.processEvents()
    st = img.adjustment_settings
    wsl = sp.sliders[sp.adjustment_keys.index("white_point")]
    bsl = sp.sliders[sp.adjustment_keys.index("black_point")]
    assert wsl.value() == pytest.approx(st["white_point"])
    assert bsl.value() == pytest.approx(st["black_point"])
    assert st["white_point"] != 0 or st["black_point"] != 0
    assert len(img.undo_stack) == undo0 + 1
    assert "White Point" in win._hints[-1] and "Black Point" in win._hints[-1]


def test_refused_while_an_area_layer_is_edited(win):
    sp = win.sliders_panel
    img = ccr_backend.images[0]
    img.active_area_id = "a1"
    try:
        assert sp.scale_to_clip(0.0) is None
        assert "Whole Image" in win._hints[-1]
    finally:
        img.active_area_id = None
