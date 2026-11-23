#!/usr/bin/env python3
"""The per-shot exposure estimate learns from each shot it made
(spec/frame-exposure.md, revision 2026-11-23)."""
import math
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import base_exposure as be  # noqa: E402
from core import capture_log as cl  # noqa: E402
from core import exposure_learn as el  # noqa: E402
from core import frame_meter as fm  # noqa: E402
from test_film_base_shoot import film_mon, _wait  # noqa: E402,F401
from test_frame_exposure import GUIDE, _negative, _noisy, _with_base  # noqa: E402


# --- the sums --------------------------------------------------------------------

def test_the_error_is_the_largest_colours():
    pred = el.predicted(0.8, 0.25, 2.0, 0.5)              # 0.8 x 0.25 x 4
    assert pred == pytest.approx(0.8)
    assert el.colour_error(0.4, pred) == pytest.approx(-1.0)
    errs = {"r": -1.0, "g": 0.25}
    assert el.shot_error(errs, ["r", "g", "b"]) is None    # blue not read yet
    errs["b"] = -0.5
    assert el.shot_error(errs, ["r", "g", "b"]) == pytest.approx(0.25)


def test_what_says_nothing():
    assert el.colour_error(0.01, 0.5) is None              # too dark to read
    assert el.colour_error(0.9, 0.05) is None              # > 3 stops: another frame
    assert el.colour_error(None, 0.5) is None
    assert el.predicted(0.8, None, 1.0, 1.0) is None
    assert el.predicted(0.8, 0.3, 1.0, 0) is None


def test_the_correction_shrinks_with_few_shots_and_is_clamped():
    assert el.correction([]) == 0.0
    assert el.correction([0.6]) == pytest.approx(0.3)      # one shot: half way
    assert el.correction([0.6, 0.6, 0.6]) == pytest.approx(0.45)
    assert el.correction([0.4, 0.5, 3.0]) == pytest.approx(0.375)   # the median, not the mean
    assert el.correction([3.0] * 12) == el.MAX_BIAS
    assert el.correction([-3.0] * 12) == -el.MAX_BIAS


def test_the_store_keeps_twelve_per_setup_and_forgets():
    box = {"t": ""}
    st = el.Store(lambda: box["t"], lambda t: box.__setitem__("t", t))
    key = el.setup_key("GFX", "trichrome")
    other = el.setup_key("GFX", "bw", "g")
    assert other == "GFX|bw|g" and el.setup_key("GFX", "colour", "g") == "GFX|colour"
    for i in range(15):
        st.add(key, 0.1 * i)
    st.add(other, -0.4)
    assert len(st.errors(key)) == el.KEEP and st.errors(key)[0] == pytest.approx(0.3)
    again = el.Store(lambda: box["t"], lambda t: box.__setitem__("t", t))
    b, n = again.bias(key)
    assert n == 12 and b == pytest.approx(statistics_median(st.errors(key)) * 12 / 13)
    again.forget(key)
    assert again.errors(key) == [] and again.errors(other) == [-0.4]
    box["t"] = "not json"
    assert again.bias(key) == (0.0, 0)


def statistics_median(v):
    import statistics
    return statistics.median(v)


def test_live_levels_take_the_correction():
    base = be.entry("P", {k: {"shutter": "1/4", "iso": "100", "base": 0.8, "levels": None}
                          for k in be.COLOURS})
    plain = fm.live_levels(base, True, 0.25)
    up = fm.live_levels(base, True, 0.25, bias=1.0)
    assert plain == {k: pytest.approx(0.2) for k in be.COLOURS}
    assert up == {k: pytest.approx(0.4) for k in be.COLOURS}


# --- in Capture ------------------------------------------------------------------

def _estimating(mon, ratio=0.25):
    _with_base(mon)
    mon.actions["guide"].setChecked(True)
    mon.set_guide_auto(False)
    mon.canvas.guide = dict(GUIDE)
    mon.feed.full = _noisy(_negative(frame_lin_ratio=ratio))
    mon._update_frame_estimate()
    assert mon._frame_est


def _shoot_and_finish(mon, usb):
    mon._shutter_is_usb = lambda: True
    mon._ensure_hot_folder = lambda: True
    usb.dest = usb.dest or mon._usb_dest
    usb.fires.clear()
    mon.shoot()
    assert _wait(lambda: not mon.sequence.running, 20)


def _files(n=1):
    return {k: cl.shot_stem("R0050", "35mm", "Gold", n, slot) + ".raf"
            for k, slot in (("r", "red"), ("g", "green"), ("b", "blue"))}


