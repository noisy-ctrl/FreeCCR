from PySide6.QtWidgets import QWidget, QVBoxLayout, QListWidget, QListWidgetItem, QLabel, QDialog, QApplication, QPushButton, QMenu, QComboBox, QToolButton, QProgressBar
from PySide6.QtGui import QPixmap, QIcon, QTransform, QPainter
from PySide6.QtCore import Qt, QSize, QTimer, Signal
import os
import logging
import numpy as np
from io import BytesIO
from core.ccr_backend import ccr_backend  # <-- Add this import
from ui import theme

class LoadingDialog(QDialog):
    def __init__(self, parent=None, cancel_callback=None):
        super().__init__(parent)
        self.setWindowTitle("Loading")
        self.setModal(True)
        self.setWindowModality(Qt.ApplicationModal)  # Block all input to the app
        # Remove close, minimize, maximize buttons
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        self.setFixedSize(400, 130)  # Wider and taller for the bar and button

        layout = QVBoxLayout()
        self.label = QLabel("Loading images, please wait")
        self.label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label)
        # Files load in parallel and finish in waves, so the count alone can
        # sit at 0 for a long first wave; the bar also counts files part-way
        # through (each RAW of a 3-way merge, decode, preview). See
        # spec/load-progress.md.
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        layout.addWidget(self.bar)

        # Add Cancel button
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setFixedWidth(100)
        self.cancel_button.setFixedHeight(theme.CONTROL_H)
        self.cancel_button.clicked.connect(self.on_cancel)
        self.cancel_callback = cancel_callback
        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(theme.GAP_ROW)
        btn_layout.addWidget(self.cancel_button, alignment=Qt.AlignCenter)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

        self.dot_count = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_dots)
        self.timer.start(400)  # Update every 400ms

    def update_dots(self):
        self.dot_count = (self.dot_count % 3) + 1  # Cycles 1, 2, 3
        # The loader publishes the batch only when it is complete, so count
        # finished files/frames from its progress, not loaded images.
        count, total = ccr_backend.get_load_progress()
        if not total:
            count = ccr_backend.get_image_count()
            total = ccr_backend.get_total_file_paths()
        merging = bool(getattr(ccr_backend, "rgb_merge_mode", False))
        what = "Merging RGB frames" if merging else "Loading images"
        self.label.setText(f"{what}, please wait" + "." * self.dot_count
                           + f" ({count}/{total} done)")
        if hasattr(ccr_backend, "get_load_fraction"):
            frac = ccr_backend.get_load_fraction()
            self.bar.setValue(max(self.bar.value(), int(round(frac * 1000))))

    def closeEvent(self, event):
        # Prevent closing by user
        event.ignore()

    def reject(self):
        # Prevent closing by Esc or other means
        pass

    def on_cancel(self):
        # Stop timer and close dialog
        self.timer.stop()
        # Clear backend
        
        # Call any additional cancel logic if provided
        if self.cancel_callback:
            self.cancel_callback()
        ccr_backend.clear()
        self.accept()

