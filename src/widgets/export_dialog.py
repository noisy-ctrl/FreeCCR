"""
Export settings dialog: scope, destination, filename template with macros,
format + JPEG quality with live size estimate, resize-on-export, conflict
policy and persisted preferences.
"""
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QRadioButton, QSlider, QSpinBox, QVBoxLayout,
)
from PySide6.QtGui import QColor

from core.ccr_backend import ccr_backend
from core import export_estimator
from core import hdr_gainmap
from core import export_canvas
from ui import theme
from utils.export_naming import build_export_names, build_filename, resolve_conflicts
from utils.unicode_path_utils import normalize_unicode_path, validate_unicode_path


@dataclass
class ExportPlan:
    """Everything the export worker needs, resolved by the dialog."""
    items: List[Tuple[int, str]] = field(default_factory=list)  # (image idx, absolute output path)
    jpg_output: bool = False
    jpg_quality: int = 92
    max_long_side: Optional[int] = None  # None = keep original size
    output_colorspace: str = "srgb"  # "srgb" | "prophoto"
    open_folder: bool = False
    destination: str = ""
    skipped: int = 0  # files dropped by the "skip existing" policy
    # Trichrome only: write the raw channel combination as an untagged 16-bit
    # linear TIFF — no conversion/adjustments/geometry/colour management.
    # See spec/trichrome-linear-tiff-export.md.
    linear_merge: bool = False
    # "JPEG + HDR gain map": the headroom in stops, else None (a plain
    # format). See spec/hdr-gain-map-export.md.
    hdr_headroom: Optional[float] = None
    # Border / canvas: an export_canvas.Canvas, else None (no border).
    # See spec/export-canvas-border.md.
    canvas: Optional[object] = None