def test_a_shot_teaches_the_next_estimate(film_mon, monkeypatch):
    mon, usb, light, lm, path = film_mon
    _estimating(mon)
    mon.frame_auto.setChecked(True)
    stops_before = dict(mon._frame_suggestion.stops)
    _shoot_and_finish(mon, usb)
    learn = mon._learn_shot
    assert learn and learn["colours"] == ["r", "g", "b"] and learn["ratio"] > 0
    # the frame landed brighter than predicted: red half a stop, the others less
    want = {"r": 0.5, "g": 0.2, "b": -0.1}
    reads = []

    def thinnest(p, guide):
        reads.append(os.path.basename(p))
        k = {"red": "r", "green": "g", "blue": "b"}[cl.name_slot(p)[2]]
        pred = el.predicted(learn["base"][k], learn["ratio"], learn["planned_e"][k],
                            learn["base_e"][k])
        ch = {"r": "R", "g": "G", "b": "B"}[k]
        return be.Reading(levels={ch: pred * 2 ** want[k]})
    mon._clip_threaded = False
    mon._read_clipping = lambda p, g: {}
    mon._read_thinnest = thinnest
    folder = usb.dest
    for k, name in _files().items():
        mon._clip_check(os.path.join(folder, name))
        _app.processEvents()
    assert len(reads) == 3
    key = mon._learn_key()
    assert mon._exposure_learn.errors(key) == [pytest.approx(0.5, abs=1e-3)]
    assert mon._learn_shot is None                       # learned once
    bias, n = mon._learned_bias()
    assert n == 1 and bias == pytest.approx(0.25, abs=1e-3)
    # the next estimate expects the frame brighter: shorter exposures
    mon._update_frame_estimate()
    after = mon._frame_suggestion.stops
    assert all(after[k] < stops_before[k] + 1e-6 for k in after)
    assert "corrected" in mon.frame_note.text() and "from 1 shot)" in mon.frame_note.text()
    rec = mon._estimate_record()
    assert rec["learned"] == {"stops": pytest.approx(0.25, abs=1e-3), "shots": 1}
    # Forget
    menu = mon.frame_auto_menu()
    (act,) = menu.actions()
    assert act.text() == "Forget What It Learned (1 shot)" and act.isEnabled()
    act.trigger()
    assert mon._learned_bias() == (0.0, 0)
    assert not mon.frame_auto_menu().actions()[0].isEnabled()
    mon._update_frame_estimate()
    assert "corrected" not in mon.frame_note.text()


def test_one_raw_read_serves_both_checks(film_mon, monkeypatch):
    mon, usb, light, lm, path = film_mon
    _estimating(mon)
    mon.frame_auto.setChecked(True)
    _shoot_and_finish(mon, usb)
    calls = []

    def read_shot(p, guide):
        calls.append(os.path.basename(p))
        return {}, be.Reading(levels={"R": 0.5, "G": 0.5, "B": 0.5})
    monkeypatch.setattr(fm, "read_shot", read_shot)
    mon._clip_threaded = False
    name = _files()["r"]
    mon._clip_check(os.path.join(usb.dest, name))
    _app.processEvents()
    assert calls == [name]
    assert "r" in mon._learn_errs


def test_a_shot_that_cant_teach_learns_nothing(film_mon):
    mon, usb, light, lm, path = film_mon
    _with_base(mon)
    # no estimate: nothing to measure against
    mon.actions["guide"].setChecked(True)
    mon.canvas.guide = dict(GUIDE)
    mon._frame_est = []
    assert mon._learn_context(be.exposures_for(mon._film_base(), True), True) is None
    # the Exposure row's settings (no exposures): the LED isn't the base's
    _estimating(mon)
    assert mon._learn_context(None, True) is None
    assert mon._learn_context(be.exposures_for(mon._film_base(), True), True) is not None
    # a colour too dark to read: the shot doesn't count
    mon.frame_auto.setChecked(True)
    _shoot_and_finish(mon, usb)
    mon._clip_threaded = False
    mon._read_clipping = lambda p, g: {}
    mon._read_thinnest = lambda p, g: be.Reading(levels={"R": 0.001, "G": 0.5, "B": 0.5})
    mon._clip_check(os.path.join(usb.dest, _files()["r"]))
    _app.processEvents()
    assert mon._learn_shot is None
    assert mon._exposure_learn.errors(mon._learn_key()) == []
    # the IR file is never read for it
    assert mon._learn_colour(cl.shot_stem("R0050", "35mm", "Gold", 1, "ir") + ".raf") is None
