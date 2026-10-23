"""Right-click popup for Highlights L / Shadows ⅃ / Contrast L ⅃: range and
keep-endpoint options with a live curve preview. See spec/x-tone-tools.md."""

import numpy as np
from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QPainterPath
from PySide6.QtWidgets import (QFrame, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QCheckBox, QSlider, QPushButton)

from core.x_tones import XTONE_SHAPE_DEFAULT, grey_curve, normalize_xtone_shape
from ui import theme

TOOL_NAMES = {"hx": "Highlights L", "sx": "Shadows \u2143", "px": "Contrast L \u2143"}
SLIDER_KEYS = {"hx": "highlights_x", "sx": "shadows_x", "px": "punch_x"}
# Which options each tool has: (option key, label, tooltip)
RANGE_TIPS = {
    "hx": ("How far down the tones it reaches. The middle is the default; "
           "Wide takes it further into the midtones and shadows, Narrow keeps "
           "it in the brightest tones."),
    "sx": ("How far up the tones it reaches. The middle is the default; "
           "Wide takes it further into the midtones, Narrow keeps it in the "
           "deepest shadows."),
}
KEEP_WHITE_TIP = ("Pure white stays exactly white: the change fades out over "
                  "the brightest quarter of the tones, so nothing new clips.")
KEEP_BLACK_TIP = ("Pure black stays exactly black: the change fades in over the "
                  "darkest quarter of the tones (Shadows \u2143 lifts black at +; "
                  "this keeps a true black).")
CHECKS = {
    "hx": (("hx_keep", "Keep white point", KEEP_WHITE_TIP),),
    "sx": (("sx_keep", "Keep black point", KEEP_BLACK_TIP),),
    "px": (("px_keep_black", "Keep black point", KEEP_BLACK_TIP),
           ("px_keep_white", "Keep white point", KEEP_WHITE_TIP)),
}


def describe_xtone_shape(shape):
    """Short text for a shape, e.g. for a tooltip / paste row."""
    shape = normalize_xtone_shape(shape)
    if shape is None:
        return "default"
    parts = []
    for tool in ("hx", "sx", "px"):
        bits = []
        rk = f"{tool}_range"
        if rk in shape and shape[rk] != XTONE_SHAPE_DEFAULT[rk]:
            bits.append(f"range {shape[rk]}")
        for key, label, _tip in CHECKS[tool]:
            if shape[key]:
                bits.append(label[0].lower() + label[1:].replace(" point", ""))
        if bits:
            parts.append(f"{TOOL_NAMES[tool]}: " + ", ".join(bits))
    return "; ".join(parts) if parts else "default"


def tool_is_custom(shape, tool):
    shape = normalize_xtone_shape(shape) or XTONE_SHAPE_DEFAULT
    return any(shape[k] != XTONE_SHAPE_DEFAULT[k] for k in shape if k.startswith(tool + "_"))


