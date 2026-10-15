#!/usr/bin/env python3
"""The white point: the brightest NEAR-GREY area, and a choice per frame
(spec/white-point-choice.md)."""

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
sys.path.insert(0, HERE)

from core import auto_white_point as awp  # noqa: E402

BASE = np.array([30000.0, 26000.0, 21000.0])
GAMMA = np.array([1.0, 1.05, 1.10])          # this film's channels: its neutral
RECT = (0.1, 0.1, 0.9, 0.9)
NEUTRAL = awp.ratio(GAMMA)
WALL = (0.2, 0.45, 0.45, 0.7)                # normalised rect of the grey wall


def negative(bright=(0.97, 1.15, 0.92), h=400, w=600, seed=0):
    """A scene in log-exposure per channel: texture 0..0.6, a grey wall at
    0.9 and a smaller, brighter area of colour `bright` (green foliage by
    default). Density = gamma x scene, per channel."""
    rng = np.random.default_rng(seed)
    s = np.repeat((rng.random((h, w)) * 0.6)[..., None], 3, axis=2)
    x1, y1, x2, y2 = WALL
    s[int(y1 * h):int(y2 * h), int(x1 * w):int(x2 * w)] = 0.9
    s[int(.55 * h):int(.62 * h), int(.6 * w):int(.75 * w)] = bright
    D = s * GAMMA
    raw = BASE / 10.0 ** D
    y0, y9, x0, x9 = int(0.1 * h), int(0.9 * h), int(0.1 * w), int(0.9 * w)
    raw[:y0], raw[y9:], raw[:, :x0], raw[:, x9:] = BASE, BASE, BASE, BASE
    return raw.astype(np.uint16)


def _dev(ratio_, ref):
    d = np.asarray(ratio_) - np.asarray(ref)
    return float(d @ awp._UT), float(d @ awp._UG)


# --- the near-grey pick ---------------------------------------------------------------

def test_the_grey_wall_is_white_not_the_brighter_foliage():
    hl = awp.measure_highlights(negative(), BASE, RECT)
    t, g = _dev(awp.ratio(hl.dmax), NEUTRAL)
    assert g > 0.05                                      # the brightest area is green
    neutral, areas = awp.pick_neutral(hl, NEUTRAL)
    assert neutral is not None
    assert max(abs(v) for v in _dev(neutral, NEUTRAL)) < 0.01   # the wall: grey
    x1, y1, x2, y2 = WALL                                # its outline is on the wall
    for a in areas:
        assert x1 - 0.02 <= a[0] and a[2] <= x2 + 0.02
        assert y1 - 0.02 <= a[1] and a[3] <= y2 + 0.02
    used, info = awp.frame_white_dmax(hl, NEUTRAL)
    assert info["neutral"] is not None
    assert max(abs(v) for v in _dev(awp.ratio(used), NEUTRAL)) < 0.01   # no magenta
    old, _ = awp.guarded_dmax(hl.dmax, NEUTRAL)          # the old rule: the foliage
    assert _dev(awp.ratio(old), NEUTRAL)[1] > 0.05
    assert np.mean(used) == pytest.approx(hl.level)       # the level is unchanged


def test_a_sunset_keeps_its_warmth():
    """Every bright area is warm against the roll: the guard still caps what
    is removed, near-grey pick or not."""
    warm = (1.0, 0.9, 0.75)
    raw = negative(bright=warm)
    h, w = raw.shape[:2]
    hl = awp.measure_highlights(raw, BASE, RECT)
    used, info = awp.frame_white_dmax(hl, NEUTRAL)
    removed_temp = _dev(awp.ratio(used), NEUTRAL)[0]
    assert removed_temp <= awp.TEMP_ROLL + 1e-6


def test_no_roll_is_the_previous_rule():
    hl = awp.measure_highlights(negative(), BASE, RECT)
    assert awp.pick_neutral(hl, None) == (None, [])
    used, info = awp.frame_white_dmax(hl, None)
    old, _ = awp.guarded_dmax(hl.dmax, None)
    assert used == pytest.approx(old) and info["ref"] == "default" and info["areas"] == []


def test_roll_average_is_the_roll_at_the_frames_level():
    used, info = awp.roll_white_dmax(1.2, NEUTRAL)
    assert awp.ratio(used) == pytest.approx(NEUTRAL) and np.mean(used) == pytest.approx(1.2)
    used, info = awp.roll_white_dmax(1.2, None)
    assert awp.ratio(used) == pytest.approx(np.ones(3)) and info["ref"] == "default"


