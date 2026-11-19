#!/usr/bin/env python3
"""Film presets across the workflow (spec/film-presets.md)."""

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

from core import base_exposure as be  # noqa: E402
from core import film_presets as fp  # noqa: E402

COL = {"shutter": "1/2", "iso": "100", "base": 0.8}
TRI = {"r": COL, "g": COL, "b": COL}
ONE = {"w": COL}


# --------------------------------------------------------------------------- #
# the records
# --------------------------------------------------------------------------- #
def test_labels_ids_and_file_names():
    p = fp.make("Colorplus200", 1, 2, "Barcelona 2022")
    assert fp.label(p) == "Colorplus200 +1 V2 (Barcelona 2022)"
    assert fp.label(p, notes=False) == "Colorplus200 +1 V2"
    assert fp.label(fp.make("Portra400")) == "Portra400 V1"
    assert fp.label(fp.make("HP5", -2)) == "HP5 −2 V1"
    assert fp.file_film(p) == "Colorplus200Push1"
    assert fp.file_film(fp.make("HP5 Plus", -1)) == "HP5PlusPull1"
    assert fp.file_film(fp.make("Portra400")) == "Portra400"
    assert fp.clamp_push(7) == 2 and fp.clamp_push("x") == 0
    assert fp.parse_label("Colorplus200 +1 V2 (Barcelona)") == {
        "film": "Colorplus200", "push": 1, "version": 2, "notes": "Barcelona"}
    assert fp.parse_label("HP5 −1") == {"film": "HP5", "push": -1, "version": 1,
                                             "notes": ""}
    assert fp.parse_label("Film base · 3 Nov")["film"] == "Film base · 3 Nov"


def test_ids_are_unique_and_versions_count_up():
    lst, a = fp.add([], fp.make("Portra400"))
    lst, b = fp.add(lst, fp.make("portra400"))           # the same ID: the existing one
    assert b["id"] == a["id"] and len(lst) == 1
    assert fp.next_version(lst, "PORTRA400", 0) == 2
    assert fp.next_version(lst, "Portra400", 1) == 1      # pushed is its own ID
    lst, c = fp.new_version(lst, a["id"])
    assert fp.label(c) == "Portra400 V2" and len(lst) == 2
    lst2, refused = fp.update(lst, c["id"], version=1)
    assert refused is None                                 # taken by V1
    lst, d = fp.update(lst, c["id"], notes="Barcelona 2022")
    assert fp.label(d) == "Portra400 V2 (Barcelona 2022)" and d["id"] == c["id"]
    assert fp.films(lst) == ["Portra400"]
    assert [p["id"] for p in fp.delete(lst, a["id"])] == [c["id"]]
    # broken records are dropped, a repeated ID keeps the first
    lst = fp.normalise('[{"film": ""}, {"film": "A"}, {"film": "a"}, 5]')
    assert [p["film"] for p in lst] == ["A"]


def test_settings_and_look_without_geometry():
    lst, p = fp.add([], fp.make("Portra400"))
    clip = {"adjustments": {"contrast": 10}, "crop": ((0, 0, 1, 1), 0), "rotation": 90,
            "look_lut": "Lab.cube", "profile": "color"}
    lst, p = fp.write_clip(lst, p["id"], clip, ["adj:contrast", "rotation", "look_lut"])
    assert "crop" not in p["clip"] and "rotation" not in p["clip"]
    assert p["chosen"] == ["adj:contrast", "look_lut"]
    assert fp.look_of(p) == "Lab.cube" and fp.summary(p) == "Film Look Lab · 1 setting"
    lst, p = fp.set_look(lst, p["id"], "")
    assert fp.look_of(p) == "" and fp.summary(p) == "1 setting"
    lst, q = fp.add(lst, fp.make("HP5"))
    lst, q = fp.set_look(lst, q["id"], "Mono.cube|colour")
    assert fp.summary(q) == "Film Look Mono (colour only)" and fp.carried(q) == 1
    assert fp.summary(fp.make("X")) == "no settings yet"


def test_stored_and_read_back():
    st = QSettings(os.path.join(_tmp(), "p.ini"), QSettings.IniFormat)
    lst, p = fp.add([], fp.make("Portra400", 0, 1, "lab"))
    fp.save(st, lst)
    assert fp.load(st) == lst


def _tmp():
    import tempfile
    return tempfile.mkdtemp()


