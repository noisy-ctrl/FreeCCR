"""What is being scanned, picked from three picture tiles (spec/scan-type-tiles.md).

RGB (trichrome), White light (colour) and B&W, each a small negative over a
light panel, drawn in code so it is sharp at any screen scale and follows the
window's look. `ScanTypeTiles.currentData()` is the scan type
(`core.scan_type.SCAN_TYPES`).
"""
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QPainter, QPainterPath, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QWidget

from core import scan_type as st
from ui import theme

PICTURE_W, PICTURE_H = 96, 60
PAD = 8                     # round the picture and under the name
NAME_GAP = 6                # picture to name
RADIUS = 6

# The light: bright enough to read as LEDs on either look.
LIGHT_R, LIGHT_G, LIGHT_B = "#e8473f", "#3fbf4a", "#3f6ff0"
LIGHT_W = "#f7f7f2"
# The negatives, as a negative looks: the bright sky dense, the dark hills
# thin, the sun densest (keys: base, outline, sky, hills, sun).
COLOUR_NEG = ("#d98a3d", "#8a4e1c", "#7a4219", "#c27330", "#4a250d")
BW_NEG = ("#d2d2cf", "#7c7c7a", "#4a4a4a", "#a9a9a6", "#262626")

PICTURES = {"trichrome": (COLOUR_NEG, (LIGHT_R, LIGHT_G, LIGHT_B)),
            "colour": (COLOUR_NEG, (LIGHT_W,)),
            "bw": (BW_NEG, (LIGHT_W,))}


def paint_picture(p, rect, kind):
    """The scan type's picture in `rect` (96 × 60 logical px is its design
    size; it scales). `p` is an active QPainter."""
    neg, lights = PICTURES[kind]
    base, outline, sky, hills, sun = (QColor(c) for c in neg)
    p.save()
    p.translate(rect.topLeft())
    p.scale(rect.width() / PICTURE_W, rect.height() / PICTURE_H)
    p.setRenderHint(QPainter.Antialiasing, True)

    # the light panel under the film, its light spilling up round the film
    bar = QRectF(8, PICTURE_H - 13, PICTURE_W - 16, 7)
    seg = bar.width() / len(lights)
    for i, c in enumerate(lights):
        cx = bar.left() + (i + 0.5) * seg
        glow = QRadialGradient(QPointF(0, 0), 1.0)
        g0 = QColor(c)
        g0.setAlpha(150)
        g1 = QColor(c)
        g1.setAlpha(0)
        glow.setColorAt(0, g0)
        glow.setColorAt(1, g1)
        p.save()
        p.setClipRect(QRectF(0, 0, PICTURE_W, bar.center().y()))   # upwards only
        p.translate(cx, bar.center().y())
        p.scale(seg * 0.5 + 8, 30)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(0, 0), 1.0, 1.0)
        p.restore()
    path = QPainterPath()
    path.addRoundedRect(bar, 3, 3)
    p.save()
    p.setClipPath(path)
    for i, c in enumerate(lights):
        p.fillRect(QRectF(bar.left() + i * seg, bar.top(), seg + 0.5, bar.height()), QColor(c))
    p.restore()
    p.setPen(QPen(QColor(0, 0, 0, 90), 0.8))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)

    # the strip of film, its sprocket holes showing the light
    film = QRectF(6, 4, PICTURE_W - 12, 36)
    p.setPen(QPen(outline, 1))
    p.setBrush(base)
    p.drawRoundedRect(film, 2, 2)
    p.setPen(Qt.NoPen)
    n_holes = 9
    step = (film.width() - 8) / n_holes
    for row_y in (film.top() + 2.5, film.bottom() - 6):
        for i in range(n_holes):
            p.setBrush(QColor(lights[i % len(lights)]))
            p.drawRoundedRect(QRectF(film.left() + 4 + i * step + (step - 5) / 2,
                                     row_y, 5, 3.5), 1, 1)

    # the picture in the frame: dense sky, densest sun, thin hills
    frame = QRectF(film.left() + 7, film.top() + 8.5, film.width() - 14, film.height() - 17)
    p.save()
    p.setClipRect(frame)
    p.fillRect(frame, sky)
    p.setBrush(sun)
    p.drawEllipse(QPointF(frame.left() + frame.width() * 0.70, frame.top() + 6.5), 4.2, 4.2)
    hill = QPainterPath(QPointF(frame.left(), frame.bottom()))
    hill.lineTo(frame.left(), frame.top() + 12)
    hill.cubicTo(frame.left() + 14, frame.top() + 4, frame.left() + 26, frame.top() + 8,
                 frame.left() + 36, frame.top() + 13)
    hill.cubicTo(frame.left() + 46, frame.top() + 9, frame.left() + 58, frame.top() + 10,
                 frame.right(), frame.top() + 14)
    hill.lineTo(frame.right(), frame.bottom())
    hill.closeSubpath()
    p.setBrush(hills)
    p.drawPath(hill)
    p.restore()
    p.restore()


