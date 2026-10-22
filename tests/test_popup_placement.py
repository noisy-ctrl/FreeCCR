#!/usr/bin/env python3
"""Hand-placed popups stay on screen, flipping up/left like a menu."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import QPoint, QRect, QSize  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

from utils.popup_placement import place_on_screen, popup_position  # noqa: E402

AVAIL = QRect(0, 25, 1440, 875)          # e.g. a laptop below the menu bar
SIZE = QSize(300, 400)


def test_opens_at_the_cursor_when_there_is_room():
    assert popup_position(QPoint(100, 100), SIZE, AVAIL) == QPoint(100, 100)


def test_flips_above_near_the_bottom():
    p = popup_position(QPoint(100, 800), SIZE, AVAIL)
    assert p == QPoint(100, 400)                       # bottom edge at the cursor


def test_flips_left_near_the_right_edge():
    p = popup_position(QPoint(1400, 100), SIZE, AVAIL)
    assert p == QPoint(1100, 100)


def test_corner_flips_both_ways():
    assert popup_position(QPoint(1400, 800), SIZE, AVAIL) == QPoint(1100, 400)


def test_clamped_when_it_fits_neither_way():
    tall = QSize(300, 860)
    p = popup_position(QPoint(100, 500), tall, AVAIL)
    assert AVAIL.contains(QRect(p, tall))              # fully visible either way
    huge = QSize(2000, 2000)
    p = popup_position(QPoint(700, 400), huge, AVAIL)
    assert (p.x(), p.y()) == (AVAIL.left(), AVAIL.top())


def test_respects_a_screen_offset():
    second = QRect(1440, 0, 1920, 1080)                # a monitor to the right
    p = popup_position(QPoint(3300, 1000), SIZE, second)
    assert second.contains(QRect(p, SIZE))


def test_place_on_screen_uses_the_real_screen():
    w = QWidget()
    w.resize(SIZE)
    avail = QGuiApplication.primaryScreen().availableGeometry()
    corner = QPoint(avail.right() - 5, avail.bottom() - 5)
    place_on_screen(w, corner)
    assert avail.contains(QRect(w.pos(), w.size()))


def test_both_popups_are_placed_on_screen(monkeypatch):
    """The Colour Organics and Highlights/Shadows/Punch × popups open through it."""
    import inspect
    from widgets import sliders_panel
    src = inspect.getsource(sliders_panel.SlidersPanel._open_organics_popup)
    assert "place_on_screen(pop, global_pos)" in src and "pop.move(" not in src
    src = inspect.getsource(sliders_panel.SlidersPanel._open_xtone_popup)
    assert "place_on_screen(pop, global_pos)" in src and "pop.move(" not in src