class ThumbnailList(QWidget):
    # A greyed-out "needs attention" tile was acted on: (entry id, action),
    # action in "choose" | "merge" | "singles" | "remove" (spec/scan-type.md).
    attention_action = Signal(str, str)

    def __init__(self, image_selected_callback):
        super().__init__()
        self.image_selected_callback = image_selected_callback
        self.init_ui()

    def _main_window(self):
        """Return the MainWindow that owns this widget."""
        return self.parent().parent()

    def refresh_profile_combo(self):
        """Repopulate the camera-profile picker from the library and reflect the
        active selection (called on startup and after import/delete/generate)."""
        if not hasattr(self, "profile_combo"):
            return
        CM = ccr_backend.CAMERA_MATRIX
        active = getattr(ccr_backend, "active_profile_path", None)
        active_n = (os.path.normcase(os.path.abspath(active))
                    if (active and active != CM) else None)
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        # Two non-removable entries first: None (bare RAW) and Camera Matrix
        # (Adobe RGB + auto-scale, the camera's built-in matrix).
        self.profile_combo.addItem("None", None)
        self.profile_combo.addItem("Camera Matrix", CM)
        sel = 1 if active == CM else 0
        for p in ccr_backend.list_camera_profiles():
            self.profile_combo.addItem(f"{p['name']}  ({p['kind'].upper()})", p["path"])
            if active_n and os.path.normcase(os.path.abspath(p["path"])) == active_n:
                sel = self.profile_combo.count() - 1
        self.profile_combo.setCurrentIndex(sel)
        self.profile_combo.blockSignals(False)

    def _on_profile_combo_changed(self, _idx):
        """User picked a profile (or None) — forward to the MainWindow, which
        owns activation + persistence + the thumbnail mismatch refresh."""
        mw = self._main_window()
        if hasattr(mw, "set_active_profile_path"):
            mw.set_active_profile_path(self.profile_combo.currentData())

    def init_ui(self):
        self.layout = QVBoxLayout()
        theme.apply_panel_spacing(self.layout)

        # Camera-profile picker — choose which imported / IT8-generated profile
        # (kept in FreeCCR's library) is applied at decode, or None. Manage the
        # library in Settings ▸ Color Management. See spec/camera-profile-library.md.
        # Eliding: profile names are user-supplied filenames and routinely
        # outrun this 216px-wide sidebar, where Qt would clip them mid-glyph.
        self.profile_combo = theme.ElidingComboBox()
        self.profile_combo.setToolTip(
            "Camera input profile applied at decode, from your imported / "
            "IT8-generated library. 'None' applies no profile. Add and manage "
            "profiles in Settings ▸ Color Management.")
        self.profile_combo.currentIndexChanged.connect(self._on_profile_combo_changed)
        _plabel = QLabel("Camera profile:")
        _plabel.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        self.layout.addWidget(_plabel)
        self.layout.addWidget(self.profile_combo)
        # Positive mode now lives only in Settings ▸ Color Management.

        # Contact sheet (spec/contact-sheet.md): the whole roll as one image,
        # pinned above the frames (not a list item, so rows stay == image
        # indices). Its dashed border sets it apart from the frames.
        self.sheet_btn = QToolButton()
        self.sheet_btn.setCheckable(True)
        self.sheet_btn.setText("Contact sheet")
        self.sheet_btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.sheet_btn.setIconSize(QSize(176, 120))
        self.sheet_btn.setFixedSize(196, 154)
        self.sheet_btn.setToolTip(
            "The whole roll as one contact sheet, each frame with its crop "
            "marked: green confirmed by you, amber auto crop, blue drawn by "
            "hand, red dashed auto crop wasn't sure. Double-click a frame on "
            "it to open that frame.")
        self.sheet_btn.setStyleSheet(
            f"QToolButton {{ border: 1px dashed {theme.EDGE_PRINT}; border-radius: 4px;"
            f" background: transparent; color: {theme.TEXT}; padding-top: 4px; }}"
            f"QToolButton:hover {{ background: {theme.SURFACE_HOVER}; }}"
            f"QToolButton:checked {{ border: 2px solid {theme.EDGE_PRINT};"
            f" background: {theme.SURFACE_ACTIVE}; }}")
        self.sheet_btn.clicked.connect(self._on_sheet_clicked)
        self.sheet_btn.setVisible(False)          # until there are frames
        self.layout.addWidget(self.sheet_btn, 0, Qt.AlignHCenter)

        self.thumbnail_list = QListWidget()
        self.thumbnail_list.setViewMode(QListWidget.IconMode)
        # Set the icon size up front so incrementally-appended items (e.g.
        # tether captures added via append_image_item, before any full
        # load_thumbnails rebuild has run) show at full size, not Qt's tiny
        # default. load_thumbnails also sets this, harmlessly.
        self.thumbnail_list.setIconSize(QSize(156, 156))
        self.thumbnail_list.setSpacing(10)
        self.thumbnail_list.setDragDropMode(QListWidget.NoDragDrop)
        self.thumbnail_list.setResizeMode(QListWidget.Adjust)
        self.thumbnail_list.setMovement(QListWidget.Static)
        self.thumbnail_list.setWrapping(False)
        self.thumbnail_list.setFlow(QListWidget.TopToBottom)
        self.thumbnail_list.setFixedWidth(196)
        self.thumbnail_list.setFocusPolicy(Qt.StrongFocus)  # <-- Ensure strong focus
        self.thumbnail_list.setFocus()  # <-- Optionally set focus immediately
        # Ctrl+click toggles, Shift+click selects a range
        self.thumbnail_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.thumbnail_list.itemClicked.connect(self.on_thumbnail_clicked)
        self.thumbnail_list.currentItemChanged.connect(self.on_current_item_changed)
        # Right-click menu: duplicate / remove the selected image(s)
        self.thumbnail_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.thumbnail_list.customContextMenuRequested.connect(self.show_context_menu)
        # Scrolling tells background renders to give way (spec/background-yields.md).
        from core import ui_activity
        self.thumbnail_list.verticalScrollBar().valueChanged.connect(
            lambda _v: ui_activity.touch())
        self.layout.addWidget(self.thumbnail_list)

        # Hot Folder Tether's trichrome shots that couldn't be merged (a frame
        # missing, or not lit red, green, blue in order): greyed out here, OUT
        # of the frame list so list rows stay == image indices. Right-click to
        # choose the files, merge anyway, load singly or remove.
        self.attention_label = QLabel("Needs attention")
        self.attention_label.setStyleSheet(
            f"color: {theme.WARN_TEXT}; font-size: {theme.FS_CAPTION}px; font-weight: bold;")
        self.attention_list = QListWidget()
        self.attention_list.setViewMode(QListWidget.IconMode)
        self.attention_list.setIconSize(QSize(156, 52))
        self.attention_list.setSpacing(6)
        self.attention_list.setMovement(QListWidget.Static)
        self.attention_list.setWrapping(False)
        self.attention_list.setFlow(QListWidget.TopToBottom)
        self.attention_list.setFixedWidth(196)
        self.attention_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.attention_list.customContextMenuRequested.connect(self._attention_menu)
        self.attention_list.itemDoubleClicked.connect(
            lambda it: self.attention_action.emit(it.data(Qt.UserRole), "choose"))
        self._attention = []
        for w in (self.attention_label, self.attention_list):
            w.setVisible(False)
            self.layout.addWidget(w)

        self.count_label = QLabel("Total: 0 image(s)")
        self.count_label.setAlignment(Qt.AlignLeft)
        self.layout.addWidget(self.count_label)

        self.setLayout(self.layout)
        
        # Set initial hint since no images are loaded when program starts
        # Use QTimer to ensure parent widgets are fully initialized
        QTimer.singleShot(100, self.set_initial_hint)

    def set_initial_hint(self):
        """
        Set the initial hint when the program starts with no images loaded.
        """
        try:
            self._main_window().sliders_panel.set_hint("<b>Hint:</b><br>Use file menu to import folder or files.")
        except AttributeError:
            # In case the parent structure isn't ready yet, try again later
            QTimer.singleShot(200, self.set_initial_hint)

    def show_loading_dialog(self):
        # Pass a callback to cancel the worker thread
        def cancel_loading():
            # Access the worker from the main window and call cancel.
            # _loader_worker can already be None (cleanup ran while the
            # dialog was still up) — Cancel must not die on None.cancel().
            main_window = self._main_window()
            worker = getattr(main_window, '_loader_worker', None)
            if worker is not None:
                worker.cancel()
        ccr_backend.reset_load_progress()     # don't flash the previous load's count
        self.loading_dialog = LoadingDialog(self, cancel_callback=cancel_loading)
        self.loading_dialog.show()
        QApplication.processEvents()

    def load_thumbnails(self):
        # Guard re-entrancy: the per-5-items processEvents below can deliver
        # a right-click whose menu mutates the backend and re-enters here.
        self._rebuilding = True
        try:
            self._load_thumbnails_inner()
        finally:
            self._rebuilding = False

    def _load_thumbnails_inner(self):
        self.thumbnail_list.clear()
        image_count = ccr_backend.get_image_count()
        logging.info(f"Loading {image_count} images from backend")
        for idx in range(image_count):
            thumbnail = ccr_backend.get_thumbnail_by_index(idx)
            # Get filename from the CCRImage object itself (always in sync);
            # sliced images carry a distinguishing display name (e.g. _s2)
            img_obj = ccr_backend.get_image_by_index(idx)
            if img_obj is None:
                filename = ""
            else:
                filename = (getattr(img_obj, "display_name", None)
                            or os.path.basename(img_obj.file_path))
            item = QListWidgetItem(filename)  # Set filename as item text
            # Apply rotations and flips using PySide6 transformations
            transformed_thumbnail = self.apply_frontend_transformations(thumbnail, idx)
            icon = QIcon(transformed_thumbnail)
            item.setIcon(icon)
            item.setData(Qt.UserRole, idx)
            self.thumbnail_list.addItem(item)
            if idx % 5 == 0:
                QApplication.processEvents()
        self.thumbnail_list.setIconSize(QSize(156, 156))
        self.count_label.setText(f"Total: {image_count} image(s)")
        self.refresh_profile_warnings()

        # Set current index to 0 if there are items
        if image_count > 0:
            self.thumbnail_list.setCurrentRow(0)
            self._main_window().image_preview.update_preview(0)
            # Clear any existing hint when images are loaded
            self._main_window().sliders_panel.clear_hint()
        else:
            # Set hint when no images are loaded, and clear the preview —
            # otherwise the last removed image stays on the canvas with
            # stale state (and its memory pinned).
            self._main_window().sliders_panel.set_hint("<b>Hint:</b><br>Use file menu to import folder or files.")
            try:
                self._main_window().image_preview.clear_preview()
            except AttributeError:
                pass

        if hasattr(self, 'loading_dialog') and self.loading_dialog is not None:
            self.loading_dialog.accept()  # Close the dialog

    def update_thumbnail(self, idx):
        thumbnail = ccr_backend.get_thumbnail_by_index(idx)
        if thumbnail is not None:
            item = self.thumbnail_list.item(idx)
            if item:
                # Apply rotations and flips using PySide6 transformations
                transformed_thumbnail = self.apply_frontend_transformations(thumbnail, idx)
                icon = QIcon(transformed_thumbnail)
                item.setIcon(icon)
                                                                                 
    def update_all_thumbnails(self):
        for idx in range(ccr_backend.get_image_count()):
            self.update_thumbnail(idx)

    # --- needs attention (spec/scan-type.md §5) ---------------------------
    @staticmethod
    def _attention_tile(thumbs, size=(156, 52)):
        """The shot's frames side by side, greyed out; a plain grey tile when
        there are no previews."""
        import cv2
        w, h = size
        canvas = np.full((h, w, 3), 58, np.uint8)
        parts = [t for t in (thumbs or []) if t is not None]
        if parts:
            th = h - 8
            parts = [cv2.resize(t, (max(1, round(t.shape[1] * th / t.shape[0])), th),
                                interpolation=cv2.INTER_AREA) for t in parts]
            total = sum(p.shape[1] for p in parts) + 4 * (len(parts) - 1)
            scale = min(1.0, (w - 8) / max(1, total))
            if scale < 1.0:
                parts = [cv2.resize(p, (max(1, int(p.shape[1] * scale)), max(1, int(th * scale))),
                                    interpolation=cv2.INTER_AREA) for p in parts]
            x = (w - (sum(p.shape[1] for p in parts) + 4 * (len(parts) - 1))) // 2
            for part in parts:
                g = cv2.cvtColor(part, cv2.COLOR_BGR2GRAY)
                g = (g.astype(np.float32) * 0.55 + 30).astype(np.uint8)
                ph, pw = g.shape
                y = (h - ph) // 2
                canvas[y:y + ph, x:x + pw] = g[..., None]
                x += pw + 4
        from PySide6.QtGui import QImage
        rgb = np.ascontiguousarray(canvas[..., ::-1])
        q = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(q)

    def set_attention(self, entries):
        """entries: [{"id", "paths", "short", "reason", "thumbs"}]."""
        self._attention = list(entries or [])
        self.attention_list.clear()
        for e in self._attention:
            item = QListWidgetItem(e.get("short", "Needs attention"))
            item.setIcon(QIcon(self._attention_tile(e.get("thumbs"))))
            names = "\n".join(os.path.basename(p) for p in e.get("paths", []))
            item.setToolTip(f"{e.get('reason', '')}\n\n{names}\n\n"
                            "Right-click: choose the files, merge anyway, load as "
                            "single frames, or remove. Double-click: choose the files.")
            item.setData(Qt.UserRole, e["id"])
            item.setForeground(Qt.gray)
            self.attention_list.addItem(item)
        has = bool(self._attention)
        self.attention_label.setVisible(has)
        self.attention_list.setVisible(has)
        if has:
            rows = min(2, len(self._attention))
            self.attention_list.setFixedHeight(rows * 84 + 8)

    def _attention_menu(self, pos):
        item = self.attention_list.itemAt(pos)
        if item is None:
            return
        eid = item.data(Qt.UserRole)
        entry = next((e for e in self._attention if e["id"] == eid), {})
        menu = QMenu(self)
        acts = {menu.addAction("Choose Files…"): "choose"}
        merge = menu.addAction("Merge Anyway")
        merge.setEnabled(len(entry.get("paths", [])) == 3)
        acts[merge] = "merge"
        acts[menu.addAction("Load as Single Frames")] = "singles"
        menu.addSeparator()
        acts[menu.addAction("Remove")] = "remove"
        chosen = menu.exec(self.attention_list.mapToGlobal(pos))
        if chosen in acts:
            self.attention_action.emit(eid, acts[chosen])

    def append_image_item(self, idx, select=True):
        """Append a single thumbnail item for a newly added backend image
        (e.g. a tether capture) without rebuilding the whole list. When select
        is True, selecting the item drives update_preview/set_current_idx via
        on_current_item_changed (so the capture shows big on the canvas).
        Returns True if the item was appended; False if skipped (a rebuild is
        in flight, or the index is no longer valid — the next full rebuild will
        include the image either way)."""
        if getattr(self, "_rebuilding", False):
            return False
        img_obj = ccr_backend.get_image_by_index(idx)
        if img_obj is None:
            logging.warning(f"append_image_item: no backend image at index {idx}")
            return False
        filename = (getattr(img_obj, "display_name", None)
                    or os.path.basename(img_obj.file_path))
        item = QListWidgetItem(filename)
        thumbnail = ccr_backend.get_thumbnail_by_index(idx)
        if thumbnail is not None:
            item.setIcon(QIcon(self.apply_frontend_transformations(thumbnail, idx)))
        item.setData(Qt.UserRole, idx)
        self.thumbnail_list.addItem(item)
        self.count_label.setText(
            f"Total: {ccr_backend.get_image_count()} image(s)")
        self.refresh_profile_warnings()
        if select:
            self.thumbnail_list.setCurrentRow(self.thumbnail_list.count() - 1)
        return True
        
    def _on_sheet_clicked(self, checked):
        mw = self._main_window()
        if not hasattr(mw, "show_contact_sheet") or not mw.show_contact_sheet(checked):
            self.set_sheet_checked(getattr(mw, "contact_sheet_shown", lambda: False)())

    def set_sheet_icon(self, pixmap):
        """The contact sheet changed: show its thumbnail, the entry sized to
        it (a long roll's sheet is tall, a short one's wide). Hidden when
        there are no frames."""
        has = pixmap is not None and not pixmap.isNull()
        if has:
            self.sheet_btn.setIcon(QIcon(pixmap))
            self.sheet_btn.setIconSize(pixmap.size())
            self.sheet_btn.setFixedHeight(pixmap.height() + 34)
        self.sheet_btn.setVisible(has)

    def set_sheet_checked(self, on):
        self.sheet_btn.blockSignals(True)
        self.sheet_btn.setChecked(bool(on))
        self.sheet_btn.blockSignals(False)

    def _leave_sheet(self):
        """Picking a frame in the list leaves the contact sheet."""
        mw = self._main_window()
        if getattr(mw, "contact_sheet_shown", lambda: False)():
            mw.show_contact_sheet(False)

    def on_thumbnail_clicked(self, item):
        self._leave_sheet()
        idx = item.data(Qt.UserRole)
        self._main_window().image_preview.update_preview(idx)
        self._main_window().sliders_panel.set_current_idx(idx)

    def on_current_item_changed(self, current, previous):
        if current:
            self._leave_sheet()
            idx = current.data(Qt.UserRole)
            self._main_window().image_preview.update_preview(idx)
            self._main_window().sliders_panel.set_current_idx(idx)
            if previous:
                prev_idx = previous.data(Qt.UserRole)
                self.update_thumbnail(prev_idx)  # Update previous thumbnail
            

    def keyPressEvent(self, event):
        # Allow up/down keys to move the selection
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            self.thumbnail_list.keyPressEvent(event)
        else:
            super().keyPressEvent(event)

    def selected_indices(self):
        """Backend indices of the selected thumbnails, ascending."""
        return sorted({item.data(Qt.UserRole)
                       for item in self.thumbnail_list.selectedItems()})

    # --- Camera-profile mismatch flagging ---------------------------------
    def _profile_mismatch(self, idx) -> bool:
        """True if the image was graded under a different camera profile than the
        one active now (so it should be re-graded / flagged)."""
        img = ccr_backend.get_image_by_index(idx)
        if img is None:
            return False
        sig = getattr(img, "profile_signature", None)
        return sig is not None and sig != ccr_backend.active_profile_signature()

    def refresh_profile_warnings(self):
        """Flag every thumbnail whose image was graded under a different camera
        profile than the active one — ⚠ prefix + amber text + tooltip. Called on
        load/append and whenever the active profile (or disable / Positive mode)
        changes."""
        active = ccr_backend.active_profile_signature()
        warn = theme.qcolor(theme.WARN_TEXT)
        normal = theme.qcolor(theme.TEXT)
        for row in range(self.thumbnail_list.count()):
            item = self.thumbnail_list.item(row)
            idx = item.data(Qt.UserRole)
            img = ccr_backend.get_image_by_index(idx)
            if img is None:
                continue
            filename = (getattr(img, "display_name", None)
                        or os.path.basename(img.file_path))
            sig = getattr(img, "profile_signature", None)
            mism = sig is not None and sig != active
            item.setText(("⚠ " + filename) if mism else filename)
            item.setForeground(warn if mism else normal)
            item.setToolTip(
                "Graded under a different camera profile or field "
                "correction.\nRight-click ▸ Replace with current camera "
                "profile." if mism else "")

    def replace_with_current_profile(self, indices):
        """Re-grade the given image(s) under the current camera profile by
        re-decoding them and replaying the conversion — KEEPING everything (the
        negative conversion / bwpoint, adjustments, crop, flips, rotation, slice).
        Only the camera-profile decode changes, unlike Reset. Bulk-safe."""
        if not indices:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            changed = ccr_backend.regrade_images_by_indices(indices)
        finally:
            QApplication.restoreOverrideCursor()
        if not changed:
            return
        for idx in indices:
            self.update_thumbnail(idx)
        self.refresh_profile_warnings()
        current = self.thumbnail_list.currentItem()
        if current is not None:
            cur_idx = current.data(Qt.UserRole)
            mw = self._main_window()
            mw.image_preview.update_preview(cur_idx)
            mw.sliders_panel.set_current_idx(cur_idx)
            mw.image_preview._update_unconvert_action_state()
        ccr_backend.save_catalog()

    def show_context_menu(self, pos):
        if getattr(self, "_rebuilding", False):
            return  # list is mid-rebuild; stale items must not act
        item = self.thumbnail_list.itemAt(pos)
        if item is None:
            return
        # Right-clicking outside the current selection targets just that
        # item. clearSelection() is required: with Ctrl/Shift still held,
        # setCurrentItem alone KEEPS committed selection ranges and would
        # make the menu act on a wrong multi-item set.
        if not item.isSelected():
            self.thumbnail_list.clearSelection()
            self.thumbnail_list.setCurrentItem(item)
        indices = self.selected_indices()
        if not indices:
            return
        suffix = f" ({len(indices)})" if len(indices) > 1 else ""
        menu = QMenu(self)
        # Copy reads the right-clicked image; Paste goes to the whole
        # selection via the paste dialog. See spec/paste-settings-dialog.md.
        sliders = getattr(self._main_window(), "sliders_panel", None)
        copy_row = self.thumbnail_list.row(item)
        copy_action = menu.addAction("Copy Settings")
        paste_action = menu.addAction(f"Paste Settings{suffix}…")
        paste_action.setEnabled(sliders is not None
                                and getattr(sliders, "clipboard", None) is not None)
        copy_action.setEnabled(sliders is not None)
        menu.addSeparator()
        duplicate_action = menu.addAction(f"Duplicate{suffix}")
        remove_action = menu.addAction(f"Remove from list{suffix}")
        menu.addSeparator()
        export_action = menu.addAction("Export…")
        reset_action = menu.addAction(f"Reset{suffix}")
        # Offered only when the selection contains slices: restores the
        # un-sliced parent image(s) in place of all their slices.
        reset_slice_action = None
        slice_count = sum(
            1 for i in indices
            if i < len(ccr_backend.images) and ccr_backend.images[i].source_ops
            and ccr_backend.images[i].slice_group is not None)
        if slice_count:
            slice_suffix = f" ({slice_count})" if slice_count > 1 else ""
            reset_slice_action = menu.addAction(f"Reset Slice{slice_suffix}")
        # "Replace with current camera profile" — only for images in the selection
        # graded under a different profile (re-decodes them, like Reset).
        mismatched = [i for i in indices if self._profile_mismatch(i)]
        replace_action = None
        if mismatched:
            menu.addSeparator()
            rsuffix = f" ({len(mismatched)})" if len(mismatched) > 1 else ""
            replace_action = menu.addAction(
                f"Replace with current camera profile{rsuffix}")
        action = menu.exec_(self.thumbnail_list.mapToGlobal(pos))
        if action == copy_action:
            sliders.copy_settings_from_index(copy_row)
        elif action == paste_action:
            sliders.paste_settings_to_indices(indices)
        elif action == duplicate_action:
            self.duplicate_images(indices)
        elif action == remove_action:
            self.remove_images(indices)
        elif action == export_action:
            # Open the export dialog pre-scoped to the right-clicked selection.
            self._main_window().image_preview.open_export_dialog(default_scope="selected")
        elif action == reset_action:
            self.reset_images(indices)
        elif reset_slice_action is not None and action == reset_slice_action:
            self.reset_slices(indices)
        elif replace_action is not None and action == replace_action:
            self.replace_with_current_profile(mismatched)

    def duplicate_images(self, indices):
        created = ccr_backend.duplicate_images_by_indices(indices)
        if not created:
            return
        self.load_thumbnails()
        first_copy = min(indices) + 1
        if first_copy < ccr_backend.get_image_count():
            self.thumbnail_list.setCurrentRow(first_copy)
        ccr_backend.save_catalog()

    @staticmethod
    def _next_selection_target(removed_indices, new_count):
        """Row to select after a removal: the image that followed the
        removed one(s) — it now occupies the lowest removed index — clamped
        to the new last image when the tail was removed."""
        if new_count <= 0:
            return None
        return min(min(removed_indices), new_count - 1)

    def remove_images(self, indices):
        if not ccr_backend.remove_images_by_indices(indices):
            return
        self.load_thumbnails()
        target = self._next_selection_target(indices, ccr_backend.get_image_count())
        if target is not None and target != 0:  # load_thumbnails already selected row 0
            self.thumbnail_list.setCurrentRow(target)
        ccr_backend.save_catalog()

    def remove_image(self, idx):
        """Single-image removal (kept for compatibility)."""
        self.remove_images([idx])

    def reset_images(self, indices):
        # Reset re-decodes the source file(s) synchronously; show a busy
        # cursor so the brief GUI-thread freeze reads as work, not a hang.
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            changed = ccr_backend.reset_images_by_indices(indices)
        finally:
            QApplication.restoreOverrideCursor()
        if not changed:
            return
        for idx in indices:
            self.update_thumbnail(idx)
        # Reset re-decodes under the current profile, so the per-image grading
        # signature now matches — clear any stale ⚠ mismatch flags.
        self.refresh_profile_warnings()
        # Refresh the preview, sliders, and convert/unconvert state for the
        # currently selected image (the list itself is unchanged).
        current = self.thumbnail_list.currentItem()
        if current is not None:
            cur_idx = current.data(Qt.UserRole)
            main_window = self._main_window()
            main_window.image_preview.update_preview(cur_idx)
            main_window.sliders_panel.set_current_idx(cur_idx)
            main_window.image_preview._update_unconvert_action_state()
        ccr_backend.save_catalog()

    def reset_slices(self, indices):
        # Reset re-decodes the source file(s) synchronously; show a busy
        # cursor so the brief GUI-thread freeze reads as work, not a hang.
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            restored = ccr_backend.reset_slice_by_indices(indices)
        finally:
            QApplication.restoreOverrideCursor()
        if restored is None:
            return
        self.load_thumbnails()
        if restored != 0:  # load_thumbnails already selected row 0
            self.thumbnail_list.setCurrentRow(restored)

    def apply_frontend_transformations(self, thumbnail, idx):
        """
        Apply rotations and flips to the thumbnail using PySide6 transformations.
        This only affects the visual display without modifying backend data.
        """
        # Get transformation settings from backend
        rotation_angle = ccr_backend.get_image_rotation_by_index(idx)
        horizontal_flip = ccr_backend.get_image_horizontal_flip_by_index(idx)
        vertical_flip = ccr_backend.get_image_vertical_flip_by_index(idx)
        
        # Start with the original thumbnail
        transformed_thumbnail = thumbnail
        
        # Rotate FIRST, then mirror — the canvas's order (and the export's,
        # see ccr_processor.apply_orientation). Mirroring first would land a
        # mirrored quarter-turn 180 degrees away from what the preview shows.
        angle = rotation_angle % 360
        if angle != 0:
            transform = QTransform()
            transform.rotate(angle)
            transformed_thumbnail = transformed_thumbnail.transformed(transform)

        # Apply 90-degree rotations (but not fine angle rotation)
        if horizontal_flip or vertical_flip:
            transform = QTransform()
            if horizontal_flip:
                transform.scale(-1, 1)  # Horizontal flip
            if vertical_flip:
                transform.scale(1, -1)  # Vertical flip
            transformed_thumbnail = transformed_thumbnail.transformed(transform)

        # Ensure the transformed thumbnail fits within the icon size (156x156)
        # while maintaining aspect ratio to prevent overlapping
        icon_size = 156
        if transformed_thumbnail.width() > icon_size or transformed_thumbnail.height() > icon_size:
            transformed_thumbnail = transformed_thumbnail.scaled(
                icon_size, icon_size, 
                Qt.KeepAspectRatio, 
                Qt.SmoothTransformation
            )
        
        # Create a centered thumbnail on a fixed-size canvas to ensure consistent spacing
        canvas = QPixmap(icon_size, icon_size)
        canvas.fill(Qt.transparent)  # Transparent background
        
        # Calculate position to center the thumbnail on the canvas
        x_offset = (icon_size - transformed_thumbnail.width()) // 2
        y_offset = (icon_size - transformed_thumbnail.height()) // 2
        
        # Draw the thumbnail centered on the canvas
        painter = QPainter(canvas)
        painter.drawPixmap(x_offset, y_offset, transformed_thumbnail)
        painter.end()
        
        return canvas