# --------------------------------------------------------------------------- #
# film bases belong to presets
# --------------------------------------------------------------------------- #
def test_film_base_names_repeat_across_scan_types_only():
    d = be.add(None, be.entry("Portra400 V1", TRI, scan="trichrome", preset="a"))
    d = be.add(d, be.entry("Portra400 V1", ONE, scan="colour", preset="a"))
    assert [b["name"] for b in d["bases"]] == ["Portra400 V1", "Portra400 V1"]
    d = be.add(d, be.entry("Portra400 V1", ONE, scan="colour"))
    assert d["bases"][2]["name"] == "Portra400 V1 2"       # listed together: refused
    old = be.add(None, be.entry("XP2", ONE))                  # no scan: colour AND B&W
    old = be.add(old, be.entry("XP2", ONE, scan="bw"))
    assert old["bases"][1]["name"] == "XP2 2"
    assert be.scans(old["bases"][0]) == {"colour", "bw"}
    assert [i for i, _b in be.bases_of(d, "a")] == [0, 1]
    assert be.base_for(d, "a", "colour")[0] == 1 and be.base_for(d, "a", "bw") == (None, None)
    d = be.relabel(d, "a", "Portra400 +1 V1")
    assert [b["name"] for b in d["bases"][:2]] == ["Portra400 +1 V1"] * 2
    d = be.unlink_preset(d, "a")
    assert not any(b.get("preset") for b in d["bases"]) and len(be.unlinked(d)) == 3
    d = be.link(d, 2, "z", "Portra400 +1 V1")                 # a colour base has it
    assert d["bases"][2].get("preset") is None
    d = be.replace(be.link(d, 0, "q", "Q V1"), 0, TRI)        # Measure Again keeps it
    assert d["bases"][0]["preset"] == "q"


def test_black_point_goes_to_the_base_of_its_scan_type():
    d = be.add(None, be.entry("P V1", TRI, scan="trichrome", preset="a"))
    d = be.add(d, be.entry("P V1", ONE, scan="colour", preset="a"))
    bp = {"bgr": [1, 2, 3], "exposure": 0.5, "setup": {"camera": "c"}}
    d = be.set_black_point(d, "P V1", bp, scan="colour")
    assert "black_point" not in d["bases"][0] and d["bases"][1]["black_point"]["bgr"]


# --------------------------------------------------------------------------- #
# Capture
# --------------------------------------------------------------------------- #
from test_film_base_shoot import film_mon  # noqa: E402,F401
from test_capture_routes import _wait  # noqa: E402


def _presets(lm):
    return fp.load(lm._settings())


def test_measure_with_an_id_makes_and_links_the_preset(film_mon):
    mon, usb, light, lm, path = film_mon
    mon._mw.capture_session = {"roll": "R0001", "format": "", "film": ""}
    mon._neg_base_pos = (0.5, 0.05, 640, 480)
    seen = []
    mon._ask_film_base_name = lambda default: (seen.append(default) or
                                               {"film": "Portra400", "push": 1,
                                                "version": 1, "notes": "lab"})
    mon.measure_btn.click()
    assert seen == [{"film": "", "push": 0, "version": 1, "notes": ""}]
    assert _wait(lambda: mon._measure is None, 20)
    base = mon._film_base()
    p = fp.find(_presets(lm), base["preset"])
    assert fp.label(p) == "Portra400 +1 V1 (lab)" and base["name"] == fp.label(p)
    # the same ID again: Measure Again on that base, no second base
    mon._neg_base_pos = (0.5, 0.05, 640, 480)
    mon._ask_film_base_name = lambda default: {"film": "portra400", "push": 1, "version": 1}
    mon._base_click_used = False
    mon.measure_btn.click()
    idx = mon._measure["index"] if mon._measure else mon._pending_measure[1]
    assert idx == mon._bases["selected"]
    assert _wait(lambda: mon._measure is None, 20)
    assert len(mon._bases["bases"]) == 1 and len(_presets(lm)) == 1
    # the roll's preset now has a trichrome base: the next default is V2
    mon._mw.capture_session["preset"] = p["id"]
    assert mon._measure_default_id()["version"] == 2


