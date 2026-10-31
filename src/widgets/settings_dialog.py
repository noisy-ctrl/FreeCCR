"""
DaVinci-style Settings dialog — a left category sidebar + a right content pane +
a footer. The first category, **Color Management**, consolidates the input camera
profile (ICC / DCP), the IT8 camera-profile wizard, and the global Positive-mode
toggle that used to live in the File menu / thumbnail panel.

UI only: it reuses MainWindow's existing colour handlers (file pickers, backend
apply, re-decode). The three global TOGGLES (Positive mode, 3-way RGB merge,
Density inversion) are STAGED — changing a checkbox does nothing until **Done**
is pressed, and closing/Escape discards the staged change (reverts to the state
the dialog opened with). Camera-profile import/delete/IT8 are immediate file
operations (they have their own dialogs). See spec/settings-page.md.
"""
import os

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QGroupBox, QSlider,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QScrollArea, QSpinBox,
    QStackedWidget, QVBoxLayout, QWidget,
)

from core import color_management
from core.awb import AWB_ALGORITHMS, AWB_DEFAULT
from core.ccr_backend import ccr_backend
from ui import theme


class _WheelScrollsPage(QObject):
    """Stops a hovered combo/spinbox/slider from eating the mouse wheel: the
    event is handed to the enclosing page's scrollbar instead, so the page
    scrolls and the value under the cursor never changes."""

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Wheel:
            return False
        area = obj.parentWidget()
        while area is not None and not isinstance(area, QScrollArea):
            area = area.parentWidget()
        if area is not None:
            QApplication.sendEvent(area.verticalScrollBar(), event)
        return True


