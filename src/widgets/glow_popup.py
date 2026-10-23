"""Right-click popup for Glow: how far the light spreads, how bright a tone
must be to glow, a before / after strip of a light in the dark, and a switch
to show on the picture where the glow lands. See spec/highlight-glow.md."""

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
                               QPushButton, QCheckBox)

from core.glow import GLOW_SHAPE_DEFAULT, GLOW_LIMITS, apply_glow, normalize_glow_shape
from ui import theme

LABEL_W = 96

ROWS = (
    ("size", "Size", ("Tight", "Wide"),
     "How far the light spreads from the highlights, as a share of the "
     "picture (the same at every size you view or export it)."),
    ("start", "Start", ("Lower", "Higher"),
     "How bright a tone has to be to glow. Lower lets more of the bright "
     "tones glow; higher keeps it to the brightest lights."),
)


def describe_glow(shape):
    shape = normalize_glow_shape(shape)
    if shape is None:
        return "default"
    return ", ".join(f"{k} {shape[k]}" for k in GLOW_SHAPE_DEFAULT
                     if shape[k] != GLOW_SHAPE_DEFAULT[k]) or "default"


CROP = 3       # the strip is the middle third of a picture this many times wider
SS = 2         # drawn at twice the size and scaled down (soft edges)


def _spot_pixmap(amount, shape, w=200, h=40):
    """A bright window and a small lamp on a dark ground, as displayed. The
    strip is a crop (the middle third) of a wider picture, because Size is a
    share of the picture's long side: a strip rendered as a whole picture of
    its own would show the glow three times smaller than on a frame."""
    s, W = SS, w * CROP
    img = np.full((h * s, W * s, 3), 0.06, np.float32)
    x0 = (W - w) // 2
    img[12 * s:28 * s, (x0 + 40) * s:(x0 + 70) * s] = (1.0, 0.92, 0.78)    # a warm window
    img[18 * s:22 * s, (x0 + 140) * s:(x0 + 144) * s] = (1.0, 1.0, 1.0)     # a small lamp
    out = apply_glow(img, amount, shape) if amount else img
    out = cv2.resize(out, (W, h), interpolation=cv2.INTER_AREA)[:, x0:x0 + w]
    rgb = np.ascontiguousarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8))
    q = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(q)


class GlowPopup(QFrame):
    """Frameless popup (closes on an outside click). Emits `changed(shape)`
    (a full options dict) on every edit, `show_mask(bool)` for the overlay,
    and `closed()` when dismissed."""

    changed = Signal(dict)
    show_mask = Signal(bool)
    closed = Signal()

    def __init__(self, shape=None, slider_value=0, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self._amount = int(slider_value)
        shape = normalize_glow_shape(shape) or dict(GLOW_SHAPE_DEFAULT)
        self.setObjectName("glowPopup")
        self.setStyleSheet(
            f"#glowPopup {{ background: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER_STRONG}; border-radius: 6px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        title = QLabel("Glow options")
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
            sl.setRange(*GLOW_LIMITS[key])
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
            "While this popup is open, the picture shows in amber where the "
            "glow lands (stronger amber, more glow): round the highlights, "
            "falling off with distance.")
        self.mask_check.toggled.connect(self.show_mask.emit)
        lay.addWidget(self.mask_check)

        cap = QLabel("A light in the dark, before and after:")
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
            s.setValue(GLOW_SHAPE_DEFAULT[k])
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
