#!/usr/bin/env python3
"""Printer Lights pad (spec/printer-lights.md): a front end to the Channel
Levels Shift sliders, 3 slider units (0.02 density) per printer point."""

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
    assert _val(panel, key) == -3                # + adds colour/density = lower value
    panel._pl_buttons[(ch, "-")].click()
    panel._pl_buttons[(ch, "-")].click()
    assert _val(panel, key) == 3


def test_shift_click_moves_four_points_and_readout_is_dcmy(panel, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    monkeypatch.setattr(QApplication, "keyboardModifiers",
                        staticmethod(lambda: Qt.ShiftModifier))
    panel._pl_buttons[("C", "+")].click()
    assert _val(panel, "ch_r_shift") == -12
    monkeypatch.setattr(QApplication, "keyboardModifiers",
                        staticmethod(lambda: Qt.NoModifier))
    panel._pl_buttons[("D", "-")].click()
    panel._pl_buttons[("D", "-")].click()
    assert _val(panel, "ch_master_shift") == 6
    assert panel._pl_readout.text().startswith("D −2   C +4   M 0   Y 0")


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
    panel._pl_buttons[("Y", "+")].click()
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
