"""Right-click popup for Shadow Lift: how far up the tones it reaches, how
much it lifts black itself, a before / after strip of a dark gradient, and a
switch to show on the picture where it acts. See spec/shadow-lift.md."""

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
                               QPushButton, QCheckBox)

from core.shadow_lift import (LIFT_SHAPE_DEFAULT, LIFT_LIMITS, apply_shadow_lift,
                               normalize_lift_shape)
from ui import theme

LABEL_W = 96

ROWS = (
    ("range", "Range", ("Narrow", "Wide"),
     "How far up the tones it reaches. Narrow keeps it to the deepest "
     "shadows; wide takes it into the lower midtones."),
    ("black", "Lift black", ("None", "More"),
     "Lifts black itself, for a matte, faded-print look. At None pure black "
     "stays black and only the shadows open."),
)


def describe_lift(shape):
    shape = normalize_lift_shape(shape)
    if shape is None:
        return "default"
    return ", ".join(f"{k} {shape[k]}" for k in LIFT_SHAPE_DEFAULT
                     if shape[k] != LIFT_SHAPE_DEFAULT[k]) or "default"


def _spot_pixmap(amount, shape, w=200, h=40):
    """A dark gradient (black to the lower midtones) with two shadow
    colours, as displayed: the shadows opening, black staying black."""
    x = np.linspace(0.0, 0.45, w, dtype=np.float32)
    img = np.repeat(np.repeat(x[None, :, None], 3, axis=2), h, axis=0)
    img[h // 2:, : w // 3] = (0.16, 0.09, 0.06)            # a warm shadow
    img[h // 2:, w // 3: 2 * w // 3] = (0.05, 0.08, 0.12)  # a cool one
    out = apply_shadow_lift(img, amount, shape) if amount else img
    rgb = np.ascontiguousarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8))
    q = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(q)


class ShadowLiftPopup(QFrame):
    """Frameless popup (closes on an outside click). Emits `changed(shape)`
    (a full options dict) on every edit, `show_mask(bool)` for the overlay,
    and `closed()` when dismissed."""

    changed = Signal(dict)
    show_mask = Signal(bool)
    closed = Signal()

    def __init__(self, shape=None, slider_value=0, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self._amount = int(slider_value)
        shape = normalize_lift_shape(shape) or dict(LIFT_SHAPE_DEFAULT)
        self.setObjectName("shadowLiftPopup")
        self.setStyleSheet(
            f"#shadowLiftPopup {{ background: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER_STRONG}; border-radius: 6px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        title = QLabel("Shadow Lift options")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        lay.addWidget(title)

        self.sliders, self.values_lbl = {}, {}
        for key, name, (left, right), tip in ROWS:
            row = QHBoxLayout()
            lbl = QLabel(name)
            lbl.setToolTip(tip)
            lbl.setFixedWidth(LABEL_W)
            sl = QSlider(Qt.Horizontal)
            sl.setRange(*LIFT_LIMITS[key])
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
            ends.setContentsMargins(LABEL_W + row.spacing(), 0, 28 + row.spacing(), 0)
            for text, align in ((left, Qt.AlignLeft), (right, Qt.AlignRight)):
                e = QLabel(text)
                e.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 10px;")
                e.setAlignment(align)
                ends.addWidget(e)
            lay.addLayout(ends)
            self.sliders[key], self.values_lbl[key] = sl, val

        self.mask_check = QCheckBox("Show where it acts")
        self.mask_check.setToolTip(
            "While this popup is open, the picture shows in green where it "
            "acts (stronger green, more lift): strongest in the deepest "
            "shadows, fading to nothing at the top of the Range.")
        self.mask_check.toggled.connect(self.show_mask.emit)
        lay.addWidget(self.mask_check)

        cap = QLabel("The shadows, before and after:")
        cap.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 10px;")
        lay.addWidget(cap)
        self.before = QLabel()
        self.after = QLabel()
        for w in (self.before, self.after):
            lay.addWidget(w, alignment=Qt.AlignHCenter)

        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setToolTip("Back to the defaults")
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
            s.setValue(LIFT_SHAPE_DEFAULT[k])
        for s in self.sliders.values():
            s.blockSignals(False)
        self._emit()

    def _refresh(self):
        for k, s in self.sliders.items():
            self.values_lbl[k].setText(str(s.value()))
        shape = self.values()
        self.before.setPixmap(_spot_pixmap(0, shape))
        # at 0 the strip shows what 60 would do, so the options can be judged
        self.after.setPixmap(_spot_pixmap(self._amount or 60, shape))

    def _emit(self, *_):
        self._refresh()
        self.changed.emit(self.values())

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)

    def hideEvent(self, event):
        self.closed.emit()
        super().hideEvent(event)
