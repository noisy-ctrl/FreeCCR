from PySide6.QtWidgets import QApplication, QMainWindow, QHBoxLayout, QWidget, QFileDialog, QMessageBox, QDialog, QVBoxLayout, QTextEdit, QPushButton, QLabel, QProgressDialog
from PySide6.QtGui import QIcon, QShortcut, QKeySequence
from PySide6.QtCore import (Qt, QEvent, QThread, Signal, QObject, QSettings,
                            QTimer, QMetaObject, Q_ARG)
from widgets.thumbnail_list import ThumbnailList
from widgets.image_preview import ImagePreview
from widgets.sliders_panel import SlidersPanel
from widgets.dust_panel import DustRemovalPanel
from widgets.crop_panel import CropPanel
from widgets.tether_banner import TetherBanner
from core.ccr_backend import ccr_backend
from utils.qt_threads import new_thread
from core.tether_watcher import (TetherWatchWorker, FolderScanner, is_supported,
                                 encode_bwpoint, decode_bwpoint)
import ctypes
from version import VERSION  # Make sure version.py is in your src folder
import copy
import html
import os
import sys
import threading
import time
import webbrowser

from utils.unicode_path_utils import normalize_unicode_path, validate_unicode_path
from utils.update_check import (fetch_latest_release, parse_version,
                                should_offer_update, REPO_URL)
from ui import theme

class UpdateCheckWorker(QObject):
    """Fetches the latest GitHub release off the GUI thread.

    Runs on a daemon threading.Thread, not a QThread: a daemon never blocks
    interpreter exit, whereas a QThread still running at teardown aborts the
    process with qFatal. urlopen's 2s timeout bounds socket ops only, not DNS
    resolution, so results that took too long overall are discarded.
    """
    finished = Signal(object)  # release dict or None

    MAX_TOTAL_SECONDS = 6.0

    def start(self):
        threading.Thread(target=self._run, daemon=True,
                         name="FreeCCR-update-check").start()

    def _run(self):
        started = time.monotonic()
        release = fetch_latest_release(timeout=2.0)
        try:
            if time.monotonic() - started <= self.MAX_TOTAL_SECONDS:
                self.finished.emit(release)
        except RuntimeError:
            pass  # Qt object already deleted — app is shutting down


class UpdateAvailableDialog(QDialog):
    REMIND = 0   # == QDialog.Rejected, so closing the window means "later"
    UPDATE = 1
    SKIP = 2

    def __init__(self, parent, release, current_version):
        super().__init__(parent)
        self.setWindowTitle("Update Available")
        self.setMinimumWidth(theme.DIALOG_W_MD)
        layout = QVBoxLayout(self)
        theme.apply_panel_spacing(layout, margin=theme.SPACE_XL, spacing=theme.GAP_SECTION)
        message = QLabel(
            f"A new version of FreeCCR is available: "
            f"<b>{html.escape(release['tag'])}</b><br>"
            f"You are running {html.escape(str(current_version))}.")
        message.setWordWrap(True)
        layout.addWidget(message)
        buttons = QHBoxLayout()
        buttons.setSpacing(theme.GAP_BTN)
        update_btn = QPushButton("Update")
        update_btn.setDefault(True)
        remind_btn = QPushButton("Remind Me Later")
        skip_btn = QPushButton("Skip This Version")
        for btn in (update_btn, remind_btn, skip_btn):
            btn.setMinimumHeight(theme.CONTROL_H)
        update_btn.clicked.connect(lambda: self.done(self.UPDATE))
        remind_btn.clicked.connect(lambda: self.done(self.REMIND))
        skip_btn.clicked.connect(lambda: self.done(self.SKIP))
        buttons.addWidget(update_btn)
        buttons.addWidget(remind_btn)
        buttons.addWidget(skip_btn)
        layout.addLayout(buttons)


class ImageLoaderWorker(QObject):
    finished = Signal()
    def __init__(self, folder=None, files=None, force_no_merge=False):
        super().__init__()
        self.folder = folder
        self.files = files
        # Load the files as normal images even when 3-way merge is on (used to
        # reload the linear TIFFs a merge-replace produced). See
        # spec/merge-linear-tiff-replace.md.
        self.force_no_merge = force_no_merge
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self.folder:
            ccr_backend.load_images_from_folder(self.folder, cancel_flag=lambda: self._cancelled)
        elif self.files:
            ccr_backend.load_images_from_files(
                self.files, cancel_flag=lambda: self._cancelled,
                force_no_merge=self.force_no_merge)
        self.finished.emit()


class MergeReplaceWorker(QObject):
    """Runs the destructive bake-and-delete for the merge-replace flow off the
    GUI thread: writes each merged image's linear TIFF, verifies it, then deletes
    the source RAWs. Emits progress and the result dict. See
    spec/merge-linear-tiff-replace.md."""
    progress = Signal(int, int, str)   # done, total, current name
    finished = Signal(object)          # result dict from bake_merged_to_linear_tiff

    def __init__(self, images):
        super().__init__()
        self.images = images
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        result = ccr_backend.bake_merged_to_linear_tiff(
            images=self.images,
            cancel_flag=lambda: self._cancelled,
            progress_cb=lambda d, t, n: self.progress.emit(d, t, n))
        self.finished.emit(result)

class _AppWideHook(QObject):
    """The main window's app-wide event filter (upstream 2.2.1: Up/Down always
    switch image; drops anywhere in the window load files), installed once
    per application and handing the few event types it cares about to the
    newest MainWindow's eventFilter."""

    _TYPES = (QEvent.KeyPress, QEvent.DragEnter, QEvent.DragMove, QEvent.Drop)
    _hook = None

    def __init__(self, app):
        super().__init__(app)
        self.window = None

    @classmethod
    def point_at(cls, window):
        app = QApplication.instance()
        if cls._hook is None or cls._hook.parent() is not app:
            cls._hook = cls(app)
            app.installEventFilter(cls._hook)
        cls._hook.window = window

    def eventFilter(self, obj, event):
        w = self.window
        if w is None or event.type() not in self._TYPES:
            return False
        try:
            return bool(w.eventFilter(obj, event))
        except RuntimeError:          # the window's C++ side is gone
            self.window = None
            return False


