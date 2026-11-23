"""Right-click popup for the developer edge effect: how far the developer
reaches (mm on the film) and whether each dye layer acts on its own. See
spec/developer-edge-effect.md."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
                               QPushButton, QCheckBox)

from core.edge_effect import EDGE_SHAPE_DEFAULT, EDGE_LIMITS, normalize_edge_shape
from ui import theme

LABEL_W = 64
VALUE_W = 58

REACH_TIP = ("How far the developer reaches across a border, in mm on the film. "
             "About 0.1 mm is what film shows; shorter keeps the effect to fine "
             "edges, wider takes in larger shapes (clouds, faces) and starts to "
             "look like a halo.")
LAYERS_TIP = ("Off: the three dye layers act together, so the effect follows "
              "the picture's lightness and adds no colour at an edge. On: each "
              "layer acts on its own density, as on film, which can leave a "
              "faint coloured border where two colours meet.")


def reach_text(v):
    return f"{int(v) / 100.0:.2f} mm"


def describe_edge(shape, full=False):
    """'reach 0.20 mm, each dye layer'. Only what differs from the default
    ('default' when nothing does), or with `full` the reach always."""
    shape = normalize_edge_shape(shape)
    s = shape or dict(EDGE_SHAPE_DEFAULT)
    parts = []
    if full or s["reach"] != EDGE_SHAPE_DEFAULT["reach"]:
        parts.append("reach " + reach_text(s["reach"]))
    if s["layers"]:
        parts.append("each dye layer")
    return ", ".join(parts) or "default"


class EdgePopup(QFrame):
    """Frameless popup (closes on an outside click). Emits `changed(shape)`
    (a full options dict) on every edit and `closed()` when dismissed."""

    changed = Signal(dict)
    closed = Signal()

    def __init__(self, shape=None, slider_value=0, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        shape = normalize_edge_shape(shape) or dict(EDGE_SHAPE_DEFAULT)
        self.setObjectName("edgePopup")
        self.setStyleSheet(
            f"#edgePopup {{ background: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER_STRONG}; border-radius: 6px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        title = QLabel("Edge Effect options")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        lay.addWidget(title)

        row = QHBoxLayout()
        lbl = QLabel("Reach")
        lbl.setToolTip(REACH_TIP)
        lbl.setFixedWidth(LABEL_W)
        self.reach = QSlider(Qt.Horizontal)
        self.reach.setRange(*EDGE_LIMITS["reach"])
        self.reach.setValue(int(shape["reach"]))
        self.reach.setToolTip(REACH_TIP)
        self.reach.valueChanged.connect(self._emit)
        self.reach_lbl = QLabel(reach_text(self.reach.value()))
        self.reach_lbl.setFixedWidth(VALUE_W)
        row.addWidget(lbl)
        row.addWidget(self.reach, 1)
        row.addWidget(self.reach_lbl)
        lay.addLayout(row)
        ends = QHBoxLayout()
        ends.setContentsMargins(LABEL_W + row.spacing(), 0, VALUE_W + row.spacing(), 0)
        for text, align in (("Finer", Qt.AlignLeft), ("Wider", Qt.AlignRight)):
            e = QLabel(text)
            e.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 10px;")
            e.setAlignment(align)
            ends.addWidget(e)
        lay.addLayout(ends)

        self.layers = QCheckBox("Each dye layer on its own")
        self.layers.setToolTip(LAYERS_TIP)
        self.layers.setChecked(bool(shape["layers"]))
        self.layers.toggled.connect(self._emit)
        lay.addWidget(self.layers)

        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setToolTip("Back to the defaults")
        self.reset_btn.clicked.connect(self.reset)
        lay.addWidget(self.reset_btn)

    def values(self):
        return {"reach": self.reach.value(), "layers": int(self.layers.isChecked())}

    def set_slider_value(self, v):
        pass

    def reset(self):
        for w in (self.reach, self.layers):
            w.blockSignals(True)
        self.reach.setValue(EDGE_SHAPE_DEFAULT["reach"])
        self.layers.setChecked(bool(EDGE_SHAPE_DEFAULT["layers"]))
        for w in (self.reach, self.layers):
            w.blockSignals(False)
        self._emit()

    def _emit(self, *_):
        self.reach_lbl.setText(reach_text(self.reach.value()))
        self.changed.emit(self.values())

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)

    def hideEvent(self, event):
        self.closed.emit()
        super().hideEvent(event)