class ExportSettingsDialog(QDialog):
    """
    Modal export configuration dialog. On accept, `self.plan` holds the
    resolved ExportPlan; the caller runs the actual export worker.
    """

    def __init__(self, parent=None, current_idx=None, selected_indices=None,
                 default_scope=None):
        super().__init__(parent)
        self.setWindowTitle("Export")
        self.setModal(True)
        self.setMinimumWidth(theme.DIALOG_W_MD)
        theme.apply_windows_dark_titlebar(self)  # dark native title bar (Win10/11)

        self.plan: Optional[ExportPlan] = None
        self._current_idx = current_idx
        self._default_scope = default_scope
        # In Positive mode every loaded image is exportable (no conversion step);
        # otherwise only converted negatives are. (Variable names kept for churn,
        # but they now mean "exportable".)
        self._positive_mode = bool(getattr(ccr_backend, "positive_mode", False))

        def _exportable(img):
            return img.converted or self._positive_mode

        self._converted_indices = [idx for idx, img in enumerate(ccr_backend.images)
                                   if _exportable(img)]
        self._current_converted = (
            current_idx is not None
            and 0 <= current_idx < len(ccr_backend.images)
            and _exportable(ccr_backend.images[current_idx])
        )
        # Selected thumbnails that are exportable — non-exportable selections
        # are dropped here.
        converted_set = set(self._converted_indices)
        self._selected_converted = [i for i in (selected_indices or []) if i in converted_set]
        # Trichrome: the linear-merge format exports MERGED images (converted
        # or not — conversion is irrelevant to the raw channel combination).
        self._merged_indices = [idx for idx, img in enumerate(ccr_backend.images)
                                if getattr(img, "is_merged", False)]
        merged_set = set(self._merged_indices)
        self._current_merged = (
            current_idx is not None and current_idx in merged_set)
        self._selected_merged = [i for i in (selected_indices or []) if i in merged_set]
        self._bpp_cache = {}  # quality -> bytes per pixel sample

        self._settings = QSettings("FreeCCR", "FreeCCR")
        self._build_ui()
        self._restore_settings()
        self._update_example()
        self._schedule_estimate()

    # ---------- UI ----------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        theme.apply_panel_spacing(layout, margin=theme.SPACE_XL, spacing=theme.GAP_SECTION)

        # Scope
        scope_group = QGroupBox("Export scope")
        scope_layout = QVBoxLayout(scope_group)
        all_label = "All images" if self._positive_mode else "All converted images"
        self.scope_all_radio = QRadioButton(f"{all_label} ({len(self._converted_indices)})")
        self.scope_all_radio.setChecked(True)
        self.scope_current_radio = QRadioButton("Current image only")
        if not self._current_converted:
            self.scope_current_radio.setEnabled(False)
            self.scope_current_radio.setToolTip("The current image has not been converted yet.")
        self.scope_selected_radio = QRadioButton(f"Selected images ({len(self._selected_converted)})")
        if not self._selected_converted:
            self.scope_selected_radio.setEnabled(False)
            self.scope_selected_radio.setToolTip("No converted images are selected.")
        scope_layout.addWidget(self.scope_all_radio)
        scope_layout.addWidget(self.scope_current_radio)
        scope_layout.addWidget(self.scope_selected_radio)
        # Connect every radio: switching between current and selected does not
        # toggle the "all" radio, so listening to "all" alone misses changes.
        self.scope_all_radio.toggled.connect(self._on_scope_changed)
        self.scope_current_radio.toggled.connect(self._on_scope_changed)
        self.scope_selected_radio.toggled.connect(self._on_scope_changed)
        layout.addWidget(scope_group)

        # Destination
        dest_layout = QHBoxLayout()
        dest_layout.setSpacing(theme.GAP_BTN)
        dest_layout.addWidget(QLabel("Destination:"))
        self.dest_edit = QLineEdit()
        self.dest_edit.textChanged.connect(self._on_dest_changed)
        dest_layout.addWidget(self.dest_edit, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse)
        dest_layout.addWidget(browse_btn)
        layout.addLayout(dest_layout)

        # Naming
        naming_group = QGroupBox("File naming")
        naming_layout = QVBoxLayout(naming_group)
        fields_layout = QHBoxLayout()
        fields_layout.addWidget(QLabel("Filename"))
        self.filename_edit = QLineEdit("{name}_ccr")
        fields_layout.addWidget(self.filename_edit, 1)
        naming_layout.addLayout(fields_layout)
        hint = QLabel("Macros: {name} = original filename, {date}, {time}, {seq}")
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        naming_layout.addWidget(hint)
        self.example_label = QLabel("")
        naming_layout.addWidget(self.example_label)
        self.filename_edit.textChanged.connect(self._update_example)
        layout.addWidget(naming_group)

        # Format / quality / estimate / resize / conflicts
        form = QFormLayout()
        form.setHorizontalSpacing(theme.GAP_BTN)
        form.setVerticalSpacing(theme.GAP_ROW)
        self.format_combo = QComboBox()
        self.format_combo.addItem("TIFF 16-bit (lossless)", "tiff")
        self.format_combo.addItem("JPEG", "jpeg")
        # The normal picture plus a gain map HDR screens use to brighten the
        # highlights that sat above white (spec/hdr-gain-map-export.md).
        self.format_combo.addItem("JPEG + HDR gain map", "hdr_jpeg")
        if self._merged_indices:
            # Trichrome only: raw channel combination, nothing else applied.
            self.format_combo.addItem("TIFF 16-bit linear merge (unprocessed)",
                                      "linear")
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        form.addRow("Format:", self.format_combo)

        # Output color space. sRGB is the current behaviour (now ICC-tagged);
        # ProPhoto re-encodes the result into the wide-gamut space and embeds
        # the matching ICC profile.
        self.colorspace_combo = QComboBox()
        self.colorspace_combo.addItem("sRGB", "srgb")
        self.colorspace_combo.addItem("ProPhoto RGB (wide gamut)", "prophoto")
        self.colorspace_combo.currentIndexChanged.connect(self._on_colorspace_changed)
        self.colorspace_row_label = QLabel("Color space:")
        form.addRow(self.colorspace_row_label, self.colorspace_combo)
        self.colorspace_hint = QLabel(
            "8-bit ProPhoto JPEG can band — prefer 16-bit TIFF for wide gamut.")
        self.colorspace_hint.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        self.colorspace_hint.setWordWrap(True)
        form.addRow("", self.colorspace_hint)

        quality_layout = QHBoxLayout()
        quality_layout.setSpacing(theme.GAP_BTN)
        self.quality_slider = QSlider(Qt.Horizontal)
        self.quality_slider.setRange(1, 100)
        self.quality_slider.setValue(92)
        self.quality_value_label = QLabel("92")
        self.quality_value_label.setMinimumWidth(theme.VALUE_COL_W)
        quality_layout.addWidget(self.quality_slider, 1)
        quality_layout.addWidget(self.quality_value_label)
        self.quality_slider.valueChanged.connect(self._on_quality_changed)
        self.quality_row_label = QLabel("Quality:")
        form.addRow(self.quality_row_label, quality_layout)
        self._quality_layout = quality_layout

        self.hdr_headroom_spin = QDoubleSpinBox()
        self.hdr_headroom_spin.setRange(hdr_gainmap.HEADROOM_MIN, hdr_gainmap.HEADROOM_MAX)
        self.hdr_headroom_spin.setSingleStep(hdr_gainmap.HEADROOM_STEP)
        self.hdr_headroom_spin.setDecimals(1)
        self.hdr_headroom_spin.setSuffix(" stops")
        self.hdr_headroom_spin.setValue(hdr_gainmap.HEADROOM_DEFAULT)
        self.hdr_headroom_spin.setToolTip(
            "How far above normal white the brightest highlights may go on an "
            "HDR screen. 1–2 stops looks natural; 3 is dramatic.")
        self.hdr_headroom_label = QLabel("HDR headroom:")
        form.addRow(self.hdr_headroom_label, self.hdr_headroom_spin)

        self.estimate_label = QLabel("Estimated total size: …")
        form.addRow("", self.estimate_label)

        self.resize_combo = QComboBox()
        self.resize_combo.addItem("Original size", 0)
        self.resize_combo.addItem("4000 px long edge", 4000)
        self.resize_combo.addItem("2000 px long edge", 2000)
        self.resize_combo.addItem("1080 px long edge", 1080)
        self.resize_combo.currentIndexChanged.connect(self._schedule_estimate)
        form.addRow("Resize:", self.resize_combo)

        self.conflict_combo = QComboBox()
        self.conflict_combo.addItem("Auto-rename (-1, -2…)", "rename")
        self.conflict_combo.addItem("Overwrite", "overwrite")
        self.conflict_combo.addItem("Skip existing", "skip")
        form.addRow("If file exists:", self.conflict_combo)
        layout.addLayout(form)

        # Border / canvas (spec/export-canvas-border.md): the finished frame,
        # centred on a canvas of a chosen size and colour.
        self.canvas_group = QGroupBox("Border")
        canvas_layout = QVBoxLayout(self.canvas_group)
        self.canvas_check = QCheckBox("Place on a canvas")
        self.canvas_check.toggled.connect(self._on_canvas_toggled)
        canvas_layout.addWidget(self.canvas_check)
        canvas_form = QFormLayout()
        canvas_form.setHorizontalSpacing(theme.GAP_BTN)
        canvas_form.setVerticalSpacing(theme.GAP_ROW)

        def _px_spin(value):
            spin = QSpinBox()
            spin.setRange(export_canvas.CANVAS_MIN, export_canvas.CANVAS_MAX)
            spin.setSuffix(" px")
            spin.setValue(value)
            spin.valueChanged.connect(self._schedule_estimate)
            return spin

        size_row = QHBoxLayout()
        size_row.setSpacing(theme.GAP_BTN)
        self.canvas_w_spin = _px_spin(export_canvas.DEFAULT_WIDTH)
        self.canvas_h_spin = _px_spin(export_canvas.DEFAULT_HEIGHT)
        size_row.addWidget(self.canvas_w_spin)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.canvas_h_spin)
        size_row.addStretch(1)
        canvas_form.addRow("Canvas:", size_row)
        self.canvas_edge_spin = _px_spin(export_canvas.DEFAULT_LONG_EDGE)
        self.canvas_edge_spin.setToolTip(
            "The frame's longer side in pixels; the other side follows the "
            "frame's ratio. A frame that would not fit the canvas is shrunk "
            "to fit.")
        canvas_form.addRow("Image long edge:", self.canvas_edge_spin)
        self._canvas_colour = tuple(export_canvas.DEFAULT_COLOUR)
        self.canvas_colour_btn = QPushButton()
        self.canvas_colour_btn.setToolTip("Choose the border colour")
        self.canvas_colour_btn.clicked.connect(self._on_pick_canvas_colour)
        colour_row = QHBoxLayout()
        colour_row.addWidget(self.canvas_colour_btn)
        colour_row.addStretch(1)
        canvas_form.addRow("Colour:", colour_row)
        canvas_layout.addLayout(canvas_form)
        canvas_hint = QLabel("Each frame is centred on the canvas.")
        canvas_hint.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        canvas_layout.addWidget(canvas_hint)
        layout.addWidget(self.canvas_group)
        self._canvas_inputs = (self.canvas_w_spin, self.canvas_h_spin,
                               self.canvas_edge_spin, self.canvas_colour_btn)
        self._show_canvas_colour()

        self.open_folder_checkbox = QCheckBox("Open folder when done")
        layout.addWidget(self.open_folder_checkbox)

        # Buttons
        layout.addWidget(theme.section_separator())
        button_layout = QHBoxLayout()
        button_layout.setSpacing(theme.GAP_BTN)
        button_layout.addStretch(1)
        self.export_button = QPushButton("Export")
        self.export_button.setFixedHeight(theme.CONTROL_H_LG)
        self.export_button.clicked.connect(self._on_export_clicked)
        cancel_button = QPushButton("Cancel")
        cancel_button.setFixedHeight(theme.CONTROL_H)
        cancel_button.clicked.connect(self.reject)
        # Export is the deliberate commit → primary + dialog default; Cancel recedes.
        theme.style_button(self.export_button, "primary", default=True)
        theme.style_button(cancel_button, "secondary")
        button_layout.addWidget(cancel_button)
        button_layout.addWidget(self.export_button)
        layout.addLayout(button_layout)

        # Debounced size estimation
        self._estimate_timer = QTimer(self)
        self._estimate_timer.setSingleShot(True)
        self._estimate_timer.setInterval(300)
        self._estimate_timer.timeout.connect(self._update_estimate)

        self._update_quality_visibility()
        self._update_colorspace_hint()

    # ---------- settings ----------

    def _restore_settings(self):
        s = self._settings
        destination = s.value("export/destination", "", type=str)
        if not destination:
            scoped = self._scope_indices()
            if scoped:
                destination = os.path.dirname(ccr_backend.images[scoped[0]].file_path)
        self.dest_edit.setText(destination)

        # An explicit default_scope (e.g. from the thumbnail right-click menu)
        # wins over the persisted choice; both fall back to "all" when the
        # requested scope has nothing to export.
        scope = self._default_scope or s.value("export/scope", "all", type=str)
        if scope == "current" and self._current_converted:
            self.scope_current_radio.setChecked(True)
        elif scope == "selected" and self._selected_converted:
            self.scope_selected_radio.setChecked(True)
        self.filename_edit.setText(s.value("export/filename_template", "{name}_ccr", type=str))
        fmt_index = self.format_combo.findData(s.value("export/format", "tiff", type=str))
        if fmt_index >= 0:
            self.format_combo.setCurrentIndex(fmt_index)
        cs_index = self.colorspace_combo.findData(s.value("export/colorspace", "srgb", type=str))
        if cs_index >= 0:
            self.colorspace_combo.setCurrentIndex(cs_index)
        self.quality_slider.setValue(s.value("export/jpeg_quality", 92, type=int))
        self.hdr_headroom_spin.setValue(hdr_gainmap.clamp_headroom(
            s.value("export/hdr_headroom", hdr_gainmap.HEADROOM_DEFAULT, type=float)))
        resize_index = self.resize_combo.findData(s.value("export/resize", 0, type=int))
        if resize_index >= 0:
            if self._is_linear():
                # Format restored to linear merge: the combo is locked at
                # "Original size" — park the preference for a format switch.
                self._resize_index_before_linear = resize_index
            else:
                self.resize_combo.setCurrentIndex(resize_index)
        conflict_index = self.conflict_combo.findData(s.value("export/conflict", "rename", type=str))
        if conflict_index >= 0:
            self.conflict_combo.setCurrentIndex(conflict_index)
        self.open_folder_checkbox.setChecked(s.value("export/open_folder", False, type=bool))
        self.canvas_w_spin.setValue(s.value("export/canvas_w", export_canvas.DEFAULT_WIDTH, type=int))
        self.canvas_h_spin.setValue(s.value("export/canvas_h", export_canvas.DEFAULT_HEIGHT, type=int))
        self.canvas_edge_spin.setValue(
            s.value("export/canvas_long_edge", export_canvas.DEFAULT_LONG_EDGE, type=int))
        self._canvas_colour = export_canvas.colour_from_hex(
            s.value("export/canvas_colour", "#ffffff", type=str))
        self._show_canvas_colour()
        self.canvas_check.setChecked(s.value("export/canvas_on", False, type=bool))
        self._update_canvas_controls()
        self._update_quality_visibility()

    def _save_settings(self):
        s = self._settings
        s.setValue("export/destination", self.dest_edit.text().strip())
        if self.scope_current_radio.isChecked():
            scope = "current"
        elif self.scope_selected_radio.isChecked():
            scope = "selected"
        else:
            scope = "all"
        s.setValue("export/scope", scope)
        s.setValue("export/filename_template", self.filename_edit.text())
        s.setValue("export/format", self.format_combo.currentData())
        # Linear merge hides the colour space and forces Original size — don't
        # let those forced values clobber the user's normal-format preferences.
        if not self._is_linear():
            s.setValue("export/colorspace", self.colorspace_combo.currentData())
            s.setValue("export/resize", self.resize_combo.currentData())
        s.setValue("export/jpeg_quality", self.quality_slider.value())
        s.setValue("export/hdr_headroom", float(self.hdr_headroom_spin.value()))
        s.setValue("export/canvas_on", self.canvas_check.isChecked())
        s.setValue("export/canvas_w", self.canvas_w_spin.value())
        s.setValue("export/canvas_h", self.canvas_h_spin.value())
        s.setValue("export/canvas_long_edge", self.canvas_edge_spin.value())
        s.setValue("export/canvas_colour", export_canvas.colour_to_hex(self._canvas_colour))
        s.setValue("export/conflict", self.conflict_combo.currentData())
        s.setValue("export/open_folder", self.open_folder_checkbox.isChecked())

    # ---------- helpers ----------

    def _is_linear(self) -> bool:
        return self.format_combo.currentData() == "linear"

    def _is_hdr(self) -> bool:
        return self.format_combo.currentData() == "hdr_jpeg"

    def _canvas_active(self) -> bool:
        return self.canvas_check.isChecked() and not self._is_linear()

    def _canvas_spec(self):
        if not self._canvas_active():
            return None
        return export_canvas.Canvas(self.canvas_w_spin.value(),
                                    self.canvas_h_spin.value(),
                                    self.canvas_edge_spin.value(),
                                    tuple(self._canvas_colour))

    def _show_canvas_colour(self):
        hexcode = export_canvas.colour_to_hex(self._canvas_colour)
        r, g, b = self._canvas_colour
        ink = "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "#ffffff"
        self.canvas_colour_btn.setText(hexcode)
        self.canvas_colour_btn.setStyleSheet(
            f"QPushButton {{ background-color: {hexcode}; color: {ink}; "
            f"border: 1px solid {theme.BORDER_STRONG}; padding: 3px 14px; }}")

    def _on_pick_canvas_colour(self):
        chosen = QColorDialog.getColor(QColor(*self._canvas_colour), self,
                                       "Border colour")
        if chosen.isValid():
            self._canvas_colour = (chosen.red(), chosen.green(), chosen.blue())
            self._show_canvas_colour()

    def _on_canvas_toggled(self, _checked=False):
        self._update_canvas_controls()
        self._schedule_estimate()

    def _update_canvas_controls(self):
        """Border inputs follow the tick box; the linear-merge format has no
        border at all; and while a canvas is on it sets the output size, so
        Resize steps aside."""
        linear = self._is_linear()
        self.canvas_group.setEnabled(not linear)
        on = self.canvas_check.isChecked() and not linear
        for w in self._canvas_inputs:
            w.setEnabled(on)
        self.resize_combo.setEnabled(not linear and not on)

    def _is_jpeg_family(self) -> bool:
        return self.format_combo.currentData() in ("jpeg", "hdr_jpeg")

    def _exportable_all(self) -> List[int]:
        return self._merged_indices if self._is_linear() else self._converted_indices

    def _current_exportable(self) -> bool:
        return self._current_merged if self._is_linear() else self._current_converted

    def _selected_exportable(self) -> List[int]:
        return self._selected_merged if self._is_linear() else self._selected_converted

    def _scope_indices(self) -> List[int]:
        if self.scope_current_radio.isChecked() and self._current_exportable():
            return [self._current_idx]
        if self.scope_selected_radio.isChecked() and self._selected_exportable():
            return list(self._selected_exportable())
        return list(self._exportable_all())

    def _update_scope_radios(self):
        """Re-label and re-enable the scope radios for the active format —
        the linear-merge format exports merged images regardless of
        conversion, the others export converted (or positive-mode) images."""
        if self._is_linear():
            all_label = "All merged images"
            current_tip = "The current image is not a merged trichrome image."
            selected_tip = "No merged images are selected."
        else:
            all_label = "All images" if self._positive_mode else "All converted images"
            current_tip = "The current image has not been converted yet."
            selected_tip = "No converted images are selected."
        self.scope_all_radio.setText(f"{all_label} ({len(self._exportable_all())})")
        current_ok = self._current_exportable()
        self.scope_current_radio.setEnabled(current_ok)
        self.scope_current_radio.setToolTip("" if current_ok else current_tip)
        selected = self._selected_exportable()
        self.scope_selected_radio.setText(f"Selected images ({len(selected)})")
        self.scope_selected_radio.setEnabled(bool(selected))
        self.scope_selected_radio.setToolTip("" if selected else selected_tip)
        # A checked radio that just became empty falls back to All.
        if ((self.scope_current_radio.isChecked() and not current_ok)
                or (self.scope_selected_radio.isChecked() and not selected)):
            self.scope_all_radio.setChecked(True)

    def _base_names(self, indices) -> List[str]:
        names = []
        for i in indices:
            img = ccr_backend.images[i]
            # Sliced images carry a distinguishing display name (e.g. _s2)
            base = getattr(img, "display_name", None) or os.path.basename(img.file_path)
            names.append(os.path.splitext(base)[0])
        return names

    def _selected_ext(self) -> str:
        return ".jpg" if self._is_jpeg_family() else ".tiff"

    def _selected_max_long_side(self) -> Optional[int]:
        value = self.resize_combo.currentData()
        return value if value else None

    def _sample_image(self):
        """Image whose converted preview seeds the JPEG bytes-per-pixel sample."""
        indices = self._scope_indices()
        if self._current_converted and self._current_idx in indices:
            return ccr_backend.images[self._current_idx]
        return ccr_backend.images[indices[0]] if indices else None

    # ---------- slots ----------

    def _on_browse(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Export Folder", self.dest_edit.text(),
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )
        if folder:
            self.dest_edit.setText(folder)

    def _on_dest_changed(self):
        self.export_button.setEnabled(bool(self.dest_edit.text().strip()))

    def _on_scope_changed(self):
        self._update_example()
        self._schedule_estimate()

    def _on_format_changed(self):
        self._update_quality_visibility()
        self._update_colorspace_hint()
        self._update_scope_radios()
        self._update_linear_mode()
        self._update_example()
        self._schedule_estimate()

    def _update_linear_mode(self):
        """Linear merge is the raw combination at full resolution: hide the
        colour-space choice (untagged camera-native data) and force the
        resize to Original size WITHOUT clobbering the user's stored resize
        preference for the normal formats."""
        self._update_linear_resize()
        self._update_canvas_controls()

    def _update_linear_resize(self):
        # Tracked with its own flag rather than the combo's enabled state,
        # because a ticked Border disables the combo too
        # (spec/export-canvas-border.md).
        linear = self._is_linear()
        self.colorspace_row_label.setVisible(not linear)
        self.colorspace_combo.setVisible(not linear)
        parked = getattr(self, "_linear_parked", False)
        if linear and not parked:
            self._resize_index_before_linear = self.resize_combo.currentIndex()
            self._linear_parked = True
            self.resize_combo.setCurrentIndex(0)  # "Original size"
        elif not linear and parked:
            self._linear_parked = False
            restore = getattr(self, "_resize_index_before_linear", None)
            if restore is not None:
                self.resize_combo.setCurrentIndex(restore)
        self.resize_combo.setEnabled(not linear and not self._canvas_active())

    def _on_colorspace_changed(self):
        self._update_colorspace_hint()

    def _update_colorspace_hint(self):
        if self._is_linear():
            self.colorspace_hint.setText(
                "Raw trichrome combination: camera-native linear RGB, no "
                "profile embedded, no conversion or adjustments. Crop and "
                "slice framing apply; rotation and flips do not.")
            self.colorspace_hint.setVisible(True)
            return
        # The banding caution is only relevant for 8-bit ProPhoto JPEG.
        self.colorspace_hint.setText(
            "8-bit ProPhoto JPEG can band — prefer 16-bit TIFF for wide gamut.")
        is_prophoto = self.colorspace_combo.currentData() == "prophoto"
        self.colorspace_hint.setVisible(is_prophoto and self._is_jpeg_family())

    def _on_quality_changed(self, value):
        self.quality_value_label.setText(str(value))
        self._schedule_estimate()

    def _update_quality_visibility(self):
        is_jpeg = self._is_jpeg_family()
        self.quality_row_label.setVisible(is_jpeg)
        for i in range(self._quality_layout.count()):
            widget = self._quality_layout.itemAt(i).widget()
            if widget is not None:
                widget.setVisible(is_jpeg)
        hdr = self._is_hdr()
        self.hdr_headroom_label.setVisible(hdr)
        self.hdr_headroom_spin.setVisible(hdr)

    def _update_example(self):
        indices = self._scope_indices()
        if not indices:
            self.example_label.setText("Example: —")
            return
        base = self._base_names(indices[:1])[0]
        seq_width = max(3, len(str(len(indices))))
        name = build_filename(base, self.filename_edit.text(), self._selected_ext(),
                              seq=1, seq_width=seq_width, now=datetime.now())
        self.example_label.setText(f"Example: {name}")

    # ---------- size estimate ----------

    def _schedule_estimate(self, *args):
        self._estimate_timer.start()

    def _update_estimate(self):
        indices = self._scope_indices()
        if not indices:
            self.estimate_label.setText("Estimated total size: —")
            return
        fmt = self.format_combo.currentData()
        if fmt == "linear":
            fmt = "tiff"  # same container; the estimator knows tiff/jpeg only
        elif fmt == "hdr_jpeg":
            fmt = "jpeg"  # the SDR base; the gain map adds a few percent
        max_long_side = self._selected_max_long_side()
        bpp = None
        if fmt == "jpeg":
            quality = self.quality_slider.value()
            bpp = self._bpp_cache.get(quality)
            if bpp is None:
                sample = self._sample_image()
                if sample is not None and sample.resized_raw is not None:
                    bpp = export_estimator.jpeg_bytes_per_pixel(sample.resized_raw, quality)
                    if bpp is not None:
                        self._bpp_cache[quality] = bpp
            if bpp is None:
                self.estimate_label.setText("Estimated total size: —")
                return
        canvas = self._canvas_spec()
        if canvas is not None:
            # Every file is the canvas size (spec/export-canvas-border.md).
            dims_list = [(canvas.height, canvas.width)] * len(indices)
            max_long_side = None
        else:
            dims_list = [export_estimator.get_original_dims(ccr_backend.images[i])
                         for i in indices]
        total = export_estimator.estimate_total_bytes(dims_list, fmt, bpp=bpp,
                                                      max_long_side=max_long_side)
        count = len(indices)
        self.estimate_label.setText(
            f"Estimated total size: {export_estimator.format_size(total)}"
            f"  ({count} file{'s' if count != 1 else ''})"
        )

    # ---------- accept ----------

    def _on_export_clicked(self):
        destination = self.dest_edit.text().strip()
        if not destination:
            return
        destination = normalize_unicode_path(destination)
        try:
            os.makedirs(destination, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Export Folder Error",
                                f"Cannot create the export folder:\n\n{destination}\n\n{e}")
            return
        if not validate_unicode_path(destination):
            QMessageBox.warning(
                self, "Unicode Path Warning",
                f"The export folder path contains characters that may cause issues:\n\n"
                f"{destination}\n\nPlease choose a simpler folder path."
            )
            return

        indices = self._scope_indices()
        if not indices:
            what = "merged" if self._is_linear() else "converted"
            QMessageBox.information(self, "Nothing to Export",
                                    f"There are no {what} images to export.")
            return

        names = build_export_names(self._base_names(indices), self.filename_edit.text(),
                                   self._selected_ext())
        full_paths = [os.path.join(destination, name) for name in names]
        resolved = resolve_conflicts(full_paths, self.conflict_combo.currentData(),
                                     exists=os.path.exists)

        items = [(idx, path) for idx, path in zip(indices, resolved) if path is not None]
        skipped = len(resolved) - len(items)
        if not items:
            QMessageBox.information(
                self, "Nothing to Export",
                "All files already exist and were skipped.\n"
                "Change the conflict policy or the file naming to export them."
            )
            return

        self.plan = ExportPlan(
            items=items,
            jpg_output=self._is_jpeg_family(),
            jpg_quality=self.quality_slider.value(),
            max_long_side=(None if (self._is_linear() or self._canvas_active())
                           else self._selected_max_long_side()),
            output_colorspace=self.colorspace_combo.currentData(),
            open_folder=self.open_folder_checkbox.isChecked(),
            destination=destination,
            skipped=skipped,
            linear_merge=self._is_linear(),
            hdr_headroom=(float(self.hdr_headroom_spin.value())
                          if self._is_hdr() else None),
            canvas=self._canvas_spec(),
        )
        self._save_settings()
        if self._is_hdr() and ccr_backend.hdr_tint_headroom is not None:
            # The HDR glow tint is scaled to this headroom: keep it in step.
            ccr_backend.hdr_tint_headroom = float(self.hdr_headroom_spin.value())
        self.accept()
