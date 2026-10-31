#!/usr/bin/env python3
"""Right-click Crop → Reset Crop (spec/crop-reset-menu.md)."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.ccr_backend import ccr_backend, CCRBackend  # noqa: E402
from test_tether_start_window import _png, mw  # noqa: E402,F401


def _cropped(w, open_images, n=3):
    open_images(n)
    imgs = ccr_backend.images
    imgs[0].crop_rect, imgs[0].crop_angle = (0.1, 0.1, 0.9, 0.9), 1.5
    imgs[0].auto_crop_example = {"rect": (0.1, 0.1, 0.9, 0.9)}
    imgs[2].crop_rect = (0.2, 0.2, 0.8, 0.8)
    imgs[2].auto_crop_rect = (0.2, 0.2, 0.8, 0.8)
    w.thumbnail_list.thumbnail_list.setCurrentRow(0)
    _app.processEvents()
    return imgs


def test_reset_crop_on_the_frame_on_screen(mw):
    w, asked, answer, saves, open_images, roll = mw
    imgs = _cropped(w, open_images)
    panel = w.sliders_panel
    acts = panel._crop_menu_actions()
    assert [a[0] for a in acts] == ["Reset Crop"] and acts[0][2]
    assert not CCRBackend.is_crop_candidate(imgs[0])           # confirmed with Done
    undo_before = len(imgs[0].undo_stack)
    panel._reset_crops(acts[0][1])
    assert imgs[0].crop_rect is None and imgs[0].crop_angle == 0.0
    assert imgs[0].auto_crop_example is None and imgs[0].auto_crop_rect is None
    assert CCRBackend.is_crop_candidate(imgs[0])               # auto crop may again
    assert len(imgs[0].undo_stack) == undo_before + 1
    assert imgs[2].crop_rect is not None                       # others untouched
    assert not panel._crop_menu_actions()[0][2]                # nothing left to reset
    assert imgs[0].pop_undo_state()
    assert imgs[0].crop_rect == (0.1, 0.1, 0.9, 0.9)


def test_reset_crop_on_the_selection(mw):
    w, asked, answer, saves, open_images, roll = mw
    imgs = _cropped(w, open_images)
    tl = w.thumbnail_list.thumbnail_list
    for r in range(3):
        tl.item(r).setSelected(True)
    acts = w.sliders_panel._crop_menu_actions()
    assert acts[1][0] == "Reset Crop on the 3 Selected Frames" and acts[1][2]
    undo1 = len(imgs[1].undo_stack)
    assert w.image_preview.reset_crops(acts[1][1]) == 2
    assert all(im.crop_rect is None for im in imgs)
    assert len(imgs[1].undo_stack) == undo1                   # had no crop: no undo entry
