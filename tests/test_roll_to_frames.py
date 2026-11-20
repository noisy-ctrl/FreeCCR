#!/usr/bin/env python3
"""The film menu's Film and Format can go to the open frames, and a frame
whose flat changes with them is re-graded (spec/roll-to-frames.md)."""
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
def _clean():
    ff.set_library([])
    ff.set_active_profile(None)
    ff.set_preset_flats({})
    yield
    ff.set_library([])
    ff.set_active_profile(None)
    ff.set_preset_flats({})


def _lib():
    """A 645 and a 6x7 white flat (the same opening: only the format differs)."""
    a = ff.build_tagged_profile(_shot()[0], "w645", light="white", fmt="645")
    b = ff.build_tagged_profile(_shot()[0], "w67", light="white", fmt="6x7")
    a.content_id, b.content_id = "aaa", "bbb"
    ff.set_library([a, b])
    return a, b


def _frame(fmt=None, decoded=True):
    f = types.SimpleNamespace(film_format=fmt, film_preset=None, merge_sources=None,
                              is_merged=False, flat_opening=None, capture_guide=None,
                              capture_type=None, color_profile="color",
                              file_path=f"/x/{id(object())}.raf")
    from core.ccr_backend import ccr_backend
    f.profile_signature = ccr_backend.active_profile_signature(f) if decoded else None
    return f


def test_mark_frames_lists_the_decoded_frames_whose_flat_changes(monkeypatch):
    from core.ccr_backend import ccr_backend
    _lib()
    frames = [_frame("645"), _frame("6x7"), _frame(None), _frame("645", decoded=False)]
    monkeypatch.setattr(ccr_backend, "get_image_by_index",
                        lambda i: frames[i] if 0 <= i < len(frames) else None)
    changed = ccr_backend.mark_frames([0, 1, 2, 3, 9], film_format="6x7")
    assert all(f.film_format == "6x7" for f in frames)
    # 0: 645 → 6x7 flat; 1 had it; 2 had neither (two formats, no opening)
    # → now the 6x7 one; 3 was never decoded; 9 isn't there
    assert changed == [0, 2]


@pytest.fixture
def mw(tmp_path):
    from ui.main_window import MainWindow
    from core.ccr_backend import ccr_backend
    w = MainWindow()
    w._settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    w.sliders_panel._settings = w._settings
    regraded = []
    w.thumbnail_list.replace_with_current_profile = regraded.extend
    w.thumbnail_list.refresh_profile_warnings = lambda: None
    w.regraded = regraded
    saved = ccr_backend.save_catalog
    ccr_backend.save_catalog = lambda *a, **k: None
    yield w
    ccr_backend.save_catalog = saved
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()


def _open(mw, frames):
    from core.ccr_backend import ccr_backend
    ccr_backend.images[:] = frames
    ccr_backend.file_paths[:] = [f.file_path for f in frames]


def test_the_menus_format_asks_and_goes_to_the_chosen_frames(mw):
    _lib()
    frames = [_frame("645"), _frame("645"), _frame("645")]
    _open(mw, frames)
    asked = []
    mw.thumbnail_list.selected_indices = lambda: [1]
    mw._ask_roll_to_frames = lambda what, n_sel, n: asked.append((what, n_sel, n)) or None
    menu = mw.image_preview.film_menu()
    # a format with a flat carries "flat field" in the shortcut column
    by = {a.text().split("\t")[0]: a for a in menu.actions()}
    assert by["120: 6x7"].text() == "120: 6x7\tflat field"
    by["120: 6x7"].trigger()
    assert mw.capture_session["format"] == "6x7"                  # the roll, as before
    assert asked == [("format", 1, 3)] and all(f.film_format == "645" for f in frames)
    assert mw.regraded == []                                      # Just the Roll
    mw._ask_roll_to_frames = lambda what, n_sel, n: "selected"
    assert mw.pick_roll_format("6x7") == [1]
    assert [f.film_format for f in frames] == ["645", "6x7", "645"]
    assert mw.regraded == [1]
    mw._ask_roll_to_frames = lambda what, n_sel, n: "all"
    assert mw.pick_roll_format("6x7") == [0, 2]                  # 1 already had it
    assert mw.regraded == [1, 0, 2]
    mw.pick_roll_format("")                                       # Not set clears it
    assert all(f.film_format is None for f in frames)


def test_the_menus_film_marks_the_frames_with_the_preset(mw):
    a, b = _lib()
    a.path, b.path = "/lib/w645.ffc", "/lib/w67.ffc"
    lst, p = fp.add([], fp.make("Portra400"))
    lst, p = fp.set_flat(lst, p["id"], "colour", "w67.ffc")
    fp.save(mw._settings, lst)
    mw.push_preset_flats()
    frames = [_frame("6x7"), _frame("6x7")]
    frames[0].profile_signature = "old"                           # graded under another flat
    _open(mw, frames)
    mw.thumbnail_list.selected_indices = lambda: [0, 1]
    mw._ask_roll_to_frames = lambda what, n_sel, n: "all"
    mw.pick_roll_film(p["id"])
    assert mw.capture_session["preset"] == p["id"]
    assert all(f.film_preset == p["id"] for f in frames)
    mw.pick_roll_film(None)
    assert all(f.film_preset is None for f in frames)


def test_no_frames_open_asks_nothing(mw):
    mw._ask_roll_to_frames = lambda *a: pytest.fail("asked with nothing open")
    assert mw.pick_roll_format("645") is None
    assert mw.capture_session["format"] == "645"


def test_the_crop_panels_format_regrades_a_frame_whose_flat_changes(monkeypatch):
    """Done with a film format picked: the frame takes that format's flat."""
    from core.ccr_backend import ccr_backend
    import test_crop_panel as tcp
    from test_film_formats import _panel
    _lib()
    ip, panel = _panel()
    img = tcp._Img(crop_rect=(0.1, 0.1, 0.9, 0.9))
    img.film_format = "645"
    img.profile_signature = ccr_backend.active_profile_signature(img)
    regraded = []
    monkeypatch.setattr(ccr_backend, "regrade_images_by_indices",
                        lambda idx, *a, **k: regraded.extend(idx) or True)
    saved = ccr_backend.images
    ccr_backend.images = [img]
    try:
        ip.update_preview = lambda *a, **k: None
        panel.aspect_combo.setCurrentIndex(panel.aspect_combo.findData("645"))
        assert ip.enter_crop_mode()
        ip.confirm_crop()
        assert img.film_format == "645" and regraded == []        # its flat stays
        panel.aspect_combo.setCurrentIndex(panel.aspect_combo.findData("6x7"))
        assert ip.enter_crop_mode()
        ip.confirm_crop()
        assert img.film_format == "6x7" and regraded == [0]       # the 6x7 flat now
    finally:
        ccr_backend.images = saved
