"""Contact sheet (spec/contact-sheet.md): the whole roll as ONE image, the way
a lab prints it. Every frame, uncropped and as scanned, in roll order on
black (rows of 6 for 35mm, 4 for medium format), numbered like edge print,
with each crop overlaid: the outside dimmed and a line round the crop,
coloured by where the crop came from.

`ContactSheet` keeps that image up to date: it compares a small signature per
frame on a timer and redraws only the frames that changed into the composite,
so it is always live and never written to disk unless you export it. Frames
are drawn from each image's existing uncropped 1080 px preview (the crop is
only applied on screen), so the sheet renders nothing of its own.

`ContactSheetView` shows it in place of the main image view, zoomable like a
frame; its thumbnail sits at the top of the thumbnail column.
"""

import copy
import math
import os
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, QSettings, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog,
                               QGraphicsPixmapItem, QGraphicsScene,
                               QGraphicsView, QHBoxLayout, QLabel, QMenu,
                               QPushButton, QVBoxLayout, QWidget)

from core import film_formats
from core.ccr_backend import ccr_backend
from ui import theme

# where a crop came from -> (line colour, caption)
SOURCES = {
    "confirmed": ((60, 207, 110), "confirmed"),
    "auto": ((240, 160, 32), "auto crop"),
    "hand": ((90, 169, 255), "drawn by hand"),
    "unsure": ((229, 72, 77), "not sure"),
    "none": ((154, 154, 154), "no crop"),
}
LEGEND = ("confirmed", "auto", "hand", "unsure")
CELL_W = 480               # a frame's cell width in the composite, px
CELL_W_LONG_ROLL = 360     # ...for rolls of more than 48 frames
GAP = 22
MARGIN = 30
LABEL_FRAC = 0.15          # edge print under each frame, x cell width: big enough
                           # to read with the whole sheet fitted on screen
FOOT_FRAC = 0.09           # the legend line, x cell width
NEG_LONG = 480             # long side of a background-decoded scan
DIM = (0, 0, 0, 125)
BG = (14, 14, 14)
EDGE_TEXT = (226, 196, 120)    # a warm edge-print tone for the frame numbers


def _same_rect(a, b, tol=1e-6):
    return (a is not None and b is not None
            and all(abs(float(x) - float(y)) < tol for x, y in zip(a, b)))


def crop_source(img):
    """(source, detail): "confirmed" (Done taught it), "auto" (auto crop's box,
    detail = its confidence), "hand", "unsure" (auto crop looked and left it,
    detail = why) or "none"."""
    rect = getattr(img, "crop_rect", None)
    last = getattr(img, "auto_crop_last", None)
    if rect is None:
        if last is not None and not last.usable:
            return "unsure", (last.reason or f"{last.confidence} confidence")
        return "none", ""
    if getattr(img, "auto_crop_example", None):
        return "confirmed", ""
    if _same_rect(rect, getattr(img, "auto_crop_rect", None)):
        return "auto", (getattr(last, "confidence", "") or "") if last is not None else ""
    return "hand", ""


def crop_polygon(rect, angle, w, h):
    """The crop box's corners in pixels of a w x h image: C + R(angle) q,
    exactly as ccr_processor.apply_crop_to_image samples it."""
    x1, y1, x2, y2 = rect
    cx, cy = (x1 + x2) / 2 * w, (y1 + y2) / 2 * h
    bw, bh = (x2 - x1) * w, (y2 - y1) * h
    t = math.radians(angle or 0.0)
    c, s = math.cos(t), math.sin(t)
    return [QPointF(cx + c * qx - s * qy, cy + s * qx + c * qy)
            for qx, qy in ((-bw / 2, -bh / 2), (bw / 2, -bh / 2),
                           (bw / 2, bh / 2), (-bw / 2, bh / 2))]


