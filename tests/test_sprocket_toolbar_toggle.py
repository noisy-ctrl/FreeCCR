#!/usr/bin/env python3
"""The toolbar's sprocket-holes button is the Settings → Film border switch,
one click away (spec/sprocket-toolbar-toggle.md)."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.ccr_backend import ccr_backend  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def test_the_button_flips_the_switch_and_follows_it():
    w = MainWindow()
    act = w.image_preview.sprocket_action
    before = ccr_backend.sprocket_mask_white
    try:
        assert act.isCheckable() and not act.icon().isNull()
        assert act.isChecked() == bool(before)
        act.trigger()                                   # a click
        assert ccr_backend.sprocket_mask_white == (not before)
        assert act.isChecked() == (not before)
        assert w._settings.value("adjust/sprocket_mask_white", type=bool) == (not before)
        w.on_sprocket_mask_toggled(bool(before))        # e.g. from Settings
        assert act.isChecked() == bool(before)
        assert ccr_backend.sprocket_mask_white == bool(before)
    finally:
        w.on_sprocket_mask_toggled(bool(before))
        w.hide()


def test_a_new_window_shows_the_saved_state():
    w = MainWindow()
    try:
        w.on_sprocket_mask_toggled(True)
        w2 = MainWindow()
        assert w2.image_preview.sprocket_action.isChecked()
        w.on_sprocket_mask_toggled(False)
        w3 = MainWindow()
        assert not w3.image_preview.sprocket_action.isChecked()
        w2.hide()
        w3.hide()
    finally:
        w.on_sprocket_mask_toggled(False)
        w.hide()


def test_it_follows_the_orientation_icons():
    """Revision 2026-10-30: after the rotate / flip tools, before Area."""
    w = MainWindow()
    try:
        acts = [a for a in w.image_preview.toolbar.actions() if not a.isSeparator()
                and (a.text() or not a.icon().isNull())]
        names = [a.text() for a in acts]
        i = names.index("Sprocket Holes White")
        assert names[i - 1] == "Mirror Horizontal"
        assert names.index("Rotate Left") < i < names.index("Area")
    finally:
        w.hide()