class ScanTile(QAbstractButton):
    """One scan type: its picture with its short name under it."""

    def __init__(self, kind, colour, parent=None):
        super().__init__(parent)
        self.kind = kind
        self._colour = colour
        self.setCheckable(True)
        self.setFocusPolicy(Qt.NoFocus)          # the row is the tab stop
        self.setText(st.TILE_NAMES[kind])
        self.setToolTip(f"{st.LABELS[kind]}\n{st.TILE_HINTS[kind]}")
        self.setAccessibleName(st.TILE_NAMES[kind])
        self.setAccessibleDescription(st.LABELS[kind])
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        fm = self.fontMetrics()
        w = max(PICTURE_W, fm.horizontalAdvance(self.text())) + 2 * PAD
        return QSize(w, PAD + PICTURE_H + NAME_GAP + fm.height() + PAD)

    minimumSizeHint = sizeHint

    def paintEvent(self, _event):
        c = self._colour
        picked = self.isChecked()
        focused = picked and self.parentWidget() is not None and self.parentWidget().hasFocus()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        box = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if picked:
            pen = QPen(QColor(c(theme.TEXT if focused else theme.TEXT_MUTED)), 2)
            bg = c(theme.SURFACE_ACTIVE)
        else:
            hover = self.underMouse()
            pen = QPen(QColor(c(theme.BORDER_STRONG if hover else theme.BORDER)), 1)
            bg = c(theme.SURFACE)
        p.setPen(pen)
        p.setBrush(QColor(bg))
        p.drawRoundedRect(box, RADIUS, RADIUS)
        pic = QRectF((self.width() - PICTURE_W) / 2, PAD, PICTURE_W, PICTURE_H)
        paint_picture(p, pic, self.kind)
        f = QFont(self.font())
        f.setBold(picked)
        p.setFont(f)
        p.setPen(QColor(c(theme.TEXT if picked else theme.TEXT_MUTED)))
        name = QRectF(0, pic.bottom() + NAME_GAP, self.width(),
                      self.height() - pic.bottom() - NAME_GAP - PAD + 2)
        p.drawText(name, Qt.AlignHCenter | Qt.AlignTop, self.text())
        p.end()


class ScanTypeTiles(QWidget):
    """The three tiles in a row; one is always picked."""

    changed = Signal(str)

    def __init__(self, scan_type=st.DEFAULT, colour=None, parent=None):
        super().__init__(parent)
        self._colour_fn = colour or (lambda v: v)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Scanning")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.GAP_BTN)
        self.tiles = {}
        for kind in st.TILE_ORDER:
            t = ScanTile(kind, self._colour, self)
            t.clicked.connect(lambda _=False, k=kind: self._pick(k, focus=True))
            lay.addWidget(t)
            self.tiles[kind] = t
        lay.addStretch(1)
        self._current = None
        self.setCurrentData(scan_type)

    def _colour(self, value):
        return self._colour_fn(value)

    def set_colour(self, fn):
        """Recolour (the dialog's `restyle`, e.g. Capture's light look)."""
        self._colour_fn = fn or (lambda v: v)
        for t in self.tiles.values():
            t.update()

    def currentData(self):
        return self._current

    def setCurrentData(self, scan_type):
        self._pick(st.clean(scan_type), emit=False)

    def _pick(self, kind, emit=True, focus=False):
        for k, t in self.tiles.items():
            t.setChecked(k == kind)              # a click on the picked one keeps it
        if focus:
            self.setFocus(Qt.MouseFocusReason)
        if kind != self._current:
            self._current = kind
            if emit:
                self.changed.emit(kind)

    def step(self, delta):
        order = list(st.TILE_ORDER)
        i = order.index(self._current) + delta
        if 0 <= i < len(order):
            self._pick(order[i])

    def keyPressEvent(self, event):
        k = event.key()
        if k in (Qt.Key_Left, Qt.Key_Up):
            self.step(-1)
        elif k in (Qt.Key_Right, Qt.Key_Down):
            self.step(1)
        else:
            super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        for t in self.tiles.values():
            t.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        for t in self.tiles.values():
            t.update()
