#!/usr/bin/env python3
"""Printer Lights as sliders (spec/printer-light-sliders.md)."""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import QPoint, QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QKeyEvent  # noqa: E402
from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from widgets.bar_slider import BarSlider  # noqa: E402


def _mouse(w, kind, x, mods=Qt.NoModifier):
    pos = QPointF(x, w.height() / 2)
    ev = QMouseEvent(kind, pos, w.mapToGlobal(pos), Qt.LeftButton,
                     Qt.LeftButton if kind != QEvent.MouseButtonRelease else Qt.NoButton,
                     mods)
    _app.sendEvent(w, ev)


def _key(w, key, mods=Qt.NoModifier):
    _app.sendEvent(w, QKeyEvent(QEvent.KeyPress, key, mods))


@pytest.fixture(autouse=True)
def _no_shift_left_behind():
    """Qt keeps the last input event's modifiers app-wide: let Shift go, or
    the next test file's clicks read as Shift-clicks."""
    yield
    from PySide6.QtWidgets import QWidget
    w = QWidget()
    # (only a key press resets it: a release or a mouse event doesn't)
    _app.sendEvent(w, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert not (_app.keyboardModifiers() & Qt.ShiftModifier)


def test_the_bar_drags_relatively_steps_and_resets():
    b = BarSlider("Cyan", -10, 10, step=0.5, key_steps=(0.5, 1.0), tick=1)
    b.resize(206, 26)                       # 200 px of travel: 0.1 a pixel
    got = []
    b.valueChanged.connect(got.append)
    _mouse(b, QEvent.MouseButtonPress, 30)  # a click far left doesn't jump
    assert b.value() == 0 and got == []
    _mouse(b, QEvent.MouseMove, 50)         # +20 px = +2
    assert b.value() == 2.0
    _mouse(b, QEvent.MouseMove, 52, Qt.ShiftModifier)   # finer: +22 px x 0.25 = 0.55
    assert b.value() == 0.5
    _mouse(b, QEvent.MouseButtonRelease, 52)
    _key(b, Qt.Key_Right)
    assert b.value() == 1.0
    _key(b, Qt.Key_Left, Qt.ShiftModifier)
    assert b.value() == 0.0
    b.setValue(50)
    assert b.value() == 10.0                # clamped
    _mouse(b, QEvent.MouseButtonDblClick, 100)
    assert b.value() == 0.0
    b.setEnabled(False)
    _key(b, Qt.Key_Right)
    assert b.value() == 0.0


@pytest.fixture
def panel(tmp_path):
    import cv2
    import numpy as np
    from core.ccr_backend import ccr_backend
    from core.ccr_image import CCRImage
    from ui.main_window import MainWindow
    w = MainWindow()
    sp = w.sliders_panel
    sp._settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    p = tmp_path / "a.png"
    cv2.imwrite(str(p), np.full((40, 60, 3), 120, np.uint8))
    img = CCRImage(str(p))
    ccr_backend.images.append(img)
    ccr_backend.file_paths.append(img.file_path)
    sp.set_sliders_enabled(True)
    sp.current_idx = 0
    yield sp
    ccr_backend.images.clear()
    ccr_backend.file_paths.clear()


def test_the_option_switches_pad_and_bars(panel):
    sp = panel
    assert sp.printer_style() == "buttons"
    assert not sp._pl_pad_widget.isHidden() and sp._pl_bars_widget.isHidden()
    sp.set_printer_style("sliders", save=True)
    assert sp._pl_pad_widget.isHidden() and not sp._pl_bars_widget.isHidden()
    assert sp._settings.value(sp.PRINTER_STYLE_KEY) == "sliders"
    assert [b.label() for b in sp._pl_bars.values()] == [
        "Density", "Gamma", "Cyan", "Magenta", "Yellow"]


def test_the_bars_move_the_same_sliders(panel):
    sp = panel
    sp.set_printer_style("sliders")
    bars = sp._pl_bars
    bars["C"].setValue(1.5, emit=True)       # more cyan = a negative R Shift
    assert sp._pl_slider("C").value() == -4.5
    bars["D"].setValue(2, emit=True)         # darker = a negative Master Shift
    assert sp._pl_slider("D").value() == -6
    bars["G"].setValue(6, emit=True)
    assert sp._pl_slider("G").value() == 6
    _key(bars["M"], Qt.Key_Right)            # half a point more magenta
    assert sp._pl_slider("M").value() == -1.5
    _key(bars["G"], Qt.Key_Left, Qt.ShiftModifier)
    assert sp._pl_slider("G").value() == 6 - sp.PL_GAMMA_SHIFT
    assert bars["C"].value_text() == "+1.5" and bars["Y"].value_text() == "0"
    # the underlying slider moving (a frame loaded, undo, the pad) moves the bar
    sp._pl_slider("Y").setValue(6)
    assert bars["Y"].value() == -2.0 and bars["Y"].value_text() == "−2"
    sp._printer_lights_reset()
    assert all(sp._pl_slider(c).value() == 0 for c in "DGCMY")
    assert all(b.value() == 0 for b in bars.values())


def test_bars_are_off_without_a_frame(panel):
    sp = panel
    sp.set_printer_style("sliders")
    sp.set_sliders_enabled(False)
    assert not any(b.isEnabled() for b in sp._pl_bars.values())


def test_the_settings_page_sets_it(panel):
    from widgets.settings_dialog import SettingsDialog
    sp = panel
    dlg = SettingsDialog(sp.window())
    combo = dlg._combo_printer_style
    combo.setCurrentIndex(combo.findData("sliders"))
    dlg.accept()
    assert sp.printer_style() == "sliders"
    assert sp._settings.value(sp.PRINTER_STYLE_KEY) == "sliders"
