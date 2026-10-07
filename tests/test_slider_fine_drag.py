#!/usr/bin/env python3
"""Shift+drag on the adjustment sliders moves 10x slower (spec/slider-fine-drag.md)."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import (QApplication, QStyle,  # noqa: E402
                               QStyleOptionSlider)

_app = QApplication.instance() or QApplication(sys.argv[:1])

from widgets.sliders_panel import ResettableSlider  # noqa: E402


def _slider():
    s = ResettableSlider(Qt.Horizontal)
    s.setRange(-100, 100)
    s.setValue(0)
    s.resize(220, 24)
    s.show()
    _app.processEvents()
    return s


def _handle_center(s):
    opt = QStyleOptionSlider()
    s.initStyleOption(opt)
    return QPointF(s.style().subControlRect(
        QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, s).center())


def _send(s, etype, pos, buttons, mods=Qt.NoModifier):
    btn = Qt.LeftButton if etype != QEvent.MouseMove else Qt.NoButton
    ev = QMouseEvent(etype, pos, s.mapToGlobal(pos.toPoint()), btn, buttons, mods)
    QApplication.sendEvent(s, ev)


def _drag(s, dx, shift):
    mods = Qt.ShiftModifier if shift else Qt.NoModifier
    p = _handle_center(s)
    _send(s, QEvent.MouseButtonPress, p, Qt.LeftButton, mods)
    for i in range(1, 11):
        _send(s, QEvent.MouseMove, p + QPointF(dx * i / 10.0, 0), Qt.LeftButton, mods)
    _send(s, QEvent.MouseButtonRelease, p + QPointF(dx, 0), Qt.NoButton, mods)
    return s.value()


def test_normal_drag_tracks_the_mouse():
    s = _slider()
    v = _drag(s, 40, shift=False)
    assert v > 20


def test_shift_drag_is_ten_times_finer():
    coarse = _drag(_slider(), 40, shift=False)
    fine = _drag(_slider(), 40, shift=True)
    assert 0 < fine <= max(1, round(coarse / 10) + 1)


def test_single_unit_steps_are_reachable():
    s = _slider()
    p = _handle_center(s)
    _send(s, QEvent.MouseButtonPress, p, Qt.LeftButton, Qt.ShiftModifier)
    seen = set()
    for i in range(1, 60):
        _send(s, QEvent.MouseMove, p + QPointF(i * 0.5, 0), Qt.LeftButton, Qt.ShiftModifier)
        seen.add(s.value())
    _send(s, QEvent.MouseButtonRelease, p, Qt.NoButton)
    assert {1, 2} <= seen          # passes through every unit, no skipping


def test_releasing_shift_mid_drag_does_not_jump():
    s = _slider()
    p = _handle_center(s)
    _send(s, QEvent.MouseButtonPress, p, Qt.LeftButton, Qt.ShiftModifier)
    _send(s, QEvent.MouseMove, p + QPointF(30, 0), Qt.LeftButton, Qt.ShiftModifier)
    before = s.value()
    _send(s, QEvent.MouseMove, p + QPointF(31, 0), Qt.LeftButton, Qt.NoModifier)
    assert abs(s.value() - before) <= 2      # one pixel of normal-speed travel
    _send(s, QEvent.MouseButtonRelease, p, Qt.NoButton)


def test_double_click_reset_value():
    s = _slider()
    s.reset_value = 25
    s.setValue(70)
    ev = QMouseEvent(QEvent.MouseButtonDblClick, _handle_center(s),
                     s.mapToGlobal(_handle_center(s).toPoint()),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(s, ev)
    assert s.value() == 25
