#!/usr/bin/env python3
"""File → Close Session… (spec/close-session.md): after Capture, between two
separators; asks Yes / No, then closes everything loaded as New Roll does;
Quit closes the window and stays in the File menu."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.ccr_backend import ccr_backend  # noqa: E402
from test_tether_start_window import _png, mw  # noqa: E402,F401


_MENUS = []


def _file_actions(w):
    for a in w.menuBar().actions():
        if a.text() == "File":
            m = a.menu()
            _MENUS.append(m)                 # keep the wrapper alive
            return list(m.actions())
    raise AssertionError("no File menu")


def test_close_session_sits_after_capture_between_lines_and_quit_is_last(mw):
    w = mw[0]
    bar_actions = w.menuBar().actions()
    _MENUS.append(bar_actions)                    # keep the wrappers alive
    file_act = next(a for a in bar_actions if a.text() == "File")
    m = file_act.menu()
    _MENUS.append(m)
    items = ["---" if x.isSeparator() else x.text() for x in m.actions()]
    i = items.index("Close Session…")
    assert items[i - 2:i + 2] == ["Capture…", "---", "Close Session…", "---"]
    assert items[-2:] == ["---", "Quit"]
    assert w.quit_action.menuRole() == QAction.NoRole
    assert w.quit_action.shortcut().toString() == "Ctrl+Q"


def test_no_keeps_everything(mw):
    w, asked, answer, saves, open_images, roll = mw
    open_images(2)
    seen = []
    w._confirm_close_session = lambda n: seen.append(n) or False
    w._confirm_new_roll = lambda n: (_ for _ in ()).throw(AssertionError("asked twice"))
    assert w.close_session() is False
    assert seen == [2] and len(ccr_backend.images) == 2 and not saves


def test_yes_saves_and_closes_everything(mw):
    w, asked, answer, saves, open_images, roll = mw
    open_images(3)
    hints = []
    w.sliders_panel.set_temporary_hint = lambda m, duration=0: hints.append(m)
    w._confirm_close_session = lambda n: True
    w._confirm_new_roll = lambda n: (_ for _ in ()).throw(AssertionError("asked twice"))
    assert w.close_session() is True
    assert ccr_backend.images == [] and w.thumbnail_list.thumbnail_list.count() == 0
    assert saves and saves[0] == 3
    assert hints == ["Closed 3 images: edits saved, the files are still on disk."]
    for p in roll.iterdir():
        assert p.exists()                             # nothing deleted


def test_nothing_loaded_doesnt_ask(mw):
    w = mw[0]
    w._confirm_close_session = lambda n: (_ for _ in ()).throw(AssertionError("asked"))
    assert w.close_session() is False


def test_the_question_is_yes_no_and_says_files_stay(mw, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    w = mw[0]
    seen = {}

    def fake_exec(box):
        seen["text"] = box.text() + " " + box.informativeText()
        seen["buttons"] = box.standardButtons()
        seen["default"] = box.defaultButton()
        return QMessageBox.No
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert w._confirm_close_session(4) is False
    assert "Close all 4 loaded images?" in seen["text"]
    assert "stay on disk" in seen["text"]
    assert seen["buttons"] == QMessageBox.Yes | QMessageBox.No
    assert seen["default"] is not None and seen["default"].text().replace("&", "") == "No"
