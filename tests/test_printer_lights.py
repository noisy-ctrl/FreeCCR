#!/usr/bin/env python3
"""Printer Lights pad (spec/printer-lights.md): a front end to the Channel
Levels Shift sliders, 3 slider units (0.02 density) per printer point. A click
moves half a point, Shift-click one; the Shift sliders step in halves."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture
def panel(monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from widgets.sliders_panel import SlidersPanel
    p = SlidersPanel()
    p.current_idx = 0
    monkeypatch.setattr(p, "on_slider_changed", lambda: p._update_printer_readout())
    for s in p.sliders:
        s.setEnabled(True)
    return p


def _val(panel, key):
    return panel.sliders[panel.adjustment_keys.index(key)].value()


def test_one_point_is_three_slider_units_of_density():
    from widgets.sliders_panel import SlidersPanel
    from core.ccr_processor import CH_SLIDER_DIV, DEFAULT_DENSITY_SLOPE
    # a printer point is 0.025 log exposure; on the density base that is
    # slope * 0.025 display units, i.e. that many Shift-slider units
    assert SlidersPanel.PRINTER_POINT_UNITS == round(
        DEFAULT_DENSITY_SLOPE * 0.025 * CH_SLIDER_DIV)


@pytest.mark.parametrize("ch,key", [("C", "ch_r_shift"), ("M", "ch_g_shift"),
                                    ("Y", "ch_b_shift"), ("D", "ch_master_shift")])
def test_buttons_move_the_right_slider(panel, ch, key):
    panel._pl_buttons[(ch, "+")].click()
    assert _val(panel, key) == -1.5              # + adds colour/density = lower value
    panel._pl_buttons[(ch, "+")].click()
    assert _val(panel, key) == -3                # two halves = one point exactly
    for _ in range(4):
        panel._pl_buttons[(ch, "-")].click()
    assert _val(panel, key) == 3


def test_shift_click_moves_one_point_and_readout_is_dcmy(panel, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    monkeypatch.setattr(QApplication, "keyboardModifiers",
                        staticmethod(lambda: Qt.ShiftModifier))
    panel._pl_buttons[("C", "+")].click()
    panel._pl_buttons[("C", "+")].click()
    assert _val(panel, "ch_r_shift") == -6
    monkeypatch.setattr(QApplication, "keyboardModifiers",
                        staticmethod(lambda: Qt.NoModifier))
    panel._pl_buttons[("C", "+")].click()
    panel._pl_buttons[("D", "-")].click()
    assert _val(panel, "ch_master_shift") == 1.5
    assert panel._pl_readout.text().startswith("D −0.5   C +2.5   M 0   Y 0")


def test_pad_is_density_first_with_no_step_row(panel):
    grid = panel._pl_buttons[("D", "-")].parentWidget().layout()
    from PySide6.QtWidgets import QGridLayout
    lay = None
    for i in range(grid.count()):
        item = grid.itemAt(i)
        if isinstance(item.layout(), QGridLayout):
            lay = item.layout()
    assert lay is not None and lay.rowCount() == 2
    for col, ch in enumerate("DCMY"):
        assert lay.itemAtPosition(0, col).widget() is panel._pl_buttons[(ch, "-")]
        assert lay.itemAtPosition(1, col).widget() is panel._pl_buttons[(ch, "+")]
    assert not hasattr(panel, "_pl_step_group")


def test_readout_shows_fractions_of_hand_set_sliders(panel):
    panel.sliders[panel.adjustment_keys.index("ch_g_shift")].setValue(-4)
    panel._update_printer_readout()
    assert "M +1.3" in panel._pl_readout.text()


def test_reset_and_limits(panel, monkeypatch):
    hints = []
    monkeypatch.setattr(panel, "set_temporary_hint", lambda m, duration=0: hints.append(m))
    sl = panel.sliders[panel.adjustment_keys.index("ch_b_shift")]
    sl.setValue(-99)
    panel._pl_buttons[("Y", "+")].click()        # -100.5 clamps to the end
    assert sl.value() == -100
    panel._pl_buttons[("Y", "+")].click()
    assert sl.value() == -100 and hints and "limit" in hints[-1]
    panel._printer_lights_reset()
    assert all(_val(panel, k) == 0 for k in
               ("ch_r_shift", "ch_g_shift", "ch_b_shift", "ch_master_shift"))


def test_disabled_sliders_are_respected(panel):
    sl = panel.sliders[panel.adjustment_keys.index("ch_r_shift")]
    sl.setEnabled(False)
    panel._pl_buttons[("C", "+")].click()
    assert sl.value() == 0


def test_section_sits_between_crosstalk_and_channel_levels(panel):
    lay = panel.crosstalk_section.parentWidget().layout()
    order = [lay.indexOf(w) for w in (panel.crosstalk_section, panel.printer_section,
                                      panel.od_section)]
    assert order == sorted(order) and -1 not in order


# --- the half-step Shift sliders ---------------------------------------------------

SHIFTS = ("ch_master_shift", "ch_r_shift", "ch_g_shift", "ch_b_shift")


def _sl(panel, key):
    return panel.sliders[panel.adjustment_keys.index(key)]


def test_only_the_shift_sliders_step_in_halves(panel):
    from widgets.sliders_panel import HalfStepSlider
    for key, sl in zip(panel.adjustment_keys, panel.sliders):
        assert isinstance(sl, HalfStepSlider) == (key in SHIFTS), key
        assert sl.value_range() == (-100, 100) or key not in SHIFTS
    gain = _sl(panel, "ch_r_gain")
    gain.setValue(7)
    assert gain.value() == 7 and gain.maximum() == 100


def test_whole_values_stay_whole_numbers(panel):
    sl = _sl(panel, "ch_g_shift")
    sl.setValue(-4)
    assert sl.value() == -4 and type(sl.value()) is int
    sl.setValue(-4.5)
    assert sl.value() == -4.5 and sl.value_text() == "-4.5"
    assert panel.slider_value_labels[panel.adjustment_keys.index("ch_g_shift")].text() == "-4.5"


def test_half_values_survive_settings_round_trip(panel, monkeypatch):
    _sl(panel, "ch_b_shift").setValue(1.5)
    vals = panel.get_slider_values()
    assert vals["ch_b_shift"] == 1.5 and vals["ch_r_shift"] == 0
    _sl(panel, "ch_b_shift").setValue(0)
    monkeypatch.setattr(panel, "_read_active_settings", lambda idx: dict(vals))
    from core.ccr_backend import ccr_backend
    monkeypatch.setattr(ccr_backend, "images", [])      # no image: sliders only
    panel._load_active_layer(0)
    assert _sl(panel, "ch_b_shift").value() == 1.5
    i = panel.adjustment_keys.index("ch_b_shift")
    assert panel.slider_value_labels[i].text() == "1.5"


def test_wheel_moves_a_whole_unit_and_double_click_resets(panel):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    sl = _sl(panel, "ch_r_shift")
    sl.setValue(1.5)
    ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120),
                     Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    sl.wheelEvent(ev)
    assert sl.value() == 2.5
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    dbl = QMouseEvent(QEvent.MouseButtonDblClick, QPointF(5, 5), QPointF(5, 5),
                      Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    sl.mouseDoubleClickEvent(dbl)
    assert sl.value() == 0