class MainWindow(QMainWindow):
    # A file Hot Folder Tether just loaded (its path). The Live Capture
    # Monitor checks the first one after a Shoot against its Hold time.
    tether_captured = Signal(str)
    # What is being scanned changed (spec/scan-type.md): "colour" |
    # "trichrome" | "bw". The Live Capture Monitor follows it.
    scan_type_changed = Signal(str)
    # Queued to the Hot Folder Tether worker (it lives on its own thread).
    _tether_triplet_req = Signal(object)
    _tether_leftover_req = Signal(object)

    def __init__(self):
        super().__init__()
        self._live_monitor = None
        self.setWindowTitle("FreeCCR")
        self.setGeometry(100, 100, 1860, 1080)
        app_icon = QIcon("./icons/freeccr_logo.png")
        self.setWindowIcon(app_icon)
        # try:
        #     myappid = 'ccr.project.client'
        #     ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        # except Exception:
        #     pass

        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)

        self.layout = QHBoxLayout(self.central_widget)
        self.layout.setSpacing(theme.GAP_PANEL)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.thumbnail_list = ThumbnailList(self.on_image_selected)
        self.thumbnail_list.attention_action.connect(self._on_attention_action)
        self.thumbnail_list.setFixedWidth(216)

        self.image_preview = ImagePreview(self)
        self.image_preview.setMinimumWidth(900)

        self.sliders_panel = SlidersPanel(self)
        self.sliders_panel.setFixedWidth(theme.PANEL_W)
        self.sliders_panel.set_sliders_enabled(False)
        self.sliders_panel.image_preview = self.image_preview

        # Dust Removal panel — covers the sliders panel while in dust mode. Kept
        # as a SIBLING of sliders_panel (not a QStackedWidget wrapping it) so
        # SlidersPanel's parent().parent() chain to MainWindow stays valid.
        # See spec/dust-removal.md.
        self.dust_panel = DustRemovalPanel(self, self.image_preview)
        self.dust_panel.setFixedWidth(theme.PANEL_W)
        self.dust_panel.setVisible(False)
        self.image_preview.set_dust_panel(self.dust_panel)

        # Crop panel — covers the sliders panel while in crop mode, same sibling
        # pattern as dust_panel (keeps SlidersPanel's parent chain valid). See
        # spec/crop-panel.md.
        self.crop_panel = CropPanel(self, self.image_preview)
        self.crop_panel.setFixedWidth(theme.PANEL_W)
        self.crop_panel.setVisible(False)
        self.image_preview.set_crop_panel(self.crop_panel)

        # Tethering (watch-folder capture) state + status banner. The banner is
        # installed INTO image_preview's own layout (not wrapped around it) so
        # ImagePreview's parent().parent() chains stay valid — see
        # spec/camera-tethering.md §9.1.5.
        self._tether_active = False
        self._tether_thread = None
        self._tether_worker = None
        self._tether_timer = None
        self._tether_save_timer = None
        self._tether_scanner = None
        self._tether_folder = None
        self._tether_count = 0
        self._tether_unavailable = False
        self._tether_prompted = False          # B/W-point prompt shown this session?
        self._tether_import_existing = set()   # paths imported at start (no prompt)
        self._tether_grouper = None            # trichrome: files waiting for a triplet
        self._tether_attention = []            # greyed-out shots needing a decision
        self._attention_seq = 0
        self._tether_lead_img = None           # the first new capture (film lead)
        self._tether_last_lit = None           # newest capture lit enough for a base
        self.tether_banner = TetherBanner()
        self.tether_banner.stopRequested.connect(self.stop_tethering)
        self.tether_banner.scanTypeChanged.connect(self.set_scan_type)
        self.tether_banner.blackPointRequested.connect(self.start_roll_black_point)
        self.image_preview.set_tether_banner(self.tether_banner)

        # Contact sheet (spec/contact-sheet.md): a SIBLING of image_preview,
        # shown in its place — not a stack around it, so ImagePreview's
        # parent().parent() chain to MainWindow stays valid.
        from widgets.contact_sheet import ContactSheet, ContactSheetView
        self.contact_sheet_model = ContactSheet(self)
        self.contact_sheet = ContactSheetView(self.contact_sheet_model, self)
        self.contact_sheet.setMinimumWidth(900)
        self.contact_sheet.setVisible(False)
        self.contact_sheet.frameActivated.connect(self.open_frame_from_sheet)
        self.contact_sheet.autoCropRequested.connect(self._auto_crop_from_sheet)
        self.contact_sheet.closeRequested.connect(lambda: self.show_contact_sheet(False))
        # Keep the sheet (and its thumbnail) current: often while it is showing,
        # now and then otherwise. Only frames that changed are redrawn.
        self._sheet_timer = QTimer(self)
        self._sheet_timer.setInterval(1500)
        self._sheet_timer.timeout.connect(self.contact_sheet_model.update)
        self.contact_sheet_model.changed.connect(
            lambda: self.thumbnail_list.set_sheet_icon(self.contact_sheet_model.thumbnail(176, 120)))
        self._sheet_timer.start()

        self.layout.addWidget(self.thumbnail_list, 0)
        self.layout.addWidget(self.image_preview, 3)
        self.layout.addWidget(self.contact_sheet, 3)
        self.layout.addWidget(self.sliders_panel, 0)
        self.layout.addWidget(self.dust_panel, 0)
        self.layout.addWidget(self.crop_panel, 0)

        # Persisted UI preferences (last-open location etc.)
        self._settings = QSettings("FreeCCR", "FreeCCR")

        # Restore the global Positive-mode toggle BEFORE any image loads so the
        # first batch decodes in the right mode (see spec/positive-mode.md).
        positive_mode = self._settings.value("import/positive_mode", False, type=bool)
        ccr_backend.positive_mode = positive_mode
        # Monochrome RAW interpretation — also a decode-level global, so restore
        # it before the first load too. See spec/monochrome-raw-mode.md.
        ccr_backend.mono_raw = self._settings.value(
            "import/mono_raw", False, type=bool)
        # Restore the global 3-way RGB merge toggle too (affects the next import).
        ccr_backend.rgb_merge_mode = self._settings.value(
            "import/rgb_merge_mode", False, type=bool)
        # Merge detail: demosaic (full res, default) vs single photosite (half
        # res). Captured per image at import. See spec/trichrome-demosaic-mode.md.
        ccr_backend.rgb_merge_demosaic = self._settings.value(
            "import/rgb_merge_demosaic", True, type=bool)
        # Merge detail → Monochrome (overrides the demosaic flag). See
        # spec/trichrome-mono-read.md.
        ccr_backend.rgb_merge_mono = self._settings.value(
            "import/rgb_merge_mono", False, type=bool)
        # What is being scanned (spec/scan-type.md). The monitor's old
        # Trichrome tick box is its first value when nothing is stored yet.
        from core import scan_type as _st
        stored = self._settings.value("scan/type", "", type=str)
        if not stored:
            legacy = self._settings.value("live/trichrome", None)
            if legacy is not None:
                stored = "trichrome" if str(legacy).lower() in ("true", "1") else "colour"
        ccr_backend.scan_type = _st.clean(stored)
        # Restore the "replace originals with linear TIFF" opt-in. When on with
        # merge mode, a successful merge import offers (with confirmation) to bake
        # each frame to a linear TIFF and delete the source RAWs. See
        # spec/merge-linear-tiff-replace.md.
        ccr_backend.rgb_merge_replace = self._settings.value(
            "import/rgb_merge_replace", False, type=bool)
        # Guards re-entrancy of the merge-replace flow (its reload also finishes
        # through _cleanup_loader).
        self._merge_replacing = False
        # Restore the two-point B/W Density-inversion default (ON — density keeps
        # real highlight headroom in the working space; linear has ~0.15 stops).
        # New two-point conversions bake this; see spec/density-bwpoint-toggle.md.
        ccr_backend.density_bwpoint = self._settings.value(
            "conversion/density_bwpoint", True, type=bool)
        # Restore the Auto Gain toggle (default ON). When on, a hidden live offset
        # on the Gain stage places the top-0.1% in-bound highlight at 99% of the
        # working-space window (supersedes the baked auto-exposure). See
        # spec/auto-gain.md.
        ccr_backend.auto_gain = self._settings.value(
            "adjust/auto_gain", True, type=bool)
        # Auto tone (spec/auto-tone.md), default on.
        ccr_backend.auto_tone = self._settings.value(
            "adjust/auto_tone", True, type=bool)
        # Auto white point per frame (spec/auto-white-point.md), default on.
        ccr_backend.auto_white_point = self._settings.value(
            "convert/auto_white_point", True, type=bool)
        self.sliders_panel._update_bwp_mode_label()
        # Confirm before a no-anchor conversion (spec/no-anchor-convert.md).
        ccr_backend.warn_no_anchor_convert = self._settings.value(
            "convert/warn_no_anchor", True, type=bool)
        # Auto crop learns from confirmed crops (spec/auto-crop-learning.md).
        ccr_backend.auto_crop_learn = self._settings.value(
            "crop/learn", True, type=bool)
        # Input transfer function for non-RAW (TIFF) imports: ask on import by
        # default, and remember the answer when the user opts out of asking.
        # Affects the NEXT import only. See spec/input-transfer-function.md.
        ccr_backend.tiff_transfer_ask = self._settings.value(
            "input/tiff_transfer_ask", True, type=bool)
        ccr_backend.tiff_transfer_mode = self._settings.value(
            "input/tiff_transfer_mode", "linear", type=str)
        ccr_backend.tiff_transfer_manual = self._settings.value(
            "input/tiff_transfer_manual", "srgb", type=str)
        # Restore the Gamma application mode (default OFF = per-channel). When on,
        # the Gamma slider is applied to luminance and RGB is scaled together, so
        # midtone moves don't shift hue. See spec/gamma-luminance-mode.md.
        ccr_backend.gamma_luminance = self._settings.value(
            "adjust/gamma_luminance", False, type=bool)
        # Restore full-resolution zoom (default ON): the zoom detail tile is
        # decoded at whatever resolution the zoom needs, up to the source's own,
        # so 100% shows real source pixels. See spec/full-res-zoom.md.
        ccr_backend.full_res_zoom = self._settings.value(
            "view/full_res_zoom", True, type=bool)
        ccr_backend.sharp_fit_preview = self._settings.value(
            "view/sharp_fit", True, type=bool)
        # Preview detail subsumes both flags (spec/preview-detail.md); without a
        # saved level, derive one from them.
        level = self._settings.value("view/preview_detail", "", type=str)
        if not level:
            level = ccr_backend.preview_detail_from_flags(
                ccr_backend.sharp_fit_preview, ccr_backend.full_res_zoom)
        ccr_backend.set_preview_detail(level)
        # Restore the Auto WB toggle + algorithm (defaults OFF / Gray World).
        # When on, a fresh conversion writes AWB-estimated temperature/tint into
        # the image's sliders — only when neither is already set. Affects only
        # FUTURE conversions and the AWB button. See spec/auto-white-balance.md.
        from core.awb import AWB_ALGORITHMS, AWB_DEFAULT
        ccr_backend.auto_awb = self._settings.value(
            "adjust/auto_awb", False, type=bool)
        stored_algo = self._settings.value("adjust/awb_algorithm", AWB_DEFAULT)
        ccr_backend.awb_algorithm = (
            stored_algo if any(a == stored_algo for a, _ in AWB_ALGORITHMS)
            else AWB_DEFAULT)
        # Restore the Channel Balance nudge keys (default OFF — the sliders they
        # move live in a collapsed section). Read before the shortcuts are built
        # below, which is what decides whether they are created enabled.
        ccr_backend.balance_hotkeys = self._settings.value(
            "adjust/balance_hotkeys", False, type=bool)
        # Restore the reversal-look sprocket-hole mask (default OFF). When on,
        # clear-film regions are painted white as the last render step for
        # B/W-point conversions. See spec/sprocket-hole-mask.md.
        ccr_backend.sprocket_mask_white = self._settings.value(
            "adjust/sprocket_mask_white", False, type=bool)
        # Reflect the restored mode in the toolbar/slider gating right away
        # (no images yet, but the negative-only actions should already grey out).
        self.image_preview._update_unconvert_action_state()

        # Restore a persisted global B/W point (set in a prior session) so
        # tethering can auto-convert captures right away. Before any image loads.
        self._restore_bwpoint()
        # Reflect the restored B/W state (and the panel's restored film-stock
        # selection) in the slope-source label right away.
        self.sliders_panel._update_bwp_mode_label()

        # App-wide (2.2.1): Up/Down switch image no matter which widget has
        # focus, and drops anywhere in this window load files. eventFilter
        # scopes both to this window. ONE app-wide hook, pointed at the newest
        # window, rather than a filter per window: every live window's filter
        # would run on every event in the app (the test suite keeps dozens).
        _AppWideHook.point_at(self)
        self.setAcceptDrops(True)
        self.create_menu()

        # Restore the active camera-profile selection (applied to every decode).
        # Done before any images load so the first batch picks it up. A fresh
        # install / a vanished or unreadable profile defaults to None (bare
        # camera-native RAW). Camera Matrix (Adobe RGB + auto-scale) and ICC/DCP
        # profiles are opt-in; "none" is the explicit bare-RAW selection.
        CM = ccr_backend.CAMERA_MATRIX
        saved = self._settings.value("import/active_profile_path", "", type=str)

        def _restore(sel):
            try:
                ccr_backend.set_active_profile(sel)
                self._settings.setValue("import/active_profile_path",
                                        sel if sel else "none")
            except Exception:
                ccr_backend.set_active_profile(None)
                self._settings.setValue("import/active_profile_path", "none")

        if saved == "none":
            ccr_backend.set_active_profile(None)
        elif saved == CM:
            ccr_backend.set_active_profile(CM)
        elif saved and os.path.exists(saved):
            _restore(saved)
        else:
            legacy = (self._settings.value("import/input_icc_path", "", type=str)
                      or self._settings.value("import/input_dcp_path", "", type=str))
            _restore(legacy if (legacy and os.path.exists(legacy)) else None)
        self.thumbnail_list.refresh_profile_combo()
        self._refresh_profile_ui()

        # Restore the active field-correction profile (independent of the camera
        # profile; also applied to every decode). A vanished or unreadable file
        # falls back to no correction and clears the setting.
        saved_ff = self._settings.value("import/field_profile_path", "", type=str)
        if saved_ff and saved_ff != "none" and os.path.exists(saved_ff):
            try:
                ccr_backend.set_active_field_profile(saved_ff)
            except Exception as e:
                print(f"Could not restore field correction {saved_ff}: {e}")
                ccr_backend.set_active_field_profile(None)
                self._settings.remove("import/field_profile_path")
        elif saved_ff and saved_ff != "none":
            self._settings.remove("import/field_profile_path")

        # Ctrl+Z (Cmd+Z on macOS): undo the last action on the current image;
        # repeated presses walk further back through the undo stack.
        self.undo_shortcut = QShortcut(QKeySequence.Undo, self)
        self.undo_shortcut.activated.connect(self.undo_last_action)

        # Mode hotkeys: C = crop, D = dust removal, W = white-balance picker.
        # WindowShortcut context (like the rotate/undo keys) so they fire no
        # matter which panel has focus. Each handler mirrors the corresponding
        # toolbar/panel button and is a no-op without a current image.
        self.crop_shortcut = QShortcut(QKeySequence("C"), self)
        self.crop_shortcut.activated.connect(self._shortcut_toggle_crop)
        self.dust_shortcut = QShortcut(QKeySequence("D"), self)
        self.dust_shortcut.activated.connect(self._shortcut_toggle_dust)
        self.wb_pick_shortcut = QShortcut(QKeySequence("W"), self)
        self.wb_pick_shortcut.activated.connect(self._shortcut_wb_pick)

        # Channel Balance nudge keys: U/I/O raise R/G/B, J/K/L lower them —
        # top row adds, home row subtracts. Same WindowShortcut context as the
        # mode keys above, so they fire whichever panel has focus; modal dialogs
        # are separate windows, so they never reach text entry there.
        # OPT-IN (Settings → General → Keyboard, default off): they are gated by
        # setEnabled rather than by a check in the handler, so while off the six
        # letters are not consumed at all. See spec/white-balance-restore.md.
        self._balance_shortcuts = []
        for key, channel, direction in (("U", "r", +1), ("I", "g", +1),
                                        ("O", "b", +1), ("J", "r", -1),
                                        ("K", "g", -1), ("L", "b", -1)):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(
                lambda c=channel, d=direction: self._shortcut_nudge_balance(c, d))
            self._balance_shortcuts.append(sc)
        self._apply_balance_hotkey_state()


        # Non-blocking startup update check (2s network timeout, off-thread)
        self._update_worker = UpdateCheckWorker(self)
        self._update_worker.finished.connect(self._on_update_check_done)
        QTimer.singleShot(1000, self._update_worker.start)

    def _on_update_check_done(self, release):
        if not release:
            return
        if not self.isVisible():
            return  # window already closed — app is shutting down
        if QApplication.activeModalWidget() is not None:
            # Don't stack on top of a file picker / progress / exit prompt
            QTimer.singleShot(
                3000, lambda: self._on_update_check_done(release))
            return
        current = parse_version(VERSION)
        skipped = parse_version(
            self._settings.value("update/skipped_version", "", type=str))
        if not should_offer_update(release["version"], current, skipped):
            return
        dialog = UpdateAvailableDialog(self, release, VERSION)
        choice = dialog.exec()
        if choice == UpdateAvailableDialog.UPDATE:
            webbrowser.open(release["url"])
        elif choice == UpdateAvailableDialog.SKIP:
            # Stay quiet until a release with a bigger major/minor appears
            self._settings.setValue("update/skipped_version", release["tag"])

    def closeEvent(self, event):
        reply = QMessageBox.question(
            self,
            "Confirm Exit",
            "Are you sure you want to exit FreeCCR?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            # Stop any active tethering session (poller + worker thread) cleanly
            # before persisting, so no capture is mid-write into a locked catalog.
            if self._tether_active:
                self.stop_tethering()
            # The live monitor turns the Scanlight off and releases the
            # camera and the serial port.
            if self._live_monitor is not None:
                self._live_monitor.close()
            # Stop any in-flight dust model download / detection thread so a
            # running QThread can't abort the process at teardown.
            if hasattr(self, "dust_panel"):
                self.dust_panel.shutdown()
            # Same for in-flight hi-res zoom renders (parentless QThreads
            # tracked by the preview): destroyed-while-running aborts the exit.
            if hasattr(self, "image_preview"):
                self.image_preview.shutdown_workers()
            # And the live slider render (spec/slider-speed.md).
            if hasattr(self, "sliders_panel"):
                self.sliders_panel.shutdown_live_render()
            # And for loader threads — active or previously abandoned. Their
            # RAW decodes are not interruptible, so wait them out; the
            # load-generation guard keeps their results from landing.
            self._stop_loader_if_running()
            for t in list(getattr(self, '_abandoned_loaders', [])):
                try:
                    t.wait()
                except RuntimeError:
                    pass
            # Persist the edit catalog so reopening these files restores
            # their conversion/slices/crop/adjustments.
            ccr_backend.save_catalog()
            event.accept()
        else:
            event.ignore()

    def on_image_selected(self, file_path):
        pass

    # --- Contact sheet (spec/contact-sheet.md) ------------------------------
    def contact_sheet_shown(self) -> bool:
        return not self.contact_sheet.isHidden()

    def show_contact_sheet(self, on=True) -> bool:
        """Show the contact sheet in place of the image view (or go back).
        It is not a scan, so the per-frame controls are greyed while it shows
        (Convert All and Auto Crop All stay: their result shows up live).
        Refused while cropping or removing dust. True when the state changed
        as asked."""
        ip = self.image_preview
        sp = self.sliders_panel
        if on:
            if ip.crop_mode or getattr(ip, "dust_mode", False):
                sp.set_temporary_hint(
                    "Finish cropping or dust removal first (Done or Esc), then "
                    "open the contact sheet.", duration=5000)
                self.thumbnail_list.set_sheet_checked(False)
                return False
            if not ccr_backend.images:
                self.thumbnail_list.set_sheet_checked(False)
                return False
            ip.setVisible(False)
            self.contact_sheet.setVisible(True)
            self.contact_sheet.activate()
            self._sheet_timer.setInterval(400)
            sp.set_sliders_enabled(False)
            for btn in ("convert_current_bwp_btn", "auto_crop_btn", "wp_combo", "wp_show_btn"):
                if hasattr(sp, btn):
                    getattr(sp, btn).setEnabled(False)
        else:
            was = self.contact_sheet_shown()
            self.contact_sheet.setVisible(False)
            ip.setVisible(True)
            self._sheet_timer.setInterval(1500)
            if was:
                # back to the frame you were on, with its own controls
                if hasattr(sp, "auto_crop_btn"):
                    sp.auto_crop_btn.setEnabled(True)
                if ip.current_idx is not None:
                    sp.set_current_idx(ip.current_idx)
                ip._update_unconvert_action_state()
        self.thumbnail_list.set_sheet_checked(on)
        return True

    def open_frame_from_sheet(self, idx, crop=False):
        """A tile was clicked (or double-clicked: open it in the Crop panel)."""
        self.show_contact_sheet(False)
        lst = self.thumbnail_list.thumbnail_list
        if lst.currentRow() != idx:
            lst.setCurrentRow(idx)          # drives update_preview + set_current_idx
        else:
            self.image_preview.update_preview(idx)
        if crop:
            self.toggle_crop_panel(True)

    def _auto_crop_from_sheet(self, idx):
        img = ccr_backend.get_image_by_index(idx)
        if img is None:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            frame, applied = ccr_backend.auto_crop_one(img)
        finally:
            QApplication.restoreOverrideCursor()
        if applied:
            self.thumbnail_list.update_thumbnail(idx)
            ccr_backend.save_catalog()
        self.sliders_panel.set_temporary_hint(
            (f"Auto crop: {frame.summary()} ({frame.confidence} confidence)." if applied
             else f"Auto crop left this frame as it was: "
                  f"{frame.reason or frame.confidence + ' confidence'}."), duration=6000)
        self.contact_sheet_model.update()

    # --- Dust removal (panel cover + canvas mode) -------------------------
    def toggle_dust_removal(self, on=None):
        """Show/hide the Dust Removal panel (covering the sliders) and
        enter/exit the canvas dust mode. `on=None` flips the current state."""
        if on is None:
            on = not self.image_preview.dust_mode
        if on:
            if self.image_preview.current_idx is None:
                self._sync_dust_action(False)
                return
            if not self.image_preview.enter_dust_mode():
                self._sync_dust_action(False)
                return
            self.sliders_panel.setVisible(False)
            self.dust_panel.bind_image()
            self.dust_panel.setVisible(True)
            self._sync_dust_action(True)
        else:
            self.dust_panel.cancel_jobs()
            self.image_preview.exit_dust_mode()
            self._show_sliders_panel()

    def _show_sliders_panel(self):
        self.dust_panel.setVisible(False)
        self.sliders_panel.setVisible(True)
        self._sync_dust_action(False)

    # --- Crop (panel cover + canvas crop mode) ---------------------------
    def toggle_crop_panel(self, on=None):
        """Show/hide the Crop panel (covering the sliders) and enter/exit the
        canvas crop mode. `on=None` flips the current state. Mirrors
        toggle_dust_removal. Exit is also driven by the canvas chokepoint
        (_exit_crop_mode -> on_crop_panel_closed) for Enter/Esc/right-click."""
        if on is None:
            on = not self.image_preview.crop_mode
        if on:
            if self.image_preview.current_idx is None:
                return
            if self.image_preview.crop_mode:
                return
            if not self.image_preview.enter_crop_mode():
                return
            self.sliders_panel.setVisible(False)
            self.dust_panel.setVisible(False)
            self.crop_panel.bind_image()
            self.crop_panel.setVisible(True)
        else:
            # Done button: commit the pending crop. confirm_crop -> _exit_crop_mode
            # -> on_crop_panel_closed restores the panel; if already out of crop
            # mode, restore directly.
            if self.image_preview.crop_mode:
                self.image_preview.confirm_crop()
            else:
                self.on_crop_panel_closed()

    def on_crop_panel_closed(self):
        """Restore the sliders panel after crop mode ends by any path. Guarded
        so entering dust mode from crop (which also exits crop) doesn't fight the
        dust panel."""
        self.crop_panel.setVisible(False)
        if not self.dust_panel.isVisible():
            self.sliders_panel.setVisible(True)

    # --- Mode hotkeys (C / D / W) ----------------------------------------
    def _shortcut_toggle_crop(self):
        """C: toggle Crop mode. Dust must be left first — the dust and crop
        panels both cover the sliders, and only the toggle chokepoints restore
        the panel stack correctly."""
        if self.image_preview.current_idx is None:
            return
        if self.image_preview.dust_mode:
            self.toggle_dust_removal(False)
        self.toggle_crop_panel(not self.image_preview.crop_mode)

    def _shortcut_toggle_dust(self):
        """D: toggle Dust Removal mode. Leave crop first (commits it, like the
        Crop panel's Done) so the panel stack swaps cleanly."""
        if self.image_preview.current_idx is None:
            return
        if not self.image_preview.dust_mode and self.image_preview.crop_mode:
            self.toggle_crop_panel(False)
        self.toggle_dust_removal()

    def _shortcut_wb_pick(self):
        """W: toggle the white-balance eyedropper — a press arms it (next click
        picks a neutral point), a second press disarms it. Gated exactly like
        the WB Picker button — converted image only, never while crop/dust mode
        owns the canvas (which already clears any armed pick)."""
        if self.image_preview.current_idx is None:
            return
        if self.image_preview.dust_mode or self.image_preview.crop_mode:
            return
        if not self.sliders_panel.wb_picker_btn.isEnabled():
            return
        self.sliders_panel._on_pick_neutral_point()

    def _shortcut_nudge_balance(self, channel, direction):
        """U/I/O and J/K/L: nudge one R/G/B Balance slider. Only reachable while
        the keys are enabled (Settings → General → Keyboard) — the shortcuts
        themselves are disabled otherwise. Gated like the other mode keys —
        needs a current image, and never fires while crop or dust mode owns the
        canvas (those modes have their own keyboard semantics). The panel itself
        no-ops when its sliders are disabled."""
        if self.image_preview.current_idx is None:
            return
        if self.image_preview.dust_mode or self.image_preview.crop_mode:
            return
        self.sliders_panel.nudge_balance(channel, direction)

    def _apply_balance_hotkey_state(self):
        """Enable/disable the six nudge shortcuts from the global flag. A
        disabled QShortcut does not consume its key, so U/I/O/J/K/L stay
        entirely free while the setting is off."""
        for sc in self._balance_shortcuts:
            sc.setEnabled(bool(ccr_backend.balance_hotkeys))

    def _sync_dust_action(self, checked: bool):
        action = getattr(self.image_preview, "dust_action", None)
        if action is not None:
            action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(False)

    def undo_last_action(self):
        """Restore the current image's previous state (adjustments, crop,
        rotation, flips). Each Ctrl+Z press pops one more snapshot."""
        # In dust mode, Ctrl+Z means "undo the last dust spot" (same as the
        # panel's button) and must PRESERVE the viewport — the general undo
        # below resets zoom (crop/rotation can move the displayed content),
        # which read as "Ctrl+Z unzoomed" while spotting dust zoomed-in.
        if getattr(self.image_preview, "dust_mode", False):
            self.dust_panel._on_undo_last()
            return
        # While Compare is held, the backend holds a temporary zeroed state
        # that the button release will overwrite — undoing now would be lost.
        if hasattr(self.sliders_panel, "_original_adjustment"):
            return
        idx = self.image_preview.current_idx
        img = ccr_backend.get_image_by_index(idx) if idx is not None else None
        if img is None:
            return
        if not img.pop_undo_state():
            self.sliders_panel.set_temporary_hint("Nothing to undo.", duration=2000)
            return
        # End the coalescing bursts so the next edit pushes a fresh snapshot
        # of the just-restored state instead of merging into a stale burst.
        self.sliders_panel.end_undo_burst()
        self.image_preview.end_undo_bursts()
        # Undo can change crop/rotation/flips, which moves or resizes the
        # displayed content — a preserved zoom would strand the viewport.
        self.image_preview._reset_zoom()
        img.update_thumbnail_and_preview()
        # Keep the Color Profile dropdown in step with the restored state.
        self.sliders_panel._sync_color_profile_combo(idx)
        self.thumbnail_list.update_thumbnail(idx)
        self.image_preview.update_preview(idx)
        remaining = len(img.undo_stack)
        self.sliders_panel.set_temporary_hint(
            f"Undo applied ({remaining} more available)." if remaining else "Undo applied.",
            duration=2500)

    def create_menu(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("File")

        open_action = file_menu.addAction("Open Files")
        open_action.triggered.connect(self.open_files)

        open_folder_action = file_menu.addAction("Open Folder")
        open_folder_action.triggered.connect(self.open_folder)

        self.tether_action = file_menu.addAction("Hot Folder Tether…")
        self.tether_action.triggered.connect(self.toggle_tethering)

        # The camera's live view and the Scanlight (spec/live-capture-monitor.md)
        self.live_monitor_action = file_menu.addAction("Live Capture Monitor…")
        self.live_monitor_action.triggered.connect(self.open_live_monitor)

        file_menu.addSeparator()
        export_action = file_menu.addAction("Export…")
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self.image_preview.open_export_dialog)

        # Settings — the colour-management features (input ICC/DCP, the IT8
        # camera-profile wizard, Positive mode) live in its Color Management tab.
        file_menu.addSeparator()
        settings_action = file_menu.addAction("Settings…")
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self.open_settings)

        # Add Help menu with About, Licenses, and Help actions
        help_menu = menu_bar.addMenu("Help")
        about_action = help_menu.addAction("About")
        about_action.triggered.connect(self.show_about_dialog)

        licenses_action = help_menu.addAction("Licenses")
        licenses_action.triggered.connect(self.show_licenses_dialog)

        help_action = help_menu.addAction("Help")
        help_action.triggered.connect(self.open_help_website)

    # --- Color management (global input profile, lives in Settings) -------
    def open_settings(self):
        """Open the Settings dialog (Color Management tab holds the input
        ICC/DCP, the IT8 wizard, and Positive mode)."""
        from widgets.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        self._settings_dialog = dlg
        try:
            dlg.exec()
        finally:
            self._settings_dialog = None

    def _refresh_profile_ui(self):
        """Reflect the active input profile in the Settings dialog when it is
        open (the active profile is shown there now, not in the File menu)."""
        dlg = getattr(self, "_settings_dialog", None)
        if dlg is not None and dlg.isVisible():
            dlg.refresh_color_management()

    def _refresh_profile_mismatch(self):
        """A camera-profile change does NOT re-decode loaded images (chosen
        behaviour). Instead it re-flags the thumbnails whose image was graded
        under a different profile, so the user can re-grade them on demand."""
        self.thumbnail_list.refresh_profile_warnings()

    def _refresh_profile_combo(self):
        """Repopulate the thumbnail-panel picker (after import / delete / generate /
        select) so it reflects the library and the active selection."""
        self.thumbnail_list.refresh_profile_combo()

    def set_active_profile_path(self, path):
        """Select the active camera profile from the library (a library path), or
        None for no profile. Persisted; re-flags thumbnail mismatches (no re-decode)
        and refreshes the picker + dialog. Replaces the old set/disable handlers."""
        from core.color_management import UnsupportedICCError
        try:
            name = ccr_backend.set_active_profile(path)
        except (UnsupportedICCError, Exception) as e:
            QMessageBox.warning(self, "Camera profile",
                                f"Could not activate the profile:\n\n{e}")
            self._refresh_profile_combo()
            return False
        # Persist the literal selection ("none" so it's distinct from a fresh
        # install, which defaults to Camera Matrix at startup).
        self._settings.setValue("import/active_profile_path", path if path else "none")
        self._refresh_profile_combo()
        self._refresh_profile_mismatch()
        self._refresh_profile_ui()
        self.sliders_panel.set_temporary_hint(
            f"Camera profile: {name}" if name else "Camera profile: None",
            duration=3000)
        return True

    def import_camera_profile_dialog(self):
        """Pick an external .icc/.icm/.dcp, copy it into FreeCCR's library (so it is
        kept for re-selection), and activate it."""
        start_dir = self._settings.value("import/last_icc_dir", "", type=str)
        path, _ = QFileDialog.getOpenFileName(
            self, "Import camera profile", start_dir,
            "Camera profiles (*.icc *.icm *.dcp);;All Files (*)")
        if not path:
            return
        self._settings.setValue("import/last_icc_dir", os.path.dirname(path))
        try:
            lib = ccr_backend.import_camera_profile(path)
        except Exception as e:
            QMessageBox.warning(self, "Import camera profile",
                                f"This file can't be used as a camera profile:\n\n{e}")
            return
        self.set_active_profile_path(lib)

    def delete_camera_profile(self, path):
        """Remove a profile from the library (with confirmation). Deactivates it
        first if it is the active one."""
        name = os.path.splitext(os.path.basename(path))[0]
        if QMessageBox.question(
                self, "Delete camera profile",
                f"Remove “{name}” from your camera-profile library?\n"
                "The file is deleted from FreeCCR's workspace.") != QMessageBox.Yes:
            return
        was_active = (path == self._settings.value("import/active_profile_path", "", type=str))
        ccr_backend.delete_camera_profile(path)
        if was_active:
            self._settings.remove("import/active_profile_path")
        self._refresh_profile_combo()
        self._refresh_profile_mismatch()
        self._refresh_profile_ui()

    def create_camera_profile_from_it8(self):
        """Open the IT8 wizard; on success add the new profile to the library and
        optionally make it the active profile."""
        from widgets.it8_profile_dialog import IT8ProfileDialog
        current_path = None
        current_merge = None
        idx = self.image_preview.current_idx
        if idx is not None:
            img = ccr_backend.get_image_by_index(idx)
            if img is not None:
                current_path = img.file_path
                # A trichrome capture is its own device space, so the wizard has
                # to profile the MERGE, not one source frame. Hand it the triplet
                # so "Use current image" works on a loaded 3-way merge.
                if getattr(img, "is_merged", False) and img.merge_sources:
                    current_merge = (list(img.merge_sources),
                                     getattr(img, "merge_demosaic", True),
                                     getattr(img, "merge_mono", False))
        dlg = IT8ProfileDialog(self, current_path=current_path,
                               current_merge=current_merge)
        if dlg.exec() != QDialog.Accepted or not dlg.saved_path:
            return
        base = os.path.basename(dlg.saved_path)
        # Ensure the freshly-saved profile is in the library (the wizard saves there
        # by default; a custom save location is copied in), then refresh the picker.
        lib = dlg.saved_path
        try:
            lib = ccr_backend.import_camera_profile(dlg.saved_path)
        except Exception:
            pass
        self._refresh_profile_combo()
        self._refresh_profile_ui()
        if dlg.apply_now and self.set_active_profile_path(lib):
            self.sliders_panel.set_temporary_hint(
                f"Camera profile saved and applied: {base}", duration=4000)
        else:
            self.sliders_panel.set_temporary_hint(
                f"Camera profile saved: {base}", duration=4000)

    # --- Field correction (flat-field, global + persistent) ---------------
    def set_active_field_profile(self, path):
        """Select the active field-correction profile (a library path), or None.
        Persisted; re-flags thumbnail mismatches (no re-decode, exactly like a
        camera-profile change). See spec/flat-field-correction.md §6.3."""
        from core.flat_field import FieldProfileError
        try:
            name = ccr_backend.set_active_field_profile(path)
        except (FieldProfileError, Exception) as e:
            QMessageBox.warning(self, "Field correction",
                                f"Could not use this profile:\n\n{e}")
            return False
        self._settings.setValue("import/field_profile_path", path if path else "none")
        self._refresh_profile_mismatch()
        self.sliders_panel.set_temporary_hint(
            f"Field correction: {name}" if name else "Field correction: None",
            duration=3000)
        return True

    def delete_field_profile(self, path):
        """Remove a field-correction profile from the library (with
        confirmation), deactivating it first if it is the active one."""
        name = os.path.splitext(os.path.basename(path))[0]
        if QMessageBox.question(
                self, "Delete field correction profile",
                f"Remove “{name}” from your field-correction library?\n"
                "The file is deleted from FreeCCR's workspace.") != QMessageBox.Yes:
            return
        was_active = (path == self._settings.value(
            "import/field_profile_path", "", type=str))
        ccr_backend.delete_field_profile(path)
        if was_active:
            self._settings.remove("import/field_profile_path")
        self._refresh_profile_mismatch()

    def create_field_correction_profile(self):
        """Open the field-correction wizard; on success the profile is already in
        the library, so just activate it if the user asked for that."""
        from widgets.field_correction_dialog import FieldCorrectionDialog
        current_path = None
        idx = self.image_preview.current_idx
        if idx is not None:
            img = ccr_backend.get_image_by_index(idx)
            if img is not None:
                current_path = img.file_path
        dlg = FieldCorrectionDialog(self, current_path=current_path)
        if dlg.exec() != QDialog.Accepted or not dlg.saved_path:
            return
        base = os.path.basename(dlg.saved_path)
        if dlg.apply_now and self.set_active_field_profile(dlg.saved_path):
            self.sliders_panel.set_temporary_hint(
                f"Field correction saved and applied: {base}", duration=4000)
        else:
            self.sliders_panel.set_temporary_hint(
                f"Field correction saved: {base}", duration=4000)

    # --- Positive mode (global, persistent) -------------------------------
    def on_positive_mode_toggled(self, checked: bool):
        """Flip the global Positive-mode flag, persist it, and re-decode all
        loaded images in the new mode (keep adjustments, drop conversion).
        Mirrors the input-ICC reprocess. See spec/positive-mode.md."""
        ccr_backend.positive_mode = bool(checked)
        self._settings.setValue("import/positive_mode", bool(checked))
        if ccr_backend.images:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                ccr_backend.reprocess_all_for_positive_mode_change()
            finally:
                QApplication.restoreOverrideCursor()
            # Every image was re-decoded, so the zoom hi-res cache (positive
            # decode base + render) is stale — drop it so it can't be re-adjusted
            # and shown over the freshly re-decoded scan.
            self.image_preview._release_hires(refresh=False)
            self.thumbnail_list.update_all_thumbnails()
            if self.image_preview.current_idx is not None:
                self.image_preview.update_preview(self.image_preview.current_idx)
            else:
                # No selection: still refresh the toolbar/slider gating.
                self.image_preview._update_unconvert_action_state()
            ccr_backend.save_catalog()
        else:
            # No images yet — just refresh the gating so the toolbar reflects
            # the new mode immediately.
            self.image_preview._update_unconvert_action_state()
        # Positive mode flips the effective profile signature (-> 'none') and
        # re-stamps every image, so re-flag the thumbnail mismatch warnings —
        # otherwise stale ⚠ markers persist (update_all_thumbnails only redraws
        # icons, never the item text/colour).
        self.thumbnail_list.refresh_profile_warnings()
        self.sliders_panel.set_temporary_hint(
            "Positive mode on — adjust any image directly."
            if checked else "Positive mode off — film-negative tools restored.",
            duration=4000)

    # --- Monochrome RAW interpretation (global, persistent) ---------------
    def on_mono_raw_toggled(self, checked: bool):
        """Flip the global "interpret RAW as monochrome" flag, persist it, and
        re-decode all loaded images in the new interpretation (keep adjustments,
        drop conversion). Mirrors Positive mode, because this likewise changes
        what read_image produces. See spec/monochrome-raw-mode.md."""
        ccr_backend.mono_raw = bool(checked)
        self._settings.setValue("import/mono_raw", bool(checked))
        if ccr_backend.images:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                ccr_backend.reprocess_all_for_mono_raw_change()
            finally:
                QApplication.restoreOverrideCursor()
            # Every image was re-decoded, so a baked hi-res zoom tile is stale.
            self.image_preview._release_hires(refresh=False)
            self.thumbnail_list.update_all_thumbnails()
            if self.image_preview.current_idx is not None:
                self.image_preview.update_preview(self.image_preview.current_idx)
            ccr_backend.save_catalog()
        self.sliders_panel.set_temporary_hint(
            "Monochrome RAW interpretation on — RAWs read as greyscale, and "
            "exports are single-channel." if checked else
            "Monochrome RAW interpretation off — RAWs read in colour again.",
            duration=5000)

    # --- 3-way RGB merge mode (global, persistent) ------------------------
    def on_rgb_merge_mode_toggled(self, checked: bool):
        """Flip the global 3-way RGB merge flag and persist it. Unlike Positive
        mode this does NOT re-process loaded images — it only affects the NEXT
        import (every 3 RAWs, sorted by filename, merged into one).
        See spec/three-way-rgb-merge.md."""
        ccr_backend.rgb_merge_mode = bool(checked)
        self._settings.setValue("import/rgb_merge_mode", bool(checked))
        if checked:
            msg = ("3-way RGB merge on — your next import merges every 3 RAWs "
                   "(R,G,B by filename).")
            if ccr_backend.positive_mode:
                msg += (" Positive mode is on; merged frames are negatives — "
                        "turn Positive off to invert them.")
        else:
            msg = "3-way RGB merge off."
        self.sliders_panel.set_temporary_hint(msg, duration=5000)

    _MERGE_DETAIL_LABELS = {
        "demosaic": "demosaic (full resolution)",
        "photosite": "single photosite (half resolution)",
        "mono": "monochrome (full resolution, no demosaic)",
    }

    def on_rgb_merge_detail_changed(self, mode: str):
        """Set how merge imports extract each frame's channel — "demosaic"
        (full res), "photosite" (half res) or "mono" (whole mosaic read as a
        monochrome sensor) — and persist it. "mono" overrides the demosaic
        flag and leaves it as it was, so switching back restores the previous
        Bayer choice. Captured per image at import — already-loaded merges keep
        their decode; only the NEXT import is affected. See
        spec/trichrome-demosaic-mode.md and spec/trichrome-mono-read.md."""
        label = self._MERGE_DETAIL_LABELS[mode]
        ccr_backend.rgb_merge_mono = (mode == "mono")
        if mode != "mono":
            ccr_backend.rgb_merge_demosaic = (mode == "demosaic")
        self._settings.setValue("import/rgb_merge_mono",
                                bool(ccr_backend.rgb_merge_mono))
        self._settings.setValue("import/rgb_merge_demosaic",
                                bool(ccr_backend.rgb_merge_demosaic))
        self.sliders_panel.set_temporary_hint(
            f"Trichrome merge detail: {label}. Applies to the next import.",
            duration=5000)

    def on_rgb_merge_demosaic_changed(self, demosaic: bool):
        """Bool form of on_rgb_merge_detail_changed (demosaic vs photosite)."""
        self.on_rgb_merge_detail_changed("demosaic" if demosaic else "photosite")

    def on_rgb_merge_replace_toggled(self, checked: bool):
        """Flip the global 'replace originals with linear TIFF' opt-in and persist
        it. When on with 3-way merge, a successful merge import prompts to bake
        each frame to a full-resolution linear TIFF and PERMANENTLY delete its
        source RAWs (see _maybe_replace_merged_with_tiff). Affects only the next
        import; nothing is deleted without confirmation.
        See spec/merge-linear-tiff-replace.md."""
        ccr_backend.rgb_merge_replace = bool(checked)
        self._settings.setValue("import/rgb_merge_replace", bool(checked))
        if checked:
            msg = ("Replace originals: your next merge import will offer to bake "
                   "linear TIFFs and delete the source RAWs (you'll confirm).")
            if not ccr_backend.rgb_merge_mode:
                msg += " Turn 3-way merge on to use it."
        else:
            msg = "Replace originals off — merge imports keep the source RAWs."
        self.sliders_panel.set_temporary_hint(msg, duration=5000)

    # --- Two-point B/W Density inversion (global, persistent) -------------
    def on_density_bwpoint_toggled(self, checked: bool):
        """Flip the global two-point Density-inversion default, persist it, and
        re-convert any loaded two-point B/W images in the new mode (density/log
        vs legacy linear). Other conversions are untouched.
        See spec/density-bwpoint-toggle.md."""
        ccr_backend.density_bwpoint = bool(checked)
        self._settings.setValue("conversion/density_bwpoint", bool(checked))
        if ccr_backend.images:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                ccr_backend.reprocess_all_for_density_bwpoint_change()
            finally:
                QApplication.restoreOverrideCursor()
            # Re-converted images invalidate the zoom hi-res cache.
            self.image_preview._release_hires(refresh=False)
            self.thumbnail_list.update_all_thumbnails()
            if self.image_preview.current_idx is not None:
                self.image_preview.update_preview(self.image_preview.current_idx)
            ccr_backend.save_catalog()
        self.sliders_panel.set_temporary_hint(
            "Density inversion on — two-point conversions recover optical density."
            if checked else
            "Density inversion off — two-point conversions use the linear stretch.",
            duration=5000)

    # --- Auto Gain (global, persistent) ----------------------------------
    def on_auto_gain_toggled(self, checked: bool):
        """Flip the global Auto Gain flag, persist it, and re-render all loaded
        images. Auto Gain is a hidden, live offset on Master Gain (it never
        moves the slider) and is NOT baked per image, so this only re-renders —
        no re-conversion and no catalog write. See spec/auto-gain.md."""
        ccr_backend.auto_gain = bool(checked)
        self._settings.setValue("adjust/auto_gain", bool(checked))
        self._rerender_all_for_global_mode(
            "Auto gain on — highlights auto-placed at the top of the window."
            if checked else
            "Auto gain off — Master Gain alone controls exposure.")

    def on_auto_tone_toggled(self, checked: bool):
        """Auto tone on / off (spec/auto-tone.md). Each converted frame keeps
        its measured toe/shoulder either way; this only decides whether the
        render applies it, so it re-renders without re-converting."""
        ccr_backend.auto_tone = bool(checked)
        self._settings.setValue("adjust/auto_tone", bool(checked))
        self._rerender_all_for_global_mode(
            "Auto tone on — shadows and highlights rolled into range where a "
            "lot would clip." if checked else
            "Auto tone off — black and white clip as converted.")

    # --- No-anchor convert warning (global, persistent) ------------------
    def on_auto_crop_learn_toggled(self, checked: bool):
        """Learn from confirmed crops on / off (spec/auto-crop-learning.md).
        Off also stops using what was learned; nothing is forgotten."""
        ccr_backend.auto_crop_learn = bool(checked)
        self._settings.setValue("crop/learn", bool(checked))

    def on_auto_white_point_toggled(self, checked: bool):
        """Auto white point on / off: the starting choice for frames that
        have none of their own (spec/white-point-choice.md). Affects the next
        Convert only: nothing already converted changes."""
        ccr_backend.auto_white_point = bool(checked)
        self._settings.setValue("convert/auto_white_point", bool(checked))
        self.sliders_panel._sync_wp_combo()
        self.sliders_panel.set_temporary_hint(
            "Auto white point on: frames start on Frame highlight, their brightest "
            "near-grey area." if checked else
            "Auto white point off: frames start with no white point (the default "
            "slope).", duration=5000)

    def on_warn_no_anchor_toggled(self, checked: bool):
        """Flip the confirm-before-unanchored-conversion flag and persist it.
        Pure UI gating — nothing is baked, rendered or re-converted, so unlike
        on_auto_gain_toggled there is no re-render pass.
        See spec/no-anchor-convert.md."""
        ccr_backend.warn_no_anchor_convert = bool(checked)
        self._settings.setValue("convert/warn_no_anchor", bool(checked))
        self.sliders_panel.set_temporary_hint(
            "Converting without a black point will ask for confirmation."
            if checked else
            "Converting without a black point will no longer ask — it does a "
            "direct invert; grade it with Channel Levels.",
            duration=5000)

    # --- Preview detail (spec/preview-detail.md) ---------------------------
    def on_preview_detail_changed(self, level: str):
        """Fast / Balanced / Full. Display resolution only: release the current
        detail tile and let the view ask again at the new size (no reprocess)."""
        level = ccr_backend.set_preview_detail(level)
        self._settings.setValue("view/preview_detail", level)
        # Keep the old keys in step so an older build still reads sensibly.
        self._settings.setValue("view/sharp_fit", ccr_backend.sharp_fit_preview)
        self._settings.setValue("view/full_res_zoom", ccr_backend.full_res_zoom)
        ip = self.image_preview
        ip._release_hires(refresh=True)
        ip._update_hires_state()
        names = {"full": "Full: sharpest preview, most processing.",
                 "balanced": "Balanced: screen renders up to 2000 px, zoom up to 6000 px.",
                 "fast": "Fast: 1080 px preview; zoom uses the half-size decode."}
        self.sliders_panel.set_temporary_hint("Preview detail: " + names[level],
                                              duration=5000)

    # --- Full-resolution zoom (global, persistent) ------------------------
    def on_sharp_fit_toggled(self, checked: bool):
        """Sharp preview at the fitted view (spec/sharp-fit-preview.md):
        display resolution only, so no reprocess."""
        ccr_backend.sharp_fit_preview = bool(checked)
        self._settings.setValue("view/sharp_fit", bool(checked))
        ip = self.image_preview
        if not checked and not ip._zoomed_in_enough():
            ip._release_hires(refresh=True)
        elif checked:
            ip._hires_timer.start(150)

    def on_full_res_zoom_toggled(self, checked: bool):
        """Flip the full-resolution zoom flag and persist it. This changes
        RESOLUTION, not the look — nothing is baked, re-converted or
        re-rendered, so unlike on_auto_gain_toggled there is no reprocess pass.
        Turning it on asks the current view for a sharper tile; turning it off
        frees the oversized one. See spec/full-res-zoom.md §5.4."""
        ccr_backend.full_res_zoom = bool(checked)
        self._settings.setValue("view/full_res_zoom", bool(checked))
        if not checked:
            # Free the oversized tile. Nothing else would drop it: resolution is
            # deliberately not part of the adjustment signature.
            self.image_preview._release_hires(refresh=False)
        # Re-evaluate the current view either way — on it asks for a sharper
        # tile (the existing one stays visible meanwhile), off re-requests at
        # the legacy resolution, so turning it off never leaves the view
        # blurrier than it would have been with the feature absent.
        self.image_preview._update_hires_state()
        self.sliders_panel.set_temporary_hint(
            "Full-resolution zoom on — 100% shows real source pixels."
            if checked else
            "Full-resolution zoom off — zoom detail stays at the half-size "
            "decode.",
            duration=5000)

    # --- Input transfer function (non-RAW / TIFF decode space) ------------
    def on_tiff_transfer_ask_toggled(self, checked: bool):
        """Whether an import containing TIFFs asks how to read their encoding.
        Affects the NEXT import only — nothing loaded is re-decoded, because the
        transfer is captured per image at import time.
        See spec/input-transfer-function.md."""
        ccr_backend.tiff_transfer_ask = bool(checked)
        self._settings.setValue("input/tiff_transfer_ask", bool(checked))

    def on_tiff_transfer_mode_changed(self, mode: str):
        """The remembered answer used when the import dialog is suppressed."""
        ccr_backend.tiff_transfer_mode = str(mode)
        self._settings.setValue("input/tiff_transfer_mode", str(mode))

    def on_tiff_transfer_manual_changed(self, value: str):
        """The space forced when the remembered answer is 'manual'."""
        ccr_backend.tiff_transfer_manual = str(value)
        self._settings.setValue("input/tiff_transfer_manual", str(value))

    @staticmethod
    def _folder_tiffs(folder: str) -> list:
        """The TIFFs an Open Folder import would see — non-recursive, matching
        that scope, without duplicating the loader's extension list."""
        from core import input_transfer as it
        try:
            names = [e.path for e in os.scandir(folder) if e.is_file()]
        except OSError:
            return []
        return [p for p in names if it.is_tiff(p)]

    def _maybe_ask_input_transfer(self, paths) -> bool:
        """Settle how this import reads its TIFFs, recording the answer in the
        transient ccr_backend.import_transfer that the loader reads.

        Returns False when the user cancelled — the import must then not start,
        because the answer changes every pixel the conversion sees, so "no
        answer" must never silently mean linear.
        See spec/input-transfer-function.md §4.1."""
        from core import input_transfer as it
        from widgets.input_transfer_dialog import (InputTransferDialog,
                                                   decision_for)
        tiffs = it.tiffs_in(paths)
        if not tiffs:
            # Nothing to decide (no TIFFs, or only FreeCCR's own baked linear
            # merge TIFFs). Clear any previous batch's answer.
            ccr_backend.import_transfer = None
            return True
        manual = getattr(ccr_backend, "tiff_transfer_manual", it.SRGB)
        if not getattr(ccr_backend, "tiff_transfer_ask", True):
            ccr_backend.import_transfer = decision_for(
                getattr(ccr_backend, "tiff_transfer_mode", "linear"), manual)
            return True
        dlg = InputTransferDialog(tiffs, self, manual_default=manual)
        if not dlg.exec_():                     # Rejected / closed
            return False
        ccr_backend.import_transfer = dlg.decision()
        if dlg.remember():
            self.on_tiff_transfer_ask_toggled(False)
            self.on_tiff_transfer_mode_changed(dlg.mode())
            self.on_tiff_transfer_manual_changed(dlg.manual())
        return True

    def _rerender_all_for_global_mode(self, hint: str):
        """Re-render every loaded image after a global DISPLAY-mode change (Auto
        gain, Gamma mode) that is read live inside apply_adjustments but is NOT
        baked per image. The cached resized_preview/thumbnail must be REGENERATED
        before re-display — update_all_thumbnails/update_preview only re-show the
        cache (see sliders_panel._on_curve_edit_finished), so without the
        regenerate pass the change wouldn't appear until the user nudged a slider.
        No re-conversion, no catalog write."""
        if ccr_backend.images:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                for img in ccr_backend.images:
                    img.update_thumbnail_and_preview()
                # The zoom hi-res cache baked the old mode — drop it.
                self.image_preview._release_hires(refresh=False)
                self.thumbnail_list.update_all_thumbnails()
                if self.image_preview.current_idx is not None:
                    self.image_preview.update_preview(self.image_preview.current_idx)
            finally:
                QApplication.restoreOverrideCursor()
        self.sliders_panel.set_temporary_hint(hint, duration=5000)

    # --- Gamma application mode (global, persistent) ----------------------
    def on_auto_awb_toggled(self, checked: bool):
        """Flip the global Auto WB flag and persist it. No re-render: the flag
        only affects FUTURE conversions (and never images with a saved
        temperature/tint). See spec/auto-white-balance.md."""
        ccr_backend.auto_awb = bool(checked)
        self._settings.setValue("adjust/auto_awb", bool(checked))
        self.sliders_panel.set_temporary_hint(
            "Auto white balance on — new conversions get an automatic "
            "Temperature/Tint estimate." if checked else
            "Auto white balance off.", duration=4000)

    def on_balance_hotkeys_toggled(self, checked: bool):
        """Flip the global Channel Balance nudge-key flag, persist it, and
        enable/disable the six shortcuts to match. See
        spec/white-balance-restore.md."""
        ccr_backend.balance_hotkeys = bool(checked)
        self._settings.setValue("adjust/balance_hotkeys", bool(checked))
        self._apply_balance_hotkey_state()
        self.sliders_panel.set_temporary_hint(
            "Channel Balance keys on — U/I/O raise R/G/B, J/K/L lower them."
            if checked else "Channel Balance keys off.", duration=4000)

    def on_awb_algorithm_changed(self, algorithm: str):
        """Persist the AWB algorithm choice. Used by the AWB button and the
        post-conversion hook; existing images are untouched."""
        ccr_backend.awb_algorithm = str(algorithm)
        self._settings.setValue("adjust/awb_algorithm", str(algorithm))

    def on_gamma_mode_toggled(self, checked: bool):
        """Flip the global Gamma mode (per-channel vs hue-preserving luminance),
        persist it, and re-render all loaded images. Gamma is a display-domain
        tone op, so this only re-renders — no re-conversion, no catalog write.
        Mirrors on_auto_gain_toggled. See spec/gamma-luminance-mode.md."""
        ccr_backend.gamma_luminance = bool(checked)
        self._settings.setValue("adjust/gamma_luminance", bool(checked))
        self._rerender_all_for_global_mode(
            "Gamma: hue-preserving (luminance) mode." if checked else
            "Gamma: per-channel mode (standard).")

    def on_sprocket_mask_toggled(self, checked: bool):
        """Flip the global reversal-look sprocket-hole mask, persist it, and
        re-render all loaded images. The mask is composited last as a display
        overlay (the alpha is cached at convert time), so this only re-renders —
        no re-conversion, no catalog write. Mirrors on_auto_gain_toggled.
        See spec/sprocket-hole-mask.md."""
        ccr_backend.sprocket_mask_white = bool(checked)
        self._settings.setValue("adjust/sprocket_mask_white", bool(checked))
        self._rerender_all_for_global_mode(
            "Sprocket holes / clear film shown white (reversal look)." if checked
            else "Sprocket holes shown as converted (black).")

    def show_about_dialog(self):
        QMessageBox.about(
            self,
            "About FreeCCR",
            f"""<b>FreeCCR</b><br>Version {VERSION}<br><br>Copyright © 2025 FreeCCR
            <br>Website: <a href="{REPO_URL}">github.com/toonoumi/FreeCCR</a><br>
            <br>Built along with PySide6 v6.7.1<br>
            <br>For more info, visit PySide6 on PyPI: https://pypi.org/project/PySide6/ <br>
            <br>For third-party licenses, see the Licenses section in the Help menu."""
        )

    def show_licenses_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Third-Party Licenses")
        dialog.setMinimumWidth(theme.DIALOG_W_MD)
        layout = QVBoxLayout(dialog)
        theme.apply_panel_spacing(layout)

        text_edit = QTextEdit(dialog)
        text_edit.setReadOnly(True)
        licenses_text = self.load_licenses_text()
        text_edit.setPlainText(licenses_text)
        layout.addWidget(text_edit)

        close_button = QPushButton("Close", dialog)
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)

        dialog.resize(700, 500)
        dialog.exec()

    def load_licenses_text(self):
        # Try both ./LICENSES and ../LICENSES
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        possible_dirs = [
            os.path.join(base_dir, "LICENSES"),
            os.path.join(os.path.dirname(base_dir), "LICENSES"),
        ]
        licenses_text = ""
        found = False
        for licenses_dir in possible_dirs:
            if os.path.isdir(licenses_dir):
                found = True
                for filename in sorted(os.listdir(licenses_dir)):
                    if filename.lower().endswith(".txt"):
                        path = os.path.join(licenses_dir, filename)
                        try:
                            with open(path, "r", encoding="utf-8") as f:
                                licenses_text += f"===== {filename} =====\n"
                                licenses_text += f.read() + "\n\n"
                        except Exception as e:
                            licenses_text += f"Could not read {filename}: {e}\n\n"
                break
        if not found:
            licenses_text = "No license files found."
        return licenses_text

    def _launch_loader(self, files=None, folder=None, force_no_merge=False):
        """Start the async image loader for a file list OR a folder (exactly one).
        Shared by Open Files, Open Folder, the command-line import, and the
        merge-replace reload (force_no_merge loads the generated TIFFs as
        normal images even while 3-way merge is on)."""
        self._stop_loader_if_running()
        # New batch = new roll: the combo returns to "Default"
        # (the backend loader clears its copy too).
        self.sliders_panel.reset_film_stock_combo()
        self.thumbnail_list.show_loading_dialog()
        self._loader_thread = new_thread()
        self._loader_worker = ImageLoaderWorker(folder=folder, files=files,
                                                force_no_merge=force_no_merge)
        self._loader_worker.moveToThread(self._loader_thread)
        self._loader_thread.started.connect(self._loader_worker.run)
        self._loader_worker.finished.connect(self._loader_thread.quit)
        self._loader_worker.finished.connect(self._loader_worker.deleteLater)
        self._loader_thread.finished.connect(self._cleanup_loader)
        self._loader_worker.finished.connect(self.thumbnail_list.load_thumbnails)
        self._loader_thread.start()

    def _cleanup_loader(self):
        """Called when the loader thread finishes. Nulls the references so isRunning() is never called on a deleted C++ object."""
        self._loader_thread = None
        self._loader_worker = None
        # Surface a 3-way merge rejection recorded by the backend loader (covers
        # the Open Folder path and decode-time non-Bayer rejections). Read once,
        # then clear so it can't reappear on a later, unrelated load.
        err = getattr(ccr_backend, "last_merge_error", None)
        if err:
            ccr_backend.last_merge_error = None
            QMessageBox.warning(self, "3-way RGB merge", err)
        # Shots the merge couldn't make into a set: greyed out under the frames
        # (spec/smart-triplets.md), no warning box.
        notes = getattr(ccr_backend, "last_merge_attention", None) or []
        ccr_backend.last_merge_attention = []
        for entry in notes:
            self._on_needs_attention(entry, hint=False)
        if notes:
            n = len(notes)
            self.sliders_panel.set_temporary_hint(
                f"{n} shot{'' if n == 1 else 's'} couldn't be merged as found: see "
                "“rejected combinations” under the frames.", duration=6000)
        # Files the loader had to drop (undecodable RAW, missing file, …).
        # Without this they silently never appear — a built exe has no console
        # to show the loader's print. Read once, then clear.
        self._report_load_failures()
        # Offer to replace trichrome originals with linear TIFFs, if enabled.
        self._maybe_replace_merged_with_tiff()

    def _report_load_failures(self):
        """One grouped warning for every file the last load could not open."""
        failures = getattr(ccr_backend, "last_load_errors", None)
        if not failures:
            return
        ccr_backend.last_load_errors = []
        from core.load_errors import format_load_failures
        QMessageBox.warning(self, "Some files could not be loaded",
                            format_load_failures(failures))

    # --- Replace trichrome originals with linear TIFFs -------------------- #
    def _maybe_replace_merged_with_tiff(self):
        """After a successful merge import, if the opt-in is on, confirm and then
        bake each merged frame to a linear TIFF, delete its source RAWs, and
        reload from the TIFFs. No-op for a normal load, the reload itself, or
        when the user declines. See spec/merge-linear-tiff-replace.md."""
        if getattr(self, "_merge_replacing", False):
            return
        if not (getattr(ccr_backend, "rgb_merge_mode", False)
                and getattr(ccr_backend, "rgb_merge_replace", False)):
            return
        if getattr(ccr_backend, "last_merge_error", None):
            return
        merged = [im for im in ccr_backend.images
                  if getattr(im, "is_merged", False)
                  and getattr(im, "merge_sources", None)]
        if not merged:
            return
        n_sources = sum(len(im.merge_sources) for im in merged)
        resp = QMessageBox.warning(
            self, "Replace originals with linear TIFF",
            f"Create {len(merged)} full-resolution linear TIFF file(s) and then "
            f"PERMANENTLY DELETE the {n_sources} original RAW file(s) that were "
            f"merged?\n\nThe TIFF keeps the full merged result (all linear data, "
            f"lossless), but the merge/demosaic choice is baked in and the "
            f"deletion cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if resp != QMessageBox.Yes:
            self.sliders_panel.set_temporary_hint(
                "Replace originals cancelled — source RAWs kept.", duration=4000)
            return
        self._start_merge_replace(merged)

    def _start_merge_replace(self, merged_images):
        self._merge_replacing = True
        self._stop_loader_if_running()
        dlg = QProgressDialog("Writing linear TIFFs…", "Cancel", 0,
                              len(merged_images), self)
        dlg.setWindowTitle("Replacing originals")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)
        self._replace_dialog = dlg
        self._replace_thread = new_thread()
        self._replace_worker = MergeReplaceWorker(merged_images)
        self._replace_worker.moveToThread(self._replace_thread)
        self._replace_thread.started.connect(self._replace_worker.run)
        self._replace_worker.progress.connect(self._on_replace_progress)
        self._replace_worker.finished.connect(self._on_replace_finished)
        self._replace_worker.finished.connect(self._replace_thread.quit)
        self._replace_worker.finished.connect(self._replace_worker.deleteLater)
        # Null the Python refs only AFTER the thread's loop has stopped — a
        # running QThread whose wrapper is GC'd hard-aborts (mirrors the loader).
        self._replace_thread.finished.connect(self._cleanup_replace)
        dlg.canceled.connect(self._replace_worker.cancel)
        self._replace_thread.start()

    def _cleanup_replace(self):
        self._replace_thread = None
        self._replace_worker = None

    def _on_replace_progress(self, done, total, name):
        dlg = getattr(self, "_replace_dialog", None)
        if dlg is not None:
            dlg.setMaximum(total)
            dlg.setValue(done)
            if name:
                dlg.setLabelText(f"Writing linear TIFF {done + 1} of {total}:\n{name}")

    def _on_replace_finished(self, result):
        # Runs on worker.finished (the thread is still stopping — its refs are
        # nulled later in _cleanup_replace on thread.finished).
        dlg = getattr(self, "_replace_dialog", None)
        if dlg is not None:
            dlg.close()
        self._replace_dialog = None
        self._merge_replacing = False

        failures = result.get("failures", [])
        tiffs = result.get("tiff_paths", [])
        deleted = result.get("deleted", [])

        if result.get("cancelled"):
            self.sliders_panel.set_temporary_hint(
                "Replace cancelled — originals kept.", duration=4000)
            return

        if failures:
            shown = "\n".join(f"• {name}: {reason}" for name, reason in failures[:8])
            if len(failures) > 8:
                shown += f"\n… and {len(failures) - 8} more"
            QMessageBox.warning(
                self, "Replace originals",
                f"Replaced {len(tiffs)} frame(s); deleted {len(deleted)} "
                f"original file(s).\n\nSome items were kept because they failed "
                f"(their originals were NOT deleted):\n\n{shown}")

        if tiffs:
            # Reload from the generated TIFFs as normal negatives (merge bypassed).
            self._launch_loader(files=tiffs, force_no_merge=True)
            self.sliders_panel.set_temporary_hint(
                f"Replaced {len(tiffs)} frame(s) with linear TIFFs; "
                f"deleted {len(deleted)} original(s).", duration=5000)

    def _stop_loader_if_running(self):
        """Cancel and join any in-progress loader thread. A loader that
        outlives the wait (in-flight RAW decodes are not interruptible) is
        parked instead of dropped: its UI effects are disconnected here, its
        backend commit is refused by the load-generation guard, and the
        Python refs are kept until it really finishes — a running QThread
        whose wrapper is garbage-collected hard-aborts the process."""
        thread = getattr(self, '_loader_thread', None)
        worker = getattr(self, '_loader_worker', None)
        self._loader_thread = None
        self._loader_worker = None
        if thread is None:
            return
        try:
            if thread.isRunning():
                if worker is not None:
                    worker.cancel()
                thread.quit()
                if not thread.wait(3000):
                    # Detach the stale thread's UI effects; keep its
                    # quit/deleteLater wiring so it still winds down.
                    if worker is not None:
                        try:
                            worker.finished.disconnect(self.thumbnail_list.load_thumbnails)
                        except (RuntimeError, TypeError):
                            pass
                    try:
                        thread.finished.disconnect(self._cleanup_loader)
                    except (RuntimeError, TypeError):
                        pass
                    if not hasattr(self, '_abandoned_loaders'):
                        self._abandoned_loaders = []
                    self._abandoned_loaders.append(thread)
                    thread.finished.connect(
                        lambda t=thread: self._reap_abandoned_loader(t))
        except RuntimeError:
            pass

    def _reap_abandoned_loader(self, thread):
        """Drop the parked reference once an abandoned loader has finished."""
        try:
            self._abandoned_loaders.remove(thread)
        except (AttributeError, ValueError):
            pass

    def open_files(self):
        # Loading a new batch replaces the current one — persist its edits first
        ccr_backend.save_catalog()
        start_dir = self._settings.value("files/last_open_dir", "", type=str)
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Images",
            start_dir,
            "Images (*.dng *.tif *.tiff *.arw *.nef *.cr2 *.cr3 *.raf *.png *.jpg *.jpeg *.rw2 *.3fr *.fff);;All Files (*)"
        )
        if files and not self._confirm_replace_session():
            return
        if files:
            # Remember where the user BROWSED to (survives restarts). Set here
            # rather than in _import_file_list so a command-line import never
            # moves it: those paths come from a shell, are often relative, and
            # `os.path.dirname("a.nef")` is "" — which would silently wipe the
            # setting and drop both dialogs back to the process cwd.
            self._settings.setValue("files/last_open_dir", os.path.dirname(files[0]))
            self._import_file_list(files)

    def _import_file_list(self, files):
        """Replace the current batch with `files`: Unicode validation, 3-way
        merge validation, then the loader. The post-dialog tail of open_files,
        shared with the command-line import (spec/cli-file-args.md)."""
        # Validate and normalize Unicode paths
        valid_files = []
        invalid_files = []

        for file_path in files:
            normalized_path = normalize_unicode_path(file_path)
            if validate_unicode_path(normalized_path):
                valid_files.append(normalized_path)
            else:
                invalid_files.append(file_path)

        if invalid_files:
            invalid_list = '\n'.join(invalid_files[:5])  # Show first 5 invalid files
            if len(invalid_files) > 5:
                invalid_list += f'\n... and {len(invalid_files) - 5} more files'
            QMessageBox.warning(
                self,
                "Unicode Path Warning",
                f"Some files have paths that may not be processed correctly due to Unicode characters:\n\n{invalid_list}\n\nThese files will be skipped. Please consider moving these files to paths with simpler names."
            )

        if valid_files:
            # 3-way RGB merge: refuse up front only a selection with no RAW at
            # all; any count is fine, the loader groups sets by their light
            # (spec/smart-triplets.md).
            # FreeCCR-baked merge TIFFs are excluded — they re-open as normal
            # images even in merge mode. See spec/merge-linear-tiff-replace.md.
            if ccr_backend.rgb_merge_mode:
                from core import ccr_merge
                to_merge = [p for p in valid_files
                            if not ccr_merge.is_freeccr_merge_tiff(p)]
                if to_merge:
                    ok, err = ccr_merge.validate_merge_inputs(
                        ccr_merge.sort_for_merge(to_merge), whole_sets=False)
                    if not ok:
                        QMessageBox.warning(self, "3-way RGB merge", err)
                        return
            # How this batch's TIFFs should be read (metadata / linear / forced).
            # Asked BEFORE the loader starts; cancelling aborts the import, since
            # the answer changes every pixel the conversion sees.
            # See spec/input-transfer-function.md §4.1.
            if not self._maybe_ask_input_transfer(valid_files):
                return
            self._launch_loader(files=valid_files)
        else:  # There were files, but all of them were invalid
            QMessageBox.critical(
                self,
                "No Valid Files",
                "None of the selected files could be processed due to Unicode character issues in their paths. Please rename the files or move them to simpler paths."
            )

    def open_folder(self):
        # Loading a new batch replaces the current one — persist its edits first
        ccr_backend.save_catalog()
        start_dir = self._settings.value("files/last_open_dir", "", type=str)
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Folder",
            start_dir
        )
        if folder and not self._confirm_replace_session():
            return
        if folder:
            # Dialog-only, like open_files above: `freeccr .` would otherwise
            # persist the literal "." as the browse location.
            self._settings.setValue("files/last_open_dir", folder)
            self._import_folder(folder)

    def _import_folder(self, folder):
        """Replace the current batch with every supported image in `folder`.
        The post-dialog tail of open_folder, shared with the command-line
        import (spec/cli-file-args.md)."""
        # Validate and normalize Unicode path
        normalized_folder = normalize_unicode_path(folder)
        if not validate_unicode_path(normalized_folder):
            QMessageBox.warning(
                self,
                "Unicode Path Warning",
                f"The selected folder path contains characters that may cause issues:\n\n{folder}\n\nPlease consider using a folder with a simpler path name."
            )
            return

        # The same TIFF colour-space question as the file-list import. Open
        # Folder never sees a file list, so scan the folder for TIFFs — non-
        # recursively, matching its own scope. See spec/input-transfer-function.md §4.1.
        if not self._maybe_ask_input_transfer(
                self._folder_tiffs(normalized_folder)):
            return

        self._launch_loader(folder=normalized_folder)

    def load_paths(self, paths):
        """Open the files and/or folders named on the command line. The public
        seam for argument-driven imports (and any later Open With / drag-and-
        drop support): it plans the paths, reports the ones that contribute
        nothing, then hands off to the same importers the dialogs use.
        See spec/cli-file-args.md."""
        from utils.cli_args import plan_open
        if not self._confirm_replace_session():
            return
        # Loading a new batch replaces the current one — persist its edits first
        ccr_backend.save_catalog()
        plan = plan_open(paths)
        # Start the import BEFORE reporting bad arguments: the report is modal,
        # and one mistyped path must not hold up the paths that were fine.
        if plan.folder:
            self._import_folder(plan.folder)
        elif plan.files:
            self._import_file_list(plan.files)
        if plan.problems:
            shown = '\n'.join(plan.problems[:5])
            if len(plan.problems) > 5:
                shown += f'\n... and {len(plan.problems) - 5} more'
            # An import may well be running behind this box (the dispatch
            # above deliberately goes first), so only say "Nothing to open"
            # when nothing actually opened — otherwise the title
            # contradicts the load the user can see starting.
            imported = bool(plan.folder or plan.files)
            QMessageBox.warning(
                self,
                "Some paths could not be opened" if imported
                else "Nothing to open",
                f"These command-line paths could not be opened:\n\n{shown}")

    # --- Live Capture Monitor (spec/live-capture-monitor.md) ---------------
    def open_live_monitor(self):
        """One window: raised if it is already open."""
        mon = self._live_monitor
        if mon is None:
            from widgets.live_monitor import LiveCaptureMonitor
            mon = LiveCaptureMonitor(main_window=self)
            mon.closed.connect(self._on_live_monitor_closed)
            self._live_monitor = mon
        mon.show()
        mon.raise_()
        mon.activateWindow()
        return mon

    def _on_live_monitor_closed(self):
        mon, self._live_monitor = self._live_monitor, None
        if mon is not None:
            mon.deleteLater()

    # --- Tethering (watch-folder capture) ---------------------------------
    def toggle_tethering(self):
        if self._tether_active:
            self.stop_tethering()
        else:
            self.enter_tethering()

    def tether_folder(self):
        """The folder Hot Folder Tether is watching, or None when it is off
        (the Live Capture Monitor's USB camera saves its shots there)."""
        return self._tether_folder if self._tether_active else None

    def _tether_folder_label(self):
        if not self._tether_folder:
            return ""
        return (os.path.basename(self._tether_folder.rstrip("/\\"))
                or self._tether_folder)

    def _tether_note(self):
        """Banner sub-text reflecting how captures will be handled."""
        g = getattr(self, "_tether_grouper", None)
        if (g is not None and g.pending
                and getattr(ccr_backend, "scan_type", "") == "trichrome"):
            return f"{len(g.pending)} of 3 frames of the next shot"
        if ccr_backend.positive_mode:
            return "Positive mode — captures import as positives."
        if ccr_backend.black_point_bgr is None:
            if self._tether_prompted:
                return ("Lead frame in: press Black Point… and draw over the "
                        "clear film base.")
            return ("No black point set — captures import unconverted. "
                    "Set the Black Point (film base) to auto-convert.")
        return ""

    def enter_tethering(self, from_monitor=False):
        """Start Hot Folder Tether from ONE window (folder, scan type, close the
        open images, import the folder's files: spec/tether-start-window.md).
        Returns the window's answers (None when already on)."""
        if self._tether_active:
            return None
        # The batch is about to grow — persist current edits first (like the
        # open flows) so nothing is lost if the session is interrupted.
        ccr_backend.save_catalog()
        start_dir = self._settings.value("files/last_tether_dir", "", type=str)
        opts = self._tether_start_options(len(ccr_backend.images or []), start_dir,
                                          from_monitor)
        if not opts or not opts.get("start") or not opts.get("folder"):
            return opts
        folder = opts["folder"]
        normalized = normalize_unicode_path(folder)
        if not validate_unicode_path(normalized):
            QMessageBox.warning(
                self, "Unicode Path Warning",
                f"The selected folder path contains characters that may cause "
                f"issues:\n\n{folder}\n\nPlease choose a folder with a simpler "
                f"path name.")
            return dict(opts, start=False)
        self._settings.setValue("files/last_tether_dir", folder)
        folder = os.path.normpath(normalized)
        if opts.get("scan_type"):
            self.set_scan_type(opts["scan_type"])

        # Snapshot the folder's current supported files. They are seeded into the
        # scanner (so the poller never re-imports them); optionally import them now.
        try:
            existing = [e.name for e in os.scandir(folder)
                        if e.is_file() and is_supported(e.name)]
        except OSError as e:
            QMessageBox.warning(self, "Hot Folder Tether Error",
                                f"Could not read the folder:\n\n{e}")
            return dict(opts, start=False)
        # Close the open images, as opening new files does (edits saved).
        if opts.get("close_open") and ccr_backend.images:
            self._close_session_for_tether()
        import_paths = []
        if existing and opts.get("import_existing"):
            import_paths = [os.path.join(folder, n) for n in existing]
            try:
                import_paths.sort(key=os.path.getmtime)  # oldest first
            except OSError:
                pass

        self._tether_folder = folder
        self._tether_count = 0
        self._tether_unavailable = False
        self._tether_prompted = False
        self._tether_lead_img = None
        self._tether_last_lit = None
        # Files imported at session start are not the "film lead" — only a
        # genuinely-new capture triggers the B/W-point prompt.
        self._tether_import_existing = {os.path.normpath(p) for p in import_paths}
        self._tether_scanner = FolderScanner(initial_names=existing)
        # Each session starts fresh: clear the global B/W point so the user
        # re-samples it from THIS roll's film lead (the first frame). Different
        # rolls/stocks have different base colour + density, so a previous
        # roll's point must not carry over.
        ccr_backend.black_point_bgr = None
        ccr_backend.white_point_bgr = None
        # And the film-stock selection resets for the same reason (the combo
        # sync also clears the backend copy).
        self.sliders_panel.reset_film_stock_combo()

        # Long-lived worker on its own event-loop QThread (spec §9.1.7); work is
        # delivered by QMetaObject.invokeMethod (queued) so it runs off-GUI.
        self._tether_thread = new_thread()
        self._tether_worker = TetherWatchWorker()
        self._tether_worker.moveToThread(self._tether_thread)
        self._tether_worker.captured.connect(self._on_capture)
        self._tether_worker.captureError.connect(self._on_capture_error)
        self._tether_worker.needsAttention.connect(self._on_needs_attention)
        self._tether_triplet_req.connect(self._tether_worker.process_triplet)
        self._tether_leftover_req.connect(self._tether_worker.describe_leftover)
        self._tether_thread.start()
        from core.scan_type import TripletGrouper
        self._tether_grouper = TripletGrouper()
        self.tether_banner.set_scan_type(ccr_backend.scan_type)

        self._tether_active = True
        self.tether_action.setText("Stop Hot Folder Tether")
        self.image_preview.show_tether_banner(
            self._tether_folder_label(), self._tether_count, self._tether_note())

        self._tether_timer = QTimer(self)
        self._tether_timer.setInterval(1000)
        self._tether_timer.timeout.connect(self._poll_tether_folder)
        self._tether_timer.start()

        self._route_existing(import_paths)

        self.sliders_panel.set_temporary_hint(
            f"Hot folder tether: watching “{self._tether_folder_label()}”. "
            f"Captures appear here automatically.", duration=5000)
        self.refresh_tether_banner()
        return opts

    # --- scan type and trichrome grouping (spec/scan-type.md) ------------
    def set_scan_type(self, t):
        from core import scan_type as st
        t = st.clean(t)
        old = getattr(ccr_backend, "scan_type", st.DEFAULT)
        if t == old:
            return
        ccr_backend.scan_type = t
        self._settings.setValue("scan/type", t)
        if self._tether_active and old == "trichrome" and self._tether_grouper is not None:
            # a part-filled triplet loads as single frames when switching away
            for p in self._tether_grouper.flush():
                self._dispatch_to_worker(p)
        self.tether_banner.set_scan_type(t)
        self._refresh_tether_banner()
        self.scan_type_changed.emit(t)

    def _route_tether_file(self, path):
        from core.ccr_merge import is_raw_path
        if (getattr(ccr_backend, "scan_type", "colour") == "trichrome"
                and self._tether_grouper is not None):
            if not is_raw_path(path):
                # merging reads the RAW: a JPEG-only arrival loads on its own
                self.sliders_panel.set_temporary_hint(
                    f"{os.path.basename(path)} isn't a RAW, so it can't join a trichrome "
                    "merge; loaded on its own.", duration=5000)
                self._dispatch_to_worker(path)
                return
            self._tether_grouper.add(path)
            for trip in self._tether_grouper.take():
                self._emit_triplet({"paths": list(trip), "check": True})
            self._refresh_tether_banner()
            return
        self._dispatch_to_worker(path)

    def _route_existing(self, paths):
        """Files already in the folder, when the user chose to import them."""
        if getattr(ccr_backend, "scan_type", "colour") != "trichrome":
            for p in paths:
                self._dispatch_to_worker(p)
            return
        from core.ccr_merge import is_raw_path
        from core import scan_type as st
        raws = [p for p in paths if is_raw_path(p)]
        for p in paths:
            if p not in raws:
                self._dispatch_to_worker(p)
        trips, left = st.group_existing(raws)
        for trip in trips:
            self._emit_triplet({"paths": list(trip), "check": True})
        if left:
            self._tether_leftover_req.emit({
                "paths": left,
                "reason": f"{len(left)} file(s) in the folder don't make a full red, "
                          "green, blue shot"})

    def _refresh_tether_banner(self):
        if not self._tether_active:
            return
        self.refresh_tether_banner()

    def _on_needs_attention(self, entry, hint=True):
        """A shot that couldn't be merged: shown greyed out under the frames."""
        reason = entry.get("reason", "")
        if entry.get("short"):
            short = entry["short"]
        elif "should be lit" in reason:
            short = "Not red, green, blue"
        elif "merge failed" in reason:
            short = "Merge failed"
        elif "arrived" in reason or "waiting" in reason:
            short = "Frames missing"
        else:
            short = "Left over"
        self._attention_seq += 1
        e = dict(entry)
        e["id"] = f"a{self._attention_seq}"
        e["short"] = short
        self._tether_attention.append(e)
        self.thumbnail_list.set_attention(self._tether_attention)
        if hint:
            self.sliders_panel.set_temporary_hint(
                f"A shot needs attention: {reason}. It's greyed out under the frames "
                "(right-click it).", duration=6000)

    def _on_attention_action(self, eid, action):
        e = next((x for x in self._tether_attention if x["id"] == eid), None)
        if e is None:
            return
        paths = list(e.get("paths") or [])
        if action == "choose":
            paths = self._choose_triplet_files(paths)
            if paths is None:
                return
            self._load_triplet_now(paths, check=False)
        elif action == "merge":
            if len(paths) != 3:
                return
            self._load_triplet_now(paths, check=False)
        elif action == "singles":
            for p in paths:
                self._load_single_now(p)
        self._tether_attention = [x for x in self._tether_attention if x["id"] != eid]
        self.thumbnail_list.set_attention(self._tether_attention)

    def _choose_triplet_files(self, paths):
        """The red, green and blue frames, chosen by hand (in name order)."""
        from core import ccr_merge
        start = (self._tether_folder or (os.path.dirname(paths[0]) if paths else ""))
        chosen, _ = QFileDialog.getOpenFileNames(
            self, "Choose the red, green and blue frames (3 RAW files)", start,
            "RAW files (" + " ".join(f"*{e}" for e in sorted(ccr_merge.RAW_EXTENSIONS)) + ")")
        if not chosen:
            return None
        ok, err = ccr_merge.validate_merge_inputs(chosen)
        if not ok or len(chosen) != 3:
            QMessageBox.warning(self, "Choose Files",
                                err or "Choose exactly 3 RAW files: red, green, blue.")
            return None
        return ccr_merge.sort_for_merge(chosen)

    def _load_triplet_now(self, paths, check):
        if self._tether_worker is not None and self._tether_active:
            self._emit_triplet({"paths": list(paths), "check": check})
            return
        # no session running: merge here, like Open with 3-way merge
        from core.tether_watcher import TetherWatchWorker
        w = TetherWatchWorker()
        self._hand_template(w)
        w.captured.connect(self._on_capture)
        w.captureError.connect(self._on_capture_error)
        w.needsAttention.connect(self._on_needs_attention)
        w.process_triplet({"paths": list(paths), "check": check})

    def _load_single_now(self, path):
        if self._tether_worker is not None and self._tether_active:
            self._dispatch_to_worker(path)
            return
        from core.tether_watcher import TetherWatchWorker
        w = TetherWatchWorker()
        w.captured.connect(self._on_capture)
        w.captureError.connect(self._on_capture_error)
        w.process(path)

    def _dispatch_to_worker(self, path):
        if self._tether_worker is None:
            return
        self._hand_template()
        QMetaObject.invokeMethod(self._tether_worker, "process",
                                 Qt.QueuedConnection, Q_ARG(str, path))

    def _emit_triplet(self, job):
        self._hand_template()
        self._tether_triplet_req.emit(job)

    def _poll_tether_folder(self):
        if not self._tether_active or not self._tether_folder:
            return
        entries = []
        try:
            for e in os.scandir(self._tether_folder):
                try:
                    if e.is_file():
                        entries.append((e.name, e.stat().st_size))
                except OSError:
                    continue
        except OSError:
            if not self._tether_unavailable:
                self._tether_unavailable = True
                self.image_preview.show_tether_banner(
                    self._tether_folder_label(), self._tether_count,
                    "folder unavailable — reconnect to resume")
            return
        if self._tether_unavailable:
            # Folder is back — clear the stale "unavailable" note.
            self._tether_unavailable = False
            self.image_preview.show_tether_banner(
                self._tether_folder_label(), self._tether_count, self._tether_note())
        for name in self._tether_scanner.feed(entries):
            self._route_tether_file(os.path.join(self._tether_folder, name))
        if self._tether_grouper is not None:
            left = self._tether_grouper.stalled()
            if left:
                self._tether_leftover_req.emit({
                    "paths": left,
                    "reason": f"{len(left)} of the 3 frames of a shot arrived; the rest "
                              f"didn't come within {int(self._tether_grouper.stall_s)} s"})
            self._refresh_tether_banner()

    def _on_capture(self, img):
        # Inherit the editable "look" (adjustments, orientation, crop) of the
        # image active just before this capture, so a rotate/brightness/etc. set
        # on one frame auto-applies to the next imported frame — a fixed
        # copy-stand rig wants identical treatment per frame. Snapshot BEFORE
        # appending the new image (current_idx still points at the prior one).
        template = (None if getattr(img, "_tether_template_applied", False)
                    else self._tether_template())
        # Always keep the capture — even one that arrives just after Stop (the
        # worker had already decoded it). Dropping it would lose a real photo.
        ccr_backend.images.append(img)
        ccr_backend.file_paths.append(img.file_path)
        idx = len(ccr_backend.images) - 1
        rerender = False
        if template is not None:
            self._apply_tether_template(img, template)
            rerender = True
        elif getattr(img, "_tether_template_applied", False) and not img.converted:
            rerender = True              # the worker gave it a look but didn't render it
        # What it was scanned as decides its colour profile and is recorded
        # (spec/scan-type.md): a merge is trichrome; a single file is colour
        # or B&W by the scan type (a single file of a trichrome scan, loaded
        # on its own, is neither).
        st = getattr(ccr_backend, "scan_type", "colour")
        if getattr(img, "is_merged", False):
            img.capture_type = "trichrome"
        else:
            img.capture_type = {"bw": "bw", "colour": "colour"}.get(st)
        want = "bw" if st == "bw" else "color"
        if getattr(img, "color_profile", "color") != want:
            img.color_profile = want
            rerender = True
        if rerender:
            # Re-bake preview/thumbnail so inherited adjustments are reflected
            # (rotation/flips/crop are applied by update_preview on selection).
            img.update_thumbnail_and_preview()
        self.thumbnail_list.append_image_item(idx, select=True)
        name = getattr(img, "display_name", None) or os.path.basename(img.file_path)
        if self._tether_active:
            self._tether_count += 1
            self.tether_captured.emit(img.file_path)
            self.image_preview.show_tether_banner(
                self._tether_folder_label(), self._tether_count, self._tether_note())
            self.sliders_panel.set_temporary_hint(f"Captured {name}", duration=2500)
            self._schedule_tether_save()
            from core import lead_frame
            lit = lead_frame.is_lit(getattr(img, "lit_level", None))
            new_capture = (os.path.normpath(img.file_path)
                           not in self._tether_import_existing)
            if lit and new_capture:
                self._tether_last_lit = img
            # Once per session, after the first genuinely-new LIT capture (the
            # film lead), prompt the user to set the roll's B/W point from it.
            # A frame too dark to show film base (the light off) doesn't count
            # (spec/tether-black-point.md).
            if not self._tether_prompted and new_capture and not lit:
                self.sliders_panel.set_temporary_hint(
                    f"Captured {name}: too dark to show the film base (light off?). "
                    "FreeCCR will ask on the next lit frame.", duration=6000)
            elif not self._tether_prompted and new_capture:
                self._tether_prompted = True
                self._tether_lead_img = img
                # No pop-up (spec/tether-start-window.md): the banner says the
                # lead is in and its Black Point… button is highlighted.
                self.refresh_tether_banner()
        else:
            # Late arrival after Stop: persist immediately (the debounced timer
            # is gone) so the photo isn't lost on close.
            ccr_backend.save_catalog()

    def _tether_template(self):
        """The look of the frame on screen, for the next capture to inherit
        (core.tether_watcher.look_template: the frame's own auto Temperature /
        Tint stays behind). None when there is no current image or its look
        is all default."""
        idx = self.image_preview.current_idx
        if idx is None:
            return None
        src = ccr_backend.get_image_by_index(idx)
        if src is None:
            return None
        from core.tether_watcher import look_template
        return look_template(src)

    @staticmethod
    def _apply_tether_template(img, t):
        from core.tether_watcher import apply_look_template
        apply_look_template(img, t)

    def _hand_template(self, worker=None):
        """Give the worker the look to apply BEFORE it converts the next
        files (spec/tether-convert-like-convert.md)."""
        w = worker if worker is not None else self._tether_worker
        if w is not None:
            try:
                w.template = self._tether_template()
            except AttributeError:           # a stand-in worker in tests
                pass

    def _on_capture_error(self, path, msg):
        self.sliders_panel.set_temporary_hint(
            f"Skipped {os.path.basename(path)}: {msg}", duration=4000)

    def _schedule_tether_save(self):
        """Coalesce catalog saves during capture bursts (spec §9.1.6)."""
        if self._tether_save_timer is None:
            self._tether_save_timer = QTimer(self)
            self._tether_save_timer.setSingleShot(True)
            self._tether_save_timer.timeout.connect(ccr_backend.save_catalog)
        self._tether_save_timer.start(3000)

    def stop_tethering(self):
        if not self._tether_active:
            return
        self._tether_active = False
        if self._tether_grouper is not None:
            left = self._tether_grouper.flush()
            if left:
                self._on_needs_attention({
                    "paths": left, "thumbs": [],
                    "reason": f"Hot Folder Tether stopped with {len(left)} frame(s) of a "
                              "trichrome shot waiting for the rest"})
            self._tether_grouper = None
        if self._tether_timer is not None:
            self._tether_timer.stop()
            self._tether_timer = None
        if self._tether_save_timer is not None:
            self._tether_save_timer.stop()
            self._tether_save_timer = None
        self._stop_tether_worker()
        self.image_preview.hide_tether_banner()
        self.tether_action.setText("Hot Folder Tether…")
        ccr_backend.save_catalog()
        self.sliders_panel.set_temporary_hint("Hot folder tether stopped.", duration=3000)

    def _stop_tether_worker(self):
        """Cancel + join the worker thread (mirrors _stop_loader_if_running)."""
        if getattr(self, "_tether_thread", None) is not None:
            try:
                if self._tether_worker is not None:
                    self._tether_worker.cancel()
                if self._tether_thread.isRunning():
                    self._tether_thread.quit()
                    self._tether_thread.wait(3000)
            except RuntimeError:
                pass
            # Just drop the Python refs (mirrors _stop_loader_if_running). The
            # thread's event loop has exited, so a deleteLater() here would never
            # be processed; GC of the wrapper releases the C++ object.
            self._tether_thread = None
            self._tether_worker = None

    def persist_bwpoint(self):
        """Write the current global B/W point to QSettings (spec §4.1) and, if a
        tethering session is live, refresh the banner so its note reflects the
        new B/W state immediately (e.g. drops the 'unconverted' warning)."""
        bp = encode_bwpoint(ccr_backend.black_point_bgr)
        if bp is not None:
            self._settings.setValue("convert/black_point_bgr", bp)
        # A cleared white point must be removed, else it would be restored next
        # session and override the default-slope choice.
        if ccr_backend.white_point_bgr is None:
            self._settings.remove("convert/white_point_bgr")
        else:
            wp = encode_bwpoint(ccr_backend.white_point_bgr)
            if wp is not None:
                self._settings.setValue("convert/white_point_bgr", wp)
        self.refresh_tether_banner()

    def refresh_tether_banner(self):
        """Re-render the banner note from current state (B/W point / Positive
        mode). No-op when not tethering or the folder is unavailable."""
        if self._tether_active and not self._tether_unavailable:
            self.image_preview.show_tether_banner(
                self._tether_folder_label(), self._tether_count, self._tether_note())
        self.tether_banner.set_black_point_nudge(
            bool(self._tether_active and self._tether_prompted
                 and ccr_backend.black_point_bgr is None
                 and not ccr_backend.positive_mode))

    def _tether_start_options(self, n_open, start_dir, from_monitor=False):
        """The Start Hot Folder Tether window's answers (dict, see
        TetherStartDialog.options), "start" False on Cancel. The test seam."""
        from widgets.tether_start_dialog import TetherStartDialog
        dlg = TetherStartDialog(self, n_open=n_open,
                                folder=start_dir if start_dir and os.path.isdir(start_dir) else "",
                                scan_type=getattr(ccr_backend, "scan_type", "colour"),
                                from_monitor=from_monitor)
        started = dlg.exec() == QDialog.Accepted
        return dlg.options(started)

    def _close_session_for_tether(self):
        """Close the open images, as opening new files does: edits saved
        (and restored when those files are reopened), list and canvas
        emptied."""
        ccr_backend.save_catalog()
        ccr_backend.clear()
        self.thumbnail_list.load_thumbnails()

    def _bring_to_front(self):
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def start_roll_black_point(self, img=None):
        """Sample the roll's black point now (the tether banner's and the
        Live Capture Monitor's Black Point buttons, and the prompt): on `img`,
        else this session's newest lit capture, else the frame on screen.
        Brings this window to the front. Returns whether sampling started.
        spec/tether-black-point.md."""
        images = ccr_backend.images
        target = None
        lead = self._tether_lead_img if self._tether_prompted else None
        for cand in (img, lead, self._tether_last_lit):
            if cand is not None and any(cand is x for x in images):
                target = cand
                break
        if target is None and images:
            idx = self.image_preview.current_idx
            if idx is not None and 0 <= idx < len(images):
                target = images[idx]
        self._bring_to_front()
        if target is None:
            self.sliders_panel.set_temporary_hint(
                "Capture the film lead first, with the light on.", duration=6000)
            return False
        row = next(i for i, x in enumerate(images) if x is target)
        if self.thumbnail_list.thumbnail_list.currentRow() != row:
            self.thumbnail_list.thumbnail_list.setCurrentRow(row)
        self.image_preview.set_bwpoint_mode("black")
        self.sliders_panel.set_temporary_hint(
            "<b>Black Point:</b> draw a rectangle over the clear film base.",
            duration=9000)
        return True

    def _restore_bwpoint(self):
        """Restore a persisted global black point into the backend at startup.
        The roll-wide white point is retired: the White Point is sampled per
        frame now, so an old session's one is dropped rather than overriding
        every frame (spec/white-point-choice.md §4)."""
        bp = decode_bwpoint(
            self._settings.value("convert/black_point_bgr", "", type=str))
        self._settings.remove("convert/white_point_bgr")
        if bp is not None:
            ccr_backend.set_black_point(bp)

    # --- Replacing the session ------------------------------------------------
    def _confirm_replace_session(self) -> bool:
        """Opening files replaces the loaded batch. Ask first whenever one is
        loaded (no prompt on an empty window, e.g. a command-line start).
        Edits are kept: save_catalog() persists them and reopening the same
        files restores them. See spec/open-confirm-and-drop.md."""
        n = len(ccr_backend.images or [])
        if n == 0:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Close current session?")
        box.setText(f"Opening these files will close the current session "
                    f"({n} image{'' if n == 1 else 's'}).")
        box.setInformativeText("Your edits are saved and come back when you "
                               "reopen those files.")
        open_btn = box.addButton("Open", QMessageBox.AcceptRole)
        cancel_btn = box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(cancel_btn)
        box.setEscapeButton(cancel_btn)
        box.exec()
        return box.clickedButton() is open_btn

    # --- Drag and drop from Finder / Explorer ----------------------------------
    # The window accepts drops itself, so a drop over any child that doesn't
    # want it (panels, labels) propagates up to the window. The few children
    # that accept drops on their own (the canvas viewport, text boxes) would
    # otherwise catch or refuse a file drop, so the window's event filter is
    # installed on exactly those — never app-wide, which would put every event
    # in the application through Python. Only EXTERNAL drags carrying local
    # file URLs are taken; in-app drags (event.source() set) pass through.
    @staticmethod
    def _dropped_paths(event):
        if event.source() is not None:
            return []
        md = event.mimeData()
        if md is None or not md.hasUrls():
            return []
        return [u.toLocalFile() for u in md.urls() if u.isLocalFile() and u.toLocalFile()]

    def _install_drop_support(self):
        # The app-wide event filter (installed in __init__) already sees drag
        # events for every widget in this window, so no per-child filters.
        self.setAcceptDrops(True)

    def _handle_drop_event(self, event) -> bool:
        etype = event.type()
        if etype not in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            return False
        paths = self._dropped_paths(event)
        if not paths:
            return False
        if etype == QEvent.Drop:
            event.acceptProposedAction()
            # Defer: running modal dialogs (confirm, TIFF question) inside the
            # platform's drop callback can wedge the drag session on macOS.
            QTimer.singleShot(0, lambda p=list(paths): self.load_paths(p))
        else:
            event.acceptProposedAction()
        return True

    def eventFilter(self, obj, event):
        in_window = isinstance(obj, QWidget) and obj.window() is self
        if event.type() in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            # Only this window's widgets (the filter is app-wide): a dialog
            # that takes drops of its own keeps them.
            if in_window and self._handle_drop_event(event):
                return True
            return super().eventFilter(obj, event)
        # Up/Down are dedicated to switching image: intercepted app-wide before
        # any focused slider/spinbox/combo/text field can use them. Only for
        # widgets in this window, so dialogs and open combo popups keep them.
        if (event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_Up, Qt.Key_Down) and in_window):
            self.thumbnail_list.thumbnail_list.setFocus()
            self.thumbnail_list.thumbnail_list.keyPressEvent(event)
            return True
        return super().eventFilter(obj, event)

    def open_help_website(self):
        webbrowser.open(REPO_URL)
