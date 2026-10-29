#!/usr/bin/env python3
"""Starting Hot Folder Tether with images open: add to them, start blank, or
cancel (spec/tether-start-choice.md)."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

_app = QApplication.instance() or QApplication([])

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from core.ccr_backend import ccr_backend  # noqa: E402


@pytest.fixture
def mw(tmp_path, monkeypatch):
    from ui.main_window import MainWindow
    from core.ccr_image import CCRImage
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()
    ccr_backend.positive_mode = False
    w = MainWindow()
    monkeypatch.setattr(w._settings, "setValue", lambda *a, **k: None)
    roll = tmp_path / "roll"
    roll.mkdir()
    picked = {"folder": str(roll)}
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        lambda *a, **k: picked["folder"])
    asked = []
    answer = {"v": "add"}
    w._tether_start_choice = lambda n: (asked.append(n), answer["v"])[1]
    saves = []
    real_save = ccr_backend.save_catalog
    monkeypatch.setattr(ccr_backend, "save_catalog",
                        lambda *a, **k: (saves.append(len(ccr_backend.images)), real_save(*a, **k))[1])

    def open_images(n):
        for i in range(n):
            p = tmp_path / f"old{i}.png"
            cv2.imwrite(str(p), np.full((30, 30, 3), 80 + i, np.uint8))
            img = CCRImage(str(p))
            ccr_backend.images.append(img)
            ccr_backend.file_paths.append(img.file_path)
        w.thumbnail_list.load_thumbnails()

    yield w, asked, answer, picked, saves, open_images, tmp_path
    w.stop_tethering()
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()


def test_nothing_open_nothing_asked(mw):
    w, asked, answer, picked, saves, open_images, tmp = mw
    w.enter_tethering()
    assert w._tether_active and asked == []


def test_add_keeps_the_open_images(mw):
    w, asked, answer, picked, saves, open_images, tmp = mw
    open_images(2)
    w.enter_tethering()
    assert asked == [2] and w._tether_active
    assert len(ccr_backend.images) == 2
    assert w.thumbnail_list.thumbnail_list.count() == 2


def test_blank_closes_them_after_saving(mw):
    w, asked, answer, picked, saves, open_images, tmp = mw
    open_images(3)
    answer["v"] = "blank"
    w.enter_tethering()
    assert asked == [3] and w._tether_active
    assert ccr_backend.images == [] and ccr_backend.file_paths == []
    assert w.thumbnail_list.thumbnail_list.count() == 0
    assert saves and saves[0] == 3                 # saved while they were still open
    from core.ccr_image import CCRImage
    p = tmp / "roll" / "new.png"
    cv2.imwrite(str(p), np.full((30, 30, 3), 200, np.uint8))
    w._prompt_bwpoint_for_session = lambda: None
    w._on_capture(CCRImage(str(p)))
    assert len(ccr_backend.images) == 1 and w.thumbnail_list.thumbnail_list.count() == 1


def test_cancel_changes_nothing(mw):
    w, asked, answer, picked, saves, open_images, tmp = mw
    open_images(1)
    answer["v"] = None
    w.enter_tethering()
    assert asked == [1] and not w._tether_active
    assert len(ccr_backend.images) == 1


def test_no_folder_no_question(mw):
    w, asked, answer, picked, saves, open_images, tmp = mw
    open_images(1)
    picked["folder"] = ""
    w.enter_tethering()
    assert asked == [] and not w._tether_active and len(ccr_backend.images) == 1


def test_the_real_question_offers_three_answers(mw, monkeypatch):
    """The dialog itself: Add to Them (default), Start Blank, Cancel."""
    from ui.main_window import MainWindow
    from PySide6.QtWidgets import QMessageBox
    w = mw[0]
    seen = {}

    def fake_exec(box):
        seen["buttons"] = [b.text() for b in box.buttons()]
        seen["default"] = box.defaultButton().text()
        seen["text"] = box.text()
        for b in box.buttons():
            if b.text() == "Start Blank":
                b.click()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert MainWindow._tether_start_choice(w, 2) == "blank"
    assert {"Add to Them", "Start Blank"} <= set(seen["buttons"])
    assert seen["default"] == "Add to Them" and seen["text"] == "2 images are open."