class SettingsDialog(QDialog):
    """Modal settings dialog. Categories on the left, the selected page on the
    right, a single Done button in the footer. The global toggles are staged and
    applied on Done (accept); closing without Done discards them."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self._mw = main_window
        self.setWindowTitle("Settings")
        self.setModal(True)

        scr = self.screen() or QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr is not None else None
        w, h = 1000, 760
        if avail is not None:
            w = min(w, avail.width() - 60)
            h = min(h, avail.height() - 80)
        self.resize(w, h)
        self.setMinimumSize(min(560, w), min(420, h))

        root = QVBoxLayout(self)
        theme.apply_panel_spacing(root)

        body = QHBoxLayout()
        body.setSpacing(theme.GAP_PANEL)
        self._sidebar = QListWidget()
        self._sidebar.setFixedWidth(170)
        self._sidebar.currentRowChanged.connect(self._stack_set)
        body.addWidget(self._sidebar)
        self._stack = QStackedWidget()
        body.addWidget(self._stack, 1)
        root.addLayout(body, 1)

        self._add_category("General", self._build_general_page())
        self._add_category("Color Management", self._build_color_management_page())
        # Settings -> Panel (spec/panel-visibility.md); only with a real panel.
        self._panel_checks = {}
        if getattr(main_window, "sliders_panel", None) is not None and hasattr(
                main_window.sliders_panel, "HIDEABLE_SECTIONS"):
            self._add_category("Panel", self._build_panel_page())

        # Wheel scrolls the page, never the value under the cursor.
        self._wheel_filter = _WheelScrollsPage(self)
        for cls in (QComboBox, QAbstractSpinBox, QSlider):
            for wdg in self._stack.findChildren(cls):
                wdg.setFocusPolicy(Qt.StrongFocus)
                wdg.installEventFilter(self._wheel_filter)

        root.addWidget(theme.section_separator())
        footer = QHBoxLayout()
        theme.apply_button_row(footer)
        footer.addStretch(1)
        done = QPushButton("Done")
        theme.style_button(done, "primary", default=True)
        done.clicked.connect(self.accept)
        footer.addWidget(done)
        root.addLayout(footer)

        self._sidebar.setCurrentRow(0)
        theme.apply_windows_dark_titlebar(self)
        self.refresh_color_management()
        self._init_toggles()        # seed staged toggles from the live backend

    def _stack_set(self, i):
        if i >= 0:
            self._stack.setCurrentIndex(i)

    def _add_category(self, name: str, page: QWidget):
        QListWidgetItem(name, self._sidebar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self._stack.addWidget(scroll)

    # ------------------------------------------------------------------ #
    # General page
    # ------------------------------------------------------------------ #
    def _build_general_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        theme.apply_panel_spacing(lay, spacing=theme.GAP_SECTION)

        grp = QGroupBox("Exposure")
        g = QVBoxLayout(grp)
        g.setSpacing(theme.GAP_ROW)
        self._cb_auto_gain = QCheckBox("Auto gain")
        g.addWidget(self._cb_auto_gain)
        g.addWidget(self._muted(
            "Automatically place the brightest highlights at the top of the "
            "working range without moving the Master Gain slider — the top 0.1% "
            "of the in-range highlights are set to 99.8% of full. Pixels outside "
            "the sampled clear/dense film range are ignored. Turn off to control "
            "exposure with Master Gain alone."))
        self._cb_auto_tone = QCheckBox("Auto tone (keep shadow and highlight detail)")
        g.addWidget(self._cb_auto_tone)
        g.addWidget(self._muted(
            "When a lot of a frame would go to pure black or pure white, roll "
            "the shadows and highlights into the range instead: a gentle toe "
            "and shoulder measured for each frame when it is converted. A "
            "frame that loses little is left as it is."))
        lay.addWidget(grp)

        grp_conv = QGroupBox("Conversion")
        gc = QVBoxLayout(grp_conv)
        gc.setSpacing(theme.GAP_ROW)
        self._cb_auto_wp = QCheckBox("Auto white point (per frame)")
        gc.addWidget(self._cb_auto_wp)
        gc.addWidget(self._muted(
            "New frames start on \"Frame highlight\": Convert makes the "
            "frame's brightest near-grey area its white point, as sampling the "
            "White Point on a white highlight does, and checks it against the "
            "rest of the roll so a sunset or a blue sky keeps its colour. Off: "
            "new frames start with no white point. Each frame can be changed "
            "under Film B/W Point → White Point."))
        self._cb_warn_no_anchor = QCheckBox(
            "Warn when converting without a black point")
        gc.addWidget(self._cb_warn_no_anchor)
        gc.addWidget(self._muted(
            "Converting with no sampled film base uses fixed density constants "
            "instead of measuring your film — the frame keeps its own cast, and "
            "you grade it with Channel Levels. Turn this off to skip the "
            "confirmation and convert straight away."))
        # Convert, then crop; nothing clips (spec/convert-crop-and-clip.md)
        self._cb_crop_after = QCheckBox("Crop each frame after converting it")
        gc.addWidget(self._cb_crop_after)
        gc.addWidget(self._muted(
            "Convert finds the frame the way Auto Crop does, measures the "
            "conversion inside it, then crops to it (one undo step). Only when "
            "auto crop is sure, and never over a crop you drew or confirmed."))
        row = QHBoxLayout()
        row.addWidget(QLabel("Highlights and shadows"))
        self._combo_clip_mode = QComboBox()
        from core.clip_guard import LABELS as _CLIP_LABELS, MODES as _CLIP_MODES
        for mode in _CLIP_MODES:
            self._combo_clip_mode.addItem(_CLIP_LABELS[mode], mode)
        row.addWidget(self._combo_clip_mode, 1)
        gc.addLayout(row)
        self._clip_note = self._muted(
            "How Convert treats the ends of each frame. Roll off: brightness "
            "as before, with the brightest and darkest parts eased into the "
            "range so no part of the picture is pure white or black. White at "
            "the brightest point: the frame's brightest part becomes white, so "
            "nothing clips, but a lamp or a reflection makes the frame darker "
            "and flatter. Works through Auto tone (above). New conversions "
            "only.")
        gc.addWidget(self._clip_note)
        self._cb_auto_tone.toggled.connect(self._combo_clip_mode.setEnabled)
        lay.addWidget(grp_conv)

        # --- Auto crop learning (spec/auto-crop-learning.md) ------------- #
        grp_crop = QGroupBox("Auto crop")
        gcr = QVBoxLayout(grp_crop)
        gcr.setSpacing(theme.GAP_ROW)
        self._cb_crop_learn = QCheckBox("Learn from my crops")
        gcr.addWidget(self._cb_crop_learn)
        gcr.addWidget(self._muted(
            "When you press Done in the Crop panel on a frame auto crop has "
            "looked at, it remembers that frame's size, position and angle for "
            "your setup (camera and scan size), and how you moved each edge. "
            "Next time it expects your frame and moves the edges the way you "
            "do. Only box geometry is saved, on this computer. Turning this off "
            "stops using it without forgetting it."))
        forget_row = QHBoxLayout()
        forget_row.setSpacing(theme.GAP_BTN)
        self._crop_memory_label = QLabel("")
        forget_row.addWidget(self._crop_memory_label, 1)
        self._btn_forget_crops = QPushButton("Forget learned crops")
        self._btn_forget_crops.clicked.connect(self._on_forget_crops)
        forget_row.addWidget(self._btn_forget_crops)
        gcr.addLayout(forget_row)
        self._refresh_crop_memory_label()
        lay.addWidget(grp_crop)

        # --- Input colour space (non-RAW / TIFF decode space) ------------ #
        from core.input_transfer import TRANSFER_CHOICES
        grp_in = QGroupBox("Input colour space")
        gi = QVBoxLayout(grp_in)
        gi.setSpacing(theme.GAP_ROW)
        self._cb_tiff_ask = QCheckBox(
            "Ask how to read TIFF colour space on import")
        gi.addWidget(self._cb_tiff_ask)
        gi.addWidget(self._muted(
            "FreeCCR's conversion measures optical density, which assumes linear "
            "data — but a scanner TIFF is often gamma-encoded. When this is on, "
            "an import containing TIFFs asks whether to use each file's own "
            "metadata, read them as linear, or force one space."))
        mode_row = QHBoxLayout()
        mode_row.setSpacing(theme.GAP_BTN)
        mode_row.addWidget(QLabel("When not asking:"))
        self._combo_tiff_mode = QComboBox()
        self._combo_tiff_mode.addItem("Use each file's metadata", "embedded")
        self._combo_tiff_mode.addItem("Read as linear", "linear")
        self._combo_tiff_mode.addItem("Specify manually", "manual")
        mode_row.addWidget(self._combo_tiff_mode, 1)
        gi.addLayout(mode_row)
        manual_row = QHBoxLayout()
        manual_row.setSpacing(theme.GAP_BTN)
        manual_row.addWidget(QLabel("Manual space:"))
        self._combo_tiff_manual = QComboBox()
        for value, label in TRANSFER_CHOICES:
            self._combo_tiff_manual.addItem(label, value)
        manual_row.addWidget(self._combo_tiff_manual, 1)
        gi.addLayout(manual_row)
        gi.addWidget(self._muted(
            "Applies to the next import. Images already loaded keep the space "
            "they were imported with."))
        self._combo_tiff_mode.currentIndexChanged.connect(self._sync_tiff_rows)
        lay.addWidget(grp_in)

        grp_gamma = QGroupBox("Gamma")
        gg = QVBoxLayout(grp_gamma)
        gg.setSpacing(theme.GAP_ROW)
        self._cb_gamma_lum = QCheckBox("Hue-preserving Gamma (apply to luminance only)")
        gg.addWidget(self._cb_gamma_lum)
        gg.addWidget(self._muted(
            "Apply the Gamma slider to luminance and scale the colour channels "
            "together, so midtone adjustments don't shift hue or saturation. Off "
            "(default) applies Gamma per channel — the standard look, which adds a "
            "little saturation as it brightens/darkens. Highlights that would "
            "exceed white still clip."))
        lay.addWidget(grp_gamma)

        grp_wb = QGroupBox("White balance")
        gw = QVBoxLayout(grp_wb)
        gw.setSpacing(theme.GAP_ROW)
        self._cb_auto_awb = QCheckBox("Auto white balance after conversion")
        gw.addWidget(self._cb_auto_awb)
        gw.addWidget(self._muted(
            "When a negative is converted, automatically estimate the color "
            "cast and set the Temperature/Tint sliders — only for images with "
            "no white balance already set, and not for frames converted with a "
            "white point (it already sets the neutral). The AWB button applies "
            "the same estimate on demand."))
        algo_row = QHBoxLayout()
        algo_row.setSpacing(theme.GAP_ROW)
        algo_row.addWidget(QLabel("Algorithm"))
        self._combo_awb_algo = QComboBox()
        for algo_id, label in AWB_ALGORITHMS:
            self._combo_awb_algo.addItem(label, algo_id)
        algo_row.addWidget(self._combo_awb_algo, 1)
        gw.addLayout(algo_row)
        gw.addWidget(self._muted(
            "Gray World (default) assumes the scene averages to gray. White "
            "Patch balances on the brightest content. Shades of Gray weights "
            "bright areas more than Gray World. Gray Edge balances on edge "
            "colors — robust when large areas share one color."))
        lay.addWidget(grp_wb)

        grp_border = QGroupBox("Film border")
        gb = QVBoxLayout(grp_border)
        gb.setSpacing(theme.GAP_ROW)
        self._cb_sprocket = QCheckBox(
            "White sprocket holes / clear film (reversal look)")
        gb.addWidget(self._cb_sprocket)
        gb.addWidget(self._muted(
            "After you set a Black Point (film base), paint the sprocket holes "
            "and clear film border white instead of black — the reversal-film "
            "look. The mask is built from areas brighter than the film base, "
            "cleaned up and feathered, and applied last on both preview and "
            "export, so no adjustment tints it. B/W-point conversions only."))
        lay.addWidget(grp_border)

        grp_zoom = QGroupBox("Preview")
        gz = QVBoxLayout(grp_zoom)
        gz.setSpacing(theme.GAP_ROW)
        # Preview detail (spec/preview-detail.md): one choice in place of the
        # old "full resolution at 100%" and "sharp preview" checkboxes.
        row = QHBoxLayout()
        row.addWidget(QLabel("Preview detail"))
        self._combo_preview_detail = QComboBox()
        for label, level in (("Full", "full"), ("Balanced", "balanced"),
                             ("Fast", "fast")):
            self._combo_preview_detail.addItem(label, level)
        row.addWidget(self._combo_preview_detail, 1)
        gz.addLayout(row)
        gz.addWidget(self._muted(
            "How much detail the preview renders after each edit. It changes "
            "only what you see while editing, never the export or the colours.\n"
            "Full: a screen-sized render at the fitted view, and real source "
            "pixels at 100% zoom.\n"
            "Balanced: the screen render is capped at 2000 px and zoom at "
            "6000 px, about half the work of Full when zoomed in.\n"
            "Fast: the 1080 px preview only, and zoom detail from the half-size "
            "decode. Least memory and time; best for a slower computer."))
        # False colour's scale (spec/false-colour.md, stop zones): also on
        # the toolbar button's right-click menu; both are remembered.
        row = QHBoxLayout()
        row.addWidget(QLabel("False colour"))
        self._combo_fc_scale = QComboBox()
        for label, key in (("Exposure bands (signal level)", "bands"),
                           ("Stop zones (stops from mid grey)", "zones")):
            self._combo_fc_scale.addItem(label, key)
        row.addWidget(self._combo_fc_scale, 1)
        gz.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Stop zones read"))
        self._combo_zone_source = QComboBox()
        for label, key in (("The negative (its exposure on the film)", "negative"),
                           ("The finished picture", "picture")):
            self._combo_zone_source.addItem(label, key)
        row.addWidget(self._combo_zone_source, 1)
        gz.addLayout(row)
        gz.addWidget(self._muted(
            "Stop zones colour the picture by stops from mid grey, 15 zones from "
            "\u22126 to +6. Read from the negative they show how the film was "
            "exposed (density above the film base, mid grey at 0.70), whatever "
            "the grade; read from the finished picture they follow the grade, "
            "but white is only about +2\u00bd stops there. Positive mode always "
            "reads the picture."))
        lay.addWidget(grp_zoom)

        # Details (sharpening) defaults: spec/details-defaults.md
        from core import detail_defaults
        grp_det = QGroupBox("Details")
        gd = QVBoxLayout(grp_det)
        gd.setSpacing(theme.GAP_ROW)
        row = QHBoxLayout()
        row.addWidget(QLabel("Sharpening starts at"))
        self._spin_details = {}
        for key in detail_defaults.KEYS:
            row.addSpacing(theme.GAP_BTN)
            row.addWidget(QLabel(detail_defaults.LABELS[key]))
            sp = QSpinBox()
            sp.setRange(0, 100)
            sp.setToolTip(f"Sharpen {detail_defaults.LABELS[key]} for an image that "
                          f"hasn't set its own (factory {detail_defaults.FACTORY[key]}).")
            self._spin_details[key] = sp
            row.addWidget(sp)
        row.addStretch(1)
        gd.addLayout(row)
        gd.addWidget(self._muted(
            "The Details section's values for every image that hasn't set its "
            "own sharpening; a frame you have changed keeps its values. Radius "
            "25 is one pixel at full size, so judge it at 100% zoom."))
        lay.addWidget(grp_det)

        grp_keys = QGroupBox("Keyboard")
        gk = QVBoxLayout(grp_keys)
        gk.setSpacing(theme.GAP_ROW)
        self._cb_balance_hotkeys = QCheckBox(
            "Channel Balance nudge keys (U / I / O and J / K / L)")
        gk.addWidget(self._cb_balance_hotkeys)
        gk.addWidget(self._muted(
            "U, I and O raise the R, G and B Balance sliders; J, K and L lower "
            "them. Off by default — Channel Balance is a collapsed section, so "
            "these would edit sliders you cannot see, and they claim six single "
            "letters. While off the keys are not used at all."))
        lay.addWidget(grp_keys)

        # File -> Live Capture Monitor (spec/live-capture-monitor.md §3.7)
        grp_live = QGroupBox("Live capture")
        gl = QVBoxLayout(grp_live)
        gl.setSpacing(theme.GAP_ROW)
        row = QHBoxLayout()
        row.addWidget(QLabel("Turn the Scanlight off when idle after"))
        self._spin_live_idle = QSpinBox()
        self._spin_live_idle.setRange(0, 60)
        self._spin_live_idle.setSuffix(" min")
        self._spin_live_idle.setSpecialValueText("never")
        row.addWidget(self._spin_live_idle)
        row.addStretch(1)
        gl.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Video format"))
        self._combo_live_format = QComboBox()
        for label, key in (("Automatic", "auto"), ("Prefer uncompressed", "raw"),
                           ("Prefer MJPEG", "mjpeg")):
            self._combo_live_format.addItem(label, key)
        row.addWidget(self._combo_live_format, 1)
        gl.addLayout(row)
        self._cb_live_ask_tether = QCheckBox(
            "Ask about Hot Folder Tether when the monitor opens")
        gl.addWidget(self._cb_live_ask_tether)
        gl.addWidget(self._muted(
            "Dimming: the lit channels breathe low while nothing is used, and come "
            "back when you move the pointer over the monitor, press a key, or the "
            "picture changes. The video format applies from the next camera "
            "opened; MJPEG's compression can hide or fake fine detail while "
            "focusing."))
        lay.addWidget(grp_live)

        lay.addStretch(1)
        return page

    def _refresh_crop_memory_label(self):
        try:
            from core.crop_memory import memory
            n, setups = memory().counts()
        except Exception:
            n, setups = 0, 0
        if n:
            self._crop_memory_label.setText(
                f"{n} confirmed crop{'s' if n != 1 else ''} learned on "
                f"{setups} setup{'s' if setups != 1 else ''}.")
        else:
            self._crop_memory_label.setText("Nothing learned yet.")
        self._btn_forget_crops.setEnabled(n > 0)

    def _on_forget_crops(self):
        """Immediate (not staged): asks first, then clears the crop memory."""
        if QMessageBox.question(
                self, "Forget learned crops",
                "Forget every crop auto crop has learned, on all setups? "
                "It goes back to finding frames on its own.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        from core.crop_memory import memory
        memory().forget()
        self._refresh_crop_memory_label()

    # ------------------------------------------------------------------ #
    # Color Management page
    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # Panel page (spec/panel-visibility.md)
    # ------------------------------------------------------------------ #
    def _build_panel_page(self) -> QWidget:
        panel = self._mw.sliders_panel
        page = QWidget()
        lay = QVBoxLayout(page)
        theme.apply_panel_spacing(lay, spacing=theme.GAP_SECTION)
        grp = QGroupBox("Show in the editing panel")
        g = QVBoxLayout(grp)
        g.setSpacing(theme.GAP_ROW)
        hidden = panel.hidden_sections()
        for sid, label, _attr in panel.HIDEABLE_SECTIONS:
            cb = QCheckBox(label)
            cb.setChecked(sid not in hidden)
            g.addWidget(cb)
            self._panel_checks[sid] = cb
        g.addWidget(self._muted(
            "Hiding a tool only removes it from the panel. Any values it already "
            "has on an image keep applying, so hide tools you leave at their "
            "defaults. Master Gain, the tone sliders and the black/white point "
            "controls are always shown."))
        lay.addWidget(grp)
        lay.addStretch(1)
        return page

    def _staged_hidden_sections(self):
        return {sid for sid, cb in self._panel_checks.items() if not cb.isChecked()}

    def _muted(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        return lbl

    def _sync_tiff_rows(self, *_):
        """The manual-space combo only applies to the 'Specify manually' default."""
        self._combo_tiff_manual.setEnabled(
            self._combo_tiff_mode.currentData() == "manual")

    def _build_color_management_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        theme.apply_panel_spacing(lay, spacing=theme.GAP_SECTION)

        # --- Camera-profile library ------------------------------------ #
        grp = QGroupBox("Camera profiles")
        g = QVBoxLayout(grp)
        g.setSpacing(theme.GAP_ROW)
        g.addWidget(self._muted(
            "Your imported and IT8-generated profiles, kept in FreeCCR's workspace "
            "(they survive updates). Pick the active one from the “Camera profile” "
            "dropdown above the thumbnails; manage which to keep here."))

        self._status = QLabel("Active: None")
        self._status.setStyleSheet(theme.section_header_qss())
        g.addWidget(self._status)

        self._profile_list = QListWidget()
        # Cap the height so the list stays compact (it otherwise expands to fill
        # the pane and pushes the Negative-conversion toggles below the fold).
        self._profile_list.setMinimumHeight(72)
        self._profile_list.setMaximumHeight(110)
        g.addWidget(self._profile_list)

        row = QHBoxLayout()
        theme.apply_button_row(row)
        btn_import = QPushButton("Import profile…")
        btn_import.clicked.connect(self._import)
        self._btn_delete = QPushButton("Delete")
        theme.style_button(self._btn_delete, "danger")
        self._btn_delete.clicked.connect(self._delete)
        row.addWidget(btn_import)
        row.addWidget(self._btn_delete)
        row.addStretch(1)
        g.addLayout(row)

        g.addWidget(theme.section_separator())
        it8_row = QHBoxLayout()
        theme.apply_button_row(it8_row)
        btn_it8 = QPushButton("Create Camera Profile from IT8…")
        btn_it8.clicked.connect(self._create_it8)
        it8_row.addWidget(btn_it8)
        it8_row.addStretch(1)
        g.addLayout(it8_row)
        g.addWidget(self._muted(
            "Build a camera ICC or DCP from a photographed IT8 calibration chart; "
            "it is added to the library above."))
        lay.addWidget(grp)

        # --- Field correction (flat-field) ----------------------------- #
        grp_ff = QGroupBox("Field correction")
        gf = QVBoxLayout(grp_ff)
        gf.setSpacing(theme.GAP_ROW)
        gf.addWidget(self._muted(
            "Corrects the dark corners and colour shading your lens, sensor and "
            "light source put into every frame, measured from one shot of an "
            "evenly lit blank surface. Applied to every decode before the "
            "negative conversion."))
        self._field_entries = []            # library rows, filled on refresh
        ff_row = QHBoxLayout()
        ff_row.setSpacing(theme.GAP_BTN)
        ff_row.addWidget(QLabel("Active:"))
        self._combo_field = QComboBox()
        self._combo_field.currentIndexChanged.connect(self._field_selected)
        ff_row.addWidget(self._combo_field, 1)
        gf.addLayout(ff_row)
        self._field_detail = self._muted("")
        gf.addWidget(self._field_detail)
        ff_btns = QHBoxLayout()
        theme.apply_button_row(ff_btns)
        btn_create = QPushButton("Create Field Correction Profile…")
        btn_create.clicked.connect(self._create_field)
        self._btn_field_delete = QPushButton("Delete")
        theme.style_button(self._btn_field_delete, "danger")
        self._btn_field_delete.clicked.connect(self._delete_field)
        ff_btns.addWidget(btn_create)
        ff_btns.addWidget(self._btn_field_delete)
        ff_btns.addStretch(1)
        gf.addLayout(ff_btns)
        lay.addWidget(grp_ff)

        # --- Negative conversion --------------------------------------- #
        grp2 = QGroupBox("Negative conversion")
        g2 = QVBoxLayout(grp2)
        g2.setSpacing(theme.GAP_ROW)
        self._cb_positive = QCheckBox(
            "Positive mode (decode RAWs as positives, skip film inversion)")
        g2.addWidget(self._cb_positive)
        g2.addWidget(self._muted(
            "Treat RAWs as ready positive photos instead of film negatives. The "
            "same toggle is on the thumbnail panel."))

        g2.addWidget(theme.section_separator())
        self._cb_density = QCheckBox(
            "Density inversion (recover optical density for clear+dense sampling)")
        g2.addWidget(self._cb_density)
        g2.addWidget(self._muted(
            "When you sample both a clear and a dense point, invert using film "
            "density (log) instead of a linear stretch. Two-point conversions "
            "only — and they look darker."))
        lay.addWidget(grp2)

        # --- Monochrome ------------------------------------------------ #
        grp_mono = QGroupBox("Monochrome")
        gm = QVBoxLayout(grp_mono)
        gm.setSpacing(theme.GAP_ROW)
        self._cb_mono_raw = QCheckBox(
            "Interpret RAW as monochrome (no demosaic)")
        gm.addWidget(self._cb_mono_raw)
        gm.addWidget(self._muted(
            "Read every photosite as one luminance sample at full sensor "
            "resolution, ignoring the colour-filter pattern the RAW declares — "
            "for monochrome sensors, including mono-converted cameras whose RAW "
            "still reports a colour filter. On a colour sensor it shows a "
            "checkerboard. Loaded RAWs are re-decoded (your adjustments are "
            "kept, the conversion is dropped). Monochrome images export as "
            "single-channel greyscale TIFF/JPEG."))
        lay.addWidget(grp_mono)

        # --- Trichrome (3-way RGB-light) capture ----------------------- #
        grp3 = QGroupBox("Trichrome capture")
        g3 = QVBoxLayout(grp3)
        g3.setSpacing(theme.GAP_ROW)
        self._cb_rgb_merge = QCheckBox(
            "3-way RGB merge (combine red/green/blue-light exposures)")
        g3.addWidget(self._cb_rgb_merge)
        g3.addWidget(self._muted(
            "Shoot a static scene three times under pure red, then green, then "
            "blue light. On your NEXT import, every 3 RAWs (sorted by filename) "
            "are merged into one colour image — each frame contributes only its "
            "own channel — then converted as a negative. "
            "RAW only (Bayer or monochrome); the selected count must be a "
            "multiple of 3. "
            "Applies to the next import only. Your edits to a merged image are "
            "saved and restored the next time you merge the same three frames."))
        detail_row = QHBoxLayout()
        detail_row.setSpacing(theme.GAP_BTN)
        detail_row.addWidget(QLabel("Merge detail:"))
        self._combo_merge_detail = QComboBox()
        self._combo_merge_detail.addItem("Demosaic (full resolution)", "demosaic")
        self._combo_merge_detail.addItem(
            "Single photosite (half resolution)", "photosite")
        self._combo_merge_detail.addItem(
            "Monochrome (full resolution, no demosaic)", "mono")
        detail_row.addWidget(self._combo_merge_detail, 1)
        g3.addLayout(detail_row)
        g3.addWidget(self._muted(
            "Demosaic interpolates each frame's own channel from its own "
            "photosites (bilinear — still no channel mixing) at full sensor "
            "resolution. Single photosite reads only the raw colour sites — "
            "no interpolation at all — at half resolution. Monochrome reads "
            "every photosite as luminance with no demosaic — for monochrome "
            "sensors, including mono-converted cameras whose RAW still reports "
            "a colour filter; on a colour sensor it shows a checkerboard. "
            "Applies to the next import."))
        self._cb_rgb_replace = QCheckBox(
            "Replace originals with linear TIFF on import")
        g3.addWidget(self._cb_rgb_replace)
        g3.addWidget(self._muted(
            "After a successful merge import, each merged frame is written as a "
            "full-resolution 16-bit linear TIFF next to its red source, then the "
            "three original RAWs are PERMANENTLY DELETED and the list reloads "
            "from the TIFFs. You confirm before anything is deleted. The "
            "merge/demosaic choice is baked in and can't be changed afterwards. "
            "The generated TIFFs reopen normally even with 3-way merge left on. "
            "Requires 3-way merge to be on."))
        lay.addWidget(grp3)

        lay.addStretch(1)
        return page

    # ------------------------------------------------------------------ #
    # Profile actions — delegate to MainWindow (file pickers, backend,
    # re-decode); these apply IMMEDIATELY, then the page refreshes its status.
    # (The global toggles below are staged — applied on Done, not here.)
    # ------------------------------------------------------------------ #
    def _import(self):
        self._mw.import_camera_profile_dialog()
        self.refresh_color_management()

    def _delete(self):
        item = self._profile_list.currentItem()
        if item is None:
            return
        self._mw.delete_camera_profile(item.data(Qt.UserRole))
        self.refresh_color_management()

    def _create_it8(self):
        self._mw.create_camera_profile_from_it8()
        self.refresh_color_management()

    # --- Field correction: immediate, like the camera-profile selection ---- #
    def _create_field(self):
        self._mw.create_field_correction_profile()
        self.refresh_color_management()

    def _delete_field(self):
        path = self._combo_field.currentData()
        if not path:
            return
        self._mw.delete_field_profile(path)
        self.refresh_color_management()

    def _field_selected(self, _idx):
        """User picked from the combo — apply immediately. Guarded by a flag so
        repopulating the combo in refresh_color_management can't re-trigger it."""
        if getattr(self, "_loading_field_combo", False):
            return
        self._mw.set_active_field_profile(self._combo_field.currentData())
        self._update_field_detail()

    def _update_field_detail(self):
        path = self._combo_field.currentData()
        entry = next((p for p in self._field_entries if p["path"] == path), None)
        self._field_detail.setText(entry["summary"] if entry else "")
        self._btn_field_delete.setEnabled(bool(path))

    def refresh_field_correction(self):
        """Repopulate the field-profile combo from the library and reflect the
        active selection (without firing the selection handler)."""
        from core import flat_field
        active = flat_field.get_active_profile()
        active_path = active.path if active is not None else None
        self._field_entries = ccr_backend.list_field_profiles()
        self._loading_field_combo = True
        try:
            self._combo_field.clear()
            self._combo_field.addItem("None", None)
            for p in self._field_entries:
                self._combo_field.addItem(p["name"], p["path"])
            idx = self._combo_field.findData(active_path) if active_path else 0
            self._combo_field.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._loading_field_combo = False
        self._update_field_detail()

    # --- Staged toggles: apply on Done (accept), discard on close ---------- #
    def _init_toggles(self):
        """Seed the toggle checkboxes from the live backend once, at open. They
        are NOT re-synced afterwards (refresh_color_management only touches the
        profile UI) so a profile import/delete can't clobber a staged toggle."""
        for cb, val in ((self._cb_positive, ccr_backend.positive_mode),
                        (self._cb_rgb_merge, ccr_backend.rgb_merge_mode),
                        (self._cb_rgb_replace,
                         getattr(ccr_backend, "rgb_merge_replace", False)),
                        (self._cb_density, ccr_backend.density_bwpoint),
                        (self._cb_auto_gain, ccr_backend.auto_gain),
                        (self._cb_auto_tone, getattr(ccr_backend, "auto_tone", True)),
                        (self._cb_auto_wp,
                         getattr(ccr_backend, "auto_white_point", True)),
                        (self._cb_warn_no_anchor,
                         getattr(ccr_backend, "warn_no_anchor_convert", True)),
                        (self._cb_crop_after,
                         getattr(ccr_backend, "crop_after_convert", True)),
                        (self._cb_crop_learn,
                         getattr(ccr_backend, "auto_crop_learn", True)),
                        (self._cb_auto_awb, ccr_backend.auto_awb),
                        (self._cb_gamma_lum, ccr_backend.gamma_luminance),
                        (self._cb_sprocket, ccr_backend.sprocket_mask_white),
                        (self._cb_balance_hotkeys,
                         getattr(ccr_backend, "balance_hotkeys", False)),
                        (self._cb_mono_raw,
                         getattr(ccr_backend, "mono_raw", False))):
            cb.blockSignals(True)
            cb.setChecked(bool(val))
            cb.blockSignals(False)
        self._combo_merge_detail.blockSignals(True)
        self._combo_merge_detail.setCurrentIndex(
            self._combo_merge_detail.findData(self._backend_merge_detail()))
        self._combo_merge_detail.blockSignals(False)
        self._combo_clip_mode.blockSignals(True)
        self._combo_clip_mode.setCurrentIndex(max(0, self._combo_clip_mode.findData(
            getattr(ccr_backend, "clip_mode", "rolloff"))))
        self._combo_clip_mode.blockSignals(False)
        self._combo_clip_mode.setEnabled(self._cb_auto_tone.isChecked())
        self._combo_preview_detail.blockSignals(True)
        self._combo_preview_detail.setCurrentIndex(max(0,
            self._combo_preview_detail.findData(
                getattr(ccr_backend, "preview_detail", "full"))))
        self._combo_preview_detail.blockSignals(False)
        from core import detail_defaults
        for key, sp in self._spin_details.items():
            sp.blockSignals(True)
            sp.setValue(detail_defaults.get(key))
            sp.blockSignals(False)
        from PySide6.QtCore import QSettings
        _st = QSettings("FreeCCR", "FreeCCR")
        try:
            self._spin_live_idle.setValue(int(_st.value("live/idle_minutes", 5)))
        except (TypeError, ValueError):
            self._spin_live_idle.setValue(5)
        self._combo_live_format.setCurrentIndex(max(0, self._combo_live_format.findData(
            str(_st.value("live/video_format", "auto") or "auto"))))
        self._cb_live_ask_tether.setChecked(_st.value("live/ask_tether", True, type=bool))
        scale, source = self._live_fc_scale()
        for combo, value in ((self._combo_fc_scale, scale),
                             (self._combo_zone_source, source)):
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(value)))
            combo.blockSignals(False)
        # Input colour space: the ask toggle, the remembered default, and the
        # space used when that default is "manual".
        self._cb_tiff_ask.blockSignals(True)
        self._cb_tiff_ask.setChecked(
            bool(getattr(ccr_backend, "tiff_transfer_ask", True)))
        self._cb_tiff_ask.blockSignals(False)
        for combo, value in (
                (self._combo_tiff_mode,
                 getattr(ccr_backend, "tiff_transfer_mode", "linear")),
                (self._combo_tiff_manual,
                 getattr(ccr_backend, "tiff_transfer_manual", "srgb"))):
            combo.blockSignals(True)
            idx = combo.findData(value)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
        self._sync_tiff_rows()
        self._combo_awb_algo.blockSignals(True)
        idx = self._combo_awb_algo.findData(
            getattr(ccr_backend, "awb_algorithm", AWB_DEFAULT))
        self._combo_awb_algo.setCurrentIndex(
            idx if idx >= 0 else self._combo_awb_algo.findData(AWB_DEFAULT))
        self._combo_awb_algo.blockSignals(False)

    def _live_fc_scale(self):
        ip = getattr(self._mw, "image_preview", None)
        if ip is not None and hasattr(ip, "fc_scale"):
            return ip.fc_scale()
        return "bands", "negative"

    @staticmethod
    def _backend_merge_detail() -> str:
        """The live Merge detail mode as the combo's data string. Monochrome
        overrides the demosaic flag. See spec/trichrome-mono-read.md."""
        if getattr(ccr_backend, "rgb_merge_mono", False):
            return "mono"
        return ("demosaic" if getattr(ccr_backend, "rgb_merge_demosaic", True)
                else "photosite")

    def _apply_pending(self):
        """Apply only the toggles whose checkbox differs from the live backend
        value. Each MainWindow handler persists + reprocesses as needed; calling
        them only on change avoids spurious reprocess passes. Positive first (it
        re-decodes), then merge, then density (replays on the new decode)."""
        if bool(self._cb_positive.isChecked()) != bool(ccr_backend.positive_mode):
            self._mw.on_positive_mode_toggled(bool(self._cb_positive.isChecked()))
        if bool(self._cb_rgb_merge.isChecked()) != bool(ccr_backend.rgb_merge_mode):
            self._mw.on_rgb_merge_mode_toggled(bool(self._cb_rgb_merge.isChecked()))
        staged_detail = self._combo_merge_detail.currentData()
        if staged_detail != self._backend_merge_detail():
            self._mw.on_rgb_merge_detail_changed(staged_detail)
        if (bool(self._cb_rgb_replace.isChecked())
                != bool(getattr(ccr_backend, "rgb_merge_replace", False))):
            self._mw.on_rgb_merge_replace_toggled(
                bool(self._cb_rgb_replace.isChecked()))
        if bool(self._cb_density.isChecked()) != bool(ccr_backend.density_bwpoint):
            self._mw.on_density_bwpoint_toggled(bool(self._cb_density.isChecked()))
        if bool(self._cb_auto_gain.isChecked()) != bool(ccr_backend.auto_gain):
            self._mw.on_auto_gain_toggled(bool(self._cb_auto_gain.isChecked()))
        if (bool(self._cb_auto_tone.isChecked())
                != bool(getattr(ccr_backend, "auto_tone", True))):
            self._mw.on_auto_tone_toggled(bool(self._cb_auto_tone.isChecked()))
        if (bool(self._cb_auto_wp.isChecked())
                != bool(getattr(ccr_backend, "auto_white_point", True))):
            self._mw.on_auto_white_point_toggled(bool(self._cb_auto_wp.isChecked()))
        if (bool(self._cb_warn_no_anchor.isChecked())
                != bool(getattr(ccr_backend, "warn_no_anchor_convert", True))):
            self._mw.on_warn_no_anchor_toggled(
                bool(self._cb_warn_no_anchor.isChecked()))
        if (bool(self._cb_crop_after.isChecked())
                != bool(getattr(ccr_backend, "crop_after_convert", True))):
            self._mw.on_crop_after_convert_toggled(bool(self._cb_crop_after.isChecked()))
        mode = self._combo_clip_mode.currentData()
        if mode and mode != getattr(ccr_backend, "clip_mode", "rolloff"):
            self._mw.on_clip_mode_changed(mode)
        if (bool(self._cb_crop_learn.isChecked())
                != bool(getattr(ccr_backend, "auto_crop_learn", True))):
            self._mw.on_auto_crop_learn_toggled(bool(self._cb_crop_learn.isChecked()))
        # Resolution-only, display-only: no reprocess pass (spec/preview-detail.md).
        level = self._combo_preview_detail.currentData()
        if level and level != getattr(ccr_backend, "preview_detail", "full"):
            self._mw.on_preview_detail_changed(level)
        from core import detail_defaults
        staged_det = {k: sp.value() for k, sp in self._spin_details.items()}
        if staged_det != detail_defaults.current():
            self._mw.on_detail_defaults_changed(staged_det)
        # Input colour space — affects the NEXT import only, so nothing is
        # re-decoded here (spec/input-transfer-function.md §4.2).
        if (bool(self._cb_tiff_ask.isChecked())
                != bool(getattr(ccr_backend, "tiff_transfer_ask", True))):
            self._mw.on_tiff_transfer_ask_toggled(
                bool(self._cb_tiff_ask.isChecked()))
        staged_tiff_mode = self._combo_tiff_mode.currentData()
        if staged_tiff_mode != getattr(ccr_backend, "tiff_transfer_mode", "linear"):
            self._mw.on_tiff_transfer_mode_changed(staged_tiff_mode)
        staged_tiff_manual = self._combo_tiff_manual.currentData()
        if staged_tiff_manual != getattr(ccr_backend, "tiff_transfer_manual", "srgb"):
            self._mw.on_tiff_transfer_manual_changed(staged_tiff_manual)
        if bool(self._cb_auto_awb.isChecked()) != bool(ccr_backend.auto_awb):
            self._mw.on_auto_awb_toggled(bool(self._cb_auto_awb.isChecked()))
        staged_algo = self._combo_awb_algo.currentData()
        if staged_algo != getattr(ccr_backend, "awb_algorithm", AWB_DEFAULT):
            self._mw.on_awb_algorithm_changed(staged_algo)
        if bool(self._cb_gamma_lum.isChecked()) != bool(ccr_backend.gamma_luminance):
            self._mw.on_gamma_mode_toggled(bool(self._cb_gamma_lum.isChecked()))
        if bool(self._cb_sprocket.isChecked()) != bool(ccr_backend.sprocket_mask_white):
            self._mw.on_sprocket_mask_toggled(bool(self._cb_sprocket.isChecked()))
        if (bool(self._cb_balance_hotkeys.isChecked())
                != bool(getattr(ccr_backend, "balance_hotkeys", False))):
            self._mw.on_balance_hotkeys_toggled(
                bool(self._cb_balance_hotkeys.isChecked()))
        # Monochrome RAW interpretation re-decodes every loaded image, so it is
        # applied last — after the toggles that only affect the next import.
        if (bool(self._cb_mono_raw.isChecked())
                != bool(getattr(ccr_backend, "mono_raw", False))):
            self._mw.on_mono_raw_toggled(bool(self._cb_mono_raw.isChecked()))
        # False colour scale: display-only.
        staged_fc = (self._combo_fc_scale.currentData(),
                     self._combo_zone_source.currentData())
        if staged_fc != self._live_fc_scale():
            ip = getattr(self._mw, "image_preview", None)
            if ip is not None and hasattr(ip, "set_fc_scale"):
                ip.set_fc_scale(*staged_fc)
        # Live capture: read by the monitor when it needs them.
        from PySide6.QtCore import QSettings
        _st = QSettings("FreeCCR", "FreeCCR")
        _st.setValue("live/idle_minutes", int(self._spin_live_idle.value()))
        _st.setValue("live/video_format", self._combo_live_format.currentData() or "auto")
        _st.setValue("live/ask_tether", bool(self._cb_live_ask_tether.isChecked()))
        # Panel visibility: display-only, so no reprocess.
        if self._panel_checks:
            panel = self._mw.sliders_panel
            staged = self._staged_hidden_sections()
            if staged != panel.hidden_sections():
                panel.set_hidden_sections(staged)

    def accept(self):
        """Done: commit the staged toggles, then close. (Escape/close → reject,
        which never reaches here, so staged toggles are discarded.)"""
        self._apply_pending()
        super().accept()

    def refresh_color_management(self):
        """Reflect the live active profile, the library list, and Positive mode."""
        self.refresh_field_correction()
        icc = getattr(ccr_backend, "input_icc_name", None)
        dcp = getattr(ccr_backend, "input_dcp_name", None)
        if getattr(ccr_backend, "active_profile_path", None) == ccr_backend.CAMERA_MATRIX:
            self._status.setText("Active: Camera Matrix  (Adobe RGB)")
        elif dcp:
            self._status.setText(f"Active: {dcp}  (DCP)")
        elif icc:
            self._status.setText(f"Active: {icc}  (ICC)")
        else:
            self._status.setText("Active: None  (raw)")

        active = getattr(ccr_backend, "active_profile_path", None)
        active_n = os.path.normcase(os.path.abspath(active)) if active else None
        self._profile_list.clear()
        for p in ccr_backend.list_camera_profiles():
            label = f"{p['name']}  ({p['kind'].upper()})"
            if active_n and os.path.normcase(os.path.abspath(p["path"])) == active_n:
                label += "   ● active"
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, p["path"])
            self._profile_list.addItem(it)
        self._btn_delete.setEnabled(self._profile_list.count() > 0)
        # NOTE: the global toggles are intentionally NOT synced here — they are
        # staged (seeded once by _init_toggles, applied on Done). Re-syncing on
        # every profile refresh would wipe a staged toggle change.
