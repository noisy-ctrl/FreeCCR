"""Contact sheet (spec/contact-sheet.md): every frame, uncropped, with its crop
drawn as a box coloured by where the crop came from. A live view that takes
the place of the main image view; nothing is written unless you ask for an
image of it.

Tiles are drawn from each frame's existing uncropped 1080 px preview (the crop
is only applied on screen), so the sheet renders nothing of its own. While it
is showing, it compares a small signature per frame every 0.4 s and redraws
only the tiles whose crop, orientation, render or source changed.
"""

import copy
import math
import os
import threading
import time

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF, QTransform)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog,
                               QHBoxLayout, QLabel, QMenu, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from core.ccr_backend import ccr_backend
from ui import theme

# where a crop came from -> (box colour, caption)
SOURCES = {
    "confirmed": ((60, 207, 110), "confirmed"),
    "auto": ((240, 160, 32), "auto crop"),
    "hand": ((90, 169, 255), "drawn by hand"),
    "unsure": ((229, 72, 77), "not sure"),
    "none": ((154, 154, 154), "no crop"),
}
SIZES = (("Small", "small", 200), ("Medium", "medium", 300), ("Large", "large", 440))
GAP = 14
CAPTION_H = 36
IMG_ASPECT = 0.72          # image area height / width in a cell
NEG_LONG = 480             # long side of a background-decoded scan
POLL_MS = 400
DIM = (0, 0, 0, 120)


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


