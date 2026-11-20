#!/usr/bin/env python3
"""What flat field a frame got, shown in the film menu, and the camera and
lens each flat was shot with (spec/flat-field-info.md)."""
import os
import sys
import types

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
sys.path.insert(0, HERE)

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core import flat_capture as fc  # noqa: E402
from core import flat_field as ff  # noqa: E402
from core import film_presets as fp  # noqa: E402
from core.ccr_backend import ccr_backend  # noqa: E402
from test_flat_format_match import LYING, UPRIGHT, _flat, _shot  # noqa: E402

FUJI = {"make": "FUJIFILM", "model": "GFX50S II"}
LENS = "Schneider-Kreuznach MAKRO-SYMMAR 5.6/120"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ff.set_library([])
    ff.set_active_profile(None)
    ff.set_preset_flats({})
    store = {}
    monkeypatch.setattr(QSettings, "setValue", lambda self, k, v: store.__setitem__(k, v))
    monkeypatch.setattr(QSettings, "value",
                        lambda self, k, d=None, type=None: store.get(k, d))
    yield store
    ff.set_library([])
    ff.set_active_profile(None)
    ff.set_preset_flats({})


def _saved(tmp_path, name, opening=LYING, fmt="6x7", light="white", camera=None):
    p = ff.build_tagged_profile(_shot(opening), name, light=light, fmt=fmt,
                                meta={"camera": dict(camera or FUJI)})
    ff.save_profile(p, str(tmp_path / f"{name}.ffc"))
    return ff.load_profile(str(tmp_path / f"{name}.ffc"))


# --- what the decode applied ---------------------------------------------------

def test_track_state():
    p = _flat("67", LYING, "6x7")
    assert ff.track_state(p, ff.opening_of(_shot(LYING)))[1] == "same"
    move, state = ff.track_state(p, ff.opening_of(_shot(LYING, dx=0.03)))
    assert state == "moved" and move is not None and move == ff.track(
        p, ff.opening_of(_shot(LYING, dx=0.03)))
    assert ff.track_state(p, None) == (None, "not found")
    plain = ff.build_profile(_shot(LYING), "plain")
    assert ff.track_state(plain, ff.opening_of(_shot(LYING))) == (None, None)


def test_the_decode_keeps_what_it_applied(tmp_path):
    from core.ccr_image import CCRImage
    img = CCRImage.__new__(CCRImage)
    img.file_path = str(tmp_path / "a.raf")
    img.merge_sources, img.film_format = None, None
    img._apply_field_correction(_shot(LYING, dx=0.02))
    assert img.flat_applied is None                              # nothing in the library
    p67 = _flat("67", LYING, "6x7")
    p67.meta["camera"] = dict(FUJI)
    p67.meta["details"] = {"lens": LENS}
    ff.set_library([_flat("645", UPRIGHT, "645"), p67])
    img._flat_opening_memo = None
    img._apply_field_correction(_shot(LYING, dx=0.02))
    a = img.flat_applied
    assert a["label"] == "White light flat · 6x7" and a["carrier"] == "moved"
    assert a["camera"] == "FUJIFILM GFX50S II" and a["lens"] == LENS
    assert a["id"] == ff.flat_id(p67)


# --- the film menu ---------------------------------------------------------------

def _lines(monkeypatch, img, idx=0):
    from widgets.image_preview import ImagePreview
    monkeypatch.setattr(ccr_backend, "get_image_by_index",
                        lambda i: img if i == idx else None)
    return ImagePreview.flat_field_lines(types.SimpleNamespace(current_idx=idx))


def _frame(fmt="6x7"):
    return types.SimpleNamespace(film_format=fmt, film_preset=None, merge_sources=None,
                                 is_merged=False, flat_opening=None, capture_guide=None,
                                 capture_type=None, color_profile="color",
                                 file_path="/x/a.raf")


