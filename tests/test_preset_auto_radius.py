#!/usr/bin/env python3
"""A film preset can measure each frame's sharpening radius
(spec/preset-auto-radius.md)."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import film_presets as fp  # noqa: E402
from core.auto_radius import RadiusResult  # noqa: E402


def test_the_key_is_kept_copied_and_named():
    lst, p = fp.add([], fp.make("Portra400"))
    assert not fp.auto_radius_of(p) and "Auto radius" not in fp.summary(p)
    lst, p = fp.set_auto_radius(lst, p["id"], True)
    assert fp.auto_radius_of(p) and fp.summary(p) == "Auto radius"
    assert fp.auto_radius_of(fp.find(fp.normalise(fp.dumps(lst)), p["id"]))
    lst, v2 = fp.new_version(lst, p["id"])
    assert fp.auto_radius_of(v2) and v2["version"] == 2
    lst, p = fp.set_auto_radius(lst, p["id"], False)
    assert not fp.auto_radius_of(p)


@pytest.fixture
def mw(tmp_path):
    from ui.main_window import MainWindow
    from core.ccr_backend import ccr_backend
    st = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    w = MainWindow()
    w._settings = st
    w.sliders_panel._settings = st
    w.sliders_panel._auto_radius_threaded = False
    yield w
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()


def _frames(mw, tmp_path, n=3):
    import cv2
    import numpy as np
    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    sp = mw.sliders_panel
    out = []
    for i in range(n):
        p = tmp_path / f"f{i}.png"
        cv2.imwrite(str(p), np.full((40, 60, 3), 120, np.uint8))
        img = CCRImage(str(p))
        img.adjustment_settings = {k: sp._default_for(k) for k in sp.adjustment_keys}
        ccr_backend.images.append(img)
        ccr_backend.file_paths.append(img.file_path)
        out.append(img)
    return out


def _measures(sp, values):
    """A stand-in Auto Radius: frame name -> slider value (None: failed)."""
    seen = []

    def measure(img):
        name = os.path.basename(img.file_path)
        seen.append(name)
        v = values.get(name)
        if v is None:
            return RadiusResult(None, None, reason="Not enough smooth areas.")
        return RadiusResult(v / 25.0, v, 0.5, 20, "grain", f"Auto Radius {v}.")
    sp._measure_radius = measure
    return seen


def test_apply_preset_measures_each_frame(mw, tmp_path):
    sp = mw.sliders_panel
    a, b, c = _frames(mw, tmp_path)
    lst, p = fp.add([], fp.make("Portra400", auto_radius=True))
    fp.save(mw._settings, lst)
    seen = _measures(sp, {"f0.png": 31, "f1.png": 38, "f2.png": None})
    sp._choose_preset = lambda presets, current, n: p["id"]
    sp.apply_preset_to([0, 1, 2])
    assert seen == ["f0.png", "f1.png", "f2.png"]
    assert a.adjustment_settings["sharpen_radius"] == 31
    assert b.adjustment_settings["sharpen_radius"] == 38
    assert c.adjustment_settings["sharpen_radius"] == sp._default_for("sharpen_radius")
    assert a.film_preset == p["id"] and len(a.undo_stack) == 1
    assert not c.undo_stack                                   # a failed one: untouched
    assert a.pop_undo_state()
    assert a.adjustment_settings["sharpen_radius"] == sp._default_for("sharpen_radius")


def test_a_radius_set_by_hand_meanwhile_is_kept(mw, tmp_path):
    sp = mw.sliders_panel
    a, b = _frames(mw, tmp_path, 2)
    _measures(sp, {"f0.png": 31, "f1.png": 38})
    sp._auto_radius_threaded = True                  # queue without running ...
    sp._radius_queue_worker = object()               # ... as if one were measuring
    sp.queue_auto_radius([a, b, a])                  # a twice: queued once
    assert [e[0] for e in sp._radius_queue] == [a, b]
    b.adjustment_settings["sharpen_radius"] = 70     # the user, meanwhile
    sp._auto_radius_threaded = False
    sp._radius_queue_worker = None
    sp._radius_next()
    assert a.adjustment_settings["sharpen_radius"] == 31
    assert b.adjustment_settings["sharpen_radius"] == 70


def test_without_auto_radius_nothing_is_measured(mw, tmp_path):
    sp = mw.sliders_panel
    _frames(mw, tmp_path, 2)
    lst, p = fp.add([], fp.make("Portra400", flats={"colour": "none"}))
    fp.save(mw._settings, lst)
    seen = _measures(sp, {"f0.png": 31})
    sp._choose_preset = lambda presets, current, n: p["id"]
    sp.apply_preset_to([0, 1])
    assert seen == []


def test_a_preset_of_auto_radius_only_can_be_applied(mw, tmp_path):
    sp = mw.sliders_panel
    a, = _frames(mw, tmp_path, 1)
    lst, p = fp.add([], fp.make("HP5", auto_radius=True))
    fp.save(mw._settings, lst)
    _measures(sp, {"f0.png": 44})
    offered = {}

    def choose(presets, current, n):
        offered["ids"] = [x["id"] for x in presets]
        return p["id"]
    sp._choose_preset = choose
    sp.apply_preset_to([0])
    assert offered["ids"] == [p["id"]]
    assert a.adjustment_settings["sharpen_radius"] == 44 and a.film_preset == p["id"]


def test_a_capture_of_such_a_preset_is_measured(mw, tmp_path):
    sp = mw.sliders_panel
    a, = _frames(mw, tmp_path, 1)
    lst, p = fp.add([], fp.make("Portra400", auto_radius=True))
    lst, q = fp.add(lst, fp.make("Gold200"))
    fp.save(mw._settings, lst)
    seen = _measures(sp, {"f0.png": 29})
    a.film_preset = q["id"]
    mw._queue_preset_radius(a)
    assert seen == []
    a.film_preset = p["id"]
    mw._queue_preset_radius(a)
    assert seen == ["f0.png"] and a.adjustment_settings["sharpen_radius"] == 29


def test_the_settings_page_box(mw):
    from widgets.settings_dialog import SettingsDialog
    st = mw._settings
    lst, p = fp.add([], fp.make("Portra400"))
    fp.save(st, lst)
    dlg = SettingsDialog(mw)
    assert dlg.show_page("Film Presets")
    page = dlg.film_presets_page
    page._st = st
    page.refresh()
    assert page.auto_radius_cb.isEnabled() and not page.auto_radius_cb.isChecked()
    page.auto_radius_cb.setChecked(True)
    assert fp.auto_radius_of(fp.find(fp.load(st), p["id"]))
    page.refresh()
    assert page.auto_radius_cb.isChecked()
    dlg.deleteLater()
