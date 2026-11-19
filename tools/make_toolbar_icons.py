"""Draw the canvas toolbar's icons (spec/toolbar-icons.md): monochrome
glyphs, black on transparency with shading by alpha, at 256 px, the way
theme.load_tinted_icon expects them; False colour is the one coloured icon
(its bands' own colours, loaded untinted). Drawn in a 24-unit grid.

    python3.11 tools/make_toolbar_icons.py            # writes src/icons/
"""
import math
import os
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QBrush, QTransform)

S = 256
U = S / 24.0
W = 1.8              # stroke, grid units
BLACK = QColor(0, 0, 0)


def canvas():
    img = QImage(S, S, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(U, U)
    return img, p


def pen(w=W, alpha=255, cap=Qt.RoundCap, join=Qt.RoundJoin, dash=None):
    c = QColor(0, 0, 0, alpha)
    pn = QPen(c, w, Qt.SolidLine, cap, join)
    if dash:
        pn.setDashPattern([d / w for d in dash])
    return pn


def cursor_path(tip_x, tip_y, s=1.0):
    """An arrow pointer, tip at (tip_x, tip_y), pointing up-left."""
    pts = [(0, 0), (0, 9.2), (2.4, 7.0), (4.2, 10.6), (5.9, 9.8), (4.2, 6.3), (7.4, 6.1)]
    path = QPainterPath()
    path.moveTo(tip_x + pts[0][0] * s, tip_y + pts[0][1] * s)
    for x, y in pts[1:]:
        path.lineTo(tip_x + x * s, tip_y + y * s)
    path.closeSubpath()
    return path


def knockout_and_cursor(p, cur, gap=1.6):
    p.save()
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    p.setPen(QPen(QColor(0, 0, 0), gap * 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(QBrush(QColor(0, 0, 0)))
    p.drawPath(cur)
    p.restore()
    p.setPen(QPen(BLACK, 1.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(QBrush(BLACK))
    p.drawPath(cur)


# --------------------------------------------------------------------------- #
def rotate(left=True):
    img, p = canvas()
    if not left:
        p.translate(24, 0)
        p.scale(-1, 1)
    # the frame being turned
    p.setPen(pen())
    p.setBrush(Qt.NoBrush)
    sq = QRectF(11.2, 11.2, 9.4, 9.4)
    p.drawRoundedRect(sq, 1.6, 1.6)
    # a quarter turn round it, from its top to its left, ending in a head
    c, r = sq.center(), 10.6
    box = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
    path = QPainterPath()
    path.arcMoveTo(box, 90)
    path.arcTo(box, 90, 82)
    p.drawPath(path)
    end = path.currentPosition()
    head = QPainterPath()
    head.moveTo(end.x() - 3.0, end.y() - 0.6)
    head.lineTo(end.x() + 3.0, end.y() - 0.2)
    head.lineTo(end.x() + 0.3, end.y() + 4.0)
    head.closeSubpath()
    p.setPen(pen(1.0))
    p.setBrush(QBrush(BLACK))
    p.drawPath(head)
    p.end()
    return img


def mirror(horizontal=True):
    img, p = canvas()
    if not horizontal:
        p.translate(24, 0)
        p.rotate(90)
    p.setPen(pen(1.4))
    p.setBrush(QBrush(BLACK))
    left = QPainterPath()
    left.moveTo(2.8, 12)
    left.lineTo(9.4, 6.6)
    left.lineTo(9.4, 17.4)
    left.closeSubpath()
    p.drawPath(left)
    # the reflection: the same triangle, outlined
    p.setBrush(Qt.NoBrush)
    p.setPen(pen(1.6))
    right = QPainterPath()
    right.moveTo(21.2, 12)
    right.lineTo(14.6, 6.6)
    right.lineTo(14.6, 17.4)
    right.closeSubpath()
    p.drawPath(right)
    p.setPen(pen(1.8))
    p.drawLine(QPointF(12, 3.2), QPointF(12, 20.8))
    p.end()
    return img


def mirror_filled(horizontal=True):
    img, p = canvas()
    if not horizontal:
        p.translate(24, 0)
        p.rotate(90)
    p.setPen(pen(1.4))
    p.setBrush(QBrush(BLACK))
    for pts in (((2.8, 12), (9.4, 6.6), (9.4, 17.4)), ((21.2, 12), (14.6, 6.6), (14.6, 17.4))):
        path = QPainterPath()
        path.moveTo(*pts[0])
        for q in pts[1:]:
            path.lineTo(*q)
        path.closeSubpath()
        p.drawPath(path)
    p.setPen(pen(1.8))
    p.drawLine(QPointF(12, 3.2), QPointF(12, 20.8))
    p.end()
    return img


def sprockets():
    img, p = canvas()
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(BLACK))
    for x in (5.0, 15.6):
        for y in (3.0, 8.2, 13.4, 18.6):
            p.drawRoundedRect(QRectF(x, y, 3.4, 3.0), 0.8, 0.8)
    p.end()
    return img


def area():
    img, p = canvas()
    p.setPen(pen(1.7, dash=[2.4, 2.0], cap=Qt.FlatCap))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(10.0, 10.0), 7.4, 7.4)
    knockout_and_cursor(p, cursor_path(13.6, 12.8, 0.92))
    p.end()
    return img


def gradient():
    img, p = canvas()
    r = QRectF(2.6, 4.0, 16.8, 12.4)
    g = QLinearGradient(r.left(), 0, r.right(), 0)
    g.setColorAt(0.0, QColor(0, 0, 0, 230))
    g.setColorAt(1.0, QColor(0, 0, 0, 20))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawRect(r.adjusted(1.6, 1.6, -1.6, -1.6))
    p.setBrush(Qt.NoBrush)
    p.setPen(pen(1.6, dash=[2.2, 1.8], cap=Qt.FlatCap))
    p.drawRect(r)
    knockout_and_cursor(p, cursor_path(14.2, 12.6, 0.92))
    p.end()
    return img


def blower():
    """A dust blower: a rubber bulb with its nozzle, nothing else."""
    img, p = canvas()
    p.translate(12, 12)
    p.rotate(40)
    p.translate(-12, -12)
    body = QPainterPath()
    body.addEllipse(QPointF(12, 16.2), 5.3, 5.6)
    neck = QPainterPath()
    neck.moveTo(9.0, 12.4)
    neck.lineTo(10.6, 9.2)
    neck.lineTo(13.4, 9.2)
    neck.lineTo(15.0, 12.4)
    neck.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(BLACK))
    p.drawPath(body.united(neck))
    # the bulb's shine, cut out
    p.save()
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    p.setPen(QPen(QColor(0, 0, 0), 1.2, Qt.SolidLine, Qt.RoundCap))
    p.setBrush(Qt.NoBrush)
    sh = QPainterPath()
    box = QRectF(8.6, 12.4, 6.8, 7.6)
    sh.arcMoveTo(box, 115)
    sh.arcTo(box, 115, 65)
    p.drawPath(sh)
    p.restore()
    # collar (a hair apart from the bulb) and nozzle
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(BLACK))
    p.drawRoundedRect(QRectF(10.2, 6.6, 3.6, 2.0), 0.6, 0.6)
    nz = QPainterPath()
    nz.moveTo(11.0, 6.8)
    nz.lineTo(11.55, 1.4)
    nz.lineTo(12.45, 1.4)
    nz.lineTo(13.0, 6.8)
    nz.closeSubpath()
    p.drawPath(nz)
    p.end()
    return img


def hdr():
    img, p = canvas()
    p.setPen(pen(1.7))
    p.setBrush(Qt.NoBrush)
    ring = QRectF(2.2, 2.2, 19.6, 19.6)
    for start in (35, 215):
        a = QPainterPath()
        a.arcMoveTo(ring, start)
        a.arcTo(ring, start, 110)
        p.drawPath(a)
    p.setPen(pen(1.55))
    t = QPainterPath()
    top, bot = 9.3, 14.7
    t.moveTo(4.6, top); t.lineTo(4.6, bot)
    t.moveTo(8.0, top); t.lineTo(8.0, bot)
    t.moveTo(4.6, 12); t.lineTo(8.0, 12)
    t.moveTo(9.9, top); t.lineTo(11.3, top)
    t.quadTo(13.9, top, 13.9, 12); t.quadTo(13.9, bot, 11.3, bot)
    t.lineTo(9.9, bot); t.closeSubpath()
    t.moveTo(15.6, bot); t.lineTo(15.6, top); t.lineTo(17.7, top)
    t.quadTo(19.5, top, 19.5, 10.75); t.quadTo(19.5, 12.3, 17.7, 12.3)
    t.lineTo(15.6, 12.3)
    t.moveTo(17.6, 12.3); t.lineTo(19.6, bot)
    p.drawPath(t)
    p.end()
    return img


def false_colour(steps=8):
    """A ring in stepped tones: the bands false colour draws (monochrome
    draft, kept for reference)."""
    img, p = canvas()
    ring = QRectF(4.0, 4.0, 16.0, 16.0)
    span = 360.0 / steps
    for i in range(steps):
        alpha = int(60 + 195 * i / (steps - 1))
        pn = QPen(QColor(0, 0, 0, alpha), 3.4, Qt.SolidLine, Qt.FlatCap)
        p.setPen(pn)
        a = QPainterPath()
        start = 90 - i * span
        a.arcMoveTo(ring, start)
        a.arcTo(ring, start, -(span - 7))
        p.drawPath(a)
    p.end()
    return img


def false_colour_coloured():
    """The ring in false colour's own band colours, black clipping to white
    clipping going round, a little softened to sit with the grey icons."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
    from core.false_colour import BANDS
    img, p = canvas()
    ring = QRectF(4.0, 4.0, 16.0, 16.0)
    cols = [c for _lo, _hi, c, _l, _m in BANDS]
    span = 360.0 / len(cols)
    for i, (r, g, b) in enumerate(cols):
        c = QColor(r, g, b)
        h, sat, v, _a = c.getHsv()
        c = QColor.fromHsv(h, int(sat * 0.85), int(v * 0.92))
        p.setPen(QPen(c, 3.4, Qt.SolidLine, Qt.FlatCap))
        a = QPainterPath()
        start = 90 - i * span
        a.arcMoveTo(ring, start)
        a.arcTo(ring, start, -(span - 8))
        p.drawPath(a)
    p.end()
    return img


def camera():
    img, p = canvas()
    p.setPen(pen(1.7))
    p.setBrush(Qt.NoBrush)
    body = QPainterPath()
    body.addRoundedRect(QRectF(2.8, 7.0, 18.4, 13.0), 2.6, 2.6)
    hump = QPainterPath()
    hump.moveTo(7.6, 7.6)
    hump.lineTo(9.6, 4.4)
    hump.lineTo(14.4, 4.4)
    hump.lineTo(16.4, 7.6)
    hump.closeSubpath()
    p.drawPath(body.united(hump).simplified())
    p.drawEllipse(QPointF(12, 13.4), 4.0, 4.0)
    p.setPen(pen(1.3, alpha=170))
    p.drawEllipse(QPointF(12, 13.4), 1.7, 1.7)
    p.end()
    return img


def crop():
    img, p = canvas()
    p.setPen(pen(1.9, cap=Qt.SquareCap, join=Qt.MiterJoin))
    p.setBrush(Qt.NoBrush)
    a = QPainterPath()
    a.moveTo(6.4, 2.6); a.lineTo(6.4, 17.6); a.lineTo(21.4, 17.6)
    b = QPainterPath()
    b.moveTo(2.6, 6.4); b.lineTo(17.6, 6.4); b.lineTo(17.6, 21.4)
    p.drawPath(a)
    p.drawPath(b)
    p.end()
    return img


def canister():
    """A 35 mm cassette, upright, its film coming out of the light trap at
    full width, then the leader's half-width tongue: square-cornered, in a
    real cassette's proportions (body ~0.6 as wide as tall, the film nearly
    the body's height)."""
    img, p = canvas()
    body = QRectF(2.2, 5.2, 9.0, 14.8)
    p.setPen(pen(1.6, cap=Qt.SquareCap, join=Qt.MiterJoin))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(body, 0.4, 0.4)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(BLACK))
    p.drawRoundedRect(QRectF(1.6, 3.9, 10.2, 1.5), 0.3, 0.3)       # top lip
    p.drawRoundedRect(QRectF(1.6, 19.8, 10.2, 1.5), 0.3, 0.3)      # bottom lip
    p.drawRoundedRect(QRectF(5.1, 1.8, 3.2, 2.3), 0.3, 0.3)        # spool end
    # the film: full width out of the trap, then the leader's tongue
    top, bot, mid = 6.0, 19.2, 12.6
    film = QPainterPath()
    film.moveTo(11.2, top)
    film.lineTo(22.6, top)
    film.lineTo(22.6, mid)
    film.lineTo(18.6, mid)
    film.cubicTo(16.9, mid, 16.0, 15.0, 15.7, bot)
    film.lineTo(11.2, bot)
    film.closeSubpath()
    p.drawPath(film)
    # perforations, cut out: along the top edge all the way, along the
    # bottom only where the film is full width
    p.save()
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    for x in (12.4, 14.5, 16.6, 18.7, 20.8):
        p.drawRoundedRect(QRectF(x, top + 0.75, 1.15, 1.3), 0.2, 0.2)
    for x in (12.4, 14.3):
        p.drawRoundedRect(QRectF(x, bot - 2.05, 1.15, 1.3), 0.2, 0.2)
    p.restore()
    p.end()
    return img


# (file in src/icons, drawing): the names the app loads
ICONS = [
    ("rotate-left-icon-size_512.png", lambda: rotate(True)),
    ("rotate-right-icon-size_512.png", lambda: rotate(False)),
    ("vertical-mirror-icon.png", lambda: mirror(False)),
    ("horizontal-mirror-icon.png", lambda: mirror(True)),
    ("sprocket-holes-icon.png", sprockets),
    ("crop-icon.png", crop),
    ("area-icon.png", area),
    ("gradient-icon.png", gradient),
    ("dust-icon.png", blower),
    ("hdr-icon.png", hdr),
    ("false-colour-icon.png", false_colour_coloured),
    ("film-canister-icon.png", canister),
]
# Capture keeps its camera (camera-icon.png, the user's choice, 2026-11-19).


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    here = os.path.dirname(os.path.abspath(__file__))
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "..", "src", "icons")
    os.makedirs(out, exist_ok=True)
    for name, fn in ICONS:
        fn().save(os.path.join(out, name))
    print("written", len(ICONS), "to", os.path.normpath(out))
