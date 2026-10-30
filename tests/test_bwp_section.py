#!/usr/bin/env python3
"""The Film B/W Point section folds away (spec/bwp-section-collapsible.md):
its sampling rows sit under a + / − header, Convert and Auto Crop stay
visible, it starts open and remembers how it was left."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

import pytest  # noqa: E402


@pytest.fixture
def fresh_setting():
    st = QSettings("FreeCCR", "FreeCCR")
    from widgets.sliders_panel import SlidersPanel
    had = st.contains(SlidersPanel.BWP_OPEN_KEY)
    old = st.value(SlidersPanel.BWP_OPEN_KEY)
    st.remove(SlidersPanel.BWP_OPEN_KEY)
    yield st, SlidersPanel.BWP_OPEN_KEY
    if had:
        st.setValue(SlidersPanel.BWP_OPEN_KEY, old)
    else:
        st.remove(SlidersPanel.BWP_OPEN_KEY)


def _window():
    from ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    _app.processEvents()
    return w, w.sliders_panel


def test_starts_open_and_folds_the_sampling_rows_only(fresh_setting):
    st, key = fresh_setting
    w, p = _window()
    try:
        sec = p.bwp_section
        assert sec._toggle_btn.isChecked() and sec._toggle_btn.text() == "- Film B/W Point"
        rows = (p.black_point_btn, p.white_point_btn, p.clear_white_point_btn,
                p.film_stock_combo, p.wp_combo, p.wp_show_btn)
        stay = (p.convert_current_bwp_btn, p.convert_all_bwp_btn,
                p.auto_crop_btn, p.auto_crop_all_btn)
        assert all(r.isVisibleTo(p) for r in rows + stay)
        sec._toggle_btn.click()
        _app.processEvents()
        assert sec._toggle_btn.text() == "+ Film B/W Point"
        assert not any(r.isVisibleTo(p) for r in rows)
        assert all(r.isVisibleTo(p) for r in stay)
        assert st.value(key, type=bool) is False
    finally:
        w.hide()


def test_remembers_being_closed(fresh_setting):
    st, key = fresh_setting
    st.setValue(key, False)
    w, p = _window()
    try:
        assert not p.bwp_section._toggle_btn.isChecked()
        assert not p.black_point_btn.isVisibleTo(p) and p.convert_current_bwp_btn.isVisibleTo(p)
        p.bwp_section._toggle_btn.click()
        _app.processEvents()
        assert p.black_point_btn.isVisibleTo(p) and st.value(key, type=bool) is True
    finally:
        w.hide()


def test_sampling_still_starts_when_folded(fresh_setting):
    """The tether's Black Point buttons don't need the panel's button shown."""
    st, key = fresh_setting
    st.setValue(key, False)
    w, p = _window()
    try:
        modes = []
        w.image_preview.set_bwpoint_mode = lambda m: modes.append(m)
        w.start_roll_black_point()            # no images: hints only
        assert modes == []
    finally:
        w.hide()


def test_convert_and_auto_crop_sit_above_the_section(fresh_setting):
    w, p = _window()
    try:
        top = lambda wid: wid.mapTo(p, wid.rect().topLeft()).y()
        sec = top(p.bwp_section)
        assert top(p.convert_current_bwp_btn) < top(p.auto_crop_btn) < sec
        p.bwp_section._toggle_btn.click()                 # folded: still above
        _app.processEvents()
        assert top(p.convert_current_bwp_btn) < top(p.bwp_section)
    finally:
        w.hide()
