"""The collage maker's view (spec/collage-maker.md): shown in the canvas's
place, like the contact sheet. A bar (Shuffle with its history, Export
Collage…, Back to Frame), the preview, the settings beside it and the strip
of members under it.
"""
import os
from typing import List

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QColorDialog, QComboBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QProgressDialog,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout,
                               QWidget)

from core import collage
from core.ccr_backend import ccr_backend
from ui import theme
from utils.qt_threads import WorkerThread
from widgets import collage_render as cr

SETTINGS_W = 270
STRIP_H = 96


class _Preview(QWidget):
    """The collage drawn to fit, on the window's dark surround."""
    resized = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_image(self, img):
        self.image = img
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.CANVAS))
        if self.image is not None and not self.image.isNull():
            dpr = self.image.devicePixelRatio() or 1.0
            w, h = self.image.width() / dpr, self.image.height() / dpr
            p.drawImage(int((self.width() - w) / 2), int((self.height() - h) / 2), self.image)
        p.end()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()


class _Prepare(WorkerThread):
    """Renders the members through Export (ExportSource.prepare) off the GUI
    thread, one at a time."""
    step = Signal(int, int)

    def __init__(self, source, jobs, parent=None):
        super().__init__(parent)
        self.source, self.jobs = source, jobs
        self.cancelled = False
        self.failed = []

    def run(self):
        for k, (img, side, bleed) in enumerate(self.jobs):
            if self.cancelled:
                return
            if not self.source.prepare(img, side, bleed):
                self.failed.append(img)
            self.step.emit(k + 1, len(self.jobs))