def test_edit_moves_a_base_to_another_push_and_save_as_new_versions(film_mon):
    mon, usb, light, lm, path = film_mon
    st = lm._settings()
    lst, p = fp.add([], fp.make("Colorplus200"))
    lst, p = fp.write_clip(lst, p["id"], {"adjustments": {"contrast": 9}}, ["adj:contrast"])
    fp.save(st, lst)
    mon._bases = be.add(None, be.entry(fp.label(p), TRI, scan="trichrome", preset=p["id"]))
    mon._refresh_film_bases()
    vals = {"_id": {"film": "Colorplus200", "push": 1}}
    mon._edit_film_base_dialog = lambda base: ("save", "Colorplus200", dict(vals))
    mon.edit_btn.click()
    base = mon._film_base()
    q = fp.find(_presets(lm), base["preset"])
    # the old preset carries settings, so it stays and the base moves
    assert fp.label(q) == "Colorplus200 +1 V1" and q["id"] != p["id"]
    assert base["name"] == "Colorplus200 +1 V1" and len(_presets(lm)) == 2
    mon._edit_film_base_dialog = lambda base: ("new", "Colorplus200", dict(vals))
    mon.edit_btn.click()
    new = mon._film_base()
    r = fp.find(_presets(lm), new["preset"])
    assert fp.label(r) == "Colorplus200 +1 V2" and new["scan"] == "trichrome"
    assert len(mon._bases["bases"]) == 2
    # a preset holding only this base is renamed instead of left empty
    mon._edit_film_base_dialog = lambda base: ("save", "x", {"_id": {"film": "Gold200",
                                                                     "push": 0}})
    mon.edit_btn.click()
    assert fp.find(_presets(lm), r["id"])["film"] == "Gold200"
    assert mon._film_base()["name"] == "Gold200 V2"


def test_rename_a_linked_base_renames_its_preset(film_mon):
    mon, usb, light, lm, path = film_mon
    st = lm._settings()
    lst, p = fp.add([], fp.make("Portra400"))
    fp.save(st, lst)
    mon._bases = be.add(None, be.entry("Portra400 V1", TRI, scan="trichrome", preset=p["id"]))
    mon._bases = be.add(mon._bases, be.entry("Portra400 V1", ONE, scan="colour",
                                             preset=p["id"]))
    mon._bases["selected"] = 0
    mon._refresh_film_bases()
    mon._ask_preset_rename = lambda preset: {"film": "Portra400", "push": 0, "version": 1,
                                             "notes": "Lisbon 2026"}
    mon._on_rename_film_base()
    assert [b["name"] for b in mon._bases["bases"]] == ["Portra400 V1 (Lisbon 2026)"] * 2


def test_the_rolls_preset_picks_its_film_base(film_mon):
    mon, usb, light, lm, path = film_mon
    st = lm._settings()
    lst, a = fp.add([], fp.make("Portra400"))
    lst, b = fp.add(lst, fp.make("Ektar100"))
    fp.save(st, lst)
    mon._bases = be.add(None, be.entry("Portra400 V1", TRI, scan="trichrome", preset=a["id"]))
    mon._bases = be.add(mon._bases, be.entry("Ektar100 V1", TRI, scan="trichrome",
                                             preset=b["id"]))
    mon._refresh_film_bases()
    assert mon.apply_roll_preset(a["id"])
    assert mon._film_base()["name"] == "Portra400 V1"
    assert mon.apply_roll_preset(b["id"]) and mon._film_base()["name"] == "Ektar100 V1"
    lst, c = fp.add(lst, fp.make("Gold200"))
    fp.save(st, lst)
    assert not mon.apply_roll_preset(c["id"])
    assert "No trichrome film base for Gold200 V1 yet" in mon.capture_caption.text()


def test_reload_takes_film_bases_changed_elsewhere(film_mon):
    mon, usb, light, lm, path = film_mon
    st = lm._settings()
    st.setValue("live/film_bases", be.dumps(be.add(None, be.entry("Elsewhere", TRI))))
    mon.reload_film_bases()
    assert mon._film_base()["name"] == "Elsewhere"


# --------------------------------------------------------------------------- #
# the Start window, captures, Write to / Apply Preset, Settings
# --------------------------------------------------------------------------- #
@pytest.fixture
def presets_store(monkeypatch, tmp_path):
    """The main window's QSettings for film presets in a temp file."""
    st = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    return st


def test_the_start_window_lists_presets():
    from widgets.tether_start_dialog import TetherStartDialog
    lst, a = fp.add([], fp.make("Portra400", 0, 2, "Barcelona"))
    dlg = TetherStartDialog(None, presets=lst, preset=a["id"], films=["Gold 200"])
    o = dlg.options(True)
    assert o["preset"] == a["id"] and o["film"] == "Portra400 V2 (Barcelona)"
    dlg.film_combo.setEditText("Gold 200")
    assert dlg.options(True)["preset"] == ""
    dlg.film_combo.setEditText("Something typed")
    assert dlg.options(True)["preset"] == ""


