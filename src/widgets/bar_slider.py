"""A bar slider: one rounded bar, its name inside on the left and its value
inside on the right, a faint line at 0 and a bright line at the value
(spec/printer-light-sliders.md). Used for Printer Lights' slider view.

Drag moves the value relatively from where it was (it never jumps to the
pointer), Shift-drag four times finer; a CLICK right of the 0 line adds a
step and left of it takes one off, Shift-click the bigger step, as the pad's
buttons do (revision 2026-11-19); double-click resets to 0; Left / Right
step, Shift for the bigger step. The wheel is ignored so the panel
scrolls."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui import theme


class BarSlider(QWidget):
    valueChanged = Signal(float)
    released = Signal()

    FINE = 0.25               # Shift-drag speed
    DRAG_PX = 3               # a press that moves less than this is a click

    def __init__(self, label, minimum, maximum, step=1.0, key_steps=(1.0, 4.0),
                 left=None, right=None, fmt=None, tick=None, parent=None):
        super().__init__(parent)
        self._label = label
        self._min, self._max = float(minimum), float(maximum)
        self._step = float(step)
        self._keys = tuple(float(k) for k in key_steps)
        self._left = QColor(left) if left else None
        self._right = QColor(right) if right else None
        self._fmt = fmt or (lambda v: f"{v:+g}" if v else "0")
        self._tick = tick
        self._value = 0.0
        self._press = None
        self._dragging = False
        self._hover = False
        self.setFixedHeight(theme.CONTROL_H - 2)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(80)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.SizeHorCursor)
        self.setAccessibleName(label)

    # --- value --------------------------------------------------------------
    def value(self):
        return self._value

    def label(self):
        return self._label

    def value_text(self):
        return self._fmt(self._value)

    def _snap(self, v):
        v = max(self._min, min(self._max, float(v)))
        if self._step > 0:
            v = round(v / self._step) * self._step
            v = max(self._min, min(self._max, v))
        return round(v, 6) + 0.0

    def setValue(self, v, emit=False):
        v = self._snap(v)
        changed = v != self._value
        self._value = v
        self.update()
        if changed and emit:
            self.valueChanged.emit(v)
        return changed

    # --- geometry -----------------------------------------------------------
    def _bar(self):
        return QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

    def _x_of(self, v):
        r = self._bar()
        span = self._max - self._min or 1.0
        return r.left() + 3 + (v - self._min) / span * (r.width() - 6)

    def units_per_px(self):
        r = self._bar()
        return (self._max - self._min) / max(1.0, r.width() - 6)

    # --- painting -----------------------------------------------------------
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._bar()
        on = self.isEnabled()
        bg = QColor(theme.SURFACE_HOVER if (self._hover and on) else theme.SURFACE)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, theme.RADIUS_MD, theme.RADIUS_MD)
        if self._left is not None or self._right is not None:
            g = QLinearGradient(r.left(), 0, r.right(), 0)
            a = 72 if on else 30
            for at, col in ((0.0, self._left), (1.0, self._right)):
                c = QColor(col) if col is not None else QColor(0, 0, 0, 0)
                if col is not None:
                    c.setAlpha(a)
                g.setColorAt(at, c)
            g.setColorAt(0.5, QColor(0, 0, 0, 0))
            p.setBrush(g)
            p.drawRoundedRect(r, theme.RADIUS_MD, theme.RADIUS_MD)
        # ticks on hover: one a point (the caller's `tick`), every 5th taller
        if self._hover and on and self._tick:
            n0 = int(self._min // self._tick)
            n1 = int(self._max // self._tick)
            for n in range(n0, n1 + 1):
                if n == 0:
                    continue
                x = self._x_of(n * self._tick)
                tall = n % 5 == 0
                c = QColor(theme.TEXT)
                c.setAlpha(70 if tall else 38)
                p.setPen(QPen(c, 1))
                h = r.height() * (0.30 if tall else 0.18)
                p.drawLine(QPointF(x, r.top() + 1), QPointF(x, r.top() + 1 + h))
        # 0 and the value
        zx = self._x_of(0.0)
        c0 = QColor(theme.TEXT_MUTED)
        c0.setAlpha(110 if on else 50)
        p.setPen(QPen(c0, 1))
        p.drawLine(QPointF(zx, r.top() + 4), QPointF(zx, r.bottom() - 4))
        vx = self._x_of(self._value)
        p.setPen(QPen(QColor(theme.TEXT if on else theme.TEXT_DISABLED), 2))
        p.drawLine(QPointF(vx, r.top() + 2), QPointF(vx, r.bottom() - 2))
        # name and value
        f = QFont(self.font())
        f.setPixelSize(theme.FS_BODY)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT if on else theme.TEXT_DISABLED))
        tr = r.adjusted(9, 0, -9, 0)
        p.drawText(tr, Qt.AlignVCenter | Qt.AlignLeft, self._label)
        p.drawText(tr, Qt.AlignVCenter | Qt.AlignRight, self.value_text())
        if self.hasFocus() and on:
            p.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, theme.RADIUS_MD, theme.RADIUS_MD)
        p.end()

    # --- input --------------------------------------------------------------
    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if not self.isEnabled() or e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        self.setFocus(Qt.MouseFocusReason)
        self._press = (e.position().x(), self._value)
        self._dragging = False
        e.accept()

    def mouseMoveEvent(self, e):
        if self._press is None:
            return super().mouseMoveEvent(e)
        x0, v0 = self._press
        if not self._dragging and abs(e.position().x() - x0) < self.DRAG_PX:
            return
        self._dragging = True
        speed = self.FINE if e.modifiers() & Qt.ShiftModifier else 1.0
        self.setValue(v0 + (e.position().x() - x0) * self.units_per_px() * speed, emit=True)
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._press is not None and e.button() == Qt.LeftButton:
            self._press = None
            if not self._dragging:
                # a click: a step towards the side clicked (right of 0 = +)
                big = bool(e.modifiers() & Qt.ShiftModifier)
                step = self._keys[1 if big else 0]
                up = e.position().x() >= self._x_of(0.0)
                self.setValue(self._value + (step if up else -step), emit=True)
            self._dragging = False
            self.released.emit()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self.isEnabled() and e.button() == Qt.LeftButton:
            self._press = None
            self.setValue(0.0, emit=True)
            self.released.emit()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e):
        if not self.isEnabled() or e.key() not in (Qt.Key_Left, Qt.Key_Right):
            return super().keyPressEvent(e)
        big = bool(e.modifiers() & Qt.ShiftModifier)
        step = self._keys[1 if big else 0]
        self.setValue(self._value + (step if e.key() == Qt.Key_Right else -step), emit=True)
        self.released.emit()
        e.accept()

    def wheelEvent(self, e):
        e.ignore()                    # the panel scrolls

    def changeEvent(self, e):
        super().changeEvent(e)
        self.update()