class CollageView(QWidget):
    closeRequested = Signal()

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self._settings = settings or QSettings("FreeCCR", "FreeCCR")
        self._members: List = []
        self._taken = None                    # the last selection used (activate)
        self.history = collage.SeedHistory(1)
        self._sig = None
        self._export_threaded = True
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(30)
        self._render_timer.timeout.connect(self.render_preview)
        self._watch = QTimer(self)
        self._watch.setInterval(800)
        self._watch.timeout.connect(self._check_members)
        self._build()
        self._load_settings()
        self._sync_controls()

    # ------------------------------------------------------------------ #
    # building
    # ------------------------------------------------------------------ #
    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("collageBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setStyleSheet(f"#collageBar {{ background: {theme.PANEL}; }}")
        b = QHBoxLayout(bar)
        b.setContentsMargins(theme.GAP_PANEL, 4, theme.GAP_PANEL, 4)
        b.setSpacing(theme.GAP_BTN)
        title = QLabel("Collage")
        title.setStyleSheet("font-weight: bold;")
        b.addWidget(title)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        b.addWidget(self.count_label)
        b.addStretch(1)
        self.back_btn_seed = QPushButton("◀")
        self.back_btn_seed.setToolTip("The arrangement before this one.")
        self.shuffle_btn = QPushButton("Shuffle")
        self.shuffle_btn.setToolTip("Arrange the frames another way (◀ ▶ walk back and forth).")
        self.fwd_btn_seed = QPushButton("▶")
        self.fwd_btn_seed.setToolTip("The arrangement after this one.")
        self.seed_label = QLabel("")
        self.seed_label.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        self.export_btn = QPushButton("Export Collage…")
        self.export_btn.setToolTip(
            "JPEG, sRGB, quality 95, full colour: each frame rendered as Export "
            "would, with Instagram's screen sharpening. A carousel is one file "
            "a post (name_1.jpg …).")
        from PySide6.QtGui import QFontMetrics
        for w in (self.back_btn_seed, self.shuffle_btn, self.fwd_btn_seed, self.export_btn):
            theme.style_button(w, "secondary")
            w.setFixedWidth(QFontMetrics(w.font()).horizontalAdvance(w.text()) + 22)
        b.addWidget(self.back_btn_seed)
        b.addWidget(self.shuffle_btn)
        b.addWidget(self.fwd_btn_seed)
        b.addWidget(self.seed_label)
        b.addWidget(self.export_btn)
        self.back_btn = QPushButton("Back to Frame")
        self.back_btn.setToolTip("Back to the frame you were on (Esc).")
        theme.style_button(self.back_btn, "inverse")
        b.addWidget(self.back_btn)
        lay.addWidget(bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.preview = _Preview()
        self.preview.resized.connect(self.schedule)
        body.addWidget(self.preview, 1)
        body.addWidget(self._build_settings())
        lay.addLayout(body, 1)

        self.strip = QListWidget()
        self.strip.setViewMode(QListWidget.IconMode)
        self.strip.setFlow(QListWidget.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setMovement(QListWidget.Snap)
        self.strip.setDragDropMode(QAbstractItemView.InternalMove)
        self.strip.setDefaultDropAction(Qt.MoveAction)
        self.strip.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.strip.setIconSize(QSize(96, 64))
        self.strip.setFixedHeight(STRIP_H)
        self.strip.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.strip.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.strip.setToolTip("The collage's frames: drag to reorder, right-click to remove. "
                              "Right-click frames in the list on the left to add them.")
        self.strip.setContextMenuPolicy(Qt.CustomContextMenu)
        self.strip.customContextMenuRequested.connect(self._strip_menu)
        self.strip.model().rowsMoved.connect(lambda *a: QTimer.singleShot(0, self._order_from_strip))
        lay.addWidget(self.strip)

        self.back_btn_seed.clicked.connect(self.history_back)
        self.fwd_btn_seed.clicked.connect(self.history_forward)
        self.shuffle_btn.clicked.connect(self.shuffle)
        self.export_btn.clicked.connect(self.export)
        self.back_btn.clicked.connect(self.closeRequested.emit)

    def _build_settings(self):
        box = QWidget()
        box.setObjectName("collageSettings")
        box.setAttribute(Qt.WA_StyledBackground, True)
        box.setStyleSheet(f"#collageSettings {{ background: {theme.PANEL}; }}")
        form = QFormLayout(box)
        form.setContentsMargins(theme.GAP_PANEL, theme.GAP_PANEL, theme.GAP_PANEL, theme.GAP_PANEL)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.fmt_combo = QComboBox()
        for key, label, w, h in collage.FORMATS:
            self.fmt_combo.addItem(label if w is None else f"{label} ({w} × {h})", key)
        form.addRow("Format", self.fmt_combo)
        self.w_spin, self.h_spin = QSpinBox(), QSpinBox()
        for s in (self.w_spin, self.h_spin):
            s.setRange(collage.MIN_SIDE, collage.MAX_SIDE)
            s.setSuffix(" px")
        self.custom_row = QWidget()
        cr_lay = QHBoxLayout(self.custom_row)
        cr_lay.setContentsMargins(0, 0, 0, 0)
        cr_lay.addWidget(self.w_spin)
        cr_lay.addWidget(QLabel("×"))
        cr_lay.addWidget(self.h_spin)
        form.addRow("Size", self.custom_row)

        self.layout_combo = QComboBox()
        for key, label in collage.LAYOUTS:
            self.layout_combo.addItem(label + (" (draft)" if key == "tear" else ""), key)
        self.layout_combo.setItemData(
            [k for k, _ in collage.LAYOUTS].index("tear"),
            "A band of one frame torn out of paper over another. A drawn edge for "
            "now: scanned card textures will replace it.", Qt.ToolTipRole)
        form.addRow("Layout", self.layout_combo)
        self.style_combo = QComboBox()
        for key, label in collage.CAROUSEL_STYLES:
            self.style_combo.addItem(label, key)
        form.addRow("Style", self.style_combo)
        self.posts_spin = QSpinBox()
        self.posts_spin.setRange(2, 10)
        form.addRow("Posts", self.posts_spin)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(80, 100)
        self.height_spin.setSuffix(" %")
        form.addRow("Height", self.height_spin)
        self.frames_spin = QSpinBox()
        self.frames_spin.setRange(2, 6)
        form.addRow("Frames", self.frames_spin)
        self.dir_combo = QComboBox()
        self.dir_combo.addItem("In a row", "across")
        self.dir_combo.addItem("In a column", "down")
        form.addRow("Direction", self.dir_combo)

        self.respect_cb = QCheckBox("Respect each frame's crop")
        self.respect_cb.setToolTip(
            "On: every frame keeps its crop's shape (a mosaic may leave room). "
            "Off: a crop may be trimmed by up to the amount below so the rows "
            "fill. Nothing is ever squashed.")
        form.addRow(self.respect_cb)
        self.variance_spin = QSpinBox()
        self.variance_spin.setRange(0, 30)
        self.variance_spin.setSuffix(" %")
        form.addRow("At most", self.variance_spin)

        self.borders_combo = QComboBox()
        for key, label in cr.BORDERS:
            self.borders_combo.addItem(label, key)
        self.borders_combo.setToolTip(
            "Film borders round each frame: generated (35 mm with its holes, 120 "
            "with the gate of your 6x6 reference), or from the scan around the crop.")
        form.addRow("Film borders", self.borders_combo)
        self.see_through_cb = QCheckBox("See-through film edges")
        self.see_through_cb.setToolTip("The film's edges and holes let the background show; "
                                       "the pictures stay solid.")
        form.addRow(self.see_through_cb)
        self.colour_btn = QPushButton()
        self.colour_btn.setToolTip("The background and border colour.")
        self.colour_btn.clicked.connect(self._choose_colour)
        form.addRow("Colour", self.colour_btn)
        self.gap_spin = QSpinBox()
        self.gap_spin.setRange(0, 200)
        self.gap_spin.setSuffix(" px")
        self.gap_spin.setToolTip("Between frames and to the edge, at the output size.")
        form.addRow("Gap", self.gap_spin)
        self.bg_combo = QComboBox()
        for key, label in cr.BACKGROUNDS:
            self.bg_combo.addItem(label, key)
        self.bg_combo.setToolTip("Behind the frames: the colour, or the first frame of the "
                                 "collage filling the canvas, blurred or sharp.")
        form.addRow("Background", self.bg_combo)

        for w in (self.fmt_combo, self.layout_combo, self.style_combo, self.dir_combo,
                  self.borders_combo, self.bg_combo):
            # as wide as the column, not their longest entry
            w.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            w.setMinimumContentsLength(8)
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            w.currentIndexChanged.connect(self._changed)
        for w in (self.w_spin, self.h_spin, self.posts_spin, self.height_spin,
                  self.frames_spin, self.variance_spin, self.gap_spin):
            w.valueChanged.connect(self._changed)
        for w in (self.respect_cb, self.see_through_cb):
            w.toggled.connect(self._changed)
        self._form = form
        scroll = QScrollArea()
        scroll.setWidget(box)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(SETTINGS_W)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return scroll

    # ------------------------------------------------------------------ #
    # settings
    # ------------------------------------------------------------------ #
    DEFAULTS = {"format": "post", "width": 1080, "height": 1350, "layout": "mosaic",
                "style": "varied", "posts": 3, "height_share": 95, "frames": 3,
                "direction": "across", "respect": True, "variance": 6,
                "borders": "none", "see_through": False, "colour": "#ffffff",
                "gap": 14, "background": "colour"}

    def _get(self, key):
        d = self.DEFAULTS[key]
        v = self._settings.value("collage/" + key, d)
        if isinstance(d, bool):
            return v in (True, "true", "1", 1)
        if isinstance(d, int):
            try:
                return int(v)
            except (TypeError, ValueError):
                return d
        return str(v)

    def _load_settings(self):
        self._loading = True
        try:
            def pick(combo, value):
                i = combo.findData(value)
                combo.setCurrentIndex(i if i >= 0 else 0)
            pick(self.fmt_combo, self._get("format"))
            self.w_spin.setValue(self._get("width"))
            self.h_spin.setValue(self._get("height"))
            pick(self.layout_combo, self._get("layout"))
            pick(self.style_combo, self._get("style"))
            self.posts_spin.setValue(self._get("posts"))
            self.height_spin.setValue(self._get("height_share"))
            self.frames_spin.setValue(self._get("frames"))
            pick(self.dir_combo, self._get("direction"))
            self.respect_cb.setChecked(self._get("respect"))
            self.variance_spin.setValue(self._get("variance"))
            pick(self.borders_combo, self._get("borders"))
            self.see_through_cb.setChecked(self._get("see_through"))
            self._colour = QColor(self._get("colour"))
            if not self._colour.isValid():
                self._colour = QColor("#ffffff")
            self.gap_spin.setValue(self._get("gap"))
            pick(self.bg_combo, self._get("background"))
            self._paint_swatch()
        finally:
            self._loading = False

    def _save_settings(self):
        s = self._settings
        s.setValue("collage/format", self.fmt_combo.currentData())
        s.setValue("collage/width", self.w_spin.value())
        s.setValue("collage/height", self.h_spin.value())
        s.setValue("collage/layout", self.layout_combo.currentData())
        s.setValue("collage/style", self.style_combo.currentData())
        s.setValue("collage/posts", self.posts_spin.value())
        s.setValue("collage/height_share", self.height_spin.value())
        s.setValue("collage/frames", self.frames_spin.value())
        s.setValue("collage/direction", self.dir_combo.currentData())
        s.setValue("collage/respect", self.respect_cb.isChecked())
        s.setValue("collage/variance", self.variance_spin.value())
        s.setValue("collage/borders", self.borders_combo.currentData())
        s.setValue("collage/see_through", self.see_through_cb.isChecked())
        s.setValue("collage/colour", self._colour.name())
        s.setValue("collage/gap", self.gap_spin.value())
        s.setValue("collage/background", self.bg_combo.currentData())

    def _paint_swatch(self):
        pm = QPixmap(28, 14)
        pm.fill(self._colour)
        self.colour_btn.setIcon(QIcon(pm))
        self.colour_btn.setText(self._colour.name())

    def _choose_colour(self):
        c = self._pick_colour(self._colour)
        if c is not None and c.isValid():
            self._colour = c
            self._paint_swatch()
            self._changed()

    def _pick_colour(self, current):
        """The test seam."""
        c = QColorDialog.getColor(current, self, "Collage colour")
        return c if c.isValid() else None

    def _sync_controls(self):
        layout = self.layout_combo.currentData()
        carousel = layout == "carousel"
        style = self.style_combo.currentData()

        def show(w, on):
            self._form.setRowVisible(w, bool(on))
        show(self.custom_row, self.fmt_combo.currentData() == "custom")
        show(self.style_combo, carousel)
        show(self.posts_spin, carousel and style != "full")
        show(self.height_spin, carousel and style == "full")
        show(self.frames_spin, layout == "triptych")
        show(self.dir_combo, layout == "triptych")
        show(self.respect_cb, layout in ("mosaic", "carousel"))
        show(self.variance_spin, layout in ("mosaic", "carousel")
             and not self.respect_cb.isChecked())
        show(self.borders_combo, layout != "tear")
        show(self.see_through_cb, layout == "strip")
        show(self.bg_combo, layout != "tear")

    def _changed(self, *_):
        if getattr(self, "_loading", False):
            return
        if (self.layout_combo.currentData() == "strip"
                and self.borders_combo.currentData() == "none"):
            # a film strip is film: say so rather than draw it behind "None"
            self._loading = True
            self.borders_combo.setCurrentIndex(self.borders_combo.findData("generated"))
            self._loading = False
        self._sync_controls()
        self._save_settings()
        self.schedule()

    # ------------------------------------------------------------------ #
    # what the engine and the drawing are given
    # ------------------------------------------------------------------ #
    def canvas_size(self):
        key = self.fmt_combo.currentData()
        for k, _label, w, h in collage.FORMATS:
            if k == key and w is not None:
                return int(w), int(h)
        return int(self.w_spin.value()), int(self.h_spin.value())

    def look(self) -> cr.Look:
        layout = self.layout_combo.currentData()
        borders = self.borders_combo.currentData()
        if layout == "strip" and borders == "none":
            borders = "generated"             # a film strip is film
        if layout == "tear":
            borders = "none"
        c = self._colour
        return cr.Look(borders=borders,
                       see_through=self.see_through_cb.isChecked() and layout == "strip",
                       colour=(c.red(), c.green(), c.blue()),
                       background=self.bg_combo.currentData() if layout != "tear" else "colour",
                       strip=(layout == "strip"), strip_fmt=cr.strip_format(self.members()))

    def settings(self) -> collage.Settings:
        W, H = self.canvas_size()
        layout = self.layout_combo.currentData()
        return collage.Settings(
            width=W, height=H, layout=layout, style=self.style_combo.currentData(),
            posts=self.posts_spin.value(), height_share=self.height_spin.value() / 100.0,
            gap=self.gap_spin.value(),
            respect=self.respect_cb.isChecked(), variance=self.variance_spin.value() / 100.0,
            frames=self.frames_spin.value(), direction=self.dir_combo.currentData(),
            piece_aspect=cr.strip_piece_aspect(cr.strip_format(self.members())))

    def arrangement(self):
        mem = self.members()
        look = self.look()
        aspects = [cr.outer_aspect(m, look.borders) for m in mem]
        return collage.arrange(aspects, self.settings(), self.history.seed), mem, look

    # ------------------------------------------------------------------ #
    # members
    # ------------------------------------------------------------------ #
    def members(self) -> List:
        loaded = {id(i) for i in ccr_backend.images}
        self._members = [m for m in self._members if id(m) in loaded]
        return list(self._members)

    def has_member(self, img) -> bool:
        return any(m is img for m in self._members)

    def set_members(self, imgs):
        self._members = []
        self.add_members(imgs, quiet=True)
        self._refresh_strip()
        self.schedule()

    def add_members(self, imgs, quiet=False):
        if not quiet:
            self._taken = frozenset(id(i) for i in imgs or [])
        for im in imgs or []:
            if im is not None and not self.has_member(im):
                self._members.append(im)
        if not quiet:
            self._refresh_strip()
            self.schedule()

    def remove_members(self, imgs):
        drop = {id(i) for i in imgs or []}
        self._taken = frozenset(drop)
        self._members = [m for m in self._members if id(m) not in drop]
        self._refresh_strip()
        self.schedule()

    def _refresh_strip(self):
        self.strip.blockSignals(True)
        self.strip.clear()
        for m in self.members():
            item = QListWidgetItem()
            thumb = getattr(m, "thumbnail", None)
            if thumb is None or thumb.isNull():
                thumb = getattr(m, "resized_preview", None)
            if thumb is not None and not thumb.isNull():
                pm = thumb if isinstance(thumb, QPixmap) else QPixmap.fromImage(thumb)
                item.setIcon(QIcon(pm.scaled(96, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            name = getattr(m, "display_name", None) or os.path.basename(m.file_path)
            item.setToolTip(name)
            item.setData(Qt.UserRole, id(m))
            self.strip.addItem(item)
        self.strip.blockSignals(False)
        n = len(self._members)
        self.count_label.setText(f"{n} frame{'s' if n != 1 else ''}")

    def _order_from_strip(self):
        by_id = {id(m): m for m in self._members}
        order = [by_id.get(self.strip.item(i).data(Qt.UserRole))
                 for i in range(self.strip.count())]
        order = [m for m in order if m is not None]
        if len(order) == len(self._members):
            self._members = order
            self.schedule()

    def _strip_menu(self, pos):
        items = self.strip.selectedItems() or ([self.strip.itemAt(pos)]
                                               if self.strip.itemAt(pos) else [])
        if not items:
            return
        ids = {it.data(Qt.UserRole) for it in items}
        menu = QMenu(self)
        n = len(ids)
        act = menu.addAction("Remove from Collage" + (f" ({n})" if n > 1 else ""))
        if menu.exec(self.strip.mapToGlobal(pos)) == act:
            self.remove_members([m for m in self._members if id(m) in ids])

    def _check_members(self):
        sig = tuple(cr.frame_signature(m) for m in self.members())
        if sig != self._sig:
            self._sig = sig
            self.schedule()

    # ------------------------------------------------------------------ #
    # shuffle and its history
    # ------------------------------------------------------------------ #
    def shuffle(self):
        self.history.shuffle()
        self.schedule()

    def history_back(self):
        if self.history.back():
            self.schedule()

    def history_forward(self):
        if self.history.forward():
            self.schedule()

    # ------------------------------------------------------------------ #
    # showing
    # ------------------------------------------------------------------ #
    def activate(self, selected=None):
        """Shown: a NEW selection of more than one frame becomes the collage;
        otherwise the collage it had, or every frame when it has none
        (spec/collage-maker.md). A selection already used (taken on opening,
        or added / removed from the list's menu) keeps the collage: one left
        over from before must not rebuild a finished collage."""
        sel = list(selected or [])
        ids = frozenset(id(i) for i in sel)
        members = self.members()
        if len(sel) > 1 and ids != self._taken:
            self.set_members(sel)
            self._taken = ids
        elif not members:
            self.set_members(list(ccr_backend.images))
        else:
            self._refresh_strip()
        self._watch.start()
        self.render_preview()
        self.setFocus()

    def deactivate(self):
        self._watch.stop()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.closeRequested.emit()
            return
        super().keyPressEvent(e)

    def schedule(self):
        self._render_timer.start()

    def render_preview(self):
        arr, mem, look = self.arrangement()
        self.back_btn_seed.setEnabled(self.history.at > 0)
        self.fwd_btn_seed.setEnabled(self.history.at < len(self.history.seeds) - 1)
        self.seed_label.setText(self.history.label())
        self.export_btn.setEnabled(bool(mem))
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        aw, ah = max(50, self.preview.width() - 24), max(50, self.preview.height() - 24)
        scale = min(aw / max(1, arr.width), ah / max(1, arr.height))
        w, h = max(1, int(arr.width * scale * dpr)), max(1, int(arr.height * scale * dpr))
        img = QImage(w, h, QImage.Format_RGB32)
        img.fill(QColor(*look.colour))
        if mem:
            p = QPainter(img)
            cr.draw(p, arr, mem, look, cr.PreviewSource(), scale * dpr, self.history.seed,
                    post_lines=True)
            p.end()
        else:
            p = QPainter(img)
            p.setPen(QColor(120, 120, 120))
            p.drawText(img.rect(), Qt.AlignCenter, "No frames: right-click frames in the "
                       "list and Add to Collage")
            p.end()
        img.setDevicePixelRatio(dpr)
        self.preview.set_image(img)
        self.last_arrangement = arr
        return arr

    # ------------------------------------------------------------------ #
    # export
    # ------------------------------------------------------------------ #
    def _ask_export_path(self):
        """The test seam: where to write (a .jpg path) or None."""
        folder = self._settings.value("collage/export_dir", os.path.expanduser("~"))
        path, _ = QFileDialog.getSaveFileName(self, "Export Collage",
                                              os.path.join(str(folder), "collage.jpg"),
                                              "JPEG (*.jpg)")
        return path or None

    @staticmethod
    def export_jobs(arr, mem, look):
        """[(image, long side, bleed share)] for Export to render: each member
        at what its biggest cell needs (with "From the scan" borders, its
        crop widened by the bleed, the long side with it), and the background
        frame at the canvas's long side."""
        jobs = {}

        def want(img, side, share):
            key = (id(img), share > 0)
            if key in jobs:
                jobs[key][1] = max(jobs[key][1], int(side))
            else:
                jobs[key] = [img, int(side), share]
        for i, side in cr.needs(arr, mem, look).items():
            img = mem[i]
            if look.borders == "scan":
                pc = cr.Piece(img)
                share = cr.cs.CAMERA_BLEED_MM / max(pc.geo.along, pc.geo.across)
                want(img, side * (1 + 2 * share), share)
            else:
                want(img, side, 0.0)
        if look.background in ("blur", "sharp") and arr.band is None and mem:
            want(mem[0], max(arr.width, arr.height), 0.0)
        return [tuple(j) for j in jobs.values()]

    def export(self):
        arr, mem, look = self.arrangement()
        if not mem:
            return []
        path = self._ask_export_path()
        if not path:
            return []
        self._settings.setValue("collage/export_dir", os.path.dirname(path))
        source = cr.ExportSource(ccr_backend)
        jobs = self.export_jobs(arr, mem, look)
        worker = _Prepare(source, jobs, self)
        if self._export_threaded:
            dlg = QProgressDialog("Rendering the frames…", "Cancel", 0, len(jobs), self)
            dlg.setWindowModality(Qt.WindowModal)
            dlg.setMinimumDuration(300)
            worker.step.connect(lambda k, n: dlg.setValue(k))
            dlg.canceled.connect(lambda: setattr(worker, "cancelled", True))
            from PySide6.QtCore import QEventLoop
            loop = QEventLoop()
            worker.finished.connect(loop.quit)
            worker.start()
            loop.exec()
            dlg.close()
        else:
            worker.run()
        if worker.cancelled:
            return []
        out = cr.render(arr, mem, look, source, self.history.seed)
        base, _ext = os.path.splitext(path)
        pages = cr.split_posts(out, arr.posts)
        written = []
        for k, page in enumerate(pages):
            name = f"{base}_{k + 1}.jpg" if len(pages) > 1 else base + ".jpg"
            if cr.save_jpeg(page, name, 95):
                written.append(name)
        self._report_export(written, worker.failed)
        return written

    def _report_export(self, written, failed):
        if not self._export_threaded:
            return
        msg = (f"Wrote {len(written)} file{'s' if len(written) != 1 else ''}"
               + (f" to {os.path.dirname(written[0])}." if written else "."))
        if failed:
            msg += f" {len(failed)} frame{'s' if len(failed) != 1 else ''} couldn't be rendered."
        QMessageBox.information(self, "Export Collage", msg)
