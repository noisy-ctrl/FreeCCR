"""Contact sheet (spec/contact-sheet.md): the whole roll as ONE image, the way
a lab prints it. Every frame, turned and mirrored as the main window shows it
(revision 2026-11-19; whole, or as its crop keeps it with Respect crop), in
roll order on black (rows of 6 for 35mm, 4 for medium format), numbered like
edge print, with each crop overlaid: the outside dimmed and a line round the
crop, coloured by where the crop came from.

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
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QRadioButton,
                               QGraphicsPixmapItem, QGraphicsScene,
                               QGraphicsView, QHBoxLayout, QLabel, QMenu,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

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
NAME_TEXT = (205, 205, 205)
FOOT_TEXT = (150, 150, 150)
# Text… → Colour (revision 2026-11-19): "edge" is the old look (warm numbers,
# light grey names); the others colour every caption and the footer's words
TEXT_COLOURS = (("edge", "Edge print", None), ("white", "White", (240, 240, 240)),
                ("light", "Light grey", (200, 200, 200)), ("mid", "Mid grey", (140, 140, 140)),
                ("warm", "Warm", EDGE_TEXT))
# Text… → Show: what the captions carry
TEXT_INFO = (("number", "Frame number"), ("name", "File name"), ("film", "Film"),
             ("format", "Film format"), ("status", "Crop status"))
DEFAULT_INFO = ("number", "name", "format", "status")


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


def orientation_of(img):
    """(quarter turns in degrees, fine rotation in degrees, mirror left-right,
    mirror top-bottom): the frame as the main window shows it."""
    return (int(getattr(img, "rotation_angle", 0) or 0) % 360,
            float(getattr(img, "fine_rotation_angle", 0) or 0) / 100.0,
            bool(getattr(img, "horizontal_mirrored", False)),
            bool(getattr(img, "vertical_mirrored", False)))


def _turned_size(w, h, degrees):
    """The bounding box of a w x h rectangle turned by `degrees`."""
    t = math.radians(degrees)
    c, s = abs(math.cos(t)), abs(math.sin(t))
    return w * c + h * s, w * s + h * c


def shown_size(sw, sh, crop_rect=None, orient=None, respect_crop=False):
    """The frame's size as the sheet shows it, in source pixels: the crop's
    box with Respect crop, else the whole scan, turned."""
    w, h = float(sw), float(sh)
    if respect_crop and crop_rect is not None:
        x1, y1, x2, y2 = crop_rect
        w, h = abs(x2 - x1) * sw, abs(y2 - y1) * sh
    rot, fine = (orient[0], orient[1]) if orient else (0, 0.0)
    return _turned_size(w, h, rot + fine)


def draw_frame(p, target, source_img, crop_rect, crop_angle, source, overlay=True,
               orient=None, respect_crop=False):
    """One frame fitted and centred in `target`, turned and mirrored as the
    main window shows it (`orient`, see orientation_of; None = as scanned),
    with its crop overlay: the outside dimmed and the crop outlined in the
    source colour (a red dashed outline round a frame auto crop wasn't sure
    of). With `respect_crop` only what the crop keeps is shown (the crop box
    and its angle, then the turns: what an export gives) and the overlay is a
    line round the frame. With `overlay` False, just the frame. Returns the
    frame's bounding rect in `target`'s coordinates."""
    if isinstance(source_img, QPixmap):
        source_img = source_img.toImage()
    sw, sh = source_img.width(), source_img.height()
    rot, fine, hflip, vflip = orient or (0, 0.0, False, False)
    cropped = bool(respect_crop and crop_rect is not None)
    W, H = shown_size(sw, sh, crop_rect, (rot, fine), respect_crop)
    scale = min(target.width() / W, target.height() / H)
    colour = QColor(*SOURCES.get(source, SOURCES["none"])[0])
    p.save()
    # the canvas's order (image_preview.apply_transformations /
    # ccr_processor.apply_orientation): turns about the centre, mirrors last
    p.translate(target.center())
    p.scale(scale, scale)
    if vflip:
        p.scale(1, -1)
    if hflip:
        p.scale(-1, 1)
    p.rotate(rot + fine)
    if cropped:
        # crop-box coordinates, centred: the source point C + R(angle) q is
        # shown at q (apply_crop_to_image)
        x1, y1, x2, y2 = crop_rect
        bw, bh = abs(x2 - x1) * sw, abs(y2 - y1) * sh
        box = QRectF(-bw / 2, -bh / 2, bw, bh)
        p.setClipRect(box)
        p.save()
        p.rotate(-(crop_angle or 0.0))
        p.translate(-(x1 + x2) / 2 * sw, -(y1 + y2) / 2 * sh)
        p.drawImage(QRectF(0, 0, sw, sh), source_img)
        p.restore()
        if overlay:
            pen = QPen(colour, 2.5)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            inset = 1.25 / scale
            p.drawRect(box.adjusted(inset, inset, -inset, -inset))
    else:
        p.translate(-sw / 2, -sh / 2)
        p.drawImage(QRectF(0, 0, sw, sh), source_img)
        if overlay and crop_rect is not None:
            poly = QPolygonF(crop_polygon(crop_rect, crop_angle, sw, sh))
            outside = QPainterPath()
            outside.addRect(QRectF(0, 0, sw, sh))
            inside = QPainterPath()
            inside.addPolygon(poly)
            inside.closeSubpath()
            p.fillPath(outside.subtracted(inside), QColor(*DIM))
            pen = QPen(colour, 2.5)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPolygon(poly)
    if overlay and source == "unsure" and not (cropped or crop_rect is not None):
        pen = QPen(colour, 2.5, Qt.DashLine)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        inset = 1.5 / scale
        p.drawRect(QRectF(inset, inset, sw - 2 * inset, sh - 2 * inset))
    p.restore()
    w, h = W * scale, H * scale
    return QRectF(target.center().x() - w / 2, target.center().y() - h / 2, w, h)


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
        # revision 2026-11-19: crops applied, and the captions' colour and info
        self.respect_crop = self._settings.value("sheet/respect_crop", False, type=bool)
        self.text_colour = self._settings.value("sheet/text_colour", "edge", type=str) or "edge"
        info = self._settings.value("sheet/text_info", None)
        if isinstance(info, str):
            info = [k for k in info.split(",") if k]
        keys = {k for k, _l in TEXT_INFO}
        self.text_info = (tuple(k for k in info if k in keys) if info is not None
                          else DEFAULT_INFO)
        self.roll_film = ""           # the roll's film (MainWindow sets it)
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

    def set_respect_crop(self, on):
        """Show each cropped frame as its crop keeps it."""
        self.respect_crop = bool(on)
        self._settings.setValue("sheet/respect_crop", self.respect_crop)
        self.update(force=True)

    def set_text_colour(self, key):
        """A TEXT_COLOURS key or "#rrggbb"."""
        key = str(key or "edge")
        if not (key in {k for k, _l, _c in TEXT_COLOURS}
                or QColor(key).isValid() and key.startswith("#")):
            key = "edge"
        self.text_colour = key
        self._settings.setValue("sheet/text_colour", key)
        self.update(force=True)

    def set_text_info(self, keys):
        keys = set(keys or ())
        self.text_info = tuple(k for k, _l in TEXT_INFO if k in keys)
        self._settings.setValue("sheet/text_info", ",".join(self.text_info))
        self.update(force=True)

    def set_roll_film(self, name):
        name = str(name or "")
        if name != self.roll_film:
            self.roll_film = name
            if "film" in self.text_info:
                self.update(force=True)

    def _colours(self):
        """(number, words, footer, legend words) as QColors."""
        named = {k: c for k, _l, c in TEXT_COLOURS}
        if self.text_colour == "edge" or (self.text_colour not in named
                                          and not QColor(self.text_colour).isValid()):
            return (QColor(*EDGE_TEXT), QColor(*NAME_TEXT), QColor(*FOOT_TEXT),
                    QColor(190, 190, 190))
        c = (QColor(*named[self.text_colour]) if self.text_colour in named
             else QColor(self.text_colour))
        return c, c, c, c

    def _film_of(self, img, presets):
        from core import film_presets as fp
        p = fp.find(presets, getattr(img, "film_preset", None))
        return fp.label(p) if p is not None else self.roll_film

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
        aspects = []
        for i in imgs:
            pv = getattr(i, "resized_preview", None)
            if pv is None or pv.isNull() or not pv.height():
                continue
            w, h = shown_size(pv.width(), pv.height(), getattr(i, "crop_rect", None),
                              orientation_of(i), self.respect_crop)
            if h > 0:
                aspects.append(w / h)       # the frame as shown: turned, cropped
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
        presets = None
        if "film" in self.text_info:
            from core import film_presets as fp
            presets = fp.load(self._settings)
        look = (self.respect_crop, self.text_colour, self.text_info, self.overlay)
        for i, img in enumerate(imgs):
            src, detail = crop_source(img)
            fmt = frame_format(img)
            film = self._film_of(img, presets) if presets is not None else ""
            sig = (id(img), getattr(img, "_render_epoch", 0),
                   tuple(img.crop_rect) if img.crop_rect else None,
                   round(float(getattr(img, "crop_angle", 0.0) or 0.0), 4),
                   src, detail, _name(img), fmt, self.negatives,
                   self._neg_state(img) if self.negatives else None,
                   orientation_of(img), film, look)
            if self._sigs[i] == sig:
                continue
            self._sigs[i] = sig
            self._draw_cell(p, i, img, src, detail, fmt, film)
            self.redrawn.append(i)
        p.end()
        summary = (format_summary(imgs), self.text_colour, self.overlay)
        if summary != self._footer:
            self._draw_footer(summary)
            if not self.redrawn:
                self.changed.emit()
                return True
        if self.redrawn:
            self.changed.emit()
            return True
        return False

    def _draw_cell(self, p, i, img, src, detail, fmt=None, film=""):
        lay = self._layout
        cell = lay.cell(i)
        p.fillRect(cell.adjusted(-2, -2, 2, 2), QColor(*BG))
        source = self._source_image(img)
        area = lay.image_rect(i)
        if source is not None and not source.isNull():
            draw_frame(p, area, source, img.crop_rect,
                       getattr(img, "crop_angle", 0.0), src, self.overlay,
                       orient=orientation_of(img), respect_crop=self.respect_crop)
        else:
            p.setPen(QColor(110, 110, 110))
            p.drawText(area, Qt.AlignCenter, "reading scan…")
        num_c, word_c, _foot, _leg = self._colours()
        info = self.text_info
        u = lay.cell_w / 100.0                     # type scales with the cell
        f = QFont()
        f.setPixelSize(max(10, int(6.0 * u)))
        f.setBold(True)
        p.setFont(f)
        top = QRectF(cell.x(), area.bottom() + 1.0 * u, cell.width(), 7.0 * u)
        num_w = 0.0
        if "number" in info:
            p.setPen(num_c)
            num = f"{i + 1}"
            num_w = p.fontMetrics().horizontalAdvance(num) + 2.5 * u
            p.drawText(top, Qt.AlignLeft | Qt.AlignVCenter, num)
        f.setBold(False)
        f.setPixelSize(max(9, int(4.4 * u)))
        p.setFont(f)
        if "name" in info:
            p.setPen(word_c)
            name_rect = QRectF(top.x() + num_w, top.y(), top.width() - num_w, top.height())
            p.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(_name(img), Qt.ElideMiddle,
                                                  int(name_rect.width())))
        f.setPixelSize(max(9, int(3.9 * u)))
        p.setFont(f)
        cap = QRectF(cell.x(), top.bottom(), cell.width(), 6.0 * u)
        # line 2: film · format · status, each only when shown; the status
        # (in its source colour) only with the overlay, whose colours it names
        words = []
        if "film" in info and film:
            words.append(film)
        if "format" in info and fmt:
            words.append(film_formats.label(fmt))
        status = "status" in info and self.overlay
        text = "  ·  ".join(words)
        x = cap.x()
        if text:
            if status:
                text += "  ·  "
            p.setPen(word_c)
            p.drawText(QRectF(x, cap.y(), cap.width(), cap.height()),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(text, Qt.ElideRight, int(cap.width())))
            x += min(cap.width(), p.fontMetrics().horizontalAdvance(text))
        if status and x < cap.right():
            p.setPen(QColor(*SOURCES[src][0]))
            rest = QRectF(x, cap.y(), cap.right() - x, cap.height())
            p.drawText(rest, Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(_caption(src, detail), Qt.ElideRight,
                                                  int(rest.width())))

    def _draw_footer(self, summary=("", None, None)):
        lay = self._layout
        self._footer = summary
        summary = summary[0] if isinstance(summary, tuple) else summary
        _num, _word, foot_c, legend_c = self._colours()
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
        p.setPen(foot_c)
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
            p.setPen(legend_c)
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


class _RowCheck(QCheckBox):
    """A check box that takes a click anywhere on itself (in a menu row a
    click it ignored would reach the menu, which closes)."""

    def hitButton(self, pos):
        return self.rect().contains(pos)


class _RowRadio(QRadioButton):
    def hitButton(self, pos):
        return self.rect().contains(pos)


class _MenuRow(QWidget):
    """A menu row around a button: a click on its margins clicks the button
    and never reaches the menu (which would close)."""

    def __init__(self, button, parent=None):
        super().__init__(parent)
        self._button = button

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self._button.click()
        e.accept()


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
        # it never widens the bar: hidden when there is no room (_fit_bar)
        hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        b.addWidget(hint)
        self.hint, self.bar = hint, bar
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
        self.crop_check = QCheckBox("Respect crop")
        self.crop_check.setToolTip(
            "Show each cropped frame as its crop keeps it (turned and mirrored "
            "as in the main window, as an export gives it). Frames with no crop "
            "show whole. Also what Export writes.")
        self.crop_check.setChecked(self.sheet.respect_crop)
        self.crop_check.toggled.connect(self.sheet.set_respect_crop)
        b.addWidget(self.crop_check)
        self.text_btn = QPushButton("Text…")
        self.text_btn.setToolTip("The captions' colour, and what they show.")
        self.text_btn.clicked.connect(self._show_text_menu)
        self.fit_btn = QPushButton("Fit")
        self.full_btn = QPushButton("100%")
        self.export_btn = QPushButton("Export…")
        for w in (self.text_btn, self.fit_btn, self.full_btn, self.export_btn):
            theme.style_button(w, "secondary")
            b.addWidget(w)
        # the way out stands out: the bar's one light button (revision 2026-11-19)
        self.back_btn = QPushButton("Back to Frame")
        self.back_btn.setToolTip("Back to the frame you were on (Esc).")
        theme.style_button(self.back_btn, "inverse")
        b.addWidget(self.back_btn)
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
        self._fit_bar()
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_bar()

    def _fit_bar(self):
        """The hint gives way when the bar is too narrow for everything."""
        lay = self.bar.layout()
        lay.invalidate()
        rest = lay.sizeHint().width() - (self.hint.sizeHint().width() + lay.spacing()
                                         if self.hint.isVisible() else 0)
        self.hint.setVisible(rest + self.hint.sizeHint().width() + lay.spacing()
                             <= max(self.width(), 1))

    # --- Text… ---------------------------------------------------------------------
    def text_menu(self):
        """Text…'s menu (the test seam): Colour (the choices, Custom…), then
        Show (what the captions carry). The choices are real radio buttons
        and check boxes in the menu, so it stays open while you tick (the
        user, 2026-11-20: it closed after every click); click away when done.
        Each row's action carries its label as text and its button as
        `action.box`."""
        from PySide6.QtWidgets import QButtonGroup
        sheet = self.sheet
        m = QMenu(self)
        head = m.addAction("Colour")
        head.setEnabled(False)
        named = {k for k, _l, _c in TEXT_COLOURS}
        group = QButtonGroup(m)
        group.setExclusive(True)
        for key, label, _c in TEXT_COLOURS:
            rb = _RowRadio(label)
            rb.setChecked(sheet.text_colour == key)
            group.addButton(rb)
            rb.toggled.connect(lambda on, k=key: on and sheet.set_text_colour(k))
            self._menu_row(m, label, rb)
        custom = m.addAction("Custom…")
        custom.setCheckable(True)
        custom.setChecked(sheet.text_colour not in named)
        custom.triggered.connect(self._pick_text_colour)
        for b in group.buttons():
            b.toggled.connect(lambda on: on and custom.setChecked(False))
        m.addSeparator()
        head = m.addAction("Show")
        head.setEnabled(False)
        for key, label in TEXT_INFO:
            cb = _RowCheck(label)
            cb.setChecked(key in sheet.text_info)

            def flip(on, k=key):
                keys = set(sheet.text_info)
                (keys.add if on else keys.discard)(k)
                sheet.set_text_info(keys)
            cb.toggled.connect(flip)
            self._menu_row(m, label, cb)
        m._group = group
        return m

    @staticmethod
    def _menu_row(menu, label, button):
        """A row holding a check box or radio button: clicking it doesn't
        close the menu. The whole row is the button's (it fills the width)."""
        from PySide6.QtWidgets import QWidgetAction
        row = _MenuRow(button, menu)
        row.setObjectName("sheetMenuRow")
        row.setAttribute(Qt.WA_StyledBackground, True)
        row.setAttribute(Qt.WA_Hover, True)
        row.setStyleSheet(f"#sheetMenuRow:hover {{ background-color: {theme.SURFACE_HOVER}; }}")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(12, 3, 16, 3)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay.addWidget(button)
        act = QWidgetAction(menu)
        act.setText(label)
        act.setDefaultWidget(row)
        act.box = button
        menu.addAction(act)
        return act

    def _choose_colour(self, current):
        """The colour picker's answer, a QColor (invalid on Cancel). The seam."""
        from PySide6.QtWidgets import QColorDialog
        return QColorDialog.getColor(current, self, "Caption colour")

    def _pick_text_colour(self):
        cur = self.sheet._colours()[1]
        c = self._choose_colour(cur)
        if c is not None and c.isValid():
            self.sheet.set_text_colour(c.name())

    def _show_text_menu(self):
        m = self.text_menu()
        m.exec(self.text_btn.mapToGlobal(self.text_btn.rect().bottomLeft()))

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
