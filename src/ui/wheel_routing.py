"""Every scroll goes to the widget under the pointer (spec/wheel-routing.md).

Qt 6 (6.7.1 here, unchanged through 6.9) locks a scroll *sequence* onto the
widget it started on: a trackpad or smooth-scrolling mouse sends ScrollBegin
... ScrollEnd, and between the two `QApplicationPrivate::wheel_widget` gets
every event wherever the pointer goes. If a sequence never sends its
ScrollEnd (it can be dropped, e.g. while a popup or modal box is up, or a
driver never sends one), the lock is never released, and a plain mouse wheel
(which has no phases, so never ends a sequence) is then delivered to that
widget for good. In FreeCCR that is usually the picture: every notch zooms
it and neither side panel will scroll.

`WheelRouter` is an application-wide event filter that sees each wheel event
twice: first as the spontaneous event to the window (where the pointer is),
then as Qt's delivery to a widget. When the delivery is not to the widget
under the pointer (or one of its ancestors / descendants, which is ordinary
routing), it delivers the event there itself, walking up the parents until
one accepts it as Qt does, and swallows the misrouted one. A trackpad
gesture keeps its own lock: the rest of a sequence goes where its
ScrollBegin went, as Qt intends. Correctly routed events are not touched.
"""
from PySide6.QtCore import QObject, QEvent, QPointF, Qt
from PySide6.QtGui import QWheelEvent, QWindow
from PySide6.QtWidgets import QApplication, QWidget

try:
    from shiboken6 import isValid as _alive
except ImportError:  # pragma: no cover
    def _alive(obj):
        return obj is not None


def widget_at(gpos):
    """The widget under a global point (a seam for tests)."""
    return QApplication.widgetAt(gpos)


def _related(a, b):
    """Same widget, or one inside the other (ordinary wheel routing)."""
    return a is b or a.isAncestorOf(b) or b.isAncestorOf(a)


class WheelRouter(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending = None       # (timestamp, global pos, phase) of the window event
        self._gesture = None       # where the current phased sequence began
        self._delivering = False
        self.redirected = 0        # how many events it has had to re-route

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.Wheel or self._delivering:
            return False
        if isinstance(obj, QWindow):
            # the spontaneous event, before Qt picks a widget for it
            self._pending = (event.timestamp(), event.globalPosition().toPoint(),
                             event.phase())
            return False
        if not isinstance(obj, QWidget) or self._pending is None:
            return False
        stamp, gpos, phase = self._pending
        if event.timestamp() != stamp:
            return False           # not the delivery of that event
        self._pending = None       # judge only the first delivery
        want = self._target(gpos, phase)
        if want is None or _related(obj, want):
            return False
        self.redirected += 1
        self._deliver(want, event, gpos)
        return True

    def _target(self, gpos, phase):
        if phase in (Qt.ScrollPhase.NoScrollPhase, Qt.ScrollPhase.ScrollBegin):
            want = widget_at(gpos)
            self._gesture = want if phase == Qt.ScrollPhase.ScrollBegin else None
            return want
        # the rest of a phased sequence goes where it began (Qt's own lock)
        want = self._gesture if (self._gesture is not None and _alive(self._gesture)) \
            else widget_at(gpos)
        if phase == Qt.ScrollPhase.ScrollEnd:
            self._gesture = None
        return want

    def _deliver(self, target, event, gpos):
        """Send the event to `target`, then up its parents until one accepts
        it (Qt's own propagation, which a re-sent event doesn't get)."""
        self._delivering = True
        try:
            w = target
            while w is not None:
                if w.isEnabled():
                    e = QWheelEvent(QPointF(w.mapFromGlobal(gpos)), QPointF(gpos),
                                    event.pixelDelta(), event.angleDelta(),
                                    event.buttons(), event.modifiers(), event.phase(),
                                    event.inverted(), event.source())
                    e.setAccepted(True)        # handlers ignore what they don't use
                    QApplication.sendEvent(w, e)
                    if e.isAccepted():
                        break
                if w.isWindow() or w.testAttribute(Qt.WidgetAttribute.WA_NoMousePropagation):
                    break
                w = w.parentWidget()
        finally:
            self._delivering = False


_router = None


def install_wheel_router(app):
    """Install once per application (idempotent). Returns the router."""
    global _router
    if _router is None:
        _router = WheelRouter(app)
        app.installEventFilter(_router)
    return _router