# --- the backend: choices ---------------------------------------------------------------

class _Img:
    def __init__(self, raw, name, choice=None):
        self.resized_raw = raw
        self.converted = False
        self.fine_rotation_angle = 0
        self.conversion_inputs = None
        self.file_path = name
        self.adjustment_settings = {}
        self.crop_rect = RECT
        self.wp_choice = choice
        self.wp_sample = None
        self.wp_sample_rect = None

    def reload_image(self):
        pass

    def update_thumbnail_and_preview(self):
        pass


@pytest.fixture
def backend(monkeypatch):
    import core.ccr_backend as mod
    from core.ccr_backend import ccr_backend
    from core.auto_black_point import RebateResult, HIGH
    for k, v in (("black_point_bgr", None), ("white_point_bgr", None),
                 ("film_stock_slopes", None), ("density_bwpoint", True),
                 ("auto_awb", False), ("auto_black_point", True),
                 ("auto_white_point", True), ("auto_tone", False)):
        monkeypatch.setattr(ccr_backend, k, v, raising=False)
    monkeypatch.setattr(ccr_backend, "detect_frame_black_point",
                        lambda img, reload=True: RebateResult(tuple(BASE), HIGH, side="top"))
    calls = {}

    def fake(img, black, white, density=False, slopes_bgr=None):
        calls[img.file_path] = (white, slopes_bgr)
        return img.resized_raw

    monkeypatch.setattr(mod, "ccr_normalize_with_bwpoint", fake)
    ccr_backend._calls = calls
    return ccr_backend


def test_convert_all_honours_each_frames_choice(backend, monkeypatch):
    imgs = [_Img(negative(seed=i), f"f{i}.RAF") for i in range(4)]
    imgs[1].wp_choice = "roll"
    imgs[2].wp_choice = "off"
    imgs[3].wp_choice = "sampled"
    imgs[3].wp_sample = (900.0, 800.0, 700.0)
    imgs[3].wp_sample_rect = (0.3, 0.3, 0.35, 0.35)
    monkeypatch.setattr(backend, "images", imgs)
    backend.apply_bwpoint_to_all_images()
    rec = [i.conversion_inputs["auto_wp"] for i in imgs]
    assert [r["choice"] for r in rec] == ["frame", "roll", "off", "sampled"]
    assert rec[0]["neutral"] is not None and rec[0]["areas"]           # near-grey found
    assert backend._calls["f1.RAF"][0] is not None
    assert backend._calls["f2.RAF"][0] is None                          # off: default slope
    assert backend._calls["f3.RAF"][0] == (900.0, 800.0, 700.0)
    assert rec[3]["areas"] == [[0.3, 0.3, 0.35, 0.35]]
    assert all("dmax" in r for r in rec)                                # the roll can use them


def test_a_film_stock_beats_frame_and_roll_but_not_a_hand_sample(backend, monkeypatch):
    imgs = [_Img(negative(seed=i), f"g{i}.RAF") for i in range(3)]
    imgs[1].wp_choice = "roll"
    imgs[2].wp_choice = "sampled"
    imgs[2].wp_sample = (900.0, 800.0, 700.0)
    monkeypatch.setattr(backend, "images", imgs)
    monkeypatch.setattr(backend, "film_stock_slopes", (0.8, 0.7, 0.6))
    backend.apply_bwpoint_to_all_images()
    assert backend._calls["g0.RAF"] == (None, (0.8, 0.7, 0.6))
    assert backend._calls["g1.RAF"] == (None, (0.8, 0.7, 0.6))
    assert backend._calls["g2.RAF"] == ((900.0, 800.0, 700.0), None)


def test_the_setting_is_the_default_choice(backend, monkeypatch):
    img = _Img(negative(), "h.RAF")
    assert backend.wp_choice_for(img) == "frame"
    monkeypatch.setattr(backend, "auto_white_point", False)
    assert backend.wp_choice_for(img) == "off"
    img.wp_choice = "roll"
    assert backend.wp_choice_for(img) == "roll"
    img.wp_choice = "sampled"                       # a sample that is gone
    assert backend.wp_choice_for(img) == "off"


