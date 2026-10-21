"""Keep a hand-placed popup inside the screen, the way QMenu does: open at
the cursor, but flip to the left of it / above it when there is no room, and
clamp as a last resort. Qt's own menus do this for themselves; popups we
move() by hand (Colour Organics, Highlights/Shadows range) did not, and
opened partly off the bottom or right edge."""
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication


def popup_position(pos: QPoint, size, avail: QRect) -> QPoint:
    """Top-left for a popup of ``size`` opened at global ``pos`` inside the
    available screen rectangle ``avail``."""
    w, h = size.width(), size.height()
    x, y = pos.x(), pos.y()
    if x + w > avail.left() + avail.width():
        x = pos.x() - w                    # open to the left of the cursor
    if y + h > avail.top() + avail.height():
        y = pos.y() - h                    # open above the cursor
    x = max(avail.left(), min(x, avail.left() + avail.width() - w))
    y = max(avail.top(), min(y, avail.top() + avail.height() - h))
    return QPoint(x, y)


def place_on_screen(widget, pos: QPoint) -> None:
    """Move ``widget`` (already sized, e.g. after adjustSize()) to ``pos``,
    kept inside the available area of the screen under ``pos``."""
    screen = (QGuiApplication.screenAt(pos) or widget.screen()
              or QGuiApplication.primaryScreen())
    if screen is None:
        widget.move(pos)
        return
    size = widget.size()
    if size.width() <= 0 or size.height() <= 0:
        size = widget.sizeHint()
    widget.move(popup_position(pos, size, screen.availableGeometry()))
