"""Right-click popup for the Highlights / Shadows sliders: range and
keep-endpoint options, with a live curve preview.
See spec/highlights-shadows-range.md."""

import numpy as np
from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QPainterPath
from PySide6.QtWidgets import (QFrame, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QCheckBox, QSlider, QPushButton)

from core.ccr_processor import tone_region_curve, TONE_SHAPE_DEFAULT
from ui import theme

TOOL_NAMES = {"hl": "Highlights", "sh": "Shadows"}
KEEP_LABELS = {"hl": "Keep white point", "sh": "Keep black point"}
KEEP_TIPS = {
    "hl": ("Only tones inside the highlight region move. The brightest "
           "highlight stays white and nothing new clips; the effect fades out "
           "smoothly before white. Its strength is limited by how narrow the "
           "range is, so the curve can never fold back on itself."),
    "sh": ("Only tones inside the shadow region move. The deepest shadow stays "
           "black and nothing new crushes; the effect fades out smoothly before "
           "black. Its strength is limited by how narrow the range is, so the "
           "curve can never fold back on itself."),
}
RANGE_TIP = ("How far the tool reaches into the midtones. The middle is the "
             "original behaviour (with Keep off).")


def describe_shape(shape):
    """Short text for a (normalised) shape, e.g. for a tooltip / paste row."""
    shape = shape or TONE_SHAPE_DEFAULT
    parts = []
    for tool in ("hl", "sh"):
        keep, r = shape.get(f"{tool}_keep", False), shape.get(f"{tool}_range", 50)
        if keep or r != 50:
            bits = []
            if keep:
                bits.append("keep " + ("white" if tool == "hl" else "black"))
            if r != 50:
                bits.append(f"range {r}")
            parts.append(f"{TOOL_NAMES[tool]}: " + ", ".join(bits))
    return "; ".join(parts) if parts else "default"


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


class ToneRangePopup(QFrame):
    """Frameless popup (closes on an outside click). Emits `changed(tool,
    keep, range)` on every edit and `closed()` when dismissed."""

    changed = Signal(str, bool, int)
    closed = Signal()

    def __init__(self, tool, keep, rng, slider_value, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.tool = tool
        self._slider_value = int(slider_value)
        self.setObjectName("toneRangePopup")
        self.setStyleSheet(
            f"#toneRangePopup {{ background: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER_STRONG}; border-radius: 6px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        title = QLabel(f"{TOOL_NAMES[tool]} range")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        lay.addWidget(title)

        self.keep_check = QCheckBox(KEEP_LABELS[tool])
        self.keep_check.setToolTip(KEEP_TIPS[tool])
        self.keep_check.setChecked(bool(keep))
        self.keep_check.toggled.connect(self._emit)
        lay.addWidget(self.keep_check)

        row = QHBoxLayout()
        lbl = QLabel("Range")
        lbl.setToolTip(RANGE_TIP)
        self.range_slider = QSlider(Qt.Horizontal)
        self.range_slider.setRange(0, 100)
        self.range_slider.setValue(int(rng))
        self.range_slider.setToolTip(RANGE_TIP)
        self.range_slider.valueChanged.connect(self._emit)
        self.range_value = QLabel(str(int(rng)))
        self.range_value.setFixedWidth(28)
        row.addWidget(lbl)
        row.addWidget(self.range_slider, 1)
        row.addWidget(self.range_value)
        lay.addLayout(row)
        # Narrow / Wide under the slider's own ends (not under "Range").
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
        self.reset_btn.setToolTip("Back to the original behaviour")
        self.reset_btn.clicked.connect(self.reset)
        lay.addWidget(self.reset_btn)
        self._refresh_preview()

    # -- state -------------------------------------------------------------
    def values(self):
        return self.keep_check.isChecked(), self.range_slider.value()

    def set_slider_value(self, v):
        self._slider_value = int(v)
        self._refresh_preview()

    def reset(self):
        for w in (self.keep_check, self.range_slider):
            w.blockSignals(True)
        self.keep_check.setChecked(TONE_SHAPE_DEFAULT[f"{self.tool}_keep"])
        self.range_slider.setValue(TONE_SHAPE_DEFAULT[f"{self.tool}_range"])
        for w in (self.keep_check, self.range_slider):
            w.blockSignals(False)
        self._emit()

    def _emit(self, *_):
        keep, rng = self.values()
        self.range_value.setText(str(rng))
        self._refresh_preview()
        self.changed.emit(self.tool, keep, rng)

    def _refresh_preview(self):
        keep, rng = self.values()
        shape = dict(TONE_SHAPE_DEFAULT)
        shape[f"{self.tool}_keep"] = keep
        shape[f"{self.tool}_range"] = rng
        xs = np.linspace(0.0, 1.0, 161)

        def curve(v):
            h, s = (v, 0) if self.tool == "hl" else (0, v)
            return tone_region_curve(xs, h, s, shape)
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
