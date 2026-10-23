#!/usr/bin/env python3
"""Upstream 2.2.1 merged into the mods: its app-wide Up/Down and Settings
wheel behaviour, together with the mods' drag-and-drop and wheel routing."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDragEnterEvent, QKeyEvent, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QScrollArea,  # noqa: E402
                               QVBoxLayout, QWidget)

_app = QApplication.instance() or QApplication(sys.argv[:1])

from ui.main_window import MainWindow  # noqa: E402

_KEEP = []


def _window():
    w = MainWindow()
    _KEEP.append(w)
    return w


def test_up_down_switch_image_from_a_focused_slider():
    """2.2.1: a focused slider in the main window no longer eats Up/Down."""
    w = _window()
    seen = []
    w.thumbnail_list.thumbnail_list.keyPressEvent = lambda e: seen.append(e.key())
    slider = w.sliders_panel.sliders[0]
    assert slider.window() is w
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier)
    assert w.eventFilter(slider, ev) is True
    assert seen == [Qt.Key_Down]


def test_up_down_left_alone_in_another_window():
    w = _window()
    dlg = QDialog(w)
    _KEEP.append(dlg)
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier)
    assert w.eventFilter(dlg, ev) is False


def test_the_filter_is_app_wide_and_drops_stay_in_this_window(tmp_path):
    w = _window()
    f = tmp_path / "scan.tif"
    f.write_bytes(b"x")
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(f))])
    # a drop-accepting widget created AFTER the window still loads files
    late = QWidget(w)
    late.setAcceptDrops(True)
    enter = QDragEnterEvent(QPoint(1, 1), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    assert w.eventFilter(late, enter) is True
    # a dialog that takes drops of its own keeps them
    dlg = QDialog(w)
    _KEEP.append(dlg)
    dlg.setAcceptDrops(True)
    enter2 = QDragEnterEvent(QPoint(1, 1), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    assert w.eventFilter(dlg, enter2) is False


def test_settings_wheel_scrolls_the_page_through_the_wheel_router():
    """The router re-delivers a wheel event to the widget under the pointer;
    over a Settings combo that must still scroll the page, not the value."""
    from ui.wheel_routing import install_wheel_router
    from widgets.settings_dialog import _WheelScrollsPage
    router = install_wheel_router(_app)
    host = QDialog()
    _KEEP.append(host)
    area = QScrollArea(host)
    page = QWidget()
    lay = QVBoxLayout(page)
    combos = []
    for _ in range(30):
        c = QComboBox()
        c.addItems(["a", "b", "c"])
        lay.addWidget(c)
        combos.append(c)
    area.setWidget(page)
    QVBoxLayout(host).addWidget(area)
    filt = _WheelScrollsPage(host)
    for c in combos:
        c.installEventFilter(filt)
    host.resize(300, 200)
    host.show()
    _app.processEvents()
    target = combos[3]
    bar = area.verticalScrollBar()
    assert bar.maximum() > 0
    before = (target.currentIndex(), bar.value())
    gpos = target.mapToGlobal(QPoint(5, 5))
    ev = QWheelEvent(QPointF(5, 5), QPointF(gpos), QPoint(0, 0), QPoint(0, -120),
                     Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    router._deliver(target, ev, gpos)
    assert target.currentIndex() == before[0]           # the value didn't change
    assert bar.value() > before[1]                       # the page scrolled


def test_one_app_wide_hook_however_many_windows():
    """Every live window's own app filter would run on every event in the
    app; one hook points at the newest window instead."""
    from ui import main_window as mw
    a, b = _window(), _window()
    hook = mw._AppWideHook._hook
    assert hook is not None and hook.window is b
    seen = []
    b.thumbnail_list.thumbnail_list.keyPressEvent = lambda e: seen.append(e.key())
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier)
    assert hook.eventFilter(b.sliders_panel.sliders[0], ev) is True
    assert seen == [Qt.Key_Up]
    # other event types never reach the window
    assert hook.eventFilter(b, QEvent(QEvent.Paint)) is False
    assert a is not b
