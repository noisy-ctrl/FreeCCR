"""The Live Capture Monitor's canvas (spec/live-capture-monitor.md §3.3, §5.4,
§5.9): the newest frame drawn at device pixels, zoom and pan, the frame
guide, the alignment line, the sharpness area and readout, the clip layer,
and the one-shot modes (eyedropper, draw area, place line).

Everything placed on the picture is kept in sensor fractions of the full
frame and mapped through the geometry the frame was rendered with
(`core.live_feed.Geometry`), so it stays on the same spot of film whatever
the zoom, window size or panel state.
"""
import math

from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPainterPath
from PySide6.QtWidgets import QWidget

from ui import theme

GUIDE_DEFAULT = {"cx": 0.5, "cy": 0.5, "w": 0.6, "h": 0.6, "angle": 0.0, "format": None}
HANDLE_PX = 10          # corner grab radius (logical px)
ROTATE_PX = 30          # beyond a corner, out to here: rotate
ZOOM_STEP = 1.25
MIN_GUIDE = 0.02        # fraction of the frame


def clean_guide(g):
    out = dict(GUIDE_DEFAULT)
    if isinstance(g, dict):
        for k in ("cx", "cy", "w", "h", "angle"):
            try:
                out[k] = float(g.get(k, out[k]))
            except (TypeError, ValueError):
                pass
        out["format"] = g.get("format") if isinstance(g.get("format"), str) else None
    out["cx"] = min(1.0, max(0.0, out["cx"]))
    out["cy"] = min(1.0, max(0.0, out["cy"]))
    out["w"] = min(2.0, max(MIN_GUIDE, out["w"]))
    out["h"] = min(2.0, max(MIN_GUIDE, out["h"]))
    out["angle"] = max(-45.0, min(45.0, out["angle"]))
    return out


def guide_ratio(fmt):
    """Long/short of a film format key, or None (free)."""
    if not fmt:
        return None
    try:
        from core.film_formats import ratio
        return ratio(fmt)
    except Exception:
        return None