def test_the_roll_prefers_recorded_near_grey_colours(backend, monkeypatch):
    def done(dmax, neutral):
        i = _Img(None, "x")
        i.converted = True
        i.conversion_inputs = {"auto_wp": {"dmax": list(dmax), "neutral": neutral}}
        return i
    grey = list(NEUTRAL)
    blue = list(awp.ratio(np.asarray(GAMMA) * [0.9, 1.0, 1.15]))
    me = _Img(None, "me")
    monkeypatch.setattr(backend, "images", [me] + [done(blue, grey) for _ in range(3)])
    assert backend.roll_highlight_reference(me) == pytest.approx(NEUTRAL)


def test_the_catalog_keeps_the_choice_and_sample(tmp_path):
    import cv2
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from core import catalog
    from core.ccr_image import CCRImage
    path = str(tmp_path / "scan.png")
    cv2.imwrite(path, np.full((300, 400, 3), 30000, np.uint16))
    cat = str(tmp_path / "catalog.json")
    img = CCRImage(path)
    img.wp_choice, img.wp_sample, img.wp_sample_rect = "sampled", (1.0, 2.0, 3.0), (0.1, 0.2, 0.3, 0.4)
    catalog.update_for_images([img], path=cat)
    back = catalog.create_images_for_path(path, path=cat)[0]
    assert (back.wp_choice, back.wp_sample, back.wp_sample_rect) == \
        ("sampled", (1.0, 2.0, 3.0), (0.1, 0.2, 0.3, 0.4))


# --- the panel ------------------------------------------------------------------------------

@pytest.fixture
def panel(backend, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel
    p = SlidersPanel()
    img = _Img(negative(), "p.RAF")
    monkeypatch.setattr(backend, "images", [img])
    monkeypatch.setattr(backend, "save_catalog", lambda *a, **k: None)
    p.current_idx = 0
    converts = []
    monkeypatch.setattr(p, "_on_convert_current_bwpoint", lambda: converts.append(img.wp_choice))
    p._converts = converts
    return p, img


def test_the_combo_follows_the_frame_and_lists_a_sample_only_when_there_is_one(panel):
    p, img = panel
    p._sync_wp_combo(0)
    assert p.wp_combo.currentData() == "frame"
    sampled = p.wp_combo.model().item(p.wp_combo.findData("sampled"))
    assert not sampled.isEnabled()
    img.wp_choice = "off"
    p._sync_wp_combo(0)
    assert p.wp_combo.currentData() == "off"


def test_changing_the_choice_reconverts_a_converted_frame(panel):
    p, img = panel
    p._sync_wp_combo(0)
    img.converted = True
    p.wp_combo.setCurrentIndex(p.wp_combo.findData("roll"))
    assert img.wp_choice == "roll" and p._converts == ["roll"]
    img.converted = False
    p.wp_combo.setCurrentIndex(p.wp_combo.findData("off"))
    assert img.wp_choice == "off" and p._converts == ["roll"]      # kept for Convert


def test_set_white_point_is_this_frames_and_converts_it(panel, backend):
    p, img = panel
    p.on_white_point_sampled((900.0, 800.0, 700.0), (0.3, 0.3, 0.4, 0.4))
    assert img.wp_sample == (900.0, 800.0, 700.0) and img.wp_choice == "sampled"
    assert backend.white_point_bgr is None                       # not roll-wide
    assert p._converts == ["sampled"]
    assert p.wp_combo.currentData() == "sampled"
    assert p.wp_combo.model().item(p.wp_combo.findData("sampled")).isEnabled()
    img.conversion_inputs = {"bw": (tuple(BASE), (900.0, 800.0, 700.0))}
    assert p._film_stock_pair() == (tuple(BASE), (900.0, 800.0, 700.0))
    p._on_clear_white_point()                                    # ✕
    assert img.wp_sample is None and img.wp_choice is None
    assert p.wp_combo.currentData() == "frame"


def test_the_roll_wide_white_point_is_dropped_at_start(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from core.ccr_backend import ccr_backend
    from ui import main_window
    s = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    s.setValue("convert/white_point_bgr", "1,2,3")
    holder = type("H", (), {"_settings": s})()
    monkeypatch.setattr(ccr_backend, "white_point_bgr", None)
    main_window.MainWindow._restore_bwpoint(holder)
    assert ccr_backend.white_point_bgr is None
    assert not s.contains("convert/white_point_bgr")