def test_the_menu_says_what_the_frame_got(monkeypatch):
    f = _frame()
    f.flat_applied = {"label": "White light flat · 6x7", "carrier": "moved",
                      "camera": "FUJIFILM GFX50S II", "lens": LENS, "id": "x"}
    f.profile_signature = None
    assert _lines(monkeypatch, f, 6) == [
        "Frame 7: White light flat · 6x7",
        f"   followed the carrier · FUJIFILM GFX50S II · {LENS}"]
    f.flat_applied = dict(f.flat_applied, carrier="not found", camera="", lens="")
    assert _lines(monkeypatch, f)[1] == "   the carrier wasn't found: applied as shot"
    f.flat_applied = None
    assert _lines(monkeypatch, f) == ["Frame 1: no flat field"]
    assert _lines(monkeypatch, None) == ["No frame open"]


def test_the_menu_says_when_a_regrade_would_change_it(monkeypatch):
    f = _frame()
    f.flat_applied = None
    f.profile_signature = ccr_backend.active_profile_signature(f)
    assert _lines(monkeypatch, f) == ["Frame 1: no flat field"]     # up to date
    ff.set_library([_flat("67", LYING, "6x7")])
    assert _lines(monkeypatch, f)[-1] == (
        "⚠ Re-grade to apply White light flat · 6x7: right-click its thumbnail")
    # decoded before this was kept: read from the choice
    del f.flat_applied
    f.profile_signature = ccr_backend.active_profile_signature(f)
    assert _lines(monkeypatch, f) == ["Frame 1: White light flat · 6x7"]


def test_formats_and_presets_that_bring_a_flat_are_marked(monkeypatch):
    from widgets.image_preview import ImagePreview
    a = _flat("645", UPRIGHT, "645")
    b = _flat("ir67", LYING, "6x7", light="ir")
    a.path = "/lib/645.ffc"
    ff.set_library([a, b])
    lst, plain = fp.add([], fp.make("Portra400"))
    lst, pinned = fp.add(lst, fp.make("XP2"))
    lst, pinned = fp.set_flat(lst, pinned["id"], "bw", "645.ffc")
    monkeypatch.setattr(ccr_backend, "scan_type", "colour", raising=False)
    fmts, named = ImagePreview._flat_marks(None, lst)
    assert fmts == {"645"} and named == {pinned["id"]}                 # white light only
    monkeypatch.setattr(ccr_backend, "scan_type", "trichrome", raising=False)
    assert ImagePreview._flat_marks(None, lst)[0] == set()             # no RGB flat


# --- camera and lens ---------------------------------------------------------------

def test_camera_and_lens_from_the_metadata_and_typed():
    p = ff.build_profile(_shot(LYING), "f", meta={"camera": dict(FUJI)})
    assert ff.camera_name(p) == "FUJIFILM GFX50S II" and ff.lens_name(p) == ""
    p.meta["camera"] = {"make": "Canon", "model": "Canon EOS R5", "lens_model": "RF 100mm",
                        "focal_length": 100.0, "aperture": 8.0}
    assert ff.camera_name(p) == "Canon EOS R5"                     # the maker once
    assert ff.lens_name(p) == "RF 100mm 100 mm f/8"
    p.meta = ff.with_details(p.meta, "GFX 50S II", LENS)
    assert ff.camera_name(p) == "GFX 50S II" and ff.lens_name(p) == LENS
    assert p.summary().startswith(f"GFX 50S II · {LENS} · ")
    assert "details" not in ff.with_details(p.meta, " ", "")         # blanks: dropped
    assert ff.camera_name(None) == "" and ff.lens_name(None) == ""


def test_naming_a_flat_keeps_its_id_so_no_frame_is_flagged(tmp_path):
    p = _saved(tmp_path, "w67")
    ff.set_library([p])
    f = _frame()
    sig = ccr_backend.active_profile_signature(f)
    old = p.content_id
    assert ff.set_details(p, "", LENS) is True
    assert ff.set_details(p, "", LENS) is False                     # nothing changed
    again = ff.load_profile(p.path)
    assert again.content_id == old and ff.lens_name(again) == LENS
    ff.set_library([again])
    assert ccr_backend.active_profile_signature(f) == sig
    assert ff.set_details(again, "", "") is True and ff.lens_name(ff.load_profile(p.path)) == ""
    # a re-shot flat is a new one
    fresh = ff.build_tagged_profile(_shot(LYING, level=30000), "w67", light="white", fmt="6x7")
    ff.save_profile(fresh, p.path)
    assert ff.load_profile(p.path).content_id != old


