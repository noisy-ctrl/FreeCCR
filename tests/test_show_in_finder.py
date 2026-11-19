#!/usr/bin/env python3
"""Right-click a thumbnail → Show in Finder (spec/show-in-finder.md)."""
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import cv2  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core.ccr_backend import ccr_backend  # noqa: E402
from core.ccr_image import CCRImage  # noqa: E402
from utils import reveal_files  # noqa: E402


def test_labels_and_commands():
    assert reveal_files.menu_label("darwin") == "Show in Finder"
    assert reveal_files.menu_label("win32") == "Show in Explorer"
    assert reveal_files.menu_label("linux") == "Show in File Manager"
    a, b, c = "/r/A_1RED.raf", "/r/A_2GREEN.raf", "/s/B.raf"
    assert reveal_files.commands([a, b, c], "darwin") == [["open", "-R", a, b, c]]
    win = reveal_files.commands([a, b, c], "win32")
    assert win == [["explorer", "/select," + os.path.normpath(a)],
                   ["explorer", "/select," + os.path.normpath(c)]]
    assert reveal_files.commands([a, b, c], "linux") == [["xdg-open", "/r"], ["xdg-open", "/s"]]
    assert reveal_files.commands([], "darwin") == []
    ran = []
    assert reveal_files.reveal([a], "darwin", run=ran.append)
    assert ran == [["open", "-R", a]]
    assert not reveal_files.reveal([], "darwin", run=ran.append)


def test_frame_files_are_a_merges_sources(tmp_path):
    files = []
    for n in ("S_1RED.png", "S_2GREEN.png", "S_3BLUE.png"):
        p = tmp_path / n
        cv2.imwrite(str(p), np.full((8, 8, 3), 100, np.uint8))
        files.append(str(p))
    merged = type("I", (), {"merge_sources": files, "file_path": files[0]})()
    assert reveal_files.frame_files(merged) == [os.path.normpath(f) for f in files]
    single = type("I", (), {"merge_sources": None, "file_path": files[1]})()
    assert reveal_files.frame_files(single) == [os.path.normpath(files[1])]
    gone = type("I", (), {"merge_sources": None, "file_path": str(tmp_path / "x.raf")})()
    assert reveal_files.frame_files(gone) == []


def test_the_thumbnail_menu_reveals_the_selected_frames(tmp_path, monkeypatch):
    from widgets.thumbnail_list import ThumbnailList
    paths = []
    imgs = []
    for n in ("one.png", "two.png"):
        p = str(tmp_path / n)
        cv2.imwrite(p, np.full((40, 60, 3), 120, np.uint8))
        paths.append(p)
        imgs.append(CCRImage(p))
    monkeypatch.setattr(ccr_backend, "images", imgs)
    tl = ThumbnailList(lambda *_a: None)
    from unittest import mock
    main = mock.MagicMock()
    main.sliders_panel.clipboard = None
    monkeypatch.setattr(tl, "_main_window", lambda: main)
    tl.load_thumbnails()
    tl.thumbnail_list.selectAll()
    shown = []
    monkeypatch.setattr(reveal_files, "reveal", lambda ps, *a, **k: shown.append(list(ps)))
    labels = []

    def pick(menu, *_a):
        acts = [a for a in menu.actions() if not a.isSeparator()]
        labels.extend(a.text() for a in acts)
        return next(a for a in acts if a.text() == reveal_files.menu_label())

    monkeypatch.setattr(QMenu, "exec_", pick)
    item = tl.thumbnail_list.item(0)
    tl.show_context_menu(tl.thumbnail_list.visualItemRect(item).center())
    assert reveal_files.menu_label() in labels
    assert shown == [[os.path.normpath(p) for p in paths]]
