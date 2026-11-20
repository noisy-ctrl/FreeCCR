#!/usr/bin/env python3
"""A film preset's flat fields (spec/preset-flat-fields.md): one per scan
type, pointing at a library flat by file name, so presets share flats."""
import os
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import film_presets as fp  # noqa: E402
from core import flat_field as ff  # noqa: E402
from test_flat_sets import _shot  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_globals():
    ff.set_library([])
    ff.set_active_profile(None)
    ff.set_preset_flats({})
    ff._capture_notes.clear()
    yield
    ff.set_library([])
    ff.set_active_profile(None)
    ff.set_preset_flats({})
    ff._capture_notes.clear()


def _lib(tmp_path):
    """A library on disk: white 645, white 35mm, RGB 645, an untagged one."""
    out = {}
    for name, light, fmt in (("w645", "white", "645"), ("w35", "white", "35mm"),
                             ("rgb645", "rgb", "645"), ("old", None, None)):
        arr = _shot()[0]
        p = (ff.build_tagged_profile(arr, name, light=light, fmt=fmt) if light
             else ff.build_profile(arr, name))
        out[name] = ff.load_profile(ff.save_profile(p, str(tmp_path / f"{name}.ffc")))
    ff.set_library(list(out.values()))
    return out


def _frame(**kw):
    d = dict(film_preset=None, merge_sources=None, is_merged=False, film_format="645",
             capture_type=None, color_profile="color", file_path="/x/a.raf")
    d.update(kw)
    return types.SimpleNamespace(**d)


# --------------------------------------------------------------------------- #
# the record
# --------------------------------------------------------------------------- #
def test_the_preset_keeps_its_flats_and_new_version_copies_them():
    lst, p = fp.add([], fp.make("Portra400"))
    assert fp.flat_of(p, "trichrome") == "" and p["flats"] == {}
    lst, p = fp.set_flat(lst, p["id"], "trichrome", "Flat rgb 645.ffc")
    lst, p = fp.set_flat(lst, p["id"], "bw", fp.NO_FLAT)
    assert p["flats"] == {"trichrome": "Flat rgb 645.ffc", "bw": "none"}
    assert fp.summary(p) == "2 flat fields"
    assert fp.flats_map(lst) == {p["id"]: p["flats"]}
    lst, p = fp.set_flat(lst, p["id"], "bw", "")             # back to Automatic
    assert p["flats"] == {"trichrome": "Flat rgb 645.ffc"}
    assert fp.set_flat(lst, p["id"], "ir", "x.ffc")[1] is None   # not a scan type
    lst, v2 = fp.new_version(lst, p["id"])
    assert v2["flats"] == p["flats"] and v2["version"] == 2
    # stored and read back; junk dropped
    raw = fp.dumps(lst).replace('"flats": {"trichrome"', '"flats": {"ir": "a", "trichrome"')
    back = fp.normalise(raw)
    assert all(q["flats"] == {"trichrome": "Flat rgb 645.ffc"} for q in back)
    # clearing the settings keeps the flats
    lst, p = fp.update(lst, p["id"], clip=None, chosen=[])
    assert p["flats"]


# --------------------------------------------------------------------------- #
# the choice per frame
# --------------------------------------------------------------------------- #
def test_a_presets_flat_wins_for_its_frames_of_that_scan_type(tmp_path):
    lib = _lib(tmp_path)
    ff.set_preset_flats({"P": {"colour": "w35.ffc", "trichrome": "none"}})
    plain = _frame()
    assert ff.profile_for(plain) is lib["w645"]                  # automatic
    mine = _frame(film_preset="P")
    assert ff.profile_for(mine).name == "w35"                    # pinned, whatever format
    trip = _frame(film_preset="P", merge_sources=["r", "g", "b"], is_merged=True)
    assert ff.profile_for(trip) is None                          # None: no correction
    ff.set_active_profile(lib["old"])
    assert ff.profile_for(trip) is None                          # even with an active one
    bw = _frame(film_preset="P", color_profile="bw")
    assert ff.profile_for(bw) is lib["w645"]                     # B&W not pinned: automatic
    other = _frame(film_preset="Q")
    assert ff.profile_for(other) is lib["w645"]
    ff.set_preset_flats({"P": {"colour": "gone.ffc"}})
    assert ff.pinned(mine) == ("missing", None)
    assert ff.profile_for(mine) is lib["w645"]                   # missing: automatic


