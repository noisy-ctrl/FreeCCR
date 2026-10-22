"""Right-click popup for Golden Highlights: the gold's hue, where it starts and
how clean the whites stay, with a before / after strip of a grey ramp.
See spec/golden-highlights.md."""

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
                               QPushButton)

from core.golden import (GOLDEN_SHAPE_DEFAULT, GOLDEN_LIMITS, apply_golden,
                         normalize_golden_shape)
from ui import theme

ROWS = (
    ("hue", "Hue", ("Amber", "Yellow"),
     "The gold's hue. The middle is the gold measured off a Noritsu scan; "
     "towards Amber is warmer and richer, towards Yellow paler (too far reads "
     "as yellow rather than gold)."),
    ("start", "Start", ("Lower", "Higher"),
     "Where in the tones the gold begins. Below it nothing changes, so "
     "shadows stay neutral."),
    ("clean", "Clean whites", ("Golden", "Clean"),
     "How much the very brightest tones ease back to neutral. A lab scan "
     "keeps its whites a clean cream; at 0 the gold runs right up to white."),
)


def describe_golden(shape):
    shape = normalize_golden_shape(shape)
    if shape is None:
        return "default"
    names = {"hue": "hue", "start": "start", "clean": "clean whites"}
    return ", ".join(f"{names[k]} {shape[k]}" for k in GOLDEN_SHAPE_DEFAULT
                     if shape[k] != GOLDEN_SHAPE_DEFAULT[k]) or "default"


def _ramp_pixmap(amount, shape, w=200, h=14):
    x = np.linspace(0.0, 1.0, w, dtype=np.float32)
    grey = np.repeat(np.repeat(x[None, :, None], 3, axis=2), h, axis=0)
    out = apply_golden(grey, amount, shape) if amount else grey
    rgb = np.ascontiguousarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8))
    q = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(q)


class GoldenPopup(QFrame):
    """Frameless popup (closes on an outside click). Emits `changed(shape)`
    (a full options dict) on every edit and `closed()` when dismissed."""

    changed = Signal(dict)
    closed = Signal()

    def __init__(self, shape=None, slider_value=0, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self._amount = int(slider_value)
        shape = normalize_golden_shape(shape) or dict(GOLDEN_SHAPE_DEFAULT)
        self.setObjectName("goldenPopup")
        self.setStyleSheet(
            f"#goldenPopup {{ background: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER_STRONG}; border-radius: 6px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        title = QLabel("Golden Highlights options")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        lay.addWidget(title)

        self.sliders, self.values_lbl = {}, {}
        for key, name, (left, right), tip in ROWS:
            row = QHBoxLayout()
            lbl = QLabel(name)
            lbl.setToolTip(tip)
            lbl.setFixedWidth(80)
            sl = QSlider(Qt.Horizontal)
            sl.setRange(*GOLDEN_LIMITS[key])
            sl.setValue(int(shape[key]))
            sl.setToolTip(tip)
            sl.valueChanged.connect(self._emit)
            val = QLabel(str(sl.value()))
            val.setFixedWidth(28)
            row.addWidget(lbl)
            row.addWidget(sl, 1)
            row.addWidget(val)
            lay.addLayout(row)
            ends = QHBoxLayout()
            ends.setContentsMargins(80 + row.spacing(), 0, 28 + row.spacing(), 0)
            for text, align in ((left, Qt.AlignLeft), (right, Qt.AlignRight)):
                e = QLabel(text)
                e.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 10px;")
                e.setAlignment(align)
                ends.addWidget(e)
            lay.addLayout(ends)
            self.sliders[key], self.values_lbl[key] = sl, val

        cap = QLabel("A grey ramp, before and after:")
        cap.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 10px;")
        lay.addWidget(cap)
        self.before = QLabel()
        self.after = QLabel()
        for w in (self.before, self.after):
            lay.addWidget(w, alignment=Qt.AlignHCenter)

        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setToolTip("Back to the measured Noritsu gold")
        self.reset_btn.clicked.connect(self.reset)
        lay.addWidget(self.reset_btn)
        self._refresh()

    def values(self):
        return {k: s.value() for k, s in self.sliders.items()}

    def set_slider_value(self, v):
        self._amount = int(v)
        self._refresh()

    def reset(self):
        for s in self.sliders.values():
            s.blockSignals(True)
        for k, s in self.sliders.items():
            s.setValue(GOLDEN_SHAPE_DEFAULT[k])
        for s in self.sliders.values():
            s.blockSignals(False)
        self._emit()

    def _refresh(self):
        for k, s in self.sliders.items():
            self.values_lbl[k].setText(str(s.value()))
        shape = self.values()
        self.before.setPixmap(_ramp_pixmap(0, shape))
        # at 0 the strip shows what 60 would do, so the options can be judged
        self.after.setPixmap(_ramp_pixmap(self._amount or 60, shape))

    def _emit(self, *_):
        self._refresh()
        self.changed.emit(self.values())

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)

    def hideEvent(self, event):
        self.closed.emit()
        super().hideEvent(event)
