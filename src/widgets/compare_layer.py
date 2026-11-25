"""The Reference compare layer (spec/reference-compare.md): the frame on
screen and a reference picture of it (a file, or another open frame) either
side of a line in the view, the reference lined up with the frame.

Display only: the reference is a child of the picture item, clipped to its
side of the line, above every display aid; the line, knob and tags are drawn
in the view's foreground. Nothing here is in the picture's pixels.
"""
from __future__ import annotations

import os
import threading
from collections import OrderedDict

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QImage, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygonF, QTransform)
from PySide6.QtWidgets import (QFileDialog, QGraphicsItem, QGraphicsPathItem,
                               QGraphicsPixmapItem, QGraphicsRectItem, QMenu, QMessageBox)

from core import reference_compare as rc
from ui import theme

GRAB_PX = 8              # the line takes the mouse this close (viewport px)
KNOB_R = 7.0
NO_PICTURE = "#3a3a3a"   # the reference's side where it has no picture
Z = 3.0                  # above False colour 1, flat map 1.4, masks 1.5, HDR 2, clip 2.5
FRAME_REF_SIDE = 2000    # a frame reference is drawn at most this big
FILE_FILTER = "Pictures (*.jpg *.jpeg *.tif *.tiff *.png);;All files (*)"

HINT_NONE = "No reference for this frame: right-click Reference to choose one."
HINT_LINING = "Lining up…"
HINT_FAILED = "Couldn't line it up: shown whole (right-click → Line Up Again)"


class _Signals(QObject):
    done = Signal(object)


class _Store:
    """QSettings, opened when first needed (tests put a dict-backed stand-in
    in ``CompareLayer.store``)."""
    def __init__(self):
        self._s = None

    def _q(self):
        if self._s is None:
            from PySide6.QtCore import QSettings
            self._s = QSettings("FreeCCR", "FreeCCR")
        return self._s

    def value(self, key, default=None):
        return self._q().value(key, default)

    def setValue(self, key, value):
        self._q().setValue(key, value)


def _bool(v, default):
    if v is None:
        return default
    if isinstance(v, str):
        return v.lower() in ("1", "true", "yes")
    return bool(v)


def frame_identity(img):
    """[catalogue key, display name] of a frame: how a frame reference is
    stored (survives a restart)."""
    from core import catalog
    return [catalog._catalog_key_for_image(img), img.display_name]


def find_frame(images, ident):
    """The open image with this identity, or None."""
    if not ident or len(ident) != 2:
        return None
    from core import catalog
    key, name = ident
    for im in images:
        try:
            if im.display_name == name and catalog._catalog_key_for_image(im) == key:
                return im
        except Exception:
            continue
    return None


def _rgb_of(qimg: QImage) -> np.ndarray:
    q = qimg.convertToFormat(QImage.Format_RGB888)
    w, h = q.width(), q.height()
    buf = np.frombuffer(q.constBits(), np.uint8).reshape(h, q.bytesPerLine())
    # a COPY: with no row padding the slice is already contiguous, and a view
    # would point into q's memory, freed when q goes
    return buf[:, :w * 3].reshape(h, w, 3).copy()