def test_presets_share_one_flat_and_a_reshoot_reaches_them_all(tmp_path):
    lib = _lib(tmp_path)
    ff.set_preset_flats({"P": {"colour": "old.ffc"}, "Q": {"colour": "old.ffc"}})
    a, b = _frame(film_preset="P"), _frame(film_preset="Q")
    assert ff.profile_for(a) is ff.profile_for(b) is lib["old"]
    again = ff.build_profile(_shot(level=20000)[0], "old")
    ff.save_profile(again, str(tmp_path / "old.ffc"))            # the same file, replaced
    lib["old"] = ff.load_profile(str(tmp_path / "old.ffc"))
    ff.set_library(list(lib.values()))
    assert ff.profile_for(a) is ff.profile_for(b) is lib["old"]


def test_the_lists_for_each_scan_type():
    names = lambda scan: sorted(p.name for p in ff.flats_for_scan(scan))  # noqa: E731
    import tempfile
    from pathlib import Path
    _lib(Path(tempfile.mkdtemp()))
    assert names("trichrome") == ["old", "rgb645"]
    assert names("colour") == ["old", "w35", "w645"]
    assert names("bw") == ["old", "w35", "w645"]


def test_a_capture_is_corrected_with_its_rolls_preset_before_it_is_marked(tmp_path):
    lib = _lib(tmp_path)
    ff.set_preset_flats({"P": {"bw": "w35.ffc"}})
    ff.note_capture(["/hot/R1_645_HP5_001_BW.raf"], "P", "bw")
    cap = _frame(file_path="/hot/R1_645_HP5_001_BW.raf")         # not marked yet
    assert ff.frame_preset(cap) == "P" and ff.frame_scan(cap) == "bw"
    assert ff.profile_for(cap) is lib["w35"]
    # a merge is found by any of its files
    ff.set_preset_flats({"P": {"trichrome": "rgb645.ffc"}})
    ff.note_capture(["/hot/r.raf", "/hot/g.raf", "/hot/b.raf"], "P", "trichrome")
    m = _frame(file_path="/hot/merged", merge_sources=["/hot/r.raf", "/hot/g.raf",
                                                       "/hot/b.raf"], is_merged=True)
    assert ff.profile_for(m) is lib["rgb645"]
    for i in range(ff.NOTE_LIMIT + 10):
        ff.note_capture([f"/hot/{i}.raf"], "P", "colour")
    assert len(ff._capture_notes) == ff.NOTE_LIMIT


def test_the_main_window_notes_the_rolls_new_captures_only(monkeypatch):
    from ui.main_window import MainWindow
    from core.ccr_backend import ccr_backend
    monkeypatch.setattr(ccr_backend, "scan_type", "bw", raising=False)
    me = types.SimpleNamespace(capture_session={"preset": "P"}, _tether_active=True,
                               _tether_import_existing={os.path.normpath("/hot/old.raf")})
    MainWindow._note_capture_preset(me, ["/hot/new.raf", "/hot/old.raf"])
    assert ff.frame_preset(_frame(file_path="/hot/new.raf")) == "P"
    assert ff.frame_scan(_frame(file_path="/hot/new.raf")) == "bw"
    assert ff.frame_preset(_frame(file_path="/hot/old.raf")) is None
    MainWindow._note_capture_preset(me, ["/hot/r.raf", "/hot/g.raf", "/hot/b.raf"])
    assert ff._capture_notes[ff._norm("/hot/g.raf")] == ("P", "trichrome")
    me.capture_session = {"preset": ""}
    MainWindow._note_capture_preset(me, ["/hot/x.raf"])
    assert ff.frame_preset(_frame(file_path="/hot/x.raf")) is None


def test_the_signature_is_per_frame(tmp_path):
    from core.ccr_backend import ccr_backend
    _lib(tmp_path)
    a, b = _frame(film_preset="P"), _frame()
    sa, sb = (ccr_backend.active_profile_signature(x) for x in (a, b))
    ff.set_preset_flats({"P": {"colour": "w35.ffc"}})
    assert ccr_backend.active_profile_signature(a) != sa         # its flat changed
    assert ccr_backend.active_profile_signature(b) == sb         # this one's didn't
    c = _frame(film_format="35mm")
    before = ccr_backend.active_profile_signature(c)
    extra = ff.build_tagged_profile(_shot()[0], "rgb35", light="rgb", fmt="35mm")
    ff.set_library(ff.library() + [extra])                       # an RGB flat added
    assert ccr_backend.active_profile_signature(c) == before     # not a white frame's flat


# --------------------------------------------------------------------------- #
# Settings → Film Presets
# --------------------------------------------------------------------------- #
class _MW:
    def __init__(self, st):
        self._settings = st
        self.pushed = 0
        self.flagged = 0

    def push_preset_flats(self):
        self.pushed += 1
        ff.set_preset_flats(fp.flats_map(fp.load(self._settings)))

    def _refresh_profile_mismatch(self):
        self.flagged += 1


