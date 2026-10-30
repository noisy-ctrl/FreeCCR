#!/usr/bin/env python3
"""Starting Hot Folder Tether from one window (spec/tether-start-window.md):
folder, scan type, close the open images, import the folder's files; and the
lead frame nudging the banner's Black Point… instead of a pop-up."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from core.ccr_backend import ccr_backend  # noqa: E402


def _png(path, v=80):
    cv2.imwrite(str(path), np.full((30, 30, 3), v, np.uint8))
    return str(path)


# --------------------------------------------------------------------------- #
# the window itself
# --------------------------------------------------------------------------- #
def test_the_window_shows_only_the_choices_that_apply(tmp_path):
    from widgets.tether_start_dialog import TetherStartDialog
    roll = tmp_path / "roll"
    roll.mkdir()
    picked = {"to": str(roll)}
    dlg = TetherStartDialog(n_open=0, folder="", scan_type="trichrome",
                            chooser=lambda start: picked["to"])
    assert not dlg.start_btn.isEnabled()                 # no folder yet
    assert dlg.close_box.isHidden() and dlg.import_box.isHidden()
    assert dlg.dont_ask_box.isHidden()
    assert dlg.scan_combo.currentData() == "trichrome"
    dlg.choose_btn.click()
    assert dlg.start_btn.isEnabled() and dlg.import_box.isHidden()   # empty folder
    _png(roll / "a.png")
    _png(roll / "b.png")
    (roll / "notes.txt").write_text("x")
    dlg.choose_btn.click()                               # same folder, recounted
    assert not dlg.import_box.isHidden()
    assert dlg.import_box.text() == "Import the 2 files already in the folder"
    dlg.import_box.setChecked(True)
    o = dlg.options(True)
    assert o == {"start": True, "folder": str(roll), "scan_type": "trichrome",
                 "close_open": False, "import_existing": True, "dont_ask": False}
    assert dlg.options(False)["start"] is False


def test_the_window_offers_closing_and_dont_ask_when_they_apply(tmp_path):
    from widgets.tether_start_dialog import TetherStartDialog
    dlg = TetherStartDialog(n_open=12, folder=str(tmp_path), from_monitor=True)
    assert not dlg.close_box.isHidden()
    assert dlg.close_box.text() == "Close the 12 open images first"
    assert not dlg.dont_ask_box.isHidden()
    assert not dlg.close_box.isChecked()                 # adding is the default
    dlg.close_box.setChecked(True)
    dlg.dont_ask_box.setChecked(True)
    o = dlg.options(True)
    assert o["close_open"] and o["dont_ask"] and o["folder"] == str(tmp_path)
    one = TetherStartDialog(n_open=1, folder=str(tmp_path))
    assert one.close_box.text() == "Close the 1 open image first"


# --------------------------------------------------------------------------- #
# the main window acting on it
# --------------------------------------------------------------------------- #
@pytest.fixture
def mw(tmp_path, monkeypatch):
    from ui.main_window import MainWindow
    from core.ccr_image import CCRImage
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()
    ccr_backend.positive_mode = False
    ccr_backend.black_point_bgr = None
    w = MainWindow()
    monkeypatch.setattr(w._settings, "setValue", lambda *a, **k: None)
    roll = tmp_path / "roll"
    roll.mkdir()
    asked = []
    answer = {"start": True, "folder": str(roll), "scan_type": "colour",
              "close_open": False, "import_existing": False, "dont_ask": False}
    w._tether_start_options = lambda n, d, m=False: (asked.append((n, m)), dict(answer))[1]
    saves = []
    real_save = ccr_backend.save_catalog
    monkeypatch.setattr(ccr_backend, "save_catalog",
                        lambda *a, **k: (saves.append(len(ccr_backend.images)), real_save(*a, **k))[1])

    def open_images(n):
        for i in range(n):
            img = CCRImage(_png(tmp_path / f"old{i}.png", 80 + i))
            ccr_backend.images.append(img)
            ccr_backend.file_paths.append(img.file_path)
        w.thumbnail_list.load_thumbnails()

    yield w, asked, answer, saves, open_images, roll
    w.stop_tethering()
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()


def test_starting_asks_once_and_keeps_open_images_by_default(mw):
    w, asked, answer, saves, open_images, roll = mw
    open_images(2)
    opts = w.enter_tethering()
    assert asked == [(2, False)] and w._tether_active and opts["start"]
    assert len(ccr_backend.images) == 2


def test_close_open_images_after_saving(mw):
    w, asked, answer, saves, open_images, roll = mw
    open_images(3)
    answer["close_open"] = True
    w.enter_tethering()
    assert w._tether_active and ccr_backend.images == []
    assert w.thumbnail_list.thumbnail_list.count() == 0
    assert saves and saves[0] == 3


def test_cancel_changes_nothing(mw):
    w, asked, answer, saves, open_images, roll = mw
    open_images(1)
    answer["start"] = False
    answer["close_open"] = True
    w.enter_tethering()
    assert not w._tether_active and len(ccr_backend.images) == 1


def test_scan_type_and_import_come_from_the_window(mw, monkeypatch):
    w, asked, answer, saves, open_images, roll = mw
    _png(roll / "a.png")
    _png(roll / "b.png")
    answer["scan_type"] = "bw"
    answer["import_existing"] = True
    started = []
    monkeypatch.setattr(w, "set_scan_type", lambda t: started.append(t))
    w.enter_tethering()
    assert started == ["bw"]
    assert {os.path.basename(p) for p in w._tether_import_existing} == {"a.png", "b.png"}


def test_already_on_shows_nothing(mw):
    w, asked, answer, saves, open_images, roll = mw
    w.enter_tethering()
    asked.clear()
    assert w.enter_tethering() is None and asked == []


def test_the_lead_frame_nudges_the_banner_not_a_pop_up(mw):
    w, asked, answer, saves, open_images, roll = mw
    from core.ccr_image import CCRImage
    w.enter_tethering()
    banner = w.tether_banner
    assert not banner._nudging if hasattr(banner, "_nudging") else True
    lead = CCRImage(_png(roll / "lead.png", 200))
    lead.lit_level = 0.5
    w._on_capture(lead)
    assert w._tether_prompted and w._tether_lead_img is lead
    assert banner._nudging
    assert "Lead frame in" in banner._note.text()
    assert "background-color" in banner._black.styleSheet()      # highlighted
    assert not hasattr(w, "_prompt_bwpoint_for_session")           # no pop-up any more
    ccr_backend.black_point_bgr = (50000.0, 40000.0, 30000.0)      # sampled
    w.refresh_tether_banner()
    assert not banner._nudging and banner._black.styleSheet() == ""
    ccr_backend.black_point_bgr = None