@pytest.fixture
def mw(presets_store):
    from ui.main_window import MainWindow
    from core.ccr_backend import ccr_backend
    w = MainWindow()
    w._settings = presets_store
    w.sliders_panel._settings = presets_store
    yield w
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()


def test_the_session_takes_the_presets_film_and_file_name(mw):
    st = mw._settings
    lst, p = fp.add([], fp.make("Colorplus200", 1, 1))
    fp.save(st, lst)
    mw._set_capture_session("R0003", "135", "ignored", preset=p["id"])
    s = mw.capture_session
    assert (s["film"], s["film_label"], s["preset"]) == ("Colorplus200Push1",
                                                         "Colorplus200 +1 V1", p["id"])
    assert mw._capture_defaults()["preset"] == p["id"]
    mw._set_capture_session("R0004", "135", "Gold 200")
    assert "preset" not in mw.capture_session and mw.capture_session["film"] == "Gold 200"


def _image(tmp_path, name="a.png"):
    import cv2
    import numpy as np
    from core.ccr_image import CCRImage
    p = tmp_path / name
    cv2.imwrite(str(p), np.full((40, 60, 3), 120, np.uint8))
    return CCRImage(str(p))


def test_the_first_capture_starts_from_the_preset_then_carries(mw, tmp_path):
    from core.ccr_backend import ccr_backend
    sp = mw.sliders_panel
    st = mw._settings
    lst, p = fp.add([], fp.make("Portra400"))
    clip = {"adjustments": {k: sp._default_for(k) for k in sp.adjustment_keys},
            "profile": "bw", "flags": {}}
    clip["adjustments"]["contrast"] = 17
    lst, p = fp.write_clip(lst, p["id"], clip, ["adj:contrast", "profile"])
    fp.save(st, lst)
    mw.capture_session = {"roll": "R1", "format": "", "film": "Portra400",
                          "preset": p["id"]}
    t = mw._tether_template()                     # nothing on screen: the preset
    assert t["adjustment_settings"]["contrast"] == 17 and t["color_profile"] == "bw"
    assert t["crop_rect"] is None and t["rotation_angle"] == 0
    img = _image(tmp_path)
    img.film_preset = p["id"]
    img.adjustment_settings = {k: sp._default_for(k) for k in sp.adjustment_keys}
    img.adjustment_settings["contrast"] = 40
    ccr_backend.images.append(img)
    ccr_backend.file_paths.append(img.file_path)
    mw.image_preview.current_idx = 0
    assert mw._tether_template()["adjustment_settings"]["contrast"] == 40   # carries
    img.film_preset = None                        # another film's frame on screen
    assert mw._tether_template()["adjustment_settings"]["contrast"] == 17


def test_paste_into_equals_the_old_paste():
    """The refactor: paste_into gives what _apply_paste built (defaults filled,
    unticked rows kept from the target)."""
    from ui.main_window import MainWindow
    sp = MainWindow().sliders_panel
    target = {"contrast": 5, "saturation": 3}
    clip = {"adjustments": {"contrast": 20, "saturation": 9}, "curves": None, "flags": {}}
    out = sp.paste_into(target, clip, {"adj:contrast"})
    assert out["contrast"] == 20 and out["saturation"] == 3
    assert set(sp.adjustment_keys) <= set(out)
    assert sp.paste_into(target, clip, {"profile"}) is None