def test_the_page_picks_a_flat_per_scan_type(tmp_path, monkeypatch):
    from core.ccr_backend import ccr_backend
    from widgets.film_presets_page import FilmPresetsPage
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    _lib(lib_dir)
    monkeypatch.setattr(ccr_backend, "field_profiles_dir", lambda: str(lib_dir))
    st = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    lst, p = fp.add([], fp.make("Portra400"))
    lst, q = fp.add(lst, fp.make("Gold200"))
    lst, q = fp.set_flat(lst, q["id"], "colour", "gone.ffc")
    fp.save(st, lst)
    mw = _MW(st)
    page = FilmPresetsPage(mw, st)
    page.refresh(select=p["id"])
    rgb = page.flat_combos["trichrome"]
    items = [rgb.itemData(i) for i in range(rgb.count())]
    assert items == ["", "none", "old.ffc", "rgb645.ffc"]
    assert rgb.itemText(3).startswith("rgb645  (RGB (trichrome) · ")
    rgb.setCurrentIndex(3)
    assert fp.find(fp.load(st), p["id"])["flats"] == {"trichrome": "rgb645.ffc"}
    assert mw.pushed >= 1 and mw.flagged == 1
    assert ff.preset_flats()[p["id"]] == {"trichrome": "rgb645.ffc"}
    page.flat_combos["colour"].setCurrentIndex(page.flat_combos["colour"].findData("none"))
    assert fp.find(fp.load(st), p["id"])["flats"]["colour"] == "none"
    page.flat_combos["trichrome"].setCurrentIndex(0)             # Automatic
    assert "trichrome" not in fp.find(fp.load(st), p["id"])["flats"]
    # a missing file says so
    page.refresh(select=q["id"])
    col = page.flat_combos["colour"]
    assert col.currentData() == "gone.ffc" and "missing" in col.currentText()


def test_show_folder_opens_the_flat_library(monkeypatch, tmp_path):
    from core.ccr_backend import ccr_backend
    from widgets.settings_dialog import SettingsDialog
    monkeypatch.setattr(ccr_backend, "field_profiles_dir", lambda: str(tmp_path))
    opened = []
    me = types.SimpleNamespace(_field_folder_opener=opened.append)
    assert SettingsDialog._show_field_folder(me) == str(tmp_path)
    assert opened == [str(tmp_path)]


# --------------------------------------------------------------------------- #
# Apply Preset re-grades the frames whose flat changes
# --------------------------------------------------------------------------- #
def test_marking_frames_regrades_only_those_whose_flat_changes(tmp_path, monkeypatch):
    from core.ccr_backend import ccr_backend
    from widgets.sliders_panel import SlidersPanel
    _lib(tmp_path)
    ff.set_preset_flats({"P": {"colour": "w35.ffc"}})
    imgs = [_frame(), _frame(film_format="35mm"), _frame()]
    for im in imgs:
        im.profile_signature = ccr_backend.active_profile_signature(im)
    imgs[2].profile_signature = None                 # never decoded
    monkeypatch.setattr(ccr_backend, "get_image_by_index",
                        lambda i: imgs[i] if 0 <= i < len(imgs) else None)
    regraded = []
    tl = types.SimpleNamespace(replace_with_current_profile=regraded.extend)
    me = types.SimpleNamespace(window=lambda: types.SimpleNamespace(thumbnail_list=tl))
    changed = SlidersPanel._mark_preset(me, [0, 1, 2], "P")
    assert all(im.film_preset == "P" for im in imgs)
    # 0 was on the 645 flat (now w35: re-graded); 1 already had w35; 2 never decoded
    assert changed == [0] and regraded == [0]


def test_a_preset_of_flats_only_can_be_applied(tmp_path, monkeypatch):
    from core.ccr_backend import ccr_backend
    from widgets.sliders_panel import SlidersPanel
    lst, p = fp.add([], fp.make("HP5"))
    lst, p = fp.set_flat(lst, p["id"], "bw", "none")
    img = _frame()
    monkeypatch.setattr(ccr_backend, "get_image_by_index", lambda i: img if i == 0 else None)
    hints, marked = [], []
    me = types.SimpleNamespace(
        _film_presets=lambda: lst, _frame_preset_id=lambda i, ps: None,
        _choose_preset=lambda presets, current, n: presets[0]["id"],
        _apply_preset_rows=lambda o, n: pytest.fail("no rows to choose"),
        set_temporary_hint=lambda t, duration=0: hints.append(t),
        _mark_preset=lambda idx, pid: marked.append((idx, pid)),
        _sync_preset_button=lambda i: None, current_idx=0)
    SlidersPanel.apply_preset_to(me, [0])
    assert marked == [([0], p["id"])] and hints == ["Marked as HP5 V1."]