class LiveCanvas(QWidget):
    view_changed = Signal()            # zoom, pan or size changed
    picked = Signal(float, float)      # sensor pixels (eyedropper)
    area_drawn = Signal(object)        # sensor fractions (x0, y0, x1, y1)
    line_placed = Signal(float)        # sensor fraction across
    guide_changed = Signal(object)     # the guide dict
    pointer_moved = Signal()           # counts as use (idle dimming)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(480, 270)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.result = None
        self.message = "Looking for a camera…"
        self.scale = None                 # None = fit; else device px per sensor px
        self.cx = None
        self.cy = None
        self.guide_on = False
        self.guide = dict(GUIDE_DEFAULT)
        self.align_on = False
        self.align_x = 0.15
        self.sharp_on = False
        self.sharp_area = None
        self.mode = None                  # None | "pick" | "area" | "line"
        self._drag = None
        self._rubber = None
        self._last_size = None

    # ------------------------------------------------------------------ #
    # state
    # ------------------------------------------------------------------ #
    def dpr(self):
        try:
            return float(self.devicePixelRatioF()) or 1.0
        except Exception:
            return 1.0

    def device_size(self):
        d = self.dpr()
        return max(1, int(round(self.width() * d))), max(1, int(round(self.height() * d)))

    def geometry_(self):
        return self.result.geometry if self.result is not None else None

    def set_result(self, result):
        self.result = result
        self.message = ""
        self.update()

    def set_message(self, text):
        self.message = text or ""
        if text:
            self.result = None
        self.update()

    def set_mode(self, mode):
        self.mode = mode
        self._rubber = None
        self.setCursor(Qt.CrossCursor if mode else Qt.ArrowCursor)
        self.update()

    def cancel_mode(self):
        if self.mode:
            self.set_mode(None)
            return True
        return False

    def set_guide(self, guide, on=None):
        self.guide = clean_guide(guide)
        if on is not None:
            self.guide_on = bool(on)
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        size = self.device_size()
        if size != self._last_size:
            self._last_size = size
            self.view_changed.emit()

    # ------------------------------------------------------------------ #
    # zoom
    # ------------------------------------------------------------------ #
    def is_fit(self):
        return self.scale is None

    def zoom(self, mode):
        """"fit", "1:1", "2:1" or "guide"."""
        g = self.geometry_()
        if mode == "fit":
            self.scale, self.cx, self.cy = None, None, None
        elif mode in ("1:1", "2:1"):
            self.scale = 1.0 if mode == "1:1" else 2.0
            if g is not None and self.cx is None:
                self.cx, self.cy = g.full_w / 2.0, g.full_h / 2.0
        elif mode == "guide" and g is not None:
            cw, ch = self.device_size()
            gw, gh = self._guide_bounds_px(g)
            self.scale = min(cw / max(1.0, gw * 1.08), ch / max(1.0, gh * 1.08))
            self.cx = self.guide["cx"] * g.full_w
            self.cy = self.guide["cy"] * g.full_h
        self.view_changed.emit()

    def _guide_bounds_px(self, g):
        w = self.guide["w"] * g.full_w
        h = self.guide["h"] * g.full_h
        a = math.radians(self.guide["angle"])
        return (abs(w * math.cos(a)) + abs(h * math.sin(a)),
                abs(w * math.sin(a)) + abs(h * math.cos(a)))

    def _zoom_about(self, factor, pos):
        g = self.geometry_()
        if g is None:
            return
        d = self.dpr()
        px, py = pos.x() * d, pos.y() * d
        sx, sy = g.to_sensor(px, py)
        cur = g.scale
        from core.live_feed import fit_scale, MAX_SCALE
        cw, ch = self.device_size()
        fs = fit_scale(g.full_w, g.full_h, cw, ch)
        new = max(fs, min(MAX_SCALE, cur * factor))
        if new <= fs * 1.0001:
            self.scale, self.cx, self.cy = None, None, None
        else:
            self.scale = new
            # keep the sensor point under the pointer
            self.cx = sx + (cw / 2.0 - px) / new
            self.cy = sy + (ch / 2.0 - py) / new
        self.view_changed.emit()

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120.0
        if steps:
            self._zoom_about(ZOOM_STEP ** steps, e.position())
        e.accept()

    def mouseDoubleClickEvent(self, e):
        if self.mode:
            return
        g = self.geometry_()
        if g is None:
            return
        if self.is_fit():
            d = self.dpr()
            self._zoom_about(1.0 / max(1e-6, g.scale), e.position())
            if self.scale is None:        # the window is bigger than 1:1
                self.zoom("1:1")
        else:
            self.zoom("fit")

    # ------------------------------------------------------------------ #
    # mapping helpers (logical widget px <-> sensor)
    # ------------------------------------------------------------------ #
    def _to_logical(self, g, sx, sy):
        x, y = g.to_canvas(sx, sy)
        d = self.dpr()
        return x / d, y / d

    def _to_sensor(self, g, pos):
        d = self.dpr()
        return g.to_sensor(pos.x() * d, pos.y() * d)

    def _guide_frame(self, g):
        """(centre QPointF, width, height) of the guide in logical px."""
        cx, cy = self._to_logical(g, self.guide["cx"] * g.full_w, self.guide["cy"] * g.full_h)
        d = self.dpr()
        return (QPointF(cx, cy), self.guide["w"] * g.full_w * g.sx / d,
                self.guide["h"] * g.full_h * g.sy / d)

    def _local(self, g, pos):
        c, w, h = self._guide_frame(g)
        a = -math.radians(self.guide["angle"])
        dx, dy = pos.x() - c.x(), pos.y() - c.y()
        return (dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a), w, h)

    def _guide_hit(self, g, pos):
        lx, ly, w, h = self._local(g, pos)
        best = None
        for sxn in (-1, 1):
            for syn in (-1, 1):
                dist = math.hypot(lx - sxn * w / 2, ly - syn * h / 2)
                if dist <= HANDLE_PX:
                    return ("resize", (sxn, syn))
                outside = abs(lx) > w / 2 or abs(ly) > h / 2
                if outside and dist <= ROTATE_PX:
                    best = ("rotate", (sxn, syn))
        if best:
            return best
        if abs(lx) <= w / 2 and abs(ly) <= h / 2:
            return ("move", None)
        return None

    # ------------------------------------------------------------------ #
    # mouse
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, e):
        g = self.geometry_()
        if e.button() != Qt.LeftButton or g is None:
            return super().mousePressEvent(e)
        pos = e.position()
        if self.mode == "pick":
            sx, sy = self._to_sensor(g, pos)
            self.set_mode(None)
            if 0 <= sx < g.full_w and 0 <= sy < g.full_h:
                self.picked.emit(sx, sy)
            return
        if self.mode == "line":
            sx, _sy = self._to_sensor(g, pos)
            self.align_x = min(1.0, max(0.0, sx / g.full_w))
            self.set_mode(None)
            self.line_placed.emit(self.align_x)
            return
        if self.mode == "area":
            self._rubber = (pos, pos)
            return
        if self.guide_on:
            hit = self._guide_hit(g, pos)
            if hit:
                self._drag = (hit[0], hit[1], pos, dict(self.guide))
                return
        if not self.is_fit():
            self._drag = ("pan", None, pos, (self.cx, self.cy, g.scale))
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        self.pointer_moved.emit()
        g = self.geometry_()
        pos = e.position()
        if self._rubber is not None:
            self._rubber = (self._rubber[0], pos)
            self.update()
            return
        if self._drag is None or g is None:
            if g is not None and self.guide_on and not self.mode:
                hit = self._guide_hit(g, pos)
                shape = {"move": Qt.SizeAllCursor, "resize": Qt.SizeFDiagCursor,
                         "rotate": Qt.CrossCursor}.get(hit[0] if hit else None, Qt.ArrowCursor)
                self.setCursor(shape)
            return
        kind, info, start, before = self._drag
        d = self.dpr()
        if kind == "pan":
            cx, cy, s = before
            self.cx = cx - (pos.x() - start.x()) * d / s
            self.cy = cy - (pos.y() - start.y()) * d / s
            self.view_changed.emit()
            return
        gd = dict(before)
        fw, fh = g.full_w, g.full_h
        if kind == "move":
            dsx = (pos.x() - start.x()) * d / g.sx
            dsy = (pos.y() - start.y()) * d / g.sy
            gd["cx"] = before["cx"] + dsx / fw
            gd["cy"] = before["cy"] + dsy / fh
        elif kind == "rotate":
            c, _w, _h = self._guide_frame(g)
            a0 = math.atan2(start.y() - c.y(), start.x() - c.x())
            a1 = math.atan2(pos.y() - c.y(), pos.x() - c.x())
            gd["angle"] = before["angle"] + math.degrees(a1 - a0)
        elif kind == "resize":
            sxn, syn = info
            self.guide = before
            lx, ly, w, h = self._local(g, pos)
            # opposite corner fixed (local coords of the guide at press)
            ox, oy = -sxn * w / 2, -syn * h / 2
            nw, nh = max(4.0, abs(lx - ox)), max(4.0, abs(ly - oy))
            r = guide_ratio(before.get("format"))
            if r:
                # keep the format; long side along the guide's current long side
                landscape = before["w"] * fw >= before["h"] * fh
                want = r if landscape else 1.0 / r
                if nw / nh > want:
                    nw = nh * want
                else:
                    nh = nw / want
            ncx_l, ncy_l = ox + sxn * nw / 2, oy + syn * nh / 2
            a = math.radians(before["angle"])
            c, _w, _h = self._guide_frame(g)
            wx = c.x() + ncx_l * math.cos(a) - ncy_l * math.sin(a)
            wy = c.y() + ncx_l * math.sin(a) + ncy_l * math.cos(a)
            scx, scy = g.to_sensor(wx * d, wy * d)
            gd["cx"], gd["cy"] = scx / fw, scy / fh
            gd["w"] = nw * d / g.sx / fw
            gd["h"] = nh * d / g.sy / fh
        self.guide = clean_guide(gd)
        self.update()

    def mouseReleaseEvent(self, e):
        g = self.geometry_()
        if self._rubber is not None and g is not None:
            a, b = self._rubber
            self._rubber = None
            ax, ay = self._to_sensor(g, a)
            bx, by = self._to_sensor(g, b)
            x0, x1 = sorted((ax / g.full_w, bx / g.full_w))
            y0, y1 = sorted((ay / g.full_h, by / g.full_h))
            self.set_mode(None)
            if (x1 - x0) * g.full_w >= 8 and (y1 - y0) * g.full_h >= 8:
                self.sharp_area = (max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1))
                self.area_drawn.emit(self.sharp_area)
            return
        if self._drag is not None:
            kind = self._drag[0]
            self._drag = None
            self.setCursor(Qt.ArrowCursor)
            if kind in ("move", "resize", "rotate"):
                self.guide_changed.emit(dict(self.guide))

    # ------------------------------------------------------------------ #
    # painting
    # ------------------------------------------------------------------ #
    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.CANVAS))
        r = self.result
        if r is None:
            if self.message:
                p.setPen(QColor(theme.TEXT_MUTED))
                p.drawText(self.rect().adjusted(24, 24, -24, -24),
                           Qt.AlignCenter | Qt.TextWordWrap, self.message)
            p.end()
            return
        g = r.geometry
        d = self.dpr()
        target = QRectF(g.ox / d, g.oy / d, g.out_w / d, g.out_h / d)
        img = r.image
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)   # drawn 1:1 in device px
        p.drawImage(target, img)
        if r.clip is not None:
            p.drawImage(target, r.clip)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.save()
        p.setClipRect(target)
        if self.align_on:
            lx, _ = self._to_logical(g, self.align_x * g.full_w, 0)
            if target.left() <= lx <= target.right():
                p.setPen(QPen(QColor(0, 0, 0, 150), 3))
                p.drawLine(QPointF(lx, target.top()), QPointF(lx, target.bottom()))
                p.setPen(QPen(QColor(0, 200, 255, 220), 1.5))
                p.drawLine(QPointF(lx, target.top()), QPointF(lx, target.bottom()))
        if self.guide_on:
            self._paint_guide(p, g)
        if self.sharp_on and self.sharp_area:
            x0, y0, x1, y1 = self.sharp_area
            ax, ay = self._to_logical(g, x0 * g.full_w, y0 * g.full_h)
            bx, by = self._to_logical(g, x1 * g.full_w, y1 * g.full_h)
            p.setPen(QPen(QColor(255, 255, 255, 210), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(QPointF(ax, ay), QPointF(bx, by)))
        p.restore()
        if self._rubber is not None:
            a, b = self._rubber
            p.setPen(QPen(QColor(255, 255, 255), 1.5, Qt.DashLine))
            p.setBrush(QColor(255, 255, 255, 30))
            p.drawRect(QRectF(a, b).normalized())
        if self.sharp_on and r.sharp is not None:
            self._paint_readout(p, target, r.sharp)
        if self.mode:
            hint = {"pick": "Click the picture to sample it  ·  Esc cancels",
                    "area": "Drag over the detail to measure  ·  Esc cancels",
                    "line": "Click where the line goes  ·  Esc cancels"}[self.mode]
            self._paint_label(p, QPointF(target.left() + 12, target.top() + 12), hint)
        p.end()

    def _paint_guide(self, p, g):
        c, w, h = self._guide_frame(g)
        p.save()
        p.translate(c)
        p.rotate(self.guide["angle"])
        rect = QRectF(-w / 2, -h / 2, w, h)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 170), 3))
        p.drawRect(rect)
        p.setPen(QPen(QColor(255, 255, 255, 235), 1.5))
        p.drawRect(rect)
        p.setPen(QPen(QColor(255, 255, 255, 70), 1))
        for i in (1, 2):
            x = rect.left() + w * i / 3
            y = rect.top() + h * i / 3
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        p.setPen(QPen(QColor(30, 30, 30, 230), 1))
        p.setBrush(QColor(255, 255, 255, 235))
        for sx in (-1, 1):
            for sy in (-1, 1):
                p.drawEllipse(QPointF(sx * w / 2, sy * h / 2), 3.5, 3.5)
        p.restore()

    def _paint_label(self, p, at, text):
        f = QFont(self.font())
        f.setPixelSize(theme.FS_CAPTION)
        p.setFont(f)
        fm = p.fontMetrics()
        box = QRectF(at.x(), at.y(), fm.horizontalAdvance(text) + 16, fm.height() + 10)
        path = QPainterPath()
        path.addRoundedRect(box, theme.RADIUS_MD, theme.RADIUS_MD)
        p.fillPath(path, QColor(20, 20, 20, 210))
        p.setPen(QColor(theme.TEXT))
        p.drawText(box.adjusted(8, 5, -8, -5), Qt.AlignLeft | Qt.AlignVCenter, text)

    def _paint_readout(self, p, target, sharp):
        score, best = sharp
        frac = 0.0 if best <= 0 else max(0.0, min(1.0, score / best))
        f = QFont(self.font())
        f.setPixelSize(theme.FS_CAPTION)
        p.setFont(f)
        box = QRectF(target.left() + 12, target.bottom() - 56, 224, 44)
        path = QPainterPath()
        path.addRoundedRect(box, theme.RADIUS_MD, theme.RADIUS_MD)
        p.fillPath(path, QColor(20, 20, 20, 210))
        p.setPen(QColor(theme.TEXT))
        what = "area" if self.sharp_area else "view"
        p.drawText(box.adjusted(8, 5, -8, 0), Qt.AlignLeft | Qt.AlignTop,
                   f"Sharpness ({what})   {frac * 100:.0f} % of best")
        bar = QRectF(box.left() + 8, box.top() + 27, box.width() - 16, 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(70, 70, 70))
        p.drawRoundedRect(bar, 3, 3)
        p.setBrush(QColor(235, 235, 235))
        p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * frac, bar.height()), 3, 3)