def _qimage_from_rgb8(arr):
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def _name(img):
    return (getattr(img, "display_name", None)
            or os.path.basename(getattr(img, "file_path", "") or "") or "?")


def _caption(source, detail):
    label = SOURCES[source][1]
    if source == "auto" and detail:
        return f"{label} ({detail})"
    if source == "unsure" and detail:
        return f"{label}: {detail}"
    return label


def draw_frame(p, target, source_img, crop_rect, crop_angle, source, overlay=True):
    """One frame, uncropped and as scanned, fitted and centred in `target`,
    with its crop overlay: the outside dimmed and the crop outlined in the
    source colour (a red dashed outline round a frame auto crop wasn't sure
    of). With `overlay` False, just the frame. Returns the frame's rect."""
    if isinstance(source_img, QPixmap):
        source_img = source_img.toImage()
    sw, sh = source_img.width(), source_img.height()
    scale = min(target.width() / sw, target.height() / sh)
    w, h = sw * scale, sh * scale
    x, y = target.x() + (target.width() - w) / 2, target.y() + (target.height() - h) / 2
    p.save()
    p.translate(x, y)
    p.drawImage(QRectF(0, 0, w, h), source_img)
    colour = QColor(*SOURCES.get(source, SOURCES["none"])[0])
    if overlay and crop_rect is not None:
        poly = QPolygonF(crop_polygon(crop_rect, crop_angle, w, h))
        outside = QPainterPath()
        outside.addRect(QRectF(0, 0, w, h))
        inside = QPainterPath()
        inside.addPolygon(poly)
        inside.closeSubpath()
        p.fillPath(outside.subtracted(inside), QColor(*DIM))
        p.setPen(QPen(colour, 2.5))
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(poly)
    elif overlay and source == "unsure":
        p.setPen(QPen(colour, 2.5, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(1.5, 1.5, w - 3, h - 3))
    p.restore()
    return QRectF(x, y, w, h)


def frame_format(img):
    """The frame's film format (a core.film_formats key) or None: the one
    picked for it in the Crop panel, else what auto crop found, else what
    its crop's proportions say."""
    fmt = film_formats.film_key(getattr(img, "film_format", None))
    if fmt:
        return fmt
    last = getattr(img, "auto_crop_last", None)
    fmt = film_formats.film_key(getattr(last, "fmt", "") if last is not None else "")
    if fmt:
        return fmt
    rect = getattr(img, "crop_rect", None)
    prev = getattr(img, "resized_preview", None)
    if rect is not None and prev is not None and not prev.isNull():
        return film_formats.from_ratio((rect[2] - rect[0]) * prev.width(),
                                       (rect[3] - rect[1]) * prev.height())
    return None


def film_columns(imgs):
    """Frames per row, as the film is cut into strips: 6 for 35mm, 4 for
    6x4.5, 3 for 6x6 / 6x7, 2 for 6x9 ... from the roll's most common format.
    With nothing to go on, 6 for a long roll and 4 for a short one."""
    fmts = [f for f in (frame_format(i) for i in imgs) if f]
    if fmts:
        return film_formats.strip_length(max(set(fmts), key=fmts.count))
    return 6 if len(imgs) > 12 else 4


def format_summary(imgs):
    """'35mm: 36x24 · 120: 6x4.5 ×3' for the footer (most common first)."""
    fmts = [f for f in (frame_format(i) for i in imgs) if f]
    if not fmts:
        return ""
    counts = sorted({f: fmts.count(f) for f in fmts}.items(), key=lambda kv: -kv[1])
    if len(counts) == 1:
        return film_formats.label(counts[0][0])
    return " · ".join(f"{film_formats.label(k)} ×{n}" for k, n in counts)


def decode_negative(img):
    """The frame's scan as an 8-bit negative (95th percentile at full scale,
    the look of the unconverted preview), read at NEG_LONG px on a shallow copy
    so the image's own decode state is never touched. Thread-safe."""
    proxy = copy.copy(img)
    raw = proxy.read_image(img.file_path, max_long_side=NEG_LONG)
    if raw is None:
        return None
    hi = float(np.percentile(raw[::4, ::4], 95.0)) or 1.0
    return np.clip(raw.astype(np.float32) * (255.0 / hi), 0, 255).astype(np.uint8)


class _NegativeLoader:
    """Background decodes of converted frames' scans, one at a time. Results
    are picked up by the sheet's next update (no Qt objects cross threads)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._queue = []
        self._done = {}
        self._busy = 0
        self._thread = None

    def want(self, img):
        with self._lock:
            if id(img) in self._done or any(q is img for q in self._queue):
                return
            self._queue.append(img)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

    def _run(self):
        while True:
            with self._lock:
                if not self._queue:
                    return
                img = self._queue.pop(0)
                self._busy += 1
            try:
                neg = decode_negative(img)
            except Exception as e:                       # never break the sheet
                print(f"Contact sheet: could not read {getattr(img, 'file_path', '?')}: {e}")
                neg = None
            with self._lock:
                self._done[id(img)] = (img, neg)
                self._busy -= 1

    def get(self, img):
        with self._lock:
            hit = self._done.get(id(img))
        return hit[1] if hit is not None and hit[0] is img else None

    def pending(self):
        """Decodes queued or running."""
        with self._lock:
            return len(self._queue) + self._busy


class SheetLayout:
    """Where each frame sits in the composite."""

    def __init__(self, n, cols, cell_w, aspect):
        self.n, self.cols, self.cell_w = n, cols, cell_w
        self.cell_h = cell_w / aspect
        self.label_h = LABEL_FRAC * cell_w
        self.foot_h = FOOT_FRAC * cell_w
        self.rows = max(1, math.ceil(n / cols))
        self.width = int(round(2 * MARGIN + cols * cell_w + (cols - 1) * GAP))
        self.height = int(round(2 * MARGIN + self.rows * (self.cell_h + self.label_h)
                                + (self.rows - 1) * GAP + self.foot_h))

    def cell(self, i):
        """The frame's cell: image area plus its edge-print label below."""
        r, c = divmod(i, self.cols)
        x = MARGIN + c * (self.cell_w + GAP)
        y = MARGIN + r * (self.cell_h + self.label_h + GAP)
        return QRectF(x, y, self.cell_w, self.cell_h + self.label_h)

    def image_rect(self, i):
        cell = self.cell(i)
        return QRectF(cell.x(), cell.y(), cell.width(), self.cell_h)

    def frame_at(self, x, y):
        for i in range(self.n):
            if self.cell(i).contains(QPointF(x, y)):
                return i
        return None

    def key(self):
        return (self.n, self.cols, self.cell_w, round(self.cell_h, 2))


class ContactSheet(QObject):
    """The composite and what keeps it current."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings("FreeCCR", "FreeCCR")
        self.negatives = self._settings.value("sheet/negatives", False, type=bool)
        # the crop overlay (dimmed outside + outline); on unless turned off
        self.overlay = self._settings.value("sheet/overlay", True, type=bool)
        self._image = None
        self._layout = None
        self._sigs = []
        self._footer = None
        self._neg_cache = {}          # id(img) -> (img, QImage, epoch): scans seen unconverted
        self._loader = _NegativeLoader()
        self.redrawn = []             # frames redrawn by the last update (tests, timing)

    # --- reading -------------------------------------------------------------
    def image(self):
        return self._image

    def layout(self):
        return self._layout

    def frame_at(self, x, y):
        return self._layout.frame_at(x, y) if self._layout is not None else None

    def thumbnail(self, w=156, h=117):
        if self._image is None:
            return QPixmap()
        return QPixmap.fromImage(self._image.scaled(w, h, Qt.KeepAspectRatio,
                                                    Qt.SmoothTransformation))

    def pending(self):
        return self._loader.pending()

    # --- updating ------------------------------------------------------------
    def set_negatives(self, on):
        self.negatives = bool(on)
        self._settings.setValue("sheet/negatives", self.negatives)
        self.update(force=True)

    def set_overlay(self, on):
        """Show or hide every frame's crop overlay, with the crop status in
        the captions and the colour legend (names and formats stay)."""
        self.overlay = bool(on)
        self._settings.setValue("sheet/overlay", self.overlay)
        self.update(force=True)

    def update(self, force=False):
        """Redraw the frames whose signature changed (all with `force`, or
        when the layout changes). Emits `changed` and returns True when the
        composite changed."""
        imgs = list(ccr_backend.images)
        self.redrawn = []
        if not imgs:
            if self._image is not None:
                self._image, self._layout, self._sigs = None, None, []
                self.changed.emit()
                return True
            return False
        for img in imgs:
            if not getattr(img, "converted", False):
                self._remember_negative(img)
        aspects = [p.width() / p.height() for p in
                   (getattr(i, "resized_preview", None) for i in imgs)
                   if p is not None and not p.isNull() and p.height()]
        aspect = float(np.median(aspects)) if aspects else 1.5
        cell_w = CELL_W_LONG_ROLL if len(imgs) > 48 else CELL_W
        layout = SheetLayout(len(imgs), film_columns(imgs), cell_w, aspect)
        if force or self._layout is None or layout.key() != self._layout.key():
            self._layout = layout
            self._image = QImage(layout.width, layout.height, QImage.Format_RGB888)
            self._image.fill(QColor(*BG))
            self._sigs = [None] * len(imgs)
            self._footer = None
        p = QPainter(self._image)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.TextAntialiasing)
        for i, img in enumerate(imgs):
            src, detail = crop_source(img)
            fmt = frame_format(img)
            sig = (id(img), getattr(img, "_render_epoch", 0),
                   tuple(img.crop_rect) if img.crop_rect else None,
                   round(float(getattr(img, "crop_angle", 0.0) or 0.0), 4),
                   src, detail, _name(img), fmt, self.negatives,
                   self._neg_state(img) if self.negatives else None, self.overlay)
            if self._sigs[i] == sig:
                continue
            self._sigs[i] = sig
            self._draw_cell(p, i, img, src, detail, fmt)
            self.redrawn.append(i)
        p.end()
        summary = format_summary(imgs)
        if summary != self._footer:
            self._draw_footer(summary)
            if not self.redrawn:
                self.changed.emit()
                return True
        if self.redrawn:
            self.changed.emit()
            return True
        return False

    def _draw_cell(self, p, i, img, src, detail, fmt=None):
        lay = self._layout
        cell = lay.cell(i)
        p.fillRect(cell.adjusted(-2, -2, 2, 2), QColor(*BG))
        source = self._source_image(img)
        area = lay.image_rect(i)
        if source is not None and not source.isNull():
            draw_frame(p, area, source, img.crop_rect,
                       getattr(img, "crop_angle", 0.0), src, self.overlay)
        else:
            p.setPen(QColor(110, 110, 110))
            p.drawText(area, Qt.AlignCenter, "reading scan…")
        u = lay.cell_w / 100.0                     # type scales with the cell
        f = QFont()
        f.setPixelSize(max(10, int(6.0 * u)))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(*EDGE_TEXT))
        num = f"{i + 1}"
        num_w = p.fontMetrics().horizontalAdvance(num) + 2.5 * u
        top = QRectF(cell.x(), area.bottom() + 1.0 * u, cell.width(), 7.0 * u)
        p.drawText(top, Qt.AlignLeft | Qt.AlignVCenter, num)
        f.setBold(False)
        f.setPixelSize(max(9, int(4.4 * u)))
        p.setFont(f)
        p.setPen(QColor(205, 205, 205))
        name_rect = QRectF(top.x() + num_w, top.y(), top.width() - num_w, top.height())
        p.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(_name(img), Qt.ElideMiddle, int(name_rect.width())))
        f.setPixelSize(max(9, int(3.9 * u)))
        p.setFont(f)
        cap = QRectF(cell.x(), top.bottom(), cell.width(), 6.0 * u)
        x = cap.x()
        if not self.overlay:
            # Overlay off: the frames as they are - no crop status (it
            # describes the overlay's outline); the film format stays.
            if fmt:
                p.setPen(QColor(205, 205, 205))
                p.drawText(cap, Qt.AlignLeft | Qt.AlignVCenter,
                           p.fontMetrics().elidedText(film_formats.label(fmt),
                                                      Qt.ElideRight, int(cap.width())))
            return
        if fmt:
            # the film format, then where the crop came from in its colour
            text = film_formats.label(fmt) + "  ·  "
            p.setPen(QColor(205, 205, 205))
            p.drawText(QRectF(x, cap.y(), cap.width(), cap.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, text)
            x += p.fontMetrics().horizontalAdvance(text)
        p.setPen(QColor(*SOURCES[src][0]))
        rest = QRectF(x, cap.y(), cap.right() - x, cap.height())
        p.drawText(rest, Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(_caption(src, detail), Qt.ElideRight,
                                              int(rest.width())))

    def _draw_footer(self, summary=""):
        lay = self._layout
        self._footer = summary
        p = QPainter(self._image)
        band = QRectF(0, lay.height - MARGIN - lay.foot_h, lay.width, lay.foot_h)
        p.fillRect(band, QColor(*BG))
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        u = lay.cell_w / 100.0
        f = QFont()
        f.setPixelSize(max(9, int(4.2 * u)))
        p.setFont(f)
        y = lay.height - MARGIN - lay.foot_h / 2
        sw = 3.0 * u                                # legend swatch
        n = lay.n
        th = 6.0 * u
        p.setPen(QColor(150, 150, 150))
        p.drawText(QRectF(MARGIN, y - th / 2, lay.width * 0.6, th), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{n} frame{'s' if n != 1 else ''} · "
                   + (summary + " · " if summary else "") + time.strftime("%d %b %Y"))
        if not self.overlay:
            p.end()                 # no crop colours shown, so no legend for them
            return
        x = lay.width - MARGIN
        for key in reversed(LEGEND):
            label = SOURCES[key][1]
            tw = p.fontMetrics().horizontalAdvance(label)
            x -= tw
            p.setPen(QColor(190, 190, 190))
            p.drawText(QRectF(x, y - th / 2, tw, th), Qt.AlignVCenter, label)
            x -= sw + 1.5 * u
            p.fillRect(QRectF(x, y - sw / 2, sw, sw), QColor(*SOURCES[key][0]))
            x -= 5.0 * u
        p.end()

    # --- negatives -------------------------------------------------------------
    def _remember_negative(self, img):
        """Keep a small copy of an unconverted frame's preview (its scan), so
        Negatives can show it after converting without decoding."""
        prev = getattr(img, "resized_preview", None)
        if prev is None or prev.isNull():
            return
        epoch = getattr(img, "_render_epoch", 0)
        hit = self._neg_cache.get(id(img))
        if hit is not None and hit[0] is img and hit[2] == epoch:
            return
        qi = prev.toImage()
        if qi.width() > NEG_LONG or qi.height() > NEG_LONG:
            qi = qi.scaled(NEG_LONG, NEG_LONG, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._neg_cache[id(img)] = (img, qi, epoch)

    def _neg_state(self, img):
        if not getattr(img, "converted", False):
            return "preview"
        hit = self._neg_cache.get(id(img))
        if hit is not None and hit[0] is img:
            return "cached"
        return "decoded" if self._loader.get(img) is not None else "waiting"

    def _source_image(self, img):
        if self.negatives and getattr(img, "converted", False):
            hit = self._neg_cache.get(id(img))
            if hit is not None and hit[0] is img:
                return hit[1]
            arr = self._loader.get(img)
            if arr is not None:
                return _qimage_from_rgb8(arr)
            self._loader.want(img)
            return None
        return getattr(img, "resized_preview", None)

    # --- export ----------------------------------------------------------------
    def save(self, path):
        """Write the sheet (JPEG quality 92, or TIFF). With Negatives on, any
        scan still being read is read now so the file is complete."""
        if self.negatives:
            for img in ccr_backend.images:
                if self._neg_state(img) == "waiting":
                    try:
                        arr = decode_negative(img)
                        if arr is not None:
                            self._loader._done[id(img)] = (img, arr)
                    except Exception:
                        pass
        self.update()
        if self._image is None:
            return False
        if path.lower().endswith((".tif", ".tiff")):
            return self._image.save(path, "TIFF")
        return self._image.save(path, "JPG", 92)


class _SheetGraphicsView(QGraphicsView):
    """Fit / zoom / pan, like a frame. Double-click opens a frame."""
    frameDoubleClicked = Signal(float, float)
    menuAt = Signal(float, float, object)

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.fit_mode = True
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor(theme.CANVAS))
        self.setFrameShape(QGraphicsView.NoFrame)

    def fit(self):
        self.fit_mode = True
        self.resetTransform()
        if not self.sceneRect().isEmpty():
            self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def actual_size(self):
        self.fit_mode = False
        self.resetTransform()

    def wheelEvent(self, event):
        f = 1.0015 ** event.angleDelta().y()
        cur = self.transform().m11()
        lo = min(1.0, self.viewport().width() / max(1.0, self.sceneRect().width())) * 0.9
        f = max(lo / cur, min(4.0 / cur, f))
        self.fit_mode = False
        self.scale(f, f)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_mode:
            self.fit()

    def mouseDoubleClickEvent(self, event):
        pt = self.mapToScene(event.position().toPoint())
        self.frameDoubleClicked.emit(pt.x(), pt.y())

    def contextMenuEvent(self, event):
        pt = self.mapToScene(event.pos())
        self.menuAt.emit(pt.x(), pt.y(), event.globalPos())


class ContactSheetView(QWidget):
    """The sheet in place of the main image view: a bar (title, Negatives,
    Overlay, Fit / 100%, Export…, Back to frame) over the zoomable image."""
    frameActivated = Signal(int, bool)     # index, open in the Crop panel
    autoCropRequested = Signal(int)
    closeRequested = Signal()

    def __init__(self, sheet, parent=None):
        super().__init__(parent)
        self.sheet = sheet
        self._build()
        sheet.changed.connect(self._reload)

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("sheetBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setStyleSheet(f"#sheetBar {{ background: {theme.PANEL}; }}")
        b = QHBoxLayout(bar)
        b.setContentsMargins(theme.GAP_PANEL, 4, theme.GAP_PANEL, 4)
        b.setSpacing(theme.GAP_BTN)
        self.title = QLabel("Contact sheet")
        self.title.setStyleSheet("font-weight: bold;")
        b.addWidget(self.title)
        hint = QLabel("Double-click a frame to open it")
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        b.addWidget(hint)
        b.addStretch(1)
        self.neg_check = QCheckBox("Negatives")
        self.neg_check.setToolTip(
            "Show every frame as its scan, also after converting. A converted "
            "frame's scan is read in the background the first time.")
        self.neg_check.setChecked(self.sheet.negatives)
        self.neg_check.toggled.connect(self.sheet.set_negatives)
        b.addWidget(self.neg_check)
        self.overlay_check = QCheckBox("Overlay")
        self.overlay_check.setToolTip(
            "Show each frame's crop: the area outside it dimmed and the crop "
            "outlined in the colour of where it came from, its status under "
            "the frame and the colour legend. Off shows the frames as they are, "
            "with just their names and formats. Also what Export writes.")
        self.overlay_check.setChecked(self.sheet.overlay)
        self.overlay_check.toggled.connect(self.sheet.set_overlay)
        b.addWidget(self.overlay_check)
        self.fit_btn = QPushButton("Fit")
        self.full_btn = QPushButton("100%")
        self.export_btn = QPushButton("Export…")
        self.back_btn = QPushButton("Back to frame")
        for w in (self.fit_btn, self.full_btn, self.export_btn, self.back_btn):
            theme.style_button(w, "secondary")
            b.addWidget(w)
        lay.addWidget(bar)

        self.scene = QGraphicsScene(self)
        self.item = QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.SmoothTransformation)
        self.scene.addItem(self.item)
        self.view = _SheetGraphicsView(self.scene)
        lay.addWidget(self.view, 1)

        self.fit_btn.clicked.connect(self.view.fit)
        self.full_btn.clicked.connect(self.view.actual_size)
        self.export_btn.clicked.connect(self._on_export)
        self.back_btn.clicked.connect(self.closeRequested.emit)
        self.view.frameDoubleClicked.connect(self._on_double_click)
        self.view.menuAt.connect(self._on_menu)

    # --- public ----------------------------------------------------------------
    def activate(self):
        self.sheet.update()
        self._reload()
        self.view.fit()
        self.setFocus()

    def _reload(self):
        img = self.sheet.image()
        n = len(ccr_backend.images)
        self.title.setText(f"Contact sheet · {n} frame{'s' if n != 1 else ''}")
        if img is None:
            self.item.setPixmap(QPixmap())
            return
        size_changed = (self.item.pixmap().isNull()
                        or self.item.pixmap().size() != img.size())
        self.item.setPixmap(QPixmap.fromImage(img))
        if size_changed:
            self.scene.setSceneRect(QRectF(0, 0, img.width(), img.height()))
            if self.view.fit_mode:
                self.view.fit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.closeRequested.emit()
            return
        super().keyPressEvent(event)

    # --- handlers --------------------------------------------------------------
    def _on_double_click(self, x, y):
        i = self.sheet.frame_at(x, y)
        if i is not None:
            self.frameActivated.emit(i, False)

    def _on_menu(self, x, y, global_pos):
        i = self.sheet.frame_at(x, y)
        m = QMenu(self)
        acts = {}
        if i is not None:
            acts[m.addAction(f"Open frame {i + 1}")] = lambda: self.frameActivated.emit(i, False)
            acts[m.addAction("Open in Crop panel")] = lambda: self.frameActivated.emit(i, True)
            acts[m.addAction("Auto Crop this frame")] = lambda: self.autoCropRequested.emit(i)
            m.addSeparator()
        acts[m.addAction("Export contact sheet…")] = self._on_export
        act = m.exec(global_pos)
        if act in acts:
            acts[act]()

    def _on_export(self):
        imgs = list(ccr_backend.images)
        if not imgs:
            return
        folder = os.path.dirname(getattr(imgs[0], "file_path", "") or "") or os.path.expanduser("~")
        path, chosen = QFileDialog.getSaveFileName(
            self, "Export contact sheet", os.path.join(folder, "contact_sheet.jpg"),
            "JPEG image (*.jpg *.jpeg);;TIFF image (*.tif *.tiff)")
        if not path:
            return
        if not path.lower().endswith((".jpg", ".jpeg", ".tif", ".tiff")):
            path += ".tif" if chosen.startswith("TIFF") else ".jpg"
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ok = self.sheet.save(path)
        finally:
            QApplication.restoreOverrideCursor()
        try:
            self.window().sliders_panel.set_temporary_hint(
                f"Contact sheet exported: {os.path.basename(path)}" if ok
                else "Could not export the contact sheet.", duration=5000)
        except AttributeError:
            pass
