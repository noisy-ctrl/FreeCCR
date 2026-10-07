#!/usr/bin/env python3
"""Opening files asks before replacing a loaded session, and files dropped
from Finder/Explorer open through the same path. spec/open-confirm-and-drop.md."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import QEvent, QMimeData, QPoint, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QLineEdit  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core.ccr_backend import ccr_backend  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

_WINDOWS = []


def _window():
    w = MainWindow()
    _WINDOWS.append(w)
    return w


def _answer(monkeypatch, which):
    """Stub QMessageBox.exec so the confirm dialog 'clicks' Open or Cancel."""
    def _exec(box):
        for b in box.buttons():
            if box.buttonRole(b) == (QMessageBox.AcceptRole if which == "open"
                                     else QMessageBox.RejectRole):
                box._clicked = b
        return 0
    monkeypatch.setattr(QMessageBox, "exec", _exec)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: getattr(box, "_clicked", None))


class TestConfirm:
    def test_no_prompt_without_a_session(self, monkeypatch):
        w = _window()
        monkeypatch.setattr(ccr_backend, "images", [])
        monkeypatch.setattr(QMessageBox, "exec", lambda box: (_ for _ in ()).throw(
            AssertionError("must not prompt")))
        assert w._confirm_replace_session() is True

    def test_open_and_cancel(self, monkeypatch):
        w = _window()
        monkeypatch.setattr(ccr_backend, "images", [object(), object()])
        _answer(monkeypatch, "open")
        assert w._confirm_replace_session() is True
        _answer(monkeypatch, "cancel")
        assert w._confirm_replace_session() is False

    def test_cancel_stops_load_paths(self, monkeypatch, tmp_path):
        w = _window()
        monkeypatch.setattr(ccr_backend, "images", [object()])
        _answer(monkeypatch, "cancel")
        called = []
        monkeypatch.setattr(w, "_import_file_list", lambda f: called.append(f))
        monkeypatch.setattr(w, "_import_folder", lambda f: called.append(f))
        f = tmp_path / "a.tif"
        f.write_bytes(b"x")
        w.load_paths([str(f)])
        assert called == []


class TestDrop:
    def _mime(self, paths):
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(p) for p in paths])
        return md

    def test_external_file_drag_is_accepted_and_dropped(self, monkeypatch, tmp_path):
        w = _window()
        w.show()
        _app.processEvents()
        f = tmp_path / "scan.tif"
        f.write_bytes(b"x")
        md = self._mime([str(f)])
        target = w.image_preview.view.viewport()
        enter = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
        assert w.eventFilter(target, enter) is True
        assert enter.isAccepted()
        loaded = []
        monkeypatch.setattr(w, "load_paths", lambda p: loaded.append(p))
        drop = QDropEvent(QPoint(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
        assert w.eventFilter(target, drop) is True
        _app.processEvents()                     # the load is deferred
        assert loaded == [[str(f)]]

    def test_non_file_drags_pass_through(self):
        w = _window()
        md = QMimeData()
        md.setText("hello")
        enter = QDragEnterEvent(QPoint(1, 1), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
        assert w.eventFilter(w.image_preview.view.viewport(), enter) is False

    def test_arrow_keys_not_stolen_from_other_widgets(self):
        w = _window()
        edit = QLineEdit()
        ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier)
        assert w.eventFilter(edit, ev) is False
