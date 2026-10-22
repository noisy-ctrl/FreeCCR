"""Right-click popup for Shadow Neutralise: how far up the tones it reaches,
how much colour still counts as a cast, how much deeper the darkest tones go,
a before / after strip of a cast shadow ramp, and a switch to show on the
picture where it acts. See spec/shadow-neutralise.md."""

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
                               QPushButton, QCheckBox)

from core.shadow_neutral import (SHADOW_SHAPE_DEFAULT, SHADOW_LIMITS,
                                 apply_shadow_neutralise, normalize_shadow_shape)
from ui import theme

ROWS = (
    ("range", "Range", ("Deep shadows", "Into midtones"),
     "How far up the tones it reaches. It is fully on in the deepest tones "
     "and fades out smoothly by this point; nothing brighter is touched."),
    ("reach", "Reach", ("Casts only", "Colours too"),
     "How much colour still counts as a cast. Lower keeps more colour in "
     "dark things (skin in shade, dark wood); higher neutralises stronger "
     "casts, and starts to take colour out of dark colours too."),
    ("depth", "Depth", ("None", "Inky"),
     "Deepens the darkest tones a little for a richer black. Black stays "
     "black, nothing clips, and tones above the range are left alone."),
)

# A dark ramp with a teal cast (the kind a conversion leaves in its shadows)
CAST = np.array([0.55, 1.0, 0.92], np.float32)


def describe_shadow(shape):
    shape = normalize_shadow_shape(shape)
    if shape is None:
        return "default"
    return ", ".join(f"{k} {shape[k]}" for k in SHADOW_SHAPE_DEFAULT
                     if shape[k] != SHADOW_SHAPE_DEFAULT[k]) or "default"


def _ramp_pixmap(amount, shape, w=200, h=14):
    """Black up to the shadows' end with a cast, as displayed (no scaling)."""
    x = np.linspace(0.0, 0.35, w, dtype=np.float32)
    ramp = np.repeat((x[:, None] * CAST)[None], h, axis=0)
    out = apply_shadow_neutralise(ramp, amount, shape) if amount else ramp
    rgb = np.ascontiguousarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8))
    q = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(q)


class ShadowPopup(QFrame):
    """Frameless popup (closes on an outside click). Emits `changed(shape)`
    (a full options dict) on every edit, `show_mask(bool)` for the overlay,
    and `closed()` when dismissed."""

    changed = Signal(dict)
    show_mask = Signal(bool)
    closed = Signal()

    def __init__(self, shape=None, slider_value=0, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self._amount = int(slider_value)
        shape = normalize_shadow_shape(shape) or dict(SHADOW_SHAPE_DEFAULT)
        self.setObjectName("shadowPopup")
        self.setStyleSheet(
            f"#shadowPopup {{ background: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER_STRONG}; border-radius: 6px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        title = QLabel("Shadow Neutralise options")
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
            sl.setRange(*SHADOW_LIMITS[key])
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

        self.mask_check = QCheckBox("Show where it acts")
        self.mask_check.setToolTip(
            "While this popup is open, the picture shows in magenta where "
            "Shadow Neutralise acts (stronger magenta, more neutralising). "
            "Strong dark colours and everything above the range should stay "
            "clear.")
        self.mask_check.toggled.connect(self.show_mask.emit)
        lay.addWidget(self.mask_check)

        cap = QLabel("Shadows with a cast, before and after:")
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
            s.setValue(SHADOW_SHAPE_DEFAULT[k])
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