def compose_tile(source_img, crop_rect, crop_angle, source, rotation, hflip, vflip,
                 max_w, max_h):
    """One tile: the uncropped frame scaled to fit max_w x max_h AS DISPLAYED
    (after its rotation / flips), the outside of the crop dimmed and the box
    outlined in the source colour. `source_img` is a QPixmap or QImage of the
    unrotated frame."""
    if isinstance(source_img, QPixmap):
        source_img = source_img.toImage()
    if source_img is None or source_img.isNull():
        return QPixmap()
    sw, sh = source_img.width(), source_img.height()
    quarter = (int(rotation) % 180) == 90
    ow, oh = (sh, sw) if quarter else (sw, sh)            # as displayed
    scale = min(max_w / ow, max_h / oh)
    w, h = max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))
    pm = QPixmap(w, h)
    pm.fill(QColor(0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawImage(QRectF(0, 0, w, h), source_img)
    colour = QColor(*SOURCES.get(source, SOURCES["none"])[0])
    if crop_rect is not None:
        poly = QPolygonF(crop_polygon(crop_rect, crop_angle, w, h))
        outside = QPainterPath()
        outside.addRect(QRectF(0, 0, w, h))
        inside = QPainterPath()
        inside.addPolygon(poly)
        inside.closeSubpath()
        p.fillPath(outside.subtracted(inside), QColor(*DIM))
        p.setPen(QPen(colour, 2))
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(poly)
    elif source == "unsure":
        pen = QPen(colour, 2, Qt.DashLine)
        p.setPen(pen)
        p.drawRect(QRectF(1, 1, w - 2, h - 2))
    p.end()
    # rotate first, then mirror: the canvas's (and the thumbnails') order
    angle = int(rotation) % 360
    if angle:
        pm = pm.transformed(QTransform().rotate(angle), Qt.SmoothTransformation)
    if hflip or vflip:
        pm = pm.transformed(QTransform().scale(-1 if hflip else 1, -1 if vflip else 1))
    return pm


def _orientation(idx):
    return (ccr_backend.get_image_rotation_by_index(idx),
            bool(ccr_backend.get_image_horizontal_flip_by_index(idx)),
            bool(ccr_backend.get_image_vertical_flip_by_index(idx)))


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
    are picked up by the sheet's poll (no Qt objects cross threads)."""

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


class SheetCanvas(QWidget):
    """The grid. Lays the frames out to the width, paints the visible tiles,
    maps clicks to frames."""
    activated = Signal(int, bool)          # frame index, open in the Crop panel
    menuRequested = Signal(int, object)    # frame index, global pos

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.tile_w = 300
        self.tiles = []          # per frame: dict(pixmap, name, caption, source)
        self.current = None
        self._hover = None
        self._cols = 1
        self._cell = (300, 216)
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._single_click)
        self._pending_click = None

    # --- layout --------------------------------------------------------------
    def relayout(self, width=None):
        width = max(1, width or self.width())
        cols = max(1, (width - GAP) // (self.tile_w + GAP))
        cw = (width - GAP * (cols + 1)) / cols
        self._cols, self._cell = cols, (cw, cw * IMG_ASPECT)
        rows = math.ceil(len(self.tiles) / cols) if self.tiles else 0
        self.setMinimumHeight(int(GAP + rows * (self._cell[1] + CAPTION_H + GAP)))
        return self._cell

    def cell_rect(self, i):
        r, c = divmod(i, self._cols)
        cw, ch = self._cell
        return QRectF(GAP + c * (cw + GAP), GAP + r * (ch + CAPTION_H + GAP), cw, ch + CAPTION_H)

    def tile_at(self, pos):
        for i in range(len(self.tiles)):
            if self.cell_rect(i).contains(QPointF(pos)):
                return i
        return None

    # --- painting ------------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.CANVAS))
        p.setRenderHint(QPainter.Antialiasing)
        small = QFont(self.font())
        small.setPointSizeF(max(7.5, small.pointSizeF() - 1))
        for i, t in enumerate(self.tiles):
            cell = self.cell_rect(i)
            if not cell.intersects(QRectF(event.rect())):
                continue
            img_area = QRectF(cell.x(), cell.y(), cell.width(), cell.height() - CAPTION_H)
            if i == self._hover or i == self.current:
                p.fillRect(cell.adjusted(-5, -5, 5, 5), QColor(theme.SURFACE_HOVER if i == self._hover
                                                              else theme.SURFACE))
            pm = t.get("pixmap")
            if pm is not None and not pm.isNull():
                x = img_area.x() + (img_area.width() - pm.width()) / 2
                y = img_area.y() + (img_area.height() - pm.height()) / 2
                p.drawPixmap(int(x), int(y), pm)
            else:
                p.setPen(QColor(theme.TEXT_MUTED))
                p.drawText(img_area, Qt.AlignCenter, "…")
            cap = QRectF(cell.x(), cell.bottom() - CAPTION_H + 3, cell.width(), CAPTION_H - 3)
            p.setFont(self.font())
            p.setPen(QColor(theme.TEXT))
            name = p.fontMetrics().elidedText(f"{i + 1}  {t['name']}", Qt.ElideMiddle, int(cap.width()))
            p.drawText(cap, Qt.AlignHCenter | Qt.AlignTop, name)
            p.setFont(small)
            p.setPen(QColor(*SOURCES[t["source"]][0]))
            line2 = p.fontMetrics().elidedText(t["caption"], Qt.ElideRight, int(cap.width()))
            p.drawText(cap, Qt.AlignHCenter | Qt.AlignBottom, line2)
        p.end()

    # --- mouse ---------------------------------------------------------------
    def mouseMoveEvent(self, event):
        i = self.tile_at(event.position().toPoint())
        if i != self._hover:
            self._hover = i
            self.setCursor(Qt.PointingHandCursor if i is not None else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, event):
        self._hover = None
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        i = self.tile_at(event.position().toPoint())
        if i is None:
            return
        # wait out a possible double-click: that opens the Crop panel instead
        self._pending_click = i
        self._click_timer.start(QApplication.doubleClickInterval())

    def mouseDoubleClickEvent(self, event):
        i = self.tile_at(event.position().toPoint())
        self._click_timer.stop()
        self._pending_click = None
        if i is not None:
            self.activated.emit(i, True)

    def _single_click(self):
        i, self._pending_click = self._pending_click, None
        if i is not None:
            self.activated.emit(i, False)

    def contextMenuEvent(self, event):
        i = self.tile_at(event.pos())
        if i is not None:
            self.menuRequested.emit(i, event.globalPos())


class ContactSheetView(QWidget):
    """Bar (title, legend, Negatives, Size, Save as image…, Back) over the
    scrollable grid. A sibling of ImagePreview, shown in its place."""
    frameActivated = Signal(int, bool)     # index, open in the Crop panel
    autoCropRequested = Signal(int)
    closeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings("FreeCCR", "FreeCCR")
        self._sigs = {}
        self._cache = {}          # id(img) -> (signature, pixmap)
        self._neg_cache = {}      # id(img) -> (img, QImage) of the unconverted scan
        self._loader = _NegativeLoader()
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.refresh)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(120)
        self._resize_timer.timeout.connect(lambda: self.refresh(force=True))
        self._build()

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
        b.addSpacing(theme.GAP_SECTION)
        for key in ("confirmed", "auto", "hand", "unsure"):
            chip = QLabel(f"<span style='color: rgb{SOURCES[key][0]};'>■</span> {SOURCES[key][1]}")
            chip.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
            b.addWidget(chip)
        b.addStretch(1)
        self.neg_check = QCheckBox("Negatives")
        self.neg_check.setToolTip(
            "Show every frame as its scan, also after converting. A converted "
            "frame's scan is read in the background the first time.")
        self.neg_check.setChecked(self._settings.value("sheet/negatives", False, type=bool))
        self.neg_check.toggled.connect(self._on_negatives)
        b.addWidget(self.neg_check)
        self.size_combo = QComboBox()
        for label, key, _w in SIZES:
            self.size_combo.addItem(label, key)
        idx = self.size_combo.findData(self._settings.value("sheet/size", "medium", type=str))
        self.size_combo.setCurrentIndex(idx if idx >= 0 else 1)
        self.size_combo.currentIndexChanged.connect(self._on_size)
        b.addWidget(self.size_combo)
        self.save_btn = QPushButton("Save as image…")
        theme.style_button(self.save_btn, "secondary")
        self.save_btn.clicked.connect(self._on_save)
        b.addWidget(self.save_btn)
        self.back_btn = QPushButton("Back to frame")
        theme.style_button(self.back_btn, "secondary")
        self.back_btn.clicked.connect(self.closeRequested.emit)
        b.addWidget(self.back_btn)
        lay.addWidget(bar)

        self.canvas = SheetCanvas()
        self.canvas.tile_w = self._tile_w()
        self.canvas.activated.connect(self.frameActivated.emit)
        self.canvas.menuRequested.connect(self._on_menu)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.canvas)
        self.scroll.setStyleSheet(f"QScrollArea {{ background: {theme.CANVAS}; border: none; }}")
        lay.addWidget(self.scroll, 1)

    # --- public --------------------------------------------------------------
    def activate(self, current=None):
        self.canvas.current = current
        self.refresh(force=True)
        self._timer.start()
        self.setFocus()

    def deactivate(self):
        self._timer.stop()

    def refresh(self, force=False):
        """Rebuild the tiles whose signature changed (all with `force`)."""
        imgs = list(ccr_backend.images)
        n = len(imgs)
        self.title.setText(f"Contact sheet · {n} frame{'s' if n != 1 else ''}")
        cw, ch = self.canvas.relayout(self.scroll.viewport().width())
        neg = self.neg_check.isChecked()
        tiles, changed = [], force or len(imgs) != len(self.canvas.tiles)
        live = set()
        for i, img in enumerate(imgs):
            live.add(id(img))
            src, detail = crop_source(img)
            if not getattr(img, "converted", False) and getattr(img, "resized_preview", None) is not None:
                self._remember_negative(img)
            sig = (id(img), int(cw), int(ch), neg, getattr(img, "_render_epoch", 0),
                   tuple(img.crop_rect) if img.crop_rect else None,
                   round(float(getattr(img, "crop_angle", 0.0) or 0.0), 4),
                   _orientation(i), src, detail, self._neg_state(img) if neg else None)
            hit = self._cache.get(id(img))
            if hit is None or hit[0] != sig or force:
                pm = self._compose(img, i, src, cw, ch, neg)
                self._cache[id(img)] = (sig, pm)
                changed = True
            else:
                pm = hit[1]
            tiles.append(dict(pixmap=pm, name=_name(img), source=src, caption=_caption(src, detail)))
        for k in [k for k in self._cache if k not in live]:
            del self._cache[k]
        self.canvas.tiles = tiles
        if changed:
            self.canvas.relayout(self.scroll.viewport().width())
            self.canvas.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.isVisible():
            self._resize_timer.start()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.closeRequested.emit()
            return
        super().keyPressEvent(event)

    # --- tiles ---------------------------------------------------------------
    def _remember_negative(self, img):
        """Keep a small copy of an unconverted frame's preview (its scan), so
        Negatives can show it after converting without decoding."""
        sig = getattr(img, "_render_epoch", 0)
        hit = self._neg_cache.get(id(img))
        if hit is not None and hit[0] is img and hit[2] == sig:
            return
        qi = img.resized_preview.toImage()
        if qi.width() > NEG_LONG or qi.height() > NEG_LONG:
            qi = qi.scaled(NEG_LONG, NEG_LONG, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._neg_cache[id(img)] = (img, qi, sig)

    def _neg_state(self, img):
        if not getattr(img, "converted", False):
            return "preview"
        hit = self._neg_cache.get(id(img))
        if hit is not None and hit[0] is img:
            return "cached"
        return "decoded" if self._loader.get(img) is not None else "waiting"

    def _source_image(self, img, neg):
        if neg and getattr(img, "converted", False):
            hit = self._neg_cache.get(id(img))
            if hit is not None and hit[0] is img:
                return hit[1]
            arr = self._loader.get(img)
            if arr is not None:
                return _qimage_from_rgb8(arr)
            self._loader.want(img)
            return None
        return getattr(img, "resized_preview", None)

    def _compose(self, img, idx, src, cw, ch, neg):
        source = self._source_image(img, neg)
        if source is None:
            return None
        rot, hf, vf = _orientation(idx)
        return compose_tile(source, img.crop_rect, getattr(img, "crop_angle", 0.0),
                            src, rot, hf, vf, cw, ch)

    def _tile_w(self):
        key = self.size_combo.currentData() if hasattr(self, "size_combo") else "medium"
        return next((w for _l, k, w in SIZES if k == key), 300)

    # --- bar handlers ----------------------------------------------------------
    def _on_negatives(self, on):
        self._settings.setValue("sheet/negatives", bool(on))
        self.refresh(force=True)

    def _on_size(self, _i):
        self._settings.setValue("sheet/size", self.size_combo.currentData())
        self.canvas.tile_w = self._tile_w()
        self.refresh(force=True)

    def _on_menu(self, idx, global_pos):
        m = QMenu(self)
        a_open = m.addAction("Open")
        a_crop = m.addAction("Open in Crop panel")
        m.addSeparator()
        a_auto = m.addAction("Auto Crop this frame")
        act = m.exec(global_pos)
        if act is a_open:
            self.frameActivated.emit(idx, False)
        elif act is a_crop:
            self.frameActivated.emit(idx, True)
        elif act is a_auto:
            self.autoCropRequested.emit(idx)

    def _on_save(self):
        imgs = list(ccr_backend.images)
        if not imgs:
            return
        folder = os.path.dirname(getattr(imgs[0], "file_path", "") or "") or os.path.expanduser("~")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save contact sheet", os.path.join(folder, "contact_sheet.jpg"),
            "JPEG image (*.jpg *.jpeg)")
        if not path:
            return
        if not path.lower().endswith((".jpg", ".jpeg")):
            path += ".jpg"
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ok = self.render_image().save(path, "JPG", 92)
        finally:
            QApplication.restoreOverrideCursor()
        try:
            hint = self.window().sliders_panel.set_temporary_hint
            hint(f"Contact sheet saved: {os.path.basename(path)}" if ok
                 else "Could not save the contact sheet.", duration=5000)
        except AttributeError:
            pass

    def render_image(self, tile=480, max_cols=5):
        """The sheet as one QImage: up to `max_cols` across, `tile` px cells,
        captions and a legend on a dark background."""
        imgs = list(ccr_backend.images)
        n = len(imgs)
        cols = max(1, min(max_cols, math.ceil(math.sqrt(n)) if n < max_cols ** 2 else max_cols))
        rows = math.ceil(n / cols) if n else 1
        cw, ch, cap, gap, head = tile, int(tile * IMG_ASPECT), 44, 20, 56
        W = gap + cols * (cw + gap)
        H = head + rows * (ch + cap + gap) + gap
        out = QImage(W, H, QImage.Format_RGB888)
        out.fill(QColor(24, 24, 24))
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        f = QFont(self.font())
        f.setPixelSize(16)
        p.setFont(f)
        p.setPen(QColor(230, 230, 230))
        p.drawText(QRectF(gap, 0, W / 2, head), Qt.AlignVCenter | Qt.AlignLeft,
                   f"Contact sheet · {n} frame{'s' if n != 1 else ''} · "
                   + time.strftime("%d %b %Y"))
        x = W - gap
        f.setPixelSize(13)
        p.setFont(f)
        for key in ("unsure", "hand", "auto", "confirmed"):
            label = SOURCES[key][1]
            tw = p.fontMetrics().horizontalAdvance(label)
            x -= tw
            p.setPen(QColor(200, 200, 200))
            p.drawText(QRectF(x, 0, tw, head), Qt.AlignVCenter, label)
            x -= 18
            p.fillRect(QRectF(x, head / 2 - 6, 12, 12), QColor(*SOURCES[key][0]))
            x -= 22
        neg = self.neg_check.isChecked()
        for i, img in enumerate(imgs):
            r, c = divmod(i, cols)
            x0, y0 = gap + c * (cw + gap), head + r * (ch + cap + gap)
            src, detail = crop_source(img)
            pm = self._compose(img, i, src, cw, ch, neg)
            if pm is None and neg:                       # scan not read yet: read it now
                try:
                    arr = decode_negative(img)
                    rot, hf, vf = _orientation(i)
                    pm = compose_tile(_qimage_from_rgb8(arr), img.crop_rect,
                                      getattr(img, "crop_angle", 0.0), src, rot, hf, vf, cw, ch)
                except Exception:
                    pm = None
            if pm is not None and not pm.isNull():
                p.drawPixmap(int(x0 + (cw - pm.width()) / 2), int(y0 + (ch - pm.height()) / 2), pm)
            f.setPixelSize(14)
            p.setFont(f)
            p.setPen(QColor(225, 225, 225))
            p.drawText(QRectF(x0, y0 + ch + 4, cw, 18), Qt.AlignHCenter,
                       p.fontMetrics().elidedText(f"{i + 1}  {_name(img)}", Qt.ElideMiddle, cw))
            f.setPixelSize(12)
            p.setFont(f)
            p.setPen(QColor(*SOURCES[src][0]))
            p.drawText(QRectF(x0, y0 + ch + 22, cw, 18), Qt.AlignHCenter,
                       p.fontMetrics().elidedText(_caption(src, detail), Qt.ElideRight, cw))
        p.end()
        return out
