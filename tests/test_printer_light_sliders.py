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


def test_the_bar_drags_relatively_and_steps():
    b = BarSlider("Cyan", -10, 10, step=0.5, key_steps=(0.5, 1.0), tick=1)
    b.resize(206, 26)                       # 200 px of travel: 0.1 a pixel
    got = []
    b.valueChanged.connect(got.append)
    _mouse(b, QEvent.MouseButtonPress, 30)  # a press far left doesn't jump
    assert b.value() == 0 and got == []
    _mouse(b, QEvent.MouseMove, 31)         # under DRAG_PX: nothing yet
    assert b.value() == 0
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
    b.setEnabled(False)
    _key(b, Qt.Key_Right)
    assert b.value() == 10.0


def test_rapid_clicks_all_step_and_a_double_click_never_resets():
    """The user, 2026-11-19: "I need to be able to click rapidly and it's
    registering as double click": Qt's double-click is the second press of
    a quick pair, so it steps like any click."""
    b = BarSlider("Cyan", -10, 10, step=0.5, key_steps=(0.5, 1.0), tick=1)
    b.resize(206, 26)
    for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease,
                 QEvent.MouseButtonDblClick, QEvent.MouseButtonRelease,
                 QEvent.MouseButtonPress, QEvent.MouseButtonRelease,
                 QEvent.MouseButtonDblClick, QEvent.MouseButtonRelease):
        _mouse(b, kind, 180)
    assert b.value() == 2.0                 # four clicks, four steps
    _mouse(b, QEvent.MouseButtonDblClick, 100)
    _mouse(b, QEvent.MouseMove, 120)        # a drag can start on one too
    _mouse(b, QEvent.MouseButtonRelease, 120)
    assert b.value() == 4.0


def test_right_clicking_a_bar_offers_its_own_reset(panel):
    from PySide6.QtWidgets import QMenu
    sp = panel
    sp._printer_light("C", "+")
    sp._printer_light("M", "+")
    menu = sp._fill_printer_menu(QMenu(), "C")
    texts = [a.text() for a in menu.actions() if a.text()]
    assert texts[:2] == ["Reset Cyan to 0", "Reset printer lights to 0"]
    menu.actions()[0].trigger()
    assert sp._pl_slider("C").value() == 0 and sp._pl_slider("M").value() != 0
    menu = sp._fill_printer_menu(QMenu(), "C")
    assert not menu.actions()[0].isEnabled()           # nothing to reset
    sp._printer_lights_reset()
    assert sp._pl_slider("M").value() == 0


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


def test_a_click_steps_towards_the_side_clicked():
    """The user, 2026-11-19: "click on the right side and it adds the amount"."""
    b = BarSlider("Cyan", -10, 10, step=0.5, key_steps=(0.5, 1.0), tick=1)
    b.resize(206, 26)
    released = []
    b.released.connect(lambda: released.append(1))
    for x, mods, want in ((180, Qt.NoModifier, 0.5), (190, Qt.ShiftModifier, 1.5),
                          (20, Qt.NoModifier, 1.0), (40, Qt.ShiftModifier, 0.0),
                          (40, Qt.NoModifier, -0.5)):
        _mouse(b, QEvent.MouseButtonPress, x, mods)
        _mouse(b, QEvent.MouseMove, x + 1, mods)          # a twitch is still a click
        _mouse(b, QEvent.MouseButtonRelease, x + 1, mods)
        assert b.value() == want, (x, mods)
    assert len(released) == 5
    g = BarSlider("Gamma", -100, 100, step=1, key_steps=(2, 4), tick=10)
    g.resize(206, 26)
    _mouse(g, QEvent.MouseButtonPress, 150)
    _mouse(g, QEvent.MouseButtonRelease, 150)
    assert g.value() == 2


def test_right_click_switches_the_view_and_sets_the_step(panel):
    """The user, 2026-11-19: right-click Printer Lights to change to sliders
    and back, and keep choosing the increment."""
    from PySide6.QtWidgets import QMenu
    sp = panel
    menu = sp._fill_printer_menu(QMenu())
    texts = [a.text() for a in menu.actions() if a.text()]
    assert texts == ["Reset printer lights to 0", "Show as Buttons", "Show as Sliders",
                     "Each Click ½ point (γ 2)", "Each Click 1 point (γ 4)",
                     "Each Click 2 points (γ 8)"]
    by = {a.text(): a for a in menu.actions()}
    assert by["Show as Buttons"].isChecked() and by["Each Click ½ point (γ 2)"].isChecked()
    by["Show as Sliders"].trigger()
    assert sp.printer_style() == "sliders"
    assert sp._settings.value(sp.PRINTER_STYLE_KEY) == "sliders"
    by["Each Click 1 point (γ 4)"].trigger()
    assert sp.printer_step() == 1.0 and float(sp._settings.value(sp.PRINTER_STEP_KEY)) == 1.0
    # the pad moves a whole point a click now, Gamma 4
    sp._printer_light("C", "+")
    assert sp._pl_slider("C").value() == -3
    sp._printer_light("G", "+")
    assert sp._pl_slider("G").value() == 4
    # and the bars step the same, Shift twice it
    _key(sp._pl_bars["Y"], Qt.Key_Right)
    assert sp._pl_slider("Y").value() == -3
    _key(sp._pl_bars["Y"], Qt.Key_Right, Qt.ShiftModifier)
    assert sp._pl_slider("Y").value() == -9
    assert "one point per click" in sp._pl_buttons[("C", "+")].toolTip()
    sp.set_printer_step(0.5)


def test_the_header_menu_has_them_too_and_bars_are_taller(panel):
    sp = panel
    menu = sp.printer_section.header_menu()
    texts = [a.text() for a in menu.actions() if a.text()]
    assert texts[0] == "Reset printer lights to 0" and "Show as Sliders" in texts
    assert texts[-1].startswith("Hide")
    from ui import theme
    assert sp._pl_bars["C"].height() == sp.PL_BAR_H > theme.CONTROL_H