def test_shoot_flat_fields_keeps_the_typed_names(tmp_path, _clean):
    from test_shoot_flat_fields import _fake_decoders, _result
    decode, merge = _fake_decoders([])
    saved = fc.build_flats(_result(), "645", str(tmp_path), decode=decode, merge=merge,
                           details={"camera": "", "lens": LENS})
    for s in saved:
        p = ff.load_profile(s["path"])
        assert ff.lens_name(p) == LENS and ff.typed_details(p)["camera"] == ""
    from widgets.flat_shoot_dialog import FlatShootDialog
    ff.remember_details("", LENS)
    typed = ff.remembered_details()
    d = FlatShootDialog("645", ["white"], camera=typed["camera"], lens=typed["lens"])
    assert d.values()["lens"] == LENS and d.values()["camera"] == ""
    d.camera_edit.setText(" GFX 50S II ")
    assert d.values()["camera"] == "GFX 50S II"


def test_the_camera_and_lens_window(tmp_path, _clean):
    from widgets.flat_details_dialog import FlatDetailsDialog
    a, b = _saved(tmp_path, "w67"), _saved(tmp_path, "w645", UPRIGHT, "645")
    ids = (a.content_id, b.content_id)
    dlg = FlatDetailsDialog([a, b])
    assert dlg.camera_edits[0].placeholderText() == "FUJIFILM GFX50S II"
    assert dlg.lens_edits[0].placeholderText() == "Not in the metadata"
    dlg.lens_edits[0].setText(LENS)
    dlg.table.selectRow(0)
    dlg.same_for_all()
    assert dlg.lens_edits[1].text() == LENS
    assert dlg.save() == 2 and dlg.save() == 0
    for p, i in zip((a, b), ids):
        r = ff.load_profile(p.path)
        assert ff.lens_name(r) == LENS and r.content_id == i
    assert ff.remembered_details() == {"camera": "", "lens": LENS}


def test_settings_opens_the_camera_and_lens_window(tmp_path, monkeypatch):
    from widgets.settings_dialog import SettingsDialog
    a = _saved(tmp_path, "w67")
    monkeypatch.setattr(ccr_backend, "field_profiles_dir", lambda: str(tmp_path))
    shown = []

    def run(dlg):
        shown.append([p.name for p in dlg.profiles])
        dlg.lens_edits[0].setText(LENS)
        return True
    me = types.SimpleNamespace(_flat_details_exec=run, refresh_color_management=lambda: None)
    assert SettingsDialog._edit_flat_details(me) == 1
    assert shown == [["w67"]] and ff.lens_name(ff.load_profile(a.path)) == LENS
    me = types.SimpleNamespace(_flat_details_exec=lambda dlg: False,
                               refresh_color_management=lambda: None)
    assert SettingsDialog._edit_flat_details(me) == 0


def test_the_wizard_takes_camera_and_lens(tmp_path, monkeypatch, _clean):
    from widgets import field_correction_dialog as fcd
    monkeypatch.setattr(ccr_backend, "field_profiles_dir", lambda: str(tmp_path))
    monkeypatch.setattr(ff, "source_metadata",
                        lambda path: {"source": "f.raf", "camera": dict(FUJI)})
    ff.remember_details("", LENS)
    dlg = fcd.FieldCorrectionDialog()
    assert dlg.lens_edit.text() == LENS and dlg.camera_edit.text() == ""
    dlg._decoded = _shot(LYING)
    dlg._source_path = str(tmp_path / "f.raf")
    assert dlg._build_and_show()
    assert dlg.camera_edit.placeholderText() == "FUJIFILM GFX50S II"
    dlg.camera_edit.setText("GFX 50S II")
    dlg.name_edit.setText("Flat test")
    assert dlg._do_save()
    p = ff.load_profile(dlg.saved_path)
    assert ff.camera_name(p) == "GFX 50S II" and ff.lens_name(p) == LENS
    assert ff.remembered_details() == {"camera": "GFX 50S II", "lens": LENS}
