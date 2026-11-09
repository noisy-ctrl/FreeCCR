#!/usr/bin/env python3
"""Capture's film base Measure sets the roll's black point
(spec/film-base-black-point.md)."""

import os
import sys
import types

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import base_exposure as be  # noqa: E402
from core import base_black_point as bbp  # noqa: E402
from test_capture_routes import _wait  # noqa: E402
from test_film_base_shoot import film_mon  # noqa: E402,F401

SETUP = {"camera": "Fujifilm GFX50S II", "profile": "", "demosaic": True, "mono": False,
         "scan_type": "trichrome"}


def _raw(bgr=(30000, 21000, 26000), h=60, w=80, seed=0, noise=200):
    rng = np.random.default_rng(seed)
    a = np.array(bgr, float)[None, None, :] + rng.normal(0, noise, (h, w, 3))
    return np.clip(a, 0, 65535).astype(np.uint16)


# --------------------------------------------------------------------------- #
# reading the patch
# --------------------------------------------------------------------------- #
def test_an_even_patch_gives_its_medians_in_bgr():
    bgr, why = bbp.measure(_raw(), (0.2, 0.2, 0.6, 0.6))
    assert why == "" and np.allclose(bgr, (30000, 21000, 26000), rtol=0.01)


def test_clipped_and_uneven_patches_are_refused():
    clipped = _raw((65400, 21000, 26000), noise=10)
    assert bbp.measure(clipped, (0.2, 0.2, 0.6, 0.6))[1].startswith("the film base is clipped")
    half = _raw()
    half[:, 40:] = _raw((15000, 10000, 13000))[:, 40:]           # half and half
    bgr, why = bbp.measure(half, (0.0, 0.0, 1.0, 1.0))
    assert bgr is None and "isn't even" in why


def test_a_patch_over_the_frames_edge_reads_its_base():
    raw = _raw()
    raw[:, 70:] = 3000                         # picture in the patch's last quarter
    bgr, why = bbp.measure(raw, (0.0, 0.0, 1.0, 1.0))
    assert why == "" and np.allclose(bgr, (30000, 21000, 26000), rtol=0.01)


def test_measure_shots_reads_the_exposure(monkeypatch):
    from core.ccr_backend import CCRBackend
    img = types.SimpleNamespace(resized_raw=_raw(), exposure=0.5)
    monkeypatch.setattr(CCRBackend, "image_exposure", staticmethod(lambda i: i.exposure))
    out = bbp.measure_shots(["a", "b", "c"], (0.2, 0.2, 0.6, 0.6), SETUP,
                            load=lambda paths: img)
    assert out["exposure"] == 0.5 and out["setup"] == SETUP
    img.exposure = None
    with pytest.raises(ValueError, match="no exposure"):
        bbp.measure_shots(["a"], (0.2, 0.2, 0.6, 0.6), SETUP, load=lambda paths: img)


# --------------------------------------------------------------------------- #
# the film base entries
# --------------------------------------------------------------------------- #
COL = {"shutter": "1/2", "iso": "100", "base": 0.8}


def _entry(bp=None):
    e = be.entry("Portra", {"r": COL, "g": COL, "b": COL}, camera="cam")
    if bp is not None:
        e["black_point"] = bp
    return e


BP = {"bgr": [30000.0, 21000.0, 26000.0], "exposure": 0.5, "setup": SETUP}


def test_entries_keep_a_black_point_and_drop_a_bad_one():
    d = be.add(None, _entry(BP))
    assert d["bases"][0]["black_point"]["exposure"] == 0.5
    for bad in ({"bgr": [1, 2]}, {"bgr": [1, 2, 3], "exposure": 0}, "x",
                {"bgr": [1, -2, 3], "exposure": 1}):
        assert "black_point" not in be.add(None, _entry(bad))["bases"][0]
    assert be.selected(be.rename(d, 0, "Portra 2"))["black_point"]
    edited = be.edit(d, 0, "Portra", {"r": dict(COL, shutter="1s"), "g": COL, "b": COL})
    assert be.selected(edited)["black_point"] == d["bases"][0]["black_point"]
    again = be.replace(d, 0, {"r": COL, "g": COL, "b": COL})
    assert "black_point" not in be.selected(again)          # Measure Again: a new one
    assert be.selected(be.set_black_point(again, "portra", BP))["black_point"]
    assert "black_point" not in be.selected(be.set_black_point(d, "Portra", None))
    assert be.summary(be.selected(d), True).endswith("· black point")


def test_a_black_point_is_used_only_on_its_setup():
    base = be.selected(be.add(None, _entry(BP)))
    assert be.black_point_usable(base, SETUP) == (True, "")
    assert be.black_point_usable(base, dict(SETUP, camera="")) == (True, "")
    for k, v, why in (("camera", "Other", "another camera"),
                      ("profile", "x.dcp", "another camera profile"),
                      ("demosaic", False, "another merge mode"),
                      ("mono", True, "another merge mode"),
                      ("scan_type", "colour", "another scan type")):
        assert be.black_point_usable(base, dict(SETUP, **{k: v})) == (False, why)
    assert be.black_point_usable(be.selected(be.add(None, _entry())), SETUP) == (False, "none")