def test_write_to_preset_and_apply_it(mw, tmp_path):
    from core.ccr_backend import ccr_backend
    sp = mw.sliders_panel
    st = mw._settings
    lst, p = fp.add([], fp.make("Portra400"))
    fp.save(st, lst)
    for n in ("a.png", "b.png", "c.png"):
        img = _image(tmp_path, n)
        img.adjustment_settings = {k: sp._default_for(k) for k in sp.adjustment_keys}
        ccr_backend.images.append(img)
        ccr_backend.file_paths.append(img.file_path)
    a = ccr_backend.images[0]
    a.adjustment_settings.update(contrast=25, temperature=12)
    sp.current_idx = 0                    # the frame on screen: read off the sliders
    for key, v in (("contrast", 25), ("temperature", 12)):
        sl = sp.sliders[sp.adjustment_keys.index(key)]
        sl.blockSignals(True)
        sl.setValue(v)
        sl.blockSignals(False)
    asked = {}

    def dialog(options, unticked, presets, current):
        asked.update(ids=[o[1] for o in options], unticked=set(unticked), current=current)
        return p["id"], {o[1] for o in options if o[1] not in unticked}
    sp._write_preset_dialog = dialog
    sp._on_write_preset()
    assert "adj:contrast" in asked["ids"] and "adj:temperature" in asked["ids"]
    assert asked["unticked"] >= {"adj:temperature", "adj:tint"}
    stored = fp.find(fp.load(st), p["id"])
    assert "adj:contrast" in stored["chosen"] and "adj:temperature" not in stored["chosen"]
    assert stored["clip"]["adjustments"]["contrast"] == 25
    assert "crop" not in stored["clip"] and a.film_preset == p["id"]
    # apply it to the other two
    sp._choose_preset = lambda presets, current, n: p["id"]
    sp._apply_preset_rows = lambda options, n: {o[1] for o in options}
    sp.apply_preset_to([1, 2])
    for img in ccr_backend.images[1:]:
        assert img.adjustment_settings["contrast"] == 25
        assert img.adjustment_settings["temperature"] == sp._default_for("temperature")
        assert img.film_preset == p["id"] and img.undo_stack
    assert sp._preset_menu_actions()[-1][0] == "Manage Film Presets…"


def test_the_colour_profile_row_has_write_to_preset(mw):
    sp = mw.sliders_panel
    from widgets.sliders_panel import PROFILE_COMBO_W
    assert sp.color_profile_combo.width() == PROFILE_COMBO_W
    assert sp.color_profile_combo.itemText(1) == "B&W"
    assert sp.write_preset_btn.text() == "Write to Preset"


def test_the_settings_page(mw):
    from widgets.settings_dialog import SettingsDialog
    st = mw._settings
    lst, p = fp.add([], fp.make("Portra400"))
    fp.save(st, lst)
    st.setValue("live/film_bases", be.dumps(be.add(None, be.entry(
        "Portra400 V1", TRI, scan="trichrome", preset=p["id"]))))
    st.sync()
    d = be.add(be.normalise(st.value("live/film_bases", "")), be.entry("HP5 -1 V2", ONE,
                                                                      scan="bw"))
    mw.set_film_bases_data(d)
    dlg = SettingsDialog(mw)
    assert dlg.show_page("Film Presets")
    page = dlg.film_presets_page
    page._st = st
    page.refresh()
    assert page.list.count() == 1 and "RGB" in page.list.item(0).text()
    assert "Trichrome" not in page.bases_note.text() and "RGB" in page.bases_note.text()
    page.notes.setText("Lisbon")
    assert page.save_id_btn.isEnabled()
    assert page.save_id()
    assert be.normalise(st.value("live/film_bases", ""))["bases"][0]["name"] == \
        "Portra400 V1 (Lisbon)"
    new = page.new_version()
    assert fp.label(new) == "Portra400 V2"
    page.version.setValue(1)                       # V1 is taken
    assert not page.save_id_btn.isEnabled()
    assert page.make_presets() == 1               # the unlinked HP5 base
    names = sorted(fp.label(x) for x in fp.load(st))
    assert "HP5 −1 V2" in names
    page._confirm = lambda title, text: True
    page.refresh(select=p["id"])
    assert page.delete_preset()
    assert not be.bases_of(be.normalise(st.value("live/film_bases", "")), p["id"])


def test_write_with_no_presets_names_the_first_one(mw, tmp_path):
    from core.ccr_backend import ccr_backend
    sp = mw.sliders_panel
    img = _image(tmp_path)
    ccr_backend.images.append(img)
    ccr_backend.file_paths.append(img.file_path)
    sp.current_idx = 0
    sl = sp.sliders[sp.adjustment_keys.index("contrast")]
    sl.blockSignals(True)
    sl.setValue(30)
    sl.blockSignals(False)
    sp._new_preset_id = lambda initial=None: {"film": "Ektar100", "push": 0, "version": 1,
                                              "notes": ""}
    got = {}
    sp._write_preset_dialog = lambda options, unticked, presets, current: (
        got.update(current=current, n=len(presets)) or (current, {"adj:contrast"}))
    sp._on_write_preset()
    p = fp.load(mw._settings)[0]
    assert fp.label(p) == "Ektar100 V1" and got == {"current": p["id"], "n": 1}
    assert p["chosen"] == ["adj:contrast"] and img.film_preset == p["id"]
