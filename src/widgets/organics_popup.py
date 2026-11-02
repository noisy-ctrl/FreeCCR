"""Right-click popup for the Colour Organics slider: how much of the labs'
measured hue move each colour region gets. See spec/colour-organics.md
(revision 2026-11-02)."""

import colorsys

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QGridLayout, QLabel,
                               QSlider, QPushButton)

from core.ccr_processor import (ORGANICS_REGIONS, ORGANICS_SHAPE_DEFAULT,
                                ORGANICS_REGION_MAX, normalize_organics_shape)
from ui import theme

INTRO = ("How much of the labs' measured hue move each region gets. "
         "100 % is as measured.")
UNMEASURED = "Violets and magentas: not measured yet, left as they are."
STEP = 5


def describe_organics(shape):
    """Short text for a (normalised) shape, e.g. for a tooltip / paste row."""
    shape = normalize_organics_shape(shape)
    if shape is None:
        return "default"
    parts = [f"{name} {shape[key]} %" for key, name, *_rest in ORGANICS_REGIONS
             if shape[key] != ORGANICS_SHAPE_DEFAULT[key]]
    return "; ".join(parts) if parts else "default"


def _swatch_style(hue_deg):
    r, g, b = colorsys.hsv_to_rgb((hue_deg % 360) / 360.0, 0.65, 0.85)
    col = f"rgb({int(r * 255)}, {int(g * 255)}, {int(b * 255)})"
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

        title = QLabel("Colour Organics by region")
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
        self.sliders, self.values_shown = {}, {}
        for row, (key, name, what, _start, _end, swatch) in enumerate(ORGANICS_REGIONS):
            sw = QLabel()
            sw.setFixedSize(18, 14)
            sw.setStyleSheet(_swatch_style(swatch))
            lbl = QLabel(name)
            lbl.setToolTip(f"{name}: {what}")
            sl = QSlider(Qt.Horizontal)
            sl.setRange(0, ORGANICS_REGION_MAX // STEP)
            sl.setValue(int(round(shape[key] / STEP)))
            sl.setToolTip(f"How much of the measured move {name.lower()} get "
                          "(0 leaves them alone, 100 % as measured). "
                          "Double-click the number for 100 %.")
            sl.setMinimumWidth(120)
            sl.valueChanged.connect(self._emit)
            val = QLabel()
            val.setFixedWidth(42)
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            val.mouseDoubleClickEvent = (
                lambda _e, s=sl, k=key: s.setValue(ORGANICS_SHAPE_DEFAULT[k] // STEP))
            grid.addWidget(sw, row, 0)
            grid.addWidget(lbl, row, 1)
            grid.addWidget(sl, row, 2)
            grid.addWidget(val, row, 3)
            self.sliders[key], self.values_shown[key] = sl, val
        lay.addLayout(grid)
        note = QLabel(UNMEASURED)
        note.setWordWrap(True)
        note.setFixedWidth(300)
        note.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        lay.addWidget(note)

        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setToolTip("Every region back to 100 %, as measured")
        self.reset_btn.clicked.connect(self.reset)
        lay.addWidget(self.reset_btn)
        self._refresh()

    # -- state -------------------------------------------------------------
    def values(self):
        return {key: self.sliders[key].value() * STEP for key in self.sliders}

    def reset(self):
        for sl in self.sliders.values():
            sl.blockSignals(True)
        for key, sl in self.sliders.items():
            sl.setValue(ORGANICS_SHAPE_DEFAULT[key] // STEP)
        for sl in self.sliders.values():
            sl.blockSignals(False)
        self._emit()

    def _refresh(self):
        for key, sl in self.sliders.items():
            self.values_shown[key].setText(f"{sl.value() * STEP} %")

    def _emit(self, *_):
        self._refresh()
        self.changed.emit(self.values())

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)

    def hideEvent(self, event):
        self.closed.emit()
        super().hideEvent(event)