def _qimage_of(rgb: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def render_frame(img) -> QImage | None:
    """Another frame as the main window shows it (its crop, turn, mirror and
    grade), at most FRAME_REF_SIDE on its long side."""
    from widgets import contact_sheet as cs
    src = getattr(img, "resized_preview", None)
    if src is None or src.isNull():
        return None
    orient = cs.orientation_of(img)
    crop = getattr(img, "crop_rect", None)
    W, H = cs.shown_size(src.width(), src.height(), crop, orient, respect_crop=True)
    s = min(1.0, FRAME_REF_SIDE / max(W, H))
    w, h = max(2, int(round(W * s))), max(2, int(round(H * s)))
    out = QImage(w, h, QImage.Format_RGB888)
    out.fill(QColor(0, 0, 0))
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setRenderHint(QPainter.Antialiasing)
    cs.draw_frame(p, QRectF(0, 0, w, h), src, crop, getattr(img, "crop_angle", 0.0) or 0.0,
                  "none", overlay=False, orient=orient, respect_crop=True)
    p.end()
    return out


class CompareLayer:
    """Owned by ImagePreview (``preview.compare``)."""

    def __init__(self, preview):
        self.p = preview
        self.on = False
        self.frac = 0.5
        self.upright = True
        self.swap = False
        self.locked = False
        self.lineup = True
        self.store = _Store()
        self.threaded = True
        self._opts_read = False
        self.clip = self.bg = self.item = None
        self.dragging = False
        self.status = ""
        self._job = 0
        self._asked = None             # the key a job is running for
        self._shown = None             # dict: key, lu, tried, pixmap, size
        self._refs = OrderedDict()     # file signature -> rc.Reference
        self._lineups = OrderedDict()  # (ref geometry, frame geometry) -> LineUp | None
        self._lock = threading.Lock()
        self._signals = _Signals()
        self._signals.done.connect(self._on_done)

    # --- options -----------------------------------------------------------
    def _read_options(self):
        if self._opts_read:
            return
        self._opts_read = True
        self.upright = str(self.store.value("view/compare_line", "upright")) != "level"
        self.swap = _bool(self.store.value("view/compare_swap", False), False)
        self.lineup = _bool(self.store.value("view/compare_lineup", True), True)

    def _save(self, key, value):
        try:
            self.store.setValue(key, value)
        except Exception:
            pass

    # --- what is on screen -------------------------------------------------
    def _image(self):
        from core.ccr_backend import ccr_backend
        return ccr_backend.get_image_by_index(getattr(self.p, "current_idx", -1))

    def reference_of(self, img):
        ref = getattr(img, "compare_ref", None) if img is not None else None
        return ref if isinstance(ref, dict) and (ref.get("file") or ref.get("frame")) else None

    def _frame_geometry(self, img):
        pm = self.p.current_pixmap
        crop = getattr(img, "crop_rect", None)
        return (id(img), tuple(crop) if crop is not None else None,
                float(getattr(img, "crop_angle", 0.0) or 0.0),
                pm.width() if pm is not None else 0, pm.height() if pm is not None else 0,
                repr(getattr(img, "source_ops", None)))

    def _source(self, img, ref):
        """(pixel signature, geometry signature, how to get it) or (None, hint)."""
        from core.ccr_backend import ccr_backend
        if ref.get("file"):
            path = ref["file"]
            try:
                st = os.stat(path)
            except OSError:
                return None, f"The reference {os.path.basename(path)} isn't there any more."
            sig = ("file", path, st.st_mtime, st.st_size)
            return (sig, sig, ("file", path)), None
        other = find_frame(ccr_backend.images, ref.get("frame"))
        if other is None:
            name = (ref.get("frame") or ["", "?"])[1]
            return None, f"The reference frame {name} isn't open."
        from widgets import contact_sheet as cs
        prev = getattr(other, "resized_preview", None)
        size = (prev.width(), prev.height()) if prev is not None else (0, 0)
        crop = getattr(other, "crop_rect", None)
        geo = ("frame", id(other), tuple(crop) if crop is not None else None,
               float(getattr(other, "crop_angle", 0.0) or 0.0), cs.orientation_of(other), size)
        pix = geo + (getattr(other, "_render_epoch", 0), id(prev))
        return (pix, geo, ("frame", other)), None

    # --- turning on and off ------------------------------------------------
    def set_on(self, on: bool) -> bool:
        """Turn the layer on or off; on for a frame with no reference asks
        where it comes from. Returns whether it is on."""
        self._read_options()
        if on:
            img = self._image()
            if img is not None and self.reference_of(img) is None:
                if not self.choose(img):
                    self._set_action(False)
                    return False
            self.on = True
        else:
            self.on = False
            self.dragging = False
        self._set_action(self.on)
        self.refresh()
        return self.on

    def _set_action(self, on):
        a = getattr(self.p, "compare_action", None)
        if a is not None and a.isChecked() != on:
            a.blockSignals(True)
            a.setChecked(on)
            a.blockSignals(False)

    # --- choosing ----------------------------------------------------------
    def ask_source(self):
        """The seam: "file" | "frame" | None (cancel)."""
        box = QMessageBox(self.p)
        box.setWindowTitle("Reference")
        box.setText("Compare this frame with which picture?")
        box.setInformativeText("A finished picture of the same frame: a lab scan, a JPEG "
                               "made elsewhere, or another frame open here.")
        f = box.addButton("Choose a File…", QMessageBox.AcceptRole)
        fr = box.addButton("Choose an Open Frame…", QMessageBox.AcceptRole)
        from core.ccr_backend import ccr_backend
        fr.setEnabled(len(ccr_backend.images) > 1)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        return "file" if box.clickedButton() is f else (
            "frame" if box.clickedButton() is fr else None)

    def ask_file(self):
        """The seam: a path or None."""
        start = str(self.store.value("view/compare_dir", "") or "")
        path, _ = QFileDialog.getOpenFileName(self.p, "Choose a Reference Picture", start,
                                              FILE_FILTER)
        return path or None

    def ask_frame(self, img):
        """The seam: another open image or None."""
        from core.ccr_backend import ccr_backend
        from widgets.reference_picker import pick_frame
        others = [im for im in ccr_backend.images if im is not img]
        return pick_frame(self.p, others)

    def choose(self, img, kind=None) -> bool:
        """Ask for this frame's reference; True when one was set."""
        if img is None:
            return False
        kind = kind or self.ask_source()
        if kind == "file":
            path = self.ask_file()
            if not path:
                return False
            self._save("view/compare_dir", os.path.dirname(path))
            self.set_reference(img, {"file": os.path.abspath(path)})
            return True
        if kind == "frame":
            other = self.ask_frame(img)
            if other is None:
                return False
            self.set_reference(img, {"frame": frame_identity(other)})
            return True
        return False

    def set_reference(self, img, ref):
        img.compare_ref = ref
        self._shown = None
        self._save_catalog()
        if self.on:
            self.refresh()

    def use_frame(self, other):
        """The frame list's "Compare the Frame on Screen with This"."""
        img = self._image()
        if img is None or other is None or other is img:
            return False
        self._read_options()
        img.compare_ref = {"frame": frame_identity(other)}
        self._shown = None
        self._save_catalog()
        self.on = True
        self._set_action(True)
        self.refresh()
        return True

    def remove_reference(self):
        img = self._image()
        if img is not None and getattr(img, "compare_ref", None):
            img.compare_ref = None
            self._shown = None
            self._save_catalog()
            self.refresh()

    @staticmethod
    def _save_catalog():
        try:
            from core.ccr_backend import ccr_backend
            ccr_backend.save_catalog()
        except Exception:
            pass

    # --- the menu ------------------------------------------------------------
    def build_menu(self) -> QMenu:
        """The right-click menu (on the line, its knob or the toolbar button)."""
        self._read_options()
        from core.ccr_backend import ccr_backend
        img = self._image()
        ref = self.reference_of(img)
        m = QMenu(self.p)
        up = m.addAction("Upright Line")
        lv = m.addAction("Level Line")
        for a, on in ((up, self.upright), (lv, not self.upright)):
            a.setCheckable(True)
            a.setChecked(on)
        up.triggered.connect(lambda: self.set_upright(True))
        lv.triggered.connect(lambda: self.set_upright(False))
        m.addAction("Swap Sides").triggered.connect(self.toggle_swap)
        m.addAction("Centre the Line").triggered.connect(self.centre)
        lk = m.addAction("Lock Line")
        lk.setCheckable(True)
        lk.setChecked(self.locked)
        lk.triggered.connect(lambda checked: self.set_locked(checked))
        m.addSeparator()
        lu = m.addAction("Line Up with the Frame")
        lu.setCheckable(True)
        lu.setChecked(self.lineup)
        lu.triggered.connect(lambda checked: self.set_lineup(checked))
        again = m.addAction("Line Up Again")
        again.setEnabled(bool(ref) and self.lineup)
        again.triggered.connect(self.line_up_again)
        m.addSeparator()
        m.addAction("Choose a File…").triggered.connect(lambda: self._choose_and_show("file"))
        fr = m.addAction("Choose an Open Frame…")
        fr.setEnabled(len(ccr_backend.images) > 1)
        fr.triggered.connect(lambda: self._choose_and_show("frame"))
        if ref and ref.get("file"):
            m.addAction("Show the Reference File").triggered.connect(
                lambda: self._reveal(ref["file"]))
        rm = m.addAction("Remove Reference")
        rm.setEnabled(bool(ref))
        rm.triggered.connect(self.remove_reference)
        return m

    def show_menu(self, global_pos):
        self.build_menu().exec(global_pos)

    def _choose_and_show(self, kind):
        img = self._image()
        if self.choose(img, kind) and not self.on:
            self.on = True
            self._set_action(True)
            self.refresh()

    @staticmethod
    def _reveal(path):
        try:
            from utils import reveal_files
            reveal_files.reveal([path])
        except Exception:
            pass

    def set_upright(self, on):
        self.upright = bool(on)
        self._save("view/compare_line", "upright" if self.upright else "level")
        self.update_clip()

    def toggle_swap(self):
        self.swap = not self.swap
        self._save("view/compare_swap", self.swap)
        self.update_clip()

    def centre(self):
        self.frac = 0.5
        self.update_clip()

    def set_locked(self, on):
        self.locked = bool(on)
        self._viewport_update()

    def set_lineup(self, on):
        self.lineup = bool(on)
        self._save("view/compare_lineup", self.lineup)
        self.refresh()

    def line_up_again(self):
        if self._shown is not None:
            with self._lock:
                self._lineups.pop(self._shown.get("lu_key"), None)
            self._shown = None
        self.refresh()

    # --- items -----------------------------------------------------------------
    def reset_items(self):
        """The picture item (and so its children) was deleted."""
        self.clip = self.bg = self.item = None

    def _hide(self):
        if self.clip is not None:
            self.clip.setVisible(False)

    def _ensure_items(self):
        parent = self.p.pixmap_item
        if self.clip is not None and self.clip.parentItem() is parent:
            return
        self.clip = QGraphicsPathItem(parent)
        self.clip.setPen(QPen(Qt.NoPen))
        self.clip.setBrush(QBrush(Qt.NoBrush))
        self.clip.setFlag(QGraphicsItem.ItemClipsChildrenToShape, True)
        self.clip.setZValue(Z)
        self.bg = QGraphicsRectItem(self.clip)
        self.bg.setPen(QPen(Qt.NoPen))
        self.bg.setBrush(QBrush(QColor(NO_PICTURE)))
        self.item = QGraphicsPixmapItem(self.clip)
        self.item.setTransformationMode(Qt.SmoothTransformation)

    # --- refreshing ------------------------------------------------------------
    def refresh(self):
        """Follow the frame on screen (after every apply_transformations)."""
        img = self._image()
        if not self.on or self.p.pixmap_item is None or img is None:
            self._hide()
            self.status = ""
            self._viewport_update()
            return
        ref = self.reference_of(img)
        if ref is None:
            self._hide()
            self.status = HINT_NONE
            self._viewport_update()
            return
        got, hint = self._source(img, ref)
        if got is None:
            self._hide()
            self.status = hint
            self._viewport_update()
            return
        pix_sig, geo_sig, how = got
        fgeo = self._frame_geometry(img)
        key = (pix_sig, fgeo, self.lineup)
        lu_key = (geo_sig, fgeo)
        if self._shown is not None and self._shown["key"] == key:
            self._show()
            return
        with self._lock:
            have_lu = lu_key in self._lineups
            lu = self._lineups.get(lu_key)
        # a frame reference's pixels are drawn here (Qt, GUI thread)
        ref_rgb = None
        if how[0] == "frame":
            q = render_frame(how[1])
            if q is None:
                self._hide()
                self.status = "The reference frame has no picture yet."
                self._viewport_update()
                return
            ref_rgb = _rgb_of(q)
            if not self.lineup or have_lu:
                self._adopt(key, lu_key, ref_rgb, lu, have_lu or not self.lineup, "")
                return
        if self._asked == key:
            return                                   # already on its way
        self._asked = key
        self.status = HINT_LINING if self.lineup else ""
        self._viewport_update()
        shown = _rgb_of(self.p.current_pixmap.toImage()) if self.lineup else None
        invert = self._shows_negative(img)
        self._job += 1
        job = dict(id=self._job, key=key, lu_key=lu_key, how=how[0],
                   path=how[1] if how[0] == "file" else None, ref_rgb=ref_rgb,
                   shown=shown, invert=invert, lineup=self.lineup,
                   have_lu=have_lu, lu=lu)
        if self.threaded:
            threading.Thread(target=self._work, args=(job,), daemon=True).start()
        else:
            self._work(job)

    @staticmethod
    def _shows_negative(img):
        from core.ccr_backend import ccr_backend
        return not (getattr(img, "converted", False) or ccr_backend.positive_mode)

    def _work(self, job):
        """Off the GUI thread: read the file, line it up."""
        out = dict(job)
        try:
            if job["how"] == "file":
                path = job["path"]
                fsig = job["key"][0]
                with self._lock:
                    ref = self._refs.get(fsig)
                if ref is None:
                    ref = rc.load_reference(path)
                    with self._lock:
                        self._refs[fsig] = ref
                        while len(self._refs) > 2:
                            self._refs.popitem(last=False)
                out["ref_rgb"] = ref.rgb
                if not ref.converted:
                    out["note"] = "Its colour profile couldn't be applied: shown as sRGB."
            lu, tried = job["lu"], job["have_lu"]
            if job["lineup"] and not tried:
                lu = rc.line_up(out["ref_rgb"], job["shown"], invert_shown=job["invert"])
                tried = True
                with self._lock:
                    self._lineups[job["lu_key"]] = lu
                    while len(self._lineups) > 8:
                        self._lineups.popitem(last=False)
            out["lu"], out["tried"] = lu, tried
        except Exception as e:                     # an unreadable file
            out["error"] = f"Couldn't read the reference: {e}"
        self._signals.done.emit(out)

    def _on_done(self, out):
        if out.get("id") != self._job:
            return                                   # a newer request won
        self._asked = None
        if out.get("error"):
            self._shown = None
            self._hide()
            self.status = out["error"]
            self._viewport_update()
            return
        self._adopt(out["key"], out["lu_key"], out["ref_rgb"], out.get("lu"),
                    out.get("tried", False), out.get("note", ""))

    def _adopt(self, key, lu_key, ref_rgb, lu, tried, note):
        pm = QPixmap.fromImage(_qimage_of(ref_rgb))
        self._shown = dict(key=key, lu_key=lu_key, lu=lu, tried=tried, pixmap=pm,
                           size=(pm.width(), pm.height()), note=note)
        self._show()

    def _show(self):
        sh = self._shown
        item = self.p.pixmap_item
        if sh is None or item is None:
            return
        self._ensure_items()
        target = item.pixmap()
        self.bg.setRect(QRectF(0, 0, target.width(), target.height()))
        if self.item.pixmap().cacheKey() != sh["pixmap"].cacheKey():
            self.item.setPixmap(sh["pixmap"])
        if self.lineup and sh["lu"] is not None:
            M = rc.pixel_matrix(sh["lu"].H, sh["size"], (target.width(), target.height()))
            self.item.setTransform(QTransform(*rc.qt_args(M)))
            self.status = sh["note"]
        else:
            self.item.setTransform(self._fitted_transform(sh["size"]))
            self.status = HINT_FAILED if (self.lineup and sh["tried"]) else sh["note"]
        self.clip.setVisible(True)
        self.update_clip()

    def _fitted_transform(self, size):
        """The whole reference upright, fitted to the picture as shown."""
        item = self.p.pixmap_item
        box = item.mapToScene(item.boundingRect()).boundingRect()
        x, y, w, h = rc.fitted_rect(size, (box.x(), box.y(), box.width(), box.height()))
        t = QTransform()
        t.translate(x, y)
        t.scale(w / size[0], h / size[1])
        inv, ok = item.sceneTransform().inverted()
        return t * inv if ok else t

    # --- the line --------------------------------------------------------------
    def _picture_vp(self):
        """The picture's visible extent in viewport coordinates."""
        view, item = self.p.view, self.p.pixmap_item
        if item is None:
            return None
        r = view.mapFromScene(item.mapToScene(item.boundingRect())).boundingRect()
        vr = view.viewport().rect()
        r = QRectF(r).intersected(QRectF(vr))
        return r if r.width() > 1 and r.height() > 1 else None

    def line_pos(self):
        """The line's x (upright) or y (level) in the viewport, held to the
        picture's visible extent; None when there's nothing to draw."""
        pic = self._picture_vp()
        if pic is None:
            return None
        vr = self.p.view.viewport().rect()
        if self.upright:
            return min(max(self.frac * vr.width(), pic.left()), pic.right())
        return min(max(self.frac * vr.height(), pic.top()), pic.bottom())

    def _active(self):
        return (self.on and self.clip is not None and self.clip.isVisible()
                and self.p.pixmap_item is not None)

    def update_clip(self):
        """The reference's side of the line, as the clip path (in the picture
        item's coordinates). Called on every zoom, pan, resize and refresh."""
        if not self._active():
            self._viewport_update()
            return
        view, item = self.p.view, self.p.pixmap_item
        vr = QRectF(view.viewport().rect())
        pos = self.line_pos()
        if pos is None:
            self.clip.setPath(QPainterPath())
            self._viewport_update()
            return
        if self.upright:
            side = (QRectF(vr.left(), vr.top(), pos - vr.left(), vr.height()) if self.swap
                    else QRectF(pos, vr.top(), vr.right() - pos, vr.height()))
        else:
            side = (QRectF(vr.left(), vr.top(), vr.width(), pos - vr.top()) if self.swap
                    else QRectF(vr.left(), pos, vr.width(), vr.bottom() - pos))
        poly = QPolygonF([view.mapToScene(side.topLeft().toPoint()),
                          view.mapToScene(side.topRight().toPoint()),
                          view.mapToScene(side.bottomRight().toPoint()),
                          view.mapToScene(side.bottomLeft().toPoint())])
        path = QPainterPath()
        path.addPolygon(item.mapFromScene(poly))
        path.closeSubpath()
        picture = QPainterPath()
        picture.addRect(item.boundingRect())
        self.clip.setPath(path.intersected(picture))   # never past the picture
        self._viewport_update()

    def _viewport_update(self):
        v = getattr(self.p, "view", None)
        if v is not None:
            v.viewport().update()

    # --- the mouse -------------------------------------------------------------
    def hit(self, vp_pos) -> bool:
        """Is this viewport point on the line or its knob?"""
        if not self._active():
            return False
        pos, pic = self.line_pos(), self._picture_vp()
        if pos is None or pic is None:
            return False
        x, y = vp_pos.x(), vp_pos.y()
        if self.upright:
            knob = QPointF(pos, pic.center().y())
            on_line = abs(x - pos) <= GRAB_PX and pic.top() <= y <= pic.bottom()
        else:
            knob = QPointF(pic.center().x(), pos)
            on_line = abs(y - pos) <= GRAB_PX and pic.left() <= x <= pic.right()
        near_knob = (x - knob.x()) ** 2 + (y - knob.y()) ** 2 <= (KNOB_R + 4) ** 2
        return on_line or near_knob

    def can_drag(self, vp_pos) -> bool:
        return not self.locked and self.hit(vp_pos)

    def press(self, vp_pos):
        self.dragging = True
        self.drag_to(vp_pos)

    def drag_to(self, vp_pos):
        vr = self.p.view.viewport().rect()
        pic = self._picture_vp()
        if pic is None:
            return
        if self.upright:
            x = min(max(vp_pos.x(), pic.left()), pic.right())
            self.frac = x / max(1.0, vr.width())
        else:
            y = min(max(vp_pos.y(), pic.top()), pic.bottom())
            self.frac = y / max(1.0, vr.height())
        self.update_clip()

    def release(self):
        self.dragging = False

    def cursor(self):
        return Qt.SplitHCursor if self.upright else Qt.SplitVCursor

    # --- drawing (the view's foreground, in viewport coordinates) -----------------
    def paint(self, painter: QPainter):
        if not self.on:
            return
        view = self.p.view
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QPainter.Antialiasing)
        f = QFont(view.font())
        f.setPixelSize(theme.FS_CAPTION)
        painter.setFont(f)
        fm = QFontMetrics(f)
        if self._active():
            pos, pic = self.line_pos(), self._picture_vp()
            if pos is not None and pic is not None:
                self._paint_line(painter, fm, pos, pic)
        if self.status:
            self._paint_status(painter, fm)
        painter.restore()

    def _tag(self, painter, fm, text, pos, anchor):
        w, h = fm.horizontalAdvance(text) + 12, fm.height() + 4
        r = QRectF(0, 0, w, h)
        if anchor == "left":
            r.moveTopRight(pos)
        elif anchor == "right":
            r.moveTopLeft(pos)
        elif anchor == "up":
            r.moveBottomLeft(pos)
        else:
            r.moveTopLeft(pos)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 165))
        painter.drawRoundedRect(r, 3, 3)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(r, Qt.AlignCenter, text)
        return r

    def _paint_line(self, painter, fm, pos, pic):
        if self.upright:
            a, b = QPointF(pos, pic.top()), QPointF(pos, pic.bottom())
        else:
            a, b = QPointF(pic.left(), pos), QPointF(pic.right(), pos)
        painter.setPen(QPen(QColor(0, 0, 0, 150), 3))
        painter.drawLine(a, b)
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.drawLine(a, b)
        c = (a + b) / 2
        painter.setPen(QPen(QColor(0, 0, 0, 160), 1.5))
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(c, KNOB_R, KNOB_R)
        dark = QColor(40, 40, 40)
        if self.locked:
            painter.setPen(QPen(dark, 1.3))
            painter.setBrush(Qt.NoBrush)
            painter.drawArc(QRectF(c.x() - 2.2, c.y() - 4.4, 4.4, 5.0), 0, 180 * 16)
            painter.setPen(Qt.NoPen)
            painter.setBrush(dark)
            painter.drawRoundedRect(QRectF(c.x() - 3.0, c.y() - 1.6, 6.0, 4.6), 0.8, 0.8)
        else:
            painter.setPen(QPen(QColor(60, 60, 60), 1.2))
            for s in (-1, 1):
                if self.upright:
                    painter.drawLine(QPointF(c.x() + s * 2.0, c.y()), QPointF(c.x() + s * 4.2, c.y()))
                else:
                    painter.drawLine(QPointF(c.x(), c.y() + s * 2.0), QPointF(c.x(), c.y() + s * 4.2))
        first, second = ("Reference", "Frame") if self.swap else ("Frame", "Reference")
        if self.upright:
            self._tag(painter, fm, first, QPointF(pos - 6, pic.top() + 8), "left")
            self._tag(painter, fm, second, QPointF(pos + 6, pic.top() + 8), "right")
        else:
            self._tag(painter, fm, first, QPointF(pic.left() + 8, pos - 6), "up")
            self._tag(painter, fm, second, QPointF(pic.left() + 8, pos + 6), "down")

    def _paint_status(self, painter, fm):
        vr = QRectF(self.p.view.viewport().rect())
        pic = self._picture_vp() or vr
        self._tag(painter, fm, self.status, QPointF(pic.left() + 8, pic.bottom() - 8), "up")