class ToneCurvePreview(QWidget):
    """The tool's curve over the full 0-1 range, at the current slider value
    (or, at 0, the +100 / -100 curves drawn faintly to show the region)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(200, 130)
        self._curves = []      # [(xs, ys, strong)]

    def set_curves(self, curves):
        self._curves = curves
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(6, 6, self.width() - 12, self.height() - 12)
        p.fillRect(r, QColor(theme.CANVAS))
        p.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        p.drawRect(r)
        grid = QPen(QColor(theme.BORDER), 1, Qt.DotLine)
        p.setPen(grid)
        for f in (0.25, 0.5, 0.75):
            p.drawLine(QPointF(r.left() + f * r.width(), r.top()),
                       QPointF(r.left() + f * r.width(), r.bottom()))
            p.drawLine(QPointF(r.left(), r.bottom() - f * r.height()),
                       QPointF(r.right(), r.bottom() - f * r.height()))
        p.setPen(QPen(QColor(theme.BORDER_STRONG), 1, Qt.SolidLine))   # identity
        p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.top()))
        for xs, ys, strong in self._curves:
            path = QPainterPath()
            for i, (x, y) in enumerate(zip(xs, ys)):
                pt = QPointF(r.left() + x * r.width(), r.bottom() - y * r.height())
                if i == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
            p.setPen(QPen(QColor(theme.TEXT if strong else theme.TEXT_MUTED),
                          2 if strong else 1, Qt.SolidLine if strong else Qt.DashLine))
            p.drawPath(path)
        p.end()



class XTonePopup(QFrame):
    """Frameless popup (closes on an outside click). Emits `changed(options)`
    (this tool's keys only) on every edit and `closed()` when dismissed."""

    changed = Signal(dict)
    closed = Signal()

    def __init__(self, tool, shape, slider_value, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.tool = tool
        self._slider_value = int(slider_value)
        shape = normalize_xtone_shape(shape) or dict(XTONE_SHAPE_DEFAULT)
        self.setObjectName("xTonePopup")
        self.setStyleSheet(
            f"#xTonePopup {{ background: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER_STRONG}; border-radius: 6px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        title = QLabel(f"{TOOL_NAMES[tool]} options")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        lay.addWidget(title)

        self.checks = {}
        for key, label, tip in CHECKS[tool]:
            cb = QCheckBox(label)
            cb.setToolTip(tip)
            cb.setChecked(bool(shape[key]))
            cb.toggled.connect(self._emit)
            lay.addWidget(cb)
            self.checks[key] = cb

        self.range_slider = None
        if tool in RANGE_TIPS:
            row = QHBoxLayout()
            lbl = QLabel("Range")
            lbl.setToolTip(RANGE_TIPS[tool])
            self.range_slider = QSlider(Qt.Horizontal)
            self.range_slider.setRange(0, 100)
            self.range_slider.setValue(int(shape[f"{tool}_range"]))
            self.range_slider.setToolTip(RANGE_TIPS[tool])
            self.range_slider.valueChanged.connect(self._emit)
            self.range_value = QLabel(str(self.range_slider.value()))
            self.range_value.setFixedWidth(28)
            row.addWidget(lbl)
            row.addWidget(self.range_slider, 1)
            row.addWidget(self.range_value)
            lay.addLayout(row)
            ends = QHBoxLayout()
            ends.setContentsMargins(lbl.sizeHint().width() + row.spacing(), 0,
                                    self.range_value.width() + row.spacing(), 0)
            for text, align in (("Narrow", Qt.AlignLeft), ("Wide", Qt.AlignRight)):
                e = QLabel(text)
                e.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 10px;")
                e.setAlignment(align)
                ends.addWidget(e)
            lay.addLayout(ends)

        self.preview = ToneCurvePreview()
        lay.addWidget(self.preview, alignment=Qt.AlignHCenter)

        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setToolTip("Back to the default behaviour")
        self.reset_btn.clicked.connect(self.reset)
        lay.addWidget(self.reset_btn)
        self._refresh_preview()

    # -- state -------------------------------------------------------------
    def values(self):
        out = {key: cb.isChecked() for key, cb in self.checks.items()}
        if self.range_slider is not None:
            out[f"{self.tool}_range"] = self.range_slider.value()
        return out

    def set_slider_value(self, v):
        self._slider_value = int(v)
        self._refresh_preview()

    def reset(self):
        widgets = list(self.checks.values()) + ([self.range_slider] if self.range_slider else [])
        for w in widgets:
            w.blockSignals(True)
        for key, cb in self.checks.items():
            cb.setChecked(XTONE_SHAPE_DEFAULT[key])
        if self.range_slider is not None:
            self.range_slider.setValue(XTONE_SHAPE_DEFAULT[f"{self.tool}_range"])
        for w in widgets:
            w.blockSignals(False)
        self._emit()

    def _emit(self, *_):
        if self.range_slider is not None:
            self.range_value.setText(str(self.range_slider.value()))
        self._refresh_preview()
        self.changed.emit(self.values())

    def _refresh_preview(self):
        shape = dict(XTONE_SHAPE_DEFAULT)
        shape.update(self.values())
        xs = np.linspace(0.0, 1.0, 161)

        def curve(v):
            kw = {SLIDER_KEYS[self.tool]: v}
            return grey_curve(xs, shape=shape, **kw)
        if self._slider_value:
            curves = [(xs, curve(self._slider_value), True)]
        else:
            curves = [(xs, curve(100), False), (xs, curve(-100), False)]
        self.preview.set_curves(curves)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)

    def hideEvent(self, event):
        self.closed.emit()
        super().hideEvent(event)