def test_convert_scales_it_to_each_frames_exposure(monkeypatch):
    from core.ccr_backend import CCRBackend
    b = CCRBackend.__new__(CCRBackend)
    b.black_point_bgr, b.white_point_bgr = None, None
    b.set_black_point(tuple(BP["bgr"]), BP["exposure"])
    monkeypatch.setattr(CCRBackend, "image_exposure", staticmethod(lambda img: img))
    assert np.allclose(b.black_point_for(2.0), np.array(BP["bgr"]) * 4)   # two stops longer
    assert b.black_point_for(0.5) == tuple(BP["bgr"])


# --------------------------------------------------------------------------- #
# Capture
# --------------------------------------------------------------------------- #
@pytest.fixture
def mon(film_mon, monkeypatch):
    m, usb, light, lm, path = film_mon
    from core.ccr_backend import CCRBackend, ccr_backend
    monkeypatch.setattr(ccr_backend, "active_profile_path", None, raising=False)
    monkeypatch.setattr(ccr_backend, "rgb_merge_demosaic", True, raising=False)
    monkeypatch.setattr(ccr_backend, "rgb_merge_mono", False, raising=False)
    monkeypatch.setattr(CCRBackend, "image_exposure", staticmethod(lambda img: img.exposure))
    loads = []

    def load(paths):
        loads.append(list(paths))
        assert all(os.path.exists(p) for p in paths)
        e = min(be.exposure_of(usb.shots[p][1]["shutter"], usb.shots[p][1]["iso"])
                for p in paths)
        return types.SimpleNamespace(resized_raw=_raw(h=720, w=1080), exposure=e)
    m._base_bp_threaded = False
    m._load_base_shots = load
    sets = []
    m._mw.set_roll_black_point = lambda bgr, exp, note=None: sets.append((bgr, exp, note))
    m._neg_base_pos = (0.5, 0.05, 640, 480)
    m._ask_film_base_name = lambda default: "Portra 400"
    yield m, usb, loads, sets, ccr_backend


def test_measure_reads_the_black_point_from_its_accepted_shots(mon):
    m, usb, loads, sets, _b = mon
    m.measure_btn.click()
    temp = m._measure["temp"]
    assert _wait(lambda: m._measure is None, 20)
    base = m._film_base()
    assert len(loads) == 1 and len(loads[0]) == 3
    # the accepted shot of each colour, red, green, blue
    for c, p in zip("rgb", loads[0]):
        lv, exp = usb.shots[p]
        assert (exp["shutter"], exp["iso"]) == (base["colours"][c]["shutter"],
                                                base["colours"][c]["iso"])
        assert lv.get(c, 0) > 0
    bp = base["black_point"]
    assert np.allclose(bp["bgr"], (30000, 21000, 26000), rtol=0.01)
    assert bp["exposure"] == pytest.approx(1 / 3)         # red's 1/3 s, the shortest
    assert bp["setup"]["camera"] == "Fujifilm GFX50S II"
    assert sets and sets[-1][1] == pytest.approx(1 / 3)
    assert np.allclose(sets[-1][0], bp["bgr"])
    assert "Black Point set from film base “Portra 400”" in m.capture_caption.text()
    assert not os.path.exists(temp)
    assert m.film_base_note.text().endswith("· black point")


def test_a_failed_read_keeps_the_black_point_as_it_was(mon):
    m, usb, loads, sets, _b = mon

    def broken(paths):
        raise ValueError("can't decode")
    m._load_base_shots = broken
    m.measure_btn.click()
    temp = m._measure["temp"]
    assert _wait(lambda: m._measure is None, 20)
    assert "black_point" not in m._film_base() and not sets
    assert "couldn't be read (can't decode)" in m.capture_caption.text()
    assert not os.path.exists(temp)


def test_picking_a_film_base_sets_its_black_point_on_this_setup_only(mon):
    m, usb, loads, sets, b = mon
    m._bases = be.add(be.add(None, _entry(BP)), dict(_entry(), name="Ektar"))
    m._save_film_bases()
    m._refresh_film_bases()
    m.film_base_combo.setCurrentIndex(0)                    # Portra: has one
    assert sets and sets[-1][1] == 0.5
    n = len(sets)
    m.film_base_combo.setCurrentIndex(1)                    # Ektar: none
    assert len(sets) == n and "Measure Again" in m.capture_caption.text()
    b.active_profile_path = "/profiles/other.dcp"
    m.film_base_combo.setCurrentIndex(0)
    assert len(sets) == n
    assert "another camera profile" in m.capture_caption.text()


def test_the_main_window_takes_it_as_the_rolls_black_point(monkeypatch):
    from core.ccr_backend import ccr_backend
    from ui.main_window import MainWindow
    before = (ccr_backend.black_point_bgr, ccr_backend.black_point_exposure)
    w = MainWindow()
    try:
        hints = []
        monkeypatch.setattr(w.sliders_panel, "set_temporary_hint",
                            lambda t, duration=0: hints.append(t))
        w.set_roll_black_point((30000, 21000, 26000), 0.5, "from the film base")
        assert ccr_backend.black_point_bgr == (30000.0, 21000.0, 26000.0)
        assert ccr_backend.black_point_exposure == 0.5
        assert w._settings.value("convert/black_point_exposure", type=float) == 0.5
        assert hints == ["from the film base"]
    finally:
        ccr_backend.set_black_point(*before) if before[0] else ccr_backend.clear_black_point()
        w.persist_bwpoint()
        w.hide()
        _KEEP.append(w)


_KEEP = []
