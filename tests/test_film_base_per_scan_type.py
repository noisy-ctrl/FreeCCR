#!/usr/bin/env python3
"""Film bases per scan type, the B&W light, and the roll's format setting the
frame guide (spec/film-base-per-scan-type.md)."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import base_exposure as be  # noqa: E402
from test_capture_routes import _wait  # noqa: E402
from test_film_base_shoot import film_mon  # noqa: E402,F401

COL = {"shutter": "1/2", "iso": "100", "base": 0.8}
TRI = {"r": COL, "g": COL, "b": COL}
ONE = {"w": COL}


def test_scan_of_and_fits_for_old_and_new_entries():
    old_tri, old_one = be.entry("Portra", TRI), be.entry("Old W", ONE)
    assert be.scan_of(old_tri) == "trichrome" and be.scan_of(old_one) is None
    assert be.fits(old_tri, "trichrome") and not be.fits(old_tri, "bw")
    assert be.fits(old_one, "colour") and be.fits(old_one, "bw")
    assert not be.fits(old_one, "trichrome")
    bw = be.entry("HP5", ONE, scan="bw", light="g")
    assert be.fits(bw, "bw") and not be.fits(bw, "colour")
    assert not be.fits(None, "bw")


def test_scan_and_light_are_kept_through_the_list():
    d = be.add(None, be.entry("HP5", ONE, scan="bw", light="g"))
    b = be.selected(d)
    assert (b["scan"], b["light"]) == ("bw", "g")
    d = be.replace(d, 0, ONE)                       # Measure Again keeps them
    assert (be.selected(d)["scan"], be.selected(d)["light"]) == ("bw", "g")
    d = be.rename(d, 0, "HP5 2")
    d = be.edit(d, 0, "HP5 2", be.selected(d)["colours"])
    assert (be.selected(d)["scan"], be.selected(d)["light"]) == ("bw", "g")
    assert be.summary(be.selected(d), False).endswith("; green light")
    assert "scan" not in be.selected(be.add(None, dict(be.entry("x", ONE), scan="nope")))


@pytest.fixture
def mon(film_mon, monkeypatch):
    m, usb, light, lm, path = film_mon
    from widgets import live_monitor as lmod
    store = {}
    real = lmod._settings

    class _S:
        def __init__(self):
            self.s = real()

        def value(self, k, d=None, type=None):
            if k in ("live/film_base_pick", "live/bw_light"):
                return store.get(k, d)
            return self.s.value(k, d, type=type) if type else self.s.value(k, d)

        def setValue(self, k, v):
            if k in ("live/film_base_pick", "live/bw_light"):
                store[k] = v
            else:
                self.s.setValue(k, v)
    monkeypatch.setattr(lmod, "_settings", lambda: _S())
    m._save_film_bases = lambda: None
    m._bases = None
    for e in (be.entry("Portra", TRI, scan="trichrome"), be.entry("HP5", ONE, scan="bw"),
              be.entry("Delta", ONE, scan="bw", light="g"), be.entry("Ektar W", ONE)):
        m._bases = be.add(m._bases, e)
    yield m, usb, light


def _listed(m):
    c = m.film_base_combo
    return [c.itemText(i) for i in range(c.count())]


def test_the_list_follows_the_scan_type_and_remembers_each_pick(mon):
    m, usb, light = mon
    m._apply_scan_type("bw")
    assert _listed(m) == ["HP5", "Delta", "Ektar W"]
    m.film_base_combo.setCurrentIndex(1)                       # Delta
    m._on_film_base_selected(1)
    assert m._film_base()["name"] == "Delta"
    assert m.bw_light() == "g"                                 # its light
    m._apply_scan_type("trichrome")
    assert _listed(m) == ["Portra"] and m._film_base()["name"] == "Portra"
    m._apply_scan_type("colour")
    assert _listed(m) == ["Ektar W"]
    m._apply_scan_type("bw")
    assert m._film_base()["name"] == "Delta"                   # remembered


def test_with_none_for_the_type_nothing_acts_on_a_hidden_base(mon):
    m, usb, light = mon
    m._bases = be.normalise({"bases": [be.entry("Portra", TRI, scan="trichrome")]})
    m._apply_scan_type("bw")
    assert _listed(m) == ["No film base for black & white yet"]
    assert m._film_base() is None and not m.edit_btn.isEnabled()
    assert not m.film_base_combo.isEnabled()


def test_the_bw_light_row_and_measure_in_bw(mon):
    m, usb, light = mon
    m._apply_scan_type("colour")
    assert not m.bw_light_row.isVisibleTo(m)
    m._apply_scan_type("bw")
    assert m.bw_light_row.isVisibleTo(m)
    m.set_bw_light("g")
    if m.light.is_open():
        assert m._levels.get("g") == 255 and not m._levels.get("w")
    m._neg_base_pos = (0.5, 0.05, 640, 480)
    m._ask_film_base_name = lambda default: "FP4"
    m.measure_btn.click()
    if m._measure is None:
        pytest.skip("this fixture's camera can't measure")
    start = m._measure["cal"]
    assert _wait(lambda: m._measure is None, 20)
    base = m._film_base()
    assert base["name"] == "FP4" and base["scan"] == "bw" and base["light"] == "g"


def test_the_rolls_format_sets_the_frame_guide(mon):
    m, usb, light = mon
    from core.film_formats import FILM_FORMATS, ratio
    keys = [k for k, *_ in FILM_FORMATS]
    k645 = next(k for k in keys if "645" in k or "4.5" in k)
    m.canvas.guide = dict(m.canvas.guide, format=None)
    m.apply_roll_format(k645)
    assert m.canvas.guide["format"] == k645
    assert m.format_combo.currentData() == k645
    before = dict(m.canvas.guide)
    m.apply_roll_format("")                                    # Not set: left alone
    assert m.canvas.guide == before
