#!/usr/bin/env python3
"""Settings -> Panel: show or hide editing-panel sections
(spec/panel-visibility.md)."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])
_LIVE = []        # keep dialogs alive (see tests/test_settings_dialog.py)


@pytest.fixture
def panel(tmp_path):
    from widgets.sliders_panel import SlidersPanel
    p = SlidersPanel()
    p._settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    p.set_hidden_sections([])
    _LIVE.append(p)
    return p


def _shown(widget):
    return not widget.isHidden()


def test_every_section_listed_and_present(panel):
    ids = [sid for sid, _l, _a in panel.HIDEABLE_SECTIONS]
    assert ids == ["printer_lights", "crosstalk", "lab_finish", "levels", "balance",
                   "curves", "bands", "noise", "details"]
    for _sid, _label, attr in panel.HIDEABLE_SECTIONS:
        assert getattr(panel, attr) is not None


def test_hide_and_show_with_separator(panel):
    panel.set_hidden_sections({"crosstalk", "balance", "details"})
    assert not _shown(panel.crosstalk_section)
    assert not _shown(panel._section_separators["crosstalk"])
    assert not _shown(panel.balance_section)
    assert not _shown(panel.details_section)
    assert not _shown(panel._section_separators["details"])
    assert _shown(panel.od_section) and _shown(panel.printer_section)
    panel.set_hidden_sections(set())
    assert _shown(panel.crosstalk_section) and _shown(panel._section_separators["crosstalk"])


def test_persisted_and_unknown_ids_ignored(panel):
    panel.set_hidden_sections({"levels", "nonsense"})
    assert panel._settings.value("panel/hidden_sections", "", type=str) == "levels"
    assert panel.hidden_sections() == {"levels"}


class _MW(QWidget):
    def __init__(self, panel):
        super().__init__()
        self.sliders_panel = panel


def test_settings_page_seeds_and_applies_on_done(panel):
    from widgets.settings_dialog import SettingsDialog
    panel.set_hidden_sections({"curves"})
    dlg = SettingsDialog(_MW(panel))
    _LIVE.append(dlg)
    names = [dlg._sidebar.item(i).text() for i in range(dlg._sidebar.count())]
    assert "Panel" in names
    assert not dlg._panel_checks["curves"].isChecked()
    assert dlg._panel_checks["crosstalk"].isChecked()
    dlg._panel_checks["crosstalk"].setChecked(False)
    dlg._panel_checks["curves"].setChecked(True)
    assert panel.hidden_sections() == {"curves"}          # staged, not applied yet
    dlg._apply_pending()
    assert panel.hidden_sections() == {"crosstalk"}
    assert not _shown(panel.crosstalk_section) and _shown(panel.curves_section)


def test_no_panel_page_without_a_panel():
    from widgets.settings_dialog import SettingsDialog

    class Bare(QWidget):
        pass
    dlg = SettingsDialog(Bare())
    _LIVE.append(dlg)
    names = [dlg._sidebar.item(i).text() for i in range(dlg._sidebar.count())]
    assert "Panel" not in names


def test_lab_finish_section_holds_the_two_tools_above_subtracted_sat(panel):
    """Golden Highlights and Shadow Neutralise sit in their own section (open
    by default) above Subtracted Sat; hiding it hides both lines round it and
    leaves the sliders (and their values) in place."""
    from PySide6.QtWidgets import QApplication
    sec = panel.lab_finish_section
    assert sec._toggle_btn.isChecked() and sec._content.isVisibleTo(sec)
    for lay in (panel.golden_slider_layout, panel.shadow_neutralise_slider_layout):
        assert sec.isAncestorOf(lay.itemAt(0).widget())
    panel.resize(380, 2400)
    panel.show()
    QApplication.processEvents()
    y = lambda w: w.mapTo(panel, w.rect().topLeft()).y()
    sub = panel.sub_saturation_slider_layout.itemAt(0).widget()
    vib = panel.vibrance_slider_layout.itemAt(0).widget()
    assert y(vib) < y(sec) < y(sub)
    panel.set_hidden_sections({"lab_finish"})
    assert not _shown(sec)
    assert not _shown(panel._section_separators["lab_finish"])
    assert not _shown(panel._lab_finish_sep_below)
    keys = panel.adjustment_keys
    assert "golden_highlights" in keys and "shadow_neutralise" in keys
    panel.set_hidden_sections(set())
    assert _shown(sec) and _shown(panel._lab_finish_sep_below)
