"""Right-click popup for the Colour Organics slider: switch each hue family
on or off, nudge where its centre sits, and set how far the families reach.
See spec/colour-organics.md (popup revision)."""

import colorsys

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QGridLayout,
                               QLabel, QCheckBox, QSlider, QPushButton)

from core.ccr_processor import (ORGANICS_FAMILIES, ORGANICS_SHAPE_DEFAULT,
                                ORGANICS_SHIFT_MAX, ORGANICS_REACH_MIN,
                                ORGANICS_REACH_MAX, normalize_organics_shape)
from ui import theme

INTRO = ("Hues near each family are drawn together. Switch a family off to "
         "leave its colours alone, or nudge where its centre sits.")
REACH_TIP = ("How far each family's pull reaches towards its neighbours. "
             "Further gathers more colours into each family, just as firmly "
             "at its centre, so the effect grows all the way to 60 %.")


def describe_organics(shape):
    """Short text for a (normalised) shape, e.g. for a tooltip / paste row."""
    shape = normalize_organics_shape(shape)
    if shape is None:
        return "default"
    parts = []
    for key, name, _what, _hue in ORGANICS_FAMILIES:
        if not shape[f"{key}_on"]:
            parts.append(f"{name} off")
        elif shape[f"{key}_shift"]:
            parts.append(f"{name} {shape[f'{key}_shift']:+d}°")
    if shape["reach"] != ORGANICS_SHAPE_DEFAULT["reach"]:
        parts.append(f"reach {shape['reach']}%")
    return "; ".join(parts) if parts else "default"


def _swatch_style(hue_deg, enabled=True):
    r, g, b = colorsys.hsv_to_rgb((hue_deg % 360) / 360.0, 0.65, 0.85)
    col = f"rgb({int(r * 255)}, {int(g * 255)}, {int(b * 255)})"
    if not enabled:
        col = theme.SURFACE
    return (f"background: {col}; border: 1px solid {theme.BORDER_STRONG}; "
            f"border-radius: 3px;")


class OrganicsPopup(QFrame):
    """Frameless popup (closes on an outside click). Emits `changed(shape)`
    (a full shape dict) on every edit and `closed()` when dismissed."""

    changed = Signal(dict)
    closed = Signal()

    def __init__(self, shape=None, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        shape = normalize_organics_shape(shape) or dict(ORGANICS_SHAPE_DEFAULT)
        self.setObjectName("organicsPopup")
        self.setStyleSheet(
            f"#organicsPopup {{ background: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER_STRONG}; border-radius: 6px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        title = QLabel("Colour Organics families")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        lay.addWidget(title)
        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        intro.setFixedWidth(300)
        intro.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        lay.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.checks, self.shifts, self.swatches, self.shift_labels = {}, {}, {}, {}
        for row, (key, name, what, _hue) in enumerate(ORGANICS_FAMILIES):
            cb = QCheckBox(name)
            cb.setToolTip(f"{name}: {what}")
            cb.setChecked(shape[f"{key}_on"])
            cb.toggled.connect(self._emit)
            sw = QLabel()
            sw.setFixedSize(18, 14)
            sl = QSlider(Qt.Horizontal)
            sl.setRange(-ORGANICS_SHIFT_MAX, ORGANICS_SHIFT_MAX)
            sl.setValue(shape[f"{key}_shift"])
            sl.setToolTip(f"Nudge where the {name.lower()} family's centre sits "
                          f"(±{ORGANICS_SHIFT_MAX}° of hue). Double-click the "
                          "number to centre it.")
            sl.setMinimumWidth(120)
            sl.valueChanged.connect(self._emit)
            val = QLabel()
            val.setFixedWidth(34)
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            val.mouseDoubleClickEvent = lambda _e, s=sl: s.setValue(0)
            grid.addWidget(cb, row, 0)
            grid.addWidget(sw, row, 1)
            grid.addWidget(sl, row, 2)
            grid.addWidget(val, row, 3)
            self.checks[key], self.shifts[key] = cb, sl
            self.swatches[key], self.shift_labels[key] = sw, val
        lay.addLayout(grid)

        row = QHBoxLayout()
        lbl = QLabel("Reach")
        lbl.setToolTip(REACH_TIP)
        self.reach_slider = QSlider(Qt.Horizontal)
        self.reach_slider.setRange(ORGANICS_REACH_MIN, ORGANICS_REACH_MAX)
        self.reach_slider.setValue(shape["reach"])
        self.reach_slider.setToolTip(REACH_TIP)
        self.reach_slider.valueChanged.connect(self._emit)
        self.reach_value = QLabel()
        self.reach_value.setFixedWidth(34)
        self.reach_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(lbl)
        row.addWidget(self.reach_slider, 1)
        row.addWidget(self.reach_value)
        lay.addLayout(row)

        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setToolTip("Back to the standard four families")
        self.reset_btn.clicked.connect(self.reset)
        lay.addWidget(self.reset_btn)
        self._refresh()

    # -- state -------------------------------------------------------------
    def values(self):
        out = {"reach": self.reach_slider.value()}
        for key, *_rest in ORGANICS_FAMILIES:
            out[f"{key}_on"] = self.checks[key].isChecked()
            out[f"{key}_shift"] = self.shifts[key].value()
        return out

    def reset(self):
        widgets = list(self.checks.values()) + list(self.shifts.values()) + [self.reach_slider]
        for w in widgets:
            w.blockSignals(True)
        for key, *_rest in ORGANICS_FAMILIES:
            self.checks[key].setChecked(ORGANICS_SHAPE_DEFAULT[f"{key}_on"])
            self.shifts[key].setValue(ORGANICS_SHAPE_DEFAULT[f"{key}_shift"])
        self.reach_slider.setValue(ORGANICS_SHAPE_DEFAULT["reach"])
        for w in widgets:
            w.blockSignals(False)
        self._emit()

    def _refresh(self):
        for key, _name, _what, hue in ORGANICS_FAMILIES:
            on = self.checks[key].isChecked()
            shift = self.shifts[key].value()
            self.swatches[key].setStyleSheet(_swatch_style(hue + shift, on))
            self.shifts[key].setEnabled(on)
            self.shift_labels[key].setText(f"{shift:+d}°" if shift else "0°")
        self.reach_value.setText(f"{self.reach_slider.value()}%")

    def _emit(self, *_):
        self._refresh()
        self.changed.emit(self.values())

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)

    def hideEvent(self, event):
        self.closed.emit()
        super().hideEvent(event)
