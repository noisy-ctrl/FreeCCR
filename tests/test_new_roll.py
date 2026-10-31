#!/usr/bin/env python3
"""File → New Roll… (spec/new-roll.md): closes the open images with their
edits saved, resets the roll's black point and film stock as a new batch
does, empties Needs attention, and keeps Hot Folder Tether watching with the
lead frame starting over."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.ccr_backend import ccr_backend  # noqa: E402
from test_tether_start_window import _png, mw  # noqa: E402,F401


def _file_menu(w):
    for a in w.menuBar().actions():
        if a.text() == "File":
            return [x for x in a.menu().actions() if not x.isSeparator()]
    raise AssertionError("no File menu")


def _roll_state():
    ccr_backend.black_point_bgr = (50000.0, 40000.0, 30000.0)
    ccr_backend.white_point_bgr = (9000.0, 8000.0, 7000.0)


def test_new_roll_is_first_in_the_file_menu(mw):
    w = mw[0]
    first = _file_menu(w)[0]
    assert first.text() == "New Roll…" and first is w.new_roll_action
    assert first.shortcut() == QKeySequence("Ctrl+N")


def test_cancel_keeps_everything(mw):
    w, asked, answer, saves, open_images, roll = mw
    open_images(3)
    _roll_state()
    asked_n = []
    w._confirm_new_roll = lambda n: asked_n.append(n) or False
    assert w.new_roll() is False
    assert asked_n == [3] and len(ccr_backend.images) == 3
    assert ccr_backend.black_point_bgr is not None and not saves


def test_new_roll_saves_closes_and_resets_the_roll(mw):
    w, asked, answer, saves, open_images, roll = mw
    open_images(3)
    _roll_state()
    stocks, hints = [], []
    w.sliders_panel.reset_film_stock_combo = lambda: stocks.append(1)
    w.sliders_panel.set_temporary_hint = lambda m, duration=0: hints.append(m)
    w._on_needs_attention({"paths": [str(roll / "x.raf")], "thumbs": [],
                           "reason": "left over"}, hint=False)
    assert w._tether_attention
    w._confirm_new_roll = lambda n: True
    assert w.new_roll() is True
    assert ccr_backend.images == [] and w.thumbnail_list.thumbnail_list.count() == 0
    assert saves and saves[0] == 3                       # edits saved before closing
    assert ccr_backend.black_point_bgr is None and ccr_backend.white_point_bgr is None
    assert stocks == [1]
    assert w._tether_attention == [] and w.thumbnail_list._attention == []
    assert hints == ["New roll: closed 3 images (edits saved)."]


def test_with_nothing_open_it_does_not_ask(mw):
    w = mw[0]
    _roll_state()
    w._confirm_new_roll = lambda n: (_ for _ in ()).throw(AssertionError("asked"))
    assert w.new_roll() is True
    assert ccr_backend.black_point_bgr is None


def test_tethering_carries_on_with_the_lead_starting_over(mw):
    w, asked, answer, saves, open_images, roll = mw
    from core.ccr_image import CCRImage
    from core.scan_type import TripletGrouper
    w.enter_tethering()
    folder = w._tether_folder
    lead = CCRImage(_png(roll / "lead.png", 200))
    lead.lit_level = 0.5
    w._on_capture(lead)
    assert w._tether_prompted and w._tether_count >= 1
    w._tether_grouper = TripletGrouper()
    w._tether_grouper.add(str(roll / "r1.raf"))           # the old roll's tail
    seen = {}
    w._confirm_new_roll = lambda n: seen.setdefault("n", n) or True
    assert w.new_roll() is True
    assert seen["n"] == 1
    assert w._tether_active and w._tether_folder == folder
    assert w._tether_count == 0 and not w._tether_prompted
    assert w._tether_lead_img is None and w._tether_last_lit is None
    assert not w.tether_banner._nudging
    assert [e["short"] for e in w._tether_attention] == ["Frames missing"]
    assert w._tether_attention[0]["paths"] == [str(roll / "r1.raf")]
    # the next lit capture is the new roll's lead
    nxt = CCRImage(_png(roll / "next.png", 190))
    nxt.lit_level = 0.5
    w._on_capture(nxt)
    assert w._tether_prompted and w._tether_lead_img is nxt


def test_the_question_names_the_watched_folder(mw, monkeypatch):
    w, asked, answer, saves, open_images, roll = mw
    from PySide6.QtWidgets import QMessageBox
    texts = {}

    def fake_exec(box):
        texts["text"], texts["info"] = box.text(), box.informativeText()
        texts["default"] = box.defaultButton().text()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert w._confirm_new_roll(1) is False                   # nothing clicked = cancel
    assert texts["text"] == "Close the 1 open image and start a new roll?"
    assert "Hot Folder Tether" not in texts["info"] and texts["default"] == "Cancel"
    w.enter_tethering()
    w._confirm_new_roll(12)
    assert texts["text"].startswith("Close the 12 open images")
    assert f"keeps watching “{os.path.basename(str(roll))}”" in texts["info"]
