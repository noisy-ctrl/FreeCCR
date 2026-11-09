"""File -> Live Capture Monitor (spec/live-capture-monitor.md): the camera's
live HDMI view for framing and focusing, the Scanlight's LED channels and
presets, the shutter and the trichrome sequence, in FreeCCR's own look.

A separate, non-modal window. It owns the camera and the serial port while
open; closing it stops a running sequence, turns the light off, closes the
port and stops the feed. On opening it asks about Hot Folder Tether.

A camera on its USB cable (spec/capture-routes.md) is listed beside the
capture devices: its live view feeds the same picture tools, and as the
Shutter it fires over USB and its files are brought into the hot folder.
"""
import json
import math
import os
import re
import sys
import time

from PySide6.QtCore import Qt, QTimer, Signal, QSettings, QEvent, QPointF, QRectF
from PySide6.QtGui import (QAction, QKeySequence, QShortcut, QPainter, QPen, QColor, QFont,
                           QPalette)
from PySide6.QtWidgets import (QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel,
                               QPushButton, QComboBox, QToolBar, QCheckBox, QSizePolicy,
                               QScrollArea, QSlider, QMenu, QInputDialog, QMessageBox,
                               QFileDialog, QApplication, QDialog)

from core import base_exposure as be
from core import camera_backends as cb
from core import capture_light as cl
from core import capture_sequence as cs
from core import capture_guide
from core import capture_log
from core import frame_meter as fm
from core import guide_tracker
from core import light_presets as lp
from core import live_feed as lf
from core import live_frame
from core import live_negative as ln
from core import orp
from core import rail_autofocus as raf
from ui import theme as _theme
from widgets.histogram_widget import HistogramWidget
from widgets.live_canvas import LiveCanvas, clean_guide, GUIDE_DEFAULT
from widgets.sliders_panel import CollapsibleSection, GradientSlider


class _LightTheme:
    """`ui.theme` as this window shows it: light grey with black text
    (spec/live-capture-monitor.md, revision 2026-11-01 (3)). Colour constants
    come back as their light counterparts (`theme.light_colour`); sizes,
    tuples and functions pass through. So every stylesheet and painter in
    this module that names `theme.TEXT_MUTED` and the like is light wherever
    and whenever it is made."""

    def __getattr__(self, name):
        value = getattr(_theme, name)
        if name.isupper() and isinstance(value, str):
            return _theme.light_colour(value)
        return value


theme = _LightTheme()


def light_up(widget):
    """Capture's light look on `widget` and everything in it: palette and
    stylesheet from the light tokens, every descendant's inline stylesheet
    recoloured (`light_qss_text` is idempotent, so ones already light stay),
    and each check box's own palette (its box is drawn from it; under a
    stylesheet a child gets the app's dark palette, not the window's)."""
    t = _theme.LIGHT_TOKENS
    pal = widget.palette()
    for role, key in ((QPalette.Window, "window"), (QPalette.Base, "surface"),
                      (QPalette.Button, "surface"), (QPalette.AlternateBase, "panel"),
                      (QPalette.WindowText, "text"), (QPalette.Text, "text"),
                      (QPalette.ButtonText, "text"), (QPalette.PlaceholderText, "text_muted"),
                      (QPalette.Highlight, "selection_bg"),
                      (QPalette.HighlightedText, "selection_text")):
        pal.setColor(role, QColor(t[key]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(t["text_disabled"]))
    widget.setPalette(pal)
    widget.setAutoFillBackground(True)
    widget.setStyleSheet(_theme.light_qss())
    for w in widget.findChildren(QWidget):
        qss = w.styleSheet()
        if qss:
            light = _theme.light_qss_text(qss)
            if light != qss:
                w.setStyleSheet(light)
    for cb_ in widget.findChildren(QCheckBox):
        cpal = cb_.palette()
        cpal.setColor(QPalette.Base, QColor(t["surface"]))
        cpal.setColor(QPalette.Text, QColor(t["text"]))
        cpal.setColor(QPalette.Window, QColor(t["window"]))
        cpal.setColor(QPalette.Disabled, QPalette.Base, QColor(t["panel"]))
        cpal.setColor(QPalette.Disabled, QPalette.Text, QColor(t["text_disabled"]))
        cb_.setPalette(cpal)

CHANNEL_COLOURS = {"r": theme.CH_R, "g": theme.CH_G, "b": theme.CH_B,
                   "w": "#8a8a8a", "ir": "#b06a7e"}    # white: a grey ring on light
LED_GROOVE_LOW = "#c4c4c4"       # an LED slider's groove at 0 (light look)
LED_GROOVE_W = "#ffffff"         # ... and White's at full
RING_KEYS = (("r", "R", "1"), ("g", "G", "2"), ("b", "B", "3"), ("w", "W", "4"))
LED_VALUE_W = 52
FRAME_ESTIMATE_MS = 1000         # the live estimate of this frame's exposure
FRAME_ESTIMATES = 5              # ... the median of this many
AUTO_GUIDE_MS = 250              # the guide looks for the frame this often
LED_ROW_H = 20                   # the LED sliders: compact rows (the user, 2026-11-01)
LED_ROW_GAP = 1
# the focus rail's jog steps, µm (spec/focus-rail.md §5)
RAIL_JOG_DEFAULT = {"fine_um": 20, "coarse_um": 200}
RAIL_JOG_LIMITS = {"fine_um": (1, 200), "coarse_um": (10, 2000)}
RAIL_JOG_BTN_W = 58
IDLE_CHECK_MS = 10_000
STATE_COLOURS = {"ok": theme.SUCCESS_HOVER, "waiting": theme.TEXT_MUTED,
                 "silent": theme.WARN_TEXT, "error": theme.DANGER, "lost": theme.DANGER,
                 "off": theme.BORDER_STRONG, "unavailable": theme.BORDER_STRONG}


def _settings():
    return QSettings("FreeCCR", "FreeCCR")


def _json_setting(key, default):
    v = _settings().value(key, None)
    if v in (None, ""):
        return default
    try:
        return json.loads(v) if isinstance(v, str) else v
    except (TypeError, ValueError):
        return default


def _muted(text=""):
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: {theme.FS_CAPTION}px;")
    return lab


def _heading(text):
    lab = QLabel(text)
    lab.setStyleSheet(theme.section_header_qss())
    return lab


# --------------------------------------------------------------------------- #
# small widgets
# --------------------------------------------------------------------------- #
class OptionsPopup(QFrame):
    """A right-click options popup in the Glow / Shadow Lift pattern: slider
    rows with value labels and a Reset. `rows` = [(key, label, (lo, hi),
    fmt(v) -> str, tip)]. Emits changed(dict) on every edit."""

    changed = Signal(dict)
    closed = Signal()
    LABEL_W = 80

    def __init__(self, title, rows, values, defaults, parent=None, step=1, value_w=56):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("liveOptionsPopup")
        self.setStyleSheet(
            f"#liveOptionsPopup {{ background: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER_STRONG}; border-radius: 6px; }}")
        self._rows = rows
        self._defaults = dict(defaults)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        t = QLabel(title)
        f = t.font()
        f.setBold(True)
        t.setFont(f)
        lay.addWidget(t)
        self.sliders, self.values_lbl = {}, {}
        for key, name, (lo, hi), fmt, tip in rows:
            row = QHBoxLayout()
            lbl = QLabel(name)
            lbl.setFixedWidth(self.LABEL_W)
            lbl.setToolTip(tip)
            sl = QSlider(Qt.Horizontal)
            sl.setRange(lo, hi)
            sl.setSingleStep(step)
            sl.setPageStep(step * 10)
            sl.setValue(int(values.get(key, defaults[key])))
            sl.setToolTip(tip)
            sl.setMinimumWidth(160)
            val = QLabel(fmt(sl.value()))
            val.setFixedWidth(value_w)
            sl.valueChanged.connect(lambda v, k=key, lab=val, fm=fmt: (lab.setText(fm(v)), self._emit()))
            row.addWidget(lbl)
            row.addWidget(sl, 1)
            row.addWidget(val)
            lay.addLayout(row)
            self.sliders[key], self.values_lbl[key] = sl, val
        reset = QPushButton("Reset")
        reset.setToolTip("Back to the defaults")
        reset.clicked.connect(self.reset)
        lay.addWidget(reset)

    def values(self):
        return {k: s.value() for k, s in self.sliders.items()}

    def reset(self):
        for k, s in self.sliders.items():
            s.blockSignals(True)
            s.setValue(int(self._defaults[k]))
            s.blockSignals(False)
        for key, _n, _r, fmt, _t in self._rows:
            self.values_lbl[key].setText(fmt(self.sliders[key].value()))
        self._emit()

    def _emit(self):
        self.changed.emit(self.values())

    def hideEvent(self, e):
        self.closed.emit()
        super().hideEvent(e)


class SoloRing(QPushButton):
    """A channel's solo button: a ring whose glow shows the channel's value
    in the selected preset, the key above it, and during a trichrome
    capture a clock hand sweeping that step's duration."""

    def __init__(self, label, colour, key, parent=None):
        super().__init__(parent)
        self.label, self.colour, self.key = label, colour, key
        self.value = 0
        self.progress = None
        self.setFixedSize(64, 74)
        self.setFlat(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("border: none; background: transparent;")

    def set_value(self, v):
        self.value = int(v)
        self.update()

    def set_progress(self, p):
        self.progress = p
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self.colour)
        cx, cy, r = 32.0, 44.0, 22.0
        on = self.value > 0
        if on:
            glow = QColor(c)
            glow.setAlpha(int(110 * self.value / 255.0))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(QPointF(cx, cy), r + 4, r + 4)
        p.setBrush(QColor(theme.PANEL))
        p.setPen(QPen(c if on else QColor(theme.BORDER_STRONG), 2.5))
        p.drawEllipse(QPointF(cx, cy), r, r)
        f = QFont(self.font())
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT if on else theme.TEXT_DISABLED))
        p.drawText(QRectF(0, cy - 8, 64, 16), Qt.AlignCenter, self.label)
        f.setBold(False)
        f.setPixelSize(10)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT_MUTED))
        p.drawText(QRectF(0, 0, 64, 14), Qt.AlignHCenter, self.key)
        if self.progress is not None:
            pen = QPen(QColor(theme.TEXT), 4)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            tip = 90 - self.progress * 360
            p.drawArc(QRectF(cx - r - 2, cy - r - 2, 2 * r + 4, 2 * r + 4),
                      int((tip - 26) * 16), int(26 * 16))
        p.end()


# --------------------------------------------------------------------------- #
# the window
# --------------------------------------------------------------------------- #
class LiveCaptureMonitor(QWidget):
    """`main_window` (FreeCCR's) is used for Hot Folder Tether; `feed` and
    `light` can be injected (tests)."""

    closed = Signal()
    _patch_read = Signal(int, object)    # a film-base test shot's reading (or error)
    _zoom_spot_found = Signal(int, object)   # where the magnifier looked (or None)

    def __init__(self, main_window=None, feed=None, light=None, usb=None, rail=None,
                 parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Capture")
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self._mw = main_window
        self._closing = False
        self._asked_tether = False
        self._started = False
        self._levels = dict(cl.OFF)
        self._sleeping = None            # the levels to restore: light off while idle
        self._last_use = time.monotonic()
        self._pick_target = None
        self._neg = {"base": None, "hi": None, "mid": None, "note": ""}
        self._neg_tables = None
        self._clip_kind = ""
        self._reset_best = 0
        self._last_fps = 0.0
        self._await_exposure = False
        self._ring_anim = None
        self._popup = None
        self._cv_found = []              # [(index, w, h)] capture devices
        self._usb_found = []             # [(key, label)] USB cameras
        self._want_key = None            # the camera to open once it is listed
        self._usb_model = ""
        self._usb_dest = None
        self._exposure = {}
        # shutter speed / ISO set from here (spec/usb-exposure-control.md)
        self._exp_choices = {}           # field -> the camera's values
        self._exp_waiting = {}           # field -> value sent, not yet confirmed
        self._exp_held = {}              # field -> value picked during a sequence
        self._addon_warned = set()       # cameras warned about this session
        # film bases (spec/film-base-exposure.md)
        self._measure = None             # the measuring in progress
        self._measure_token = 0          # voids a finished measuring's timers / reads
        self._pending_measure = None     # (name, index) waiting for a Film Base click
        self._neg_base_pos = None        # the last Film Base click, frame fractions
        self._new_frame_quiet = 0.0      # no "new frame" note before this
        self._cam_zoom = "1"             # the camera's magnifier (spec/live-magnifier.md)
        self._shoot_when_whole = None    # a Shoot waiting for Camera zoom to be Whole
        self._zoom_whole = None          # the last whole-frame picture before magnifying
        self._zoom_token = 0
        self._zoom_spot = None           # (fx, fy) where it last looked
        self._shoot_exp_before = None    # the camera's exposure before a film-base Shoot
        self._log_shot = None            # the shot being logged (spec/capture-log-and-names.md)
        self._read_patch = be.read_patch
        # the focus rail (spec/focus-rail.md)
        self._af = None                  # the autofocus sweep running (or last run)
        self._af_then = None             # called with the sweep's result when it ends
        self._af_shot = None             # autofocus before a shot (spec/focus-rail.md rev. 2 C)
        self._rail_zeroed = False        # Set Zero pressed since the rail connected
        self._rail_home_id = None
        self._last_frame_t = -1e9
        self._live_trouble = None        # USB live view stopped: see _on_live_paused
        self._camera_up = False          # a camera connected (the feed's or a USB one)
        # the light comes on white as it connects (2026-11-09; tests turn it off)
        self.light_on_connect = True
        # this frame's own exposure (spec/frame-exposure.md)
        self._frame_est = []             # recent live ratios
        self._frame_metered = None       # a Meter's Suggestion for the frame in view
        self._frame_suggestion = None    # what the line shows (metered, else live)
        self._frame_exposures = None     # armed by Use for This Frame: the next Shoot
        self._read_frame = fm.read_frame

        st = _settings()
        self._toggles = _json_setting("live/toggles", {"denoise": True})
        self._denoise_opts = lf.clean_options(_json_setting("live/denoise", {}),
                                              lf.DENOISE_DEFAULT, lf.DENOISE_LIMITS)
        self._edge_opts = lf.clean_options(_json_setting("live/edge_amp", {}),
                                           lf.EDGE_DEFAULT, lf.EDGE_LIMITS)
        self._timing = cs.clean_timing(_json_setting("live/timing", {}))
        self._presets = lp.normalise(st.value("live/presets", ""))
        self._bases = be.normalise(st.value("live/film_bases", ""))
        self._rail_jog = lf.clean_options(_json_setting("live/rail_jog", {}),
                                          RAIL_JOG_DEFAULT, RAIL_JOG_LIMITS)
        self._rail_af_opts = raf.clean_options(_json_setting("live/rail_af", {}))

        self.feed = feed if feed is not None else lf.LiveFeed()
        if light is None:
            from core.light_link import LightClient
            light = LightClient()
        self.light = light
        if usb is None:
            from core.usb_camera import UsbCamera
            usb = UsbCamera()
        self.usb = usb
        if rail is None:
            from core.rail_link import RailClient
            rail = RailClient()
        self.rail = rail
        if hasattr(self.feed, "sources"):
            self.feed.sources["usb"] = self.usb.frame_source
        self.sequence = cs.CaptureSequence(_SequenceLight(self), on_step=self._on_step,
                                           on_finished=self._on_sequence_finished)
        self._usb_shutter = _UsbShutter(self)

        self._build()
        self._apply_light()
        self._load_view_state()
        self._wire()
        self._apply_toggles()
        self._refresh_presets()
        self._refresh_film_bases()
        self._apply_scan_type(self.scan_type())
        self._on_ports(self.light.ports)          # the link may have reported already
        self._refresh_light_status()
        self._on_rail_ports(self.rail.ports)
        self._refresh_rail()
        geo = st.value("live/geometry")
        if geo is not None:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass
        else:
            self.resize(1400, 880)
        QApplication.instance().installEventFilter(self)

    # ------------------------------------------------------------------ #
    # building
    # ------------------------------------------------------------------ #
    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        left = QVBoxLayout()
        left.setContentsMargins(theme.GAP_PANEL, 0, 0, 0)
        left.setSpacing(0)
        self.toolbar = self._build_toolbar()
        left.addWidget(self.toolbar)
        self.canvas = LiveCanvas()
        left.addWidget(self.canvas, 1)
        left.addWidget(self._build_status_strip())
        root.addLayout(left, 1)
        self.panel = self._build_panel()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.panel)
        self.scroll.setFixedWidth(theme.PANEL_W + 14)
        root.addWidget(self.scroll)
        self._build_wake_overlay()

    def _apply_light(self):
        """Light grey with black text (revision 2026-11-01 (3)): `light_up`
        on the window, the control panel's own palette (it fills its
        background inside the scroll area, out of the window palette's
        reach), and a neutral mid-grey surround for the picture."""
        t = _theme.LIGHT_TOKENS
        light_up(self)
        ppal = self.panel.palette()
        ppal.setColor(QPalette.Window, QColor(t["window"]))
        self.panel.setPalette(ppal)
        self.canvas.surround = QColor(t["canvas"])
        self.canvas.message_colour = QColor(t["text"])

    def light_dialog(self, dlg):
        """A window Capture opens, made light like Capture (the user,
        2026-11-01: the Start Hot Folder Tether window stayed dark)."""
        light_up(dlg)
        if hasattr(dlg, "restyle"):               # its later stylesheets too
            dlg.restyle = _theme.light_qss_text
        return dlg

    def _build_wake_overlay(self):
        """Covers the whole control panel while the light is off for being
        idle: nothing is shot in the dark, and only Wake turns it back on
        (spec/live-capture-monitor.md §7.5)."""
        ov = QFrame(self.scroll)
        ov.setObjectName("WakeOverlay")
        ov.setStyleSheet(
            "#WakeOverlay { background-color: rgba(30, 30, 30, 248); }")
        lay = QVBoxLayout(ov)
        lay.setContentsMargins(theme.SPACE_XL, theme.SPACE_XL, theme.SPACE_XL, theme.SPACE_XL)
        lay.addStretch(1)
        title = QLabel("Asleep while idle")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color: {theme.TEXT}; font-weight: bold;")
        lay.addWidget(title)
        self.wake_note = _muted("")
        self.wake_note.setAlignment(Qt.AlignCenter)
        self.wake_note.setWordWrap(True)
        lay.addWidget(self.wake_note)
        self.wake_btn = QPushButton("Wake")
        self.wake_btn.setFixedHeight(theme.CONTROL_H_LG)
        theme.style_button(self.wake_btn, "primary")
        self.wake_btn.setToolTip("Turn the Scanlight back on at the levels it had and "
                                 "reconnect the camera")
        self.wake_btn.clicked.connect(self._wake)
        lay.addWidget(self.wake_btn)
        lay.addStretch(1)
        ov.setVisible(False)
        self.wake_overlay = ov
        self.scroll.installEventFilter(self)

    def _place_wake_overlay(self):
        self.wake_overlay.setGeometry(self.scroll.rect())
        self.wake_overlay.raise_()

    def _build_toolbar(self):
        tb = QToolBar()
        tb.setStyleSheet("QToolBar { spacing: 1px; }")
        self.actions = {}

        def toggle(key, text, tip, menu=None):
            a = QAction(text, self)
            a.setCheckable(True)
            a.setToolTip(tip)
            a.toggled.connect(lambda on, k=key: self._on_toggle(k, on))
            tb.addAction(a)
            self.actions[key] = a
            if menu is not None:
                btn = tb.widgetForAction(a)
                if btn is not None:
                    btn.setContextMenuPolicy(Qt.CustomContextMenu)
                    btn.customContextMenuRequested.connect(
                        lambda pos, b=btn, m=menu: m(b.mapToGlobal(pos)))
            return a
        toggle("denoise", "Reduce noise",
               "Averages the last few frames, so the view is quieter. Restarts "
               "by itself when the film moves.\nRight-click: strength.",
               self._denoise_menu)
        toggle("edge", "Edge amplify",
               "Sharpens edges in the view (luma only, so colour noise isn't "
               "amplified) to judge focus.\nRight-click: amount and radius.",
               self._edge_menu)
        toggle("sharp", "Sharpness",
               "A focus meter: how sharp the view (or a drawn area) is, as a "
               "share of the best this session. Brightness doesn't move it.\n"
               "Click the meter to start a new best.\n"
               "Right-click: draw the area, whole view, reset the best.",
               self._sharp_menu)
        toggle("fc", "False colour",
               "Exposure bands on the live picture (the main canvas's scale).")
        toggle("guide", "Frame guide",
               "A box for placing the negative the same way every time: drag "
               "to move, a corner to resize, just outside a corner to rotate.\n"
               "Right-click: film format, nudges, fit the view to it.",
               self._guide_menu)
        toggle("align", "Alignment",
               "A vertical line fixed on the sensor, to line the same film edge "
               "up against every time.\nRight-click: place the line.",
               self._align_menu)
        tb.addSeparator()
        toggle("negative", "Negative Preview",
               "A rough positive of the white-lit negative, for judging the "
               "frame (FreeCCR converts the RAW). Its controls are in the "
               "panel under Negative Preview.\nRight-click: colour negative or "
               "black and white.",
               self._negative_menu)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        # The frame guide's film format, left of Camera zoom (the user,
        # 2026-11-01: "a 'Format' drop down ... There you select format and it
        # changes the frame guide"); the guide's menu offers the same list
        fmt_box = QWidget()
        fz = QHBoxLayout(fmt_box)
        fz.setContentsMargins(0, 0, theme.GAP_BTN, 0)
        fz.setSpacing(theme.GAP_TIGHT * 2)
        fz.addWidget(QLabel("Format"))
        self.format_combo = QComboBox()
        self.format_combo.setToolTip("The frame guide's film format: the guide takes its "
                                     "shape. Free: any shape.")
        from core.film_formats import FILM_FORMATS
        self.format_combo.addItem("Free", "")
        for key, label, _ratio, _cols in FILM_FORMATS:
            self.format_combo.addItem(label, key)
        # compact in the toolbar (the camera's own lists sit beside it); the
        # open list is as wide as its longest entry
        self.format_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.format_combo.setMinimumContentsLength(8)
        self.format_combo.view().setMinimumWidth(
            self.format_combo.view().sizeHintForColumn(0) + 24)
        fz.addWidget(self.format_combo)
        self._format_action = tb.addWidget(fmt_box)
        # The camera's own magnifier sits beside the window's zoom, labelled
        # so the two can't be confused (spec/live-magnifier.md revision 1)
        cam_box = QWidget()
        cz = QHBoxLayout(cam_box)
        cz.setContentsMargins(0, 0, theme.GAP_BTN, 0)
        cz.setSpacing(theme.GAP_TIGHT * 2)
        cz.addWidget(QLabel("Camera zoom"))
        self.cam_zoom_combo = QComboBox()
        self.cam_zoom_combo.setToolTip("The camera's own live view magnification, "
                                       "for focusing")
        cz.addWidget(self.cam_zoom_combo)
        self._cam_zoom_action = tb.addWidget(cam_box)
        self._cam_zoom_action.setVisible(False)
        view_box = QWidget()
        vz = QHBoxLayout(view_box)
        vz.setContentsMargins(0, 0, 0, 0)
        vz.setSpacing(theme.GAP_TIGHT * 2)
        vz.addWidget(QLabel("View"))
        self.zoom_combo = QComboBox()
        # factors of the fitted view: "1:1" meant less than Fit on a USB live
        # view (1024 px) and did nothing
        for label, key in self.VIEW_ZOOMS:
            self.zoom_combo.addItem(label, key)
        self.zoom_combo.setToolTip("The window's zoom, as times the fitted view. The wheel "
                                   "zooms about the pointer, drag pans, double-click "
                                   "switches Fit / 4× (F).")
        self.zoom_combo.activated.connect(self._on_view_zoom_chosen)
        vz.addWidget(self.zoom_combo)
        tb.addWidget(view_box)
        a = QAction("Panel", self)
        a.setCheckable(True)
        a.setChecked(True)
        a.setToolTip("Show or hide the side panel")
        a.toggled.connect(self._on_panel_toggled)
        tb.addAction(a)
        self.panel_action = a
        return tb

    def _build_status_strip(self):
        w = QWidget()
        r = QHBoxLayout(w)
        r.setContentsMargins(0, theme.GAP_ROW, theme.GAP_PANEL, theme.GAP_ROW)
        r.setSpacing(theme.GAP_BTN)
        r.addWidget(QLabel("Camera"))
        self.camera_combo = QComboBox()
        self.camera_combo.setMinimumWidth(190)
        self.camera_combo.addItem("Looking…", None)
        r.addWidget(self.camera_combo)
        self.find_btn = QPushButton("Find Cameras")
        self.find_btn.setToolTip("Look for capture devices again (each is opened once "
                                 "to check it delivers a picture)")
        r.addWidget(self.find_btn)
        self.camera_info = _muted("")
        self.camera_info.setWordWrap(False)
        r.addWidget(self.camera_info)
        self.addon_badge = QLabel("")
        self.addon_badge.setStyleSheet(f"color: {theme.WARN_TEXT}; font-size: {theme.FS_CAPTION}px;")
        self.addon_badge.setVisible(False)
        r.addWidget(self.addon_badge)
        self.release_btn = QPushButton("Release Camera from Other Apps")
        self.release_btn.setToolTip("macOS's own camera service is holding the camera: "
                                    "end its hold so FreeCCR can open it")
        self.release_btn.setVisible(False)
        r.addWidget(self.release_btn)
        r.addStretch(1)
        self.tether_label = _muted("Hot folder: off")
        self.tether_label.setWordWrap(False)
        r.addWidget(self.tether_label)
        self.tether_btn = QPushButton("Start Hot Folder Tether…")
        self.tether_btn.setToolTip("Load the frames your camera's tether app saves into a "
                                   "folder as you shoot (File → Hot Folder Tether)")
        r.addWidget(self.tether_btn)
        return w

    def _build_panel(self):
        outer = QWidget()
        outer.setFixedWidth(theme.PANEL_W)
        lay = QVBoxLayout(outer)
        theme.apply_panel_spacing(lay)

        self.histogram = HistogramWidget()
        self.histogram.setFixedHeight(140)
        self.histogram.set_caption("Live")
        lay.addWidget(self.histogram)

        # --- Scanlight ---
        lay.addWidget(_heading("Scanlight"))
        r = QHBoxLayout()
        theme.apply_button_row(r)
        self.light_dot = QLabel("●")
        self.port_combo = QComboBox()
        theme.shrinkable_combo(self.port_combo)
        self.port_combo.setToolTip("The Scanlight's serial port (likely USB ports first)")
        self.rescan_btn = QPushButton("Rescan")
        self.rescan_btn.setToolTip("List the serial ports again")
        self.connect_btn = QPushButton("Connect")
        r.addWidget(self.light_dot)
        r.addWidget(self.port_combo, 1)
        r.addWidget(self.rescan_btn)
        r.addWidget(self.connect_btn)
        lay.addLayout(r)
        self.light_status = _muted("Not connected")
        lay.addWidget(self.light_status)
        lay.addWidget(theme.section_separator())

        # --- LED ---
        lay.addWidget(_heading("LED"))
        self.led_sliders, self.led_values = {}, {}
        leds = QVBoxLayout()
        leds.setContentsMargins(0, 0, 0, 0)
        leds.setSpacing(LED_ROW_GAP)
        alone = {"w": "White runs alone: it turns the others off.",
                 "ir": "Infrared runs alone: it turns the others off."}
        for k in cl.CHANNELS:
            row = QHBoxLayout()
            row.setSpacing(theme.GAP_TIGHT)
            lab = QLabel(cl.NAMES[k])
            lab.setFixedWidth(theme.LABEL_COL_W)
            lab.setFixedHeight(LED_ROW_H)
            lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            s = GradientSlider((LED_GROOVE_LOW, LED_GROOVE_W if k == "w"
                                else CHANNEL_COLOURS[k]), Qt.Horizontal)
            s.setRange(0, 255)
            s.reset_value = 0
            s.setFixedHeight(LED_ROW_H)
            s.setToolTip(f"{cl.NAMES[k]} LED. Double-click: off.\n"
                         + alone.get(k, "Red, green and blue mix; they turn White and "
                                        "Infrared off."))
            v = QLabel("0.0 %")
            v.setFixedWidth(LED_VALUE_W)
            v.setFixedHeight(LED_ROW_H)
            v.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            s.valueChanged.connect(lambda val, ch=k: self._on_led_slider(ch, val))
            row.addWidget(lab)
            row.addWidget(s, 1)
            row.addWidget(v)
            leds.addLayout(row)
            self.led_sliders[k], self.led_values[k] = s, v
        lay.addLayout(leds)
        lay.addWidget(theme.section_separator())

        # --- Presets ---
        lay.addWidget(_heading("Presets"))
        r = QHBoxLayout()
        theme.apply_button_row(r)
        self.preset_combo = QComboBox()
        theme.shrinkable_combo(self.preset_combo)
        self.preset_combo.setContextMenuPolicy(Qt.CustomContextMenu)
        self.preset_combo.setToolTip("Light levels for solo and trichrome.\n"
                                     "Right-click: rename, delete, import.")
        self.save_as_btn = QPushButton("Save As…")
        self.save_as_btn.setToolTip("Save the LED levels as a new preset")
        self.save_btn = QPushButton("Save")
        self.save_btn.setToolTip("Overwrite the selected preset with the LED levels")
        r.addWidget(self.preset_combo, 1)
        r.addWidget(self.save_as_btn)
        r.addWidget(self.save_btn)
        lay.addLayout(r)
        rings = QHBoxLayout()
        rings.addStretch(1)
        self.rings = {}
        for k, label, key in RING_KEYS:
            ring = SoloRing(label, CHANNEL_COLOURS[k], key)
            ring.setToolTip(f"Light only {cl.NAMES[k]}, at the preset's value ({key})")
            ring.clicked.connect(lambda _c=False, ch=k: self.solo(ch))
            rings.addWidget(ring)
            rings.addStretch(1)
            self.rings[k] = ring
        lay.addLayout(rings)
        lay.addWidget(theme.section_separator())

        # --- Capture ---
        lay.addWidget(_heading("Capture"))
        r = QHBoxLayout()
        theme.apply_button_row(r)
        # What is being scanned (spec/scan-type.md): decides what Shoot does
        # and how Hot Folder Tether loads the files. One app-wide setting.
        from core import scan_type as st
        from core.ccr_backend import ccr_backend
        self.scan_combo = QComboBox()
        for t in st.SCAN_TYPES:
            self.scan_combo.addItem(st.LABELS[t], t)
        theme.shrinkable_combo(self.scan_combo)
        self.scan_combo.setToolTip(
            "What you are scanning.\nColour · white light and Black & white: one "
            "shot with the light as it is (white, or a single colour).\nColour · "
            "trichrome: one shot each under the preset's red, green and blue, then "
            "back to white; Hot Folder Tether merges each three into one frame.\n"
            "Black & white frames load in Black & White.")
        self.scan_combo.setCurrentIndex(max(0, self.scan_combo.findData(
            st.clean(getattr(ccr_backend, "scan_type", st.DEFAULT)))))
        self.timing_btn = QPushButton("Timing…")
        self.timing_btn.setToolTip("Focus, pulse, hold and period")
        r.addWidget(self.scan_combo, 1)
        r.addWidget(self.timing_btn)
        lay.addLayout(r)
        r = QHBoxLayout()
        theme.apply_button_row(r)
        lab = QLabel("Shutter")
        r.addWidget(lab)
        self.shutter_combo = QComboBox()
        theme.shrinkable_combo(self.shutter_combo)
        self.shutter_combo.setToolTip(
            "What fires the camera.\nScanlight: its release output, on the Timing "
            "settings.\nUSB: the camera over its USB cable; each shot waits for its "
            "file, which is brought into the hot folder. A single shot then doesn't "
            "need the Scanlight; trichrome does (it sets the colours).")
        self.shutter_combo.addItem("Scanlight", "scanlight")
        r.addWidget(self.shutter_combo, 1)
        lay.addLayout(r)
        # Shutter speed and ISO (spec/usb-exposure-control.md): only with a
        # USB camera open that lets the computer set them.
        self.exposure_row = QWidget()
        r = QHBoxLayout(self.exposure_row)
        r.setContentsMargins(0, 0, 0, 0)
        theme.apply_button_row(r)
        exp_lab = QLabel("Exposure")
        col = max(lab.sizeHint().width(), exp_lab.sizeHint().width())
        lab.setFixedWidth(col)                   # the two rows' pickers line up
        exp_lab.setFixedWidth(col)
        r.addWidget(exp_lab)
        why = ("\nThe camera's own dials don't work while it is connected over USB, "
               "so exposure is set here. With live view running, the picture "
               "pauses for under a second while it is set.")
        self.shutter_speed_combo = QComboBox()
        theme.shrinkable_combo(self.shutter_speed_combo)
        self.shutter_speed_combo.setToolTip("Shutter speed." + why)
        self.iso_combo = QComboBox()
        theme.shrinkable_combo(self.iso_combo)
        self.iso_combo.setToolTip("ISO (Auto: the camera's automatic ISO settings)." + why)
        r.addWidget(self.shutter_speed_combo, 1)
        r.addWidget(self.iso_combo, 1)
        self.exposure_row.setVisible(False)
        lay.addWidget(self.exposure_row)
        # Film bases (spec/film-base-exposure.md): a shutter speed per colour,
        # measured on the film base; shown with the Exposure row.
        self.film_base_box = QWidget()
        fb = QVBoxLayout(self.film_base_box)
        fb.setContentsMargins(0, 0, 0, 0)
        fb.setSpacing(theme.GAP_ROW)
        r = QHBoxLayout()
        r.setContentsMargins(0, 0, 0, 0)
        theme.apply_button_row(r)
        base_lab = QLabel("Film base")
        col = max(col, base_lab.sizeHint().width())
        for w in (lab, exp_lab, base_lab):
            w.setFixedWidth(col)
        r.addWidget(base_lab)
        self.film_base_combo = QComboBox()
        theme.shrinkable_combo(self.film_base_combo)
        self.film_base_combo.setContextMenuPolicy(Qt.CustomContextMenu)
        self.film_base_combo.setToolTip(
            "Film bases measured so far: each colour's shutter speed (and LED level) "
            "that puts this film's base just under the sensor's ceiling.\n"
            "Right-click: measure again, edit, rename, delete.")
        self.measure_btn = QPushButton("Measure…")
        self.measure_btn.setToolTip(
            "With the film base in view (click it in Negative Preview, or let the "
            "border be found): test shots read the base in the RAW and pick each "
            "colour's shutter speed at ISO 100. The shots aren't kept.")
        self.edit_btn = QPushButton("Edit…")
        self.edit_btn.setToolTip(
            "Set this film base's shutter speed, ISO and LED level per colour by "
            "hand. Save overwrites it; Save as New keeps it and adds the edit.")
        r.addWidget(self.film_base_combo, 1)
        r.addWidget(self.edit_btn)
        r.addWidget(self.measure_btn)
        fb.addLayout(r)
        self.film_base_note = _muted("")
        fb.addWidget(self.film_base_note)
        self.film_base_use = QCheckBox("Shoot at these exposures")
        self.film_base_use.setToolTip(
            "Shoot over USB sets each colour's shutter speed and LED level from the "
            "film base above before its shot. Off: Shoot uses the Exposure row.")
        self.film_base_use.setChecked(_settings().value("live/film_base_use", True)
                                      not in (False, "false", "0", 0))
        fb.addWidget(self.film_base_use)
        # this frame's own exposure, inside the frame guide (spec/frame-exposure.md)
        self.frame_note = _muted("")
        fb.addWidget(self.frame_note)
        # revision 3: every Shoot uses this frame's suggestion, no Use click
        self.frame_auto = QCheckBox("Per shot, scale exposure to the frame's density")
        self.frame_auto.setToolTip(
            "Every Shoot uses the frame's own exposures, from the line above (the "
            "live view's estimate, or a Meter reading): longer for a dense frame. "
            "With no estimate for the frame, it shoots at the film base's exposures "
            "and says so. Only the shutter speed and ISO change.")
        self.frame_auto.setChecked(_settings().value("live/frame_auto", False)
                                   in (True, "true", "1", 1))
        fb.addWidget(self.frame_auto)
        r = QHBoxLayout()
        r.setContentsMargins(0, 0, 0, 0)
        theme.apply_button_row(r)
        self.meter_btn = QPushButton("Meter")
        self.meter_btn.setToolTip(
            "One RAW test shot per colour at the film base's exposures, read inside the "
            "frame guide: this frame's exposure exactly. The shots aren't kept.")
        self.use_frame_btn = QPushButton("Use for This Frame")
        self.use_frame_btn.setToolTip(
            "The next Shoot uses this frame's exposures (longer for a dense frame), then "
            "it goes back to the film base's. Only the shutter speed and ISO change, so "
            "the file records it and conversion follows.")
        r.addWidget(self.meter_btn, 1)
        r.addWidget(self.use_frame_btn, 2)
        self.meter_btn.setVisible(self._meter_shots_on())
        fb.addLayout(r)
        self.film_base_box.setVisible(False)
        lay.addWidget(self.film_base_box)
        self.shoot_btn = QPushButton("Shoot   (S)")
        self.shoot_btn.setFixedHeight(theme.CONTROL_H_LG)
        theme.style_button(self.shoot_btn, "primary")
        lay.addWidget(self.shoot_btn)
        # the roll and the next shot's number (spec/capture-log-and-names.md)
        self.roll_note = _muted("")
        lay.addWidget(self.roll_note)
        # The roll's black point is sampled in the main window on a capture
        # (spec/tether-black-point.md): this brings it there, light ready.
        self.black_point_btn = QPushButton("Set Black Point…")
        self.black_point_btn.setFixedHeight(theme.CONTROL_H)
        self.black_point_btn.setToolTip(
            "Set the roll's black point from the newest lit capture: FreeCCR's "
            "main window comes forward; draw a rectangle over the clear film base.")
        self.black_point_btn.clicked.connect(self._on_black_point)
        self.black_point_btn.setEnabled(
            hasattr(self._mw, "start_roll_black_point"))
        lay.addWidget(self.black_point_btn)
        self.capture_caption = _muted("")
        lay.addWidget(self.capture_caption)

        self._build_rail_section(lay)

        # --- Negative Preview ---
        self.neg_section = CollapsibleSection("Negative Preview")
        r = QHBoxLayout()
        theme.apply_button_row(r)
        self.neg_buttons = {}
        for key, text, tip in (("base", "Film Base", "Click, then click the film base (the rebate)"),
                               ("hi", "Highlight", "Click, then click a neutral highlight"),
                               ("mid", "Mid Grey", "Optional: click, then a neutral mid-tone"),
                               ("auto", "Auto", "Find the base and highlight in the picture")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, k=key: self._on_neg_button(k))
            r.addWidget(b)
            self.neg_buttons[key] = b
        self.neg_section.add_layout(r)
        self.neg_sliders = {}
        for key, name, grad in (("temperature", "Temperature", theme.TEMP_GRADIENT),
                                ("tint", "Tint", theme.TINT_GRADIENT),
                                ("brightness", "Brightness", None)):
            row = QHBoxLayout()
            row.setSpacing(theme.GAP_TIGHT)
            lab = QLabel(name)
            lab.setFixedWidth(theme.LABEL_COL_W)
            lab.setFixedHeight(theme.CONTROL_H)
            lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            from widgets.sliders_panel import ResettableSlider
            s = GradientSlider(grad, Qt.Horizontal) if grad else ResettableSlider(Qt.Horizontal)
            s.setRange(-100, 100)
            s.setValue(0)
            s.reset_value = 0
            s.setFixedHeight(theme.CONTROL_H)
            v = QLabel("0")
            v.setFixedWidth(theme.VALUE_COL_W)
            s.valueChanged.connect(lambda val, lab_=v: (lab_.setText(str(val)), self._rebuild_negative()))
            row.addWidget(lab)
            row.addWidget(s, 1)
            row.addWidget(v)
            self.neg_section.add_layout(row)
            self.neg_sliders[key] = s
        self.neg_bw = QCheckBox("Black && white")
        self.neg_bw.toggled.connect(lambda _on: self._push_params())
        self.neg_section.add_widget(self.neg_bw)
        self.neg_note = _muted("Film base and highlight: Auto until sampled.")
        self.neg_section.add_widget(self.neg_note)
        lay.addWidget(self.neg_section)
        lay.addStretch(1)
        return outer

    def _build_rail_section(self, lay):
        """Focus Rail (spec/focus-rail.md §5): a stepper on the camera's rail,
        over the Open Rail Protocol."""
        sec = self.rail_section = CollapsibleSection("Focus Rail")
        r = QHBoxLayout()
        theme.apply_button_row(r)
        self.rail_dot = QLabel("●")
        self.rail_port_combo = QComboBox()
        theme.shrinkable_combo(self.rail_port_combo)
        self.rail_port_combo.setToolTip(
            "The focus rail's serial port (an ESP32's USB first; the light's port "
            "isn't listed while the light is connected)")
        self.rail_rescan_btn = QPushButton("Rescan")
        self.rail_rescan_btn.setToolTip("List the serial ports again")
        self.rail_connect_btn = QPushButton("Connect")
        r.addWidget(self.rail_dot)
        r.addWidget(self.rail_port_combo, 1)
        r.addWidget(self.rail_rescan_btn)
        r.addWidget(self.rail_connect_btn)
        sec.add_layout(r)
        self.rail_status = _muted("Not connected")
        sec.add_widget(self.rail_status)
        r = QHBoxLayout()
        theme.apply_button_row(r)
        self.rail_jog_btns = {}
        for key, sign, size, keys in (("down_coarse", -1, "coarse", "{"),
                                      ("down_fine", -1, "fine", "["),
                                      ("up_fine", 1, "fine", "]"),
                                      ("up_coarse", 1, "coarse", "}")):
            b = QPushButton()
            b._jog = (sign, size, keys)              # the tooltip is set with the rail's units
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda _p, w=b: self._rail_jog_menu(w.mapToGlobal(_p)))
            b.clicked.connect(lambda _c=False, sg=sign, sz=size: self.rail_jog(sg, sz))
            b.setMinimumWidth(RAIL_JOG_BTN_W)       # four and the position in one row
            self.rail_jog_btns[key] = b
        self.rail_pos = QLabel("—")
        self.rail_pos.setAlignment(Qt.AlignCenter)
        self.rail_pos.setToolTip("Where the rail is")
        r.addWidget(self.rail_jog_btns["down_coarse"], 1)
        r.addWidget(self.rail_jog_btns["down_fine"], 1)
        r.addWidget(self.rail_pos, 2)
        r.addWidget(self.rail_jog_btns["up_fine"], 1)
        r.addWidget(self.rail_jog_btns["up_coarse"], 1)
        sec.add_layout(r)
        r = QHBoxLayout()
        theme.apply_button_row(r)
        self.rail_zero_btn = QPushButton("Set Zero")
        self.rail_zero_btn.setToolTip("Call this position 0 (no end switch needed)")
        self.rail_home_btn = QPushButton("Home")
        self.rail_home_btn.setToolTip("Run to the rail's end, on its switch or a stall, "
                                      "and call that 0")
        self.rail_stop_btn = QPushButton("Stop")
        self.rail_stop_btn.setToolTip("Stop the rail (Esc)")
        self.rail_af_btn = QPushButton("Autofocus")
        self.rail_af_btn.setToolTip(
            "Sweep the rail through focus reading the sharpness meter (the drawn area, "
            "or the whole view) and stop at the sharpest point. Best at Camera zoom "
            "8×.\nRight-click: range, steps, backlash, settle.")
        self.rail_af_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        for b in (self.rail_zero_btn, self.rail_home_btn, self.rail_stop_btn,
                  self.rail_af_btn):
            r.addWidget(b, 1)
        sec.add_layout(r)
        # autofocus before each shot (spec/focus-rail.md revision 2 C)
        r = QHBoxLayout()
        theme.apply_button_row(r)
        self.rail_af_shoot = QCheckBox("Autofocus before each shot")
        self.rail_af_shoot.setToolTip(
            "Shoot first magnifies the camera, focuses with the rail, goes back to the "
            "whole frame and then shoots, as set up (one colour or red, green and "
            "blue). It focuses under the light as it is. If no focus is found, "
            "nothing is shot.")
        self.rail_af_zoom = QComboBox()
        for label, data in (("at 8×", "8"), ("at Max", "16"), ("no zoom", "")):
            self.rail_af_zoom.addItem(label, data)
        self.rail_af_zoom.setToolTip(
            "The camera zoom it focuses at (the camera's magnifier, when it has one)")
        self.rail_af_shoot.setChecked(_settings().value("live/rail_af_shoot", False,
                                                        type=bool))
        i = self.rail_af_zoom.findData(str(_settings().value("live/rail_af_zoom", "8")))
        self.rail_af_zoom.setCurrentIndex(max(0, i))
        r.addWidget(self.rail_af_shoot, 1)
        r.addWidget(self.rail_af_zoom)
        sec.add_layout(r)
        self.rail_caption = _muted("")
        sec.add_widget(self.rail_caption)
        if _settings().value("live/rail_open", False, type=bool):
            sec._toggle_btn.setChecked(True)
            sec._on_toggle(True)
        sec._toggle_btn.toggled.connect(
            lambda on: _settings().setValue("live/rail_open", bool(on)))
        lay.addWidget(sec)

    def _wire(self):
        c = self.canvas
        c.view_changed.connect(self._push_params)
        c.picked.connect(self._on_picked)
        c.box_picked.connect(self._on_box_picked)
        c.area_drawn.connect(lambda a: (self._push_params(), self._save_view_state()))
        c.line_placed.connect(lambda x: self._save_view_state())
        c.guide_changed.connect(lambda g: self._save_view_state())
        c.pointer_moved.connect(self._used)
        c.meter_clicked.connect(self._reset_sharp_best)
        self.histogram.clip_hover.connect(self._on_clip_hover)
        self.feed.frame_ready.connect(self._on_frame)
        self.feed.cameras_found.connect(self._on_cameras)
        self.feed.status.connect(self._on_feed_status)
        self.camera_combo.activated.connect(self._on_camera_chosen)
        self.find_btn.clicked.connect(self.find_cameras)
        self.usb.cameras_found.connect(self._on_usb_cameras)
        self.usb.state.connect(self._on_usb_state)
        self.usb.file_saved.connect(self._on_usb_file)
        self.usb.file_skipped.connect(self._on_usb_skipped)
        self.usb.exposure_changed.connect(self._on_exposure)
        self.usb.zoom_levels.connect(self._on_zoom_levels)
        if hasattr(self.usb, "addons_found"):
            self.usb.addons_found.connect(self._on_addons)
        if hasattr(self.usb, "live_paused"):
            self.usb.live_paused.connect(self._on_live_paused)
        if hasattr(self.usb, "live_restarting"):
            self.usb.live_restarting.connect(self._on_live_restarting)
        if hasattr(self.usb, "live_not_back"):
            self.usb.live_not_back.connect(self._on_live_not_back)
        self.usb.fire_failed.connect(self._on_fire_failed)
        if hasattr(self.usb, "exposure_choices"):
            self.usb.exposure_choices.connect(self._on_exposure_choices)
        if hasattr(self.usb, "exposure_set"):
            self.usb.exposure_set.connect(self._on_exposure_set)
        for field, name in self.EXP_FIELDS:
            combo = getattr(self, name)
            combo.activated.connect(
                lambda i, f=field, c=combo: self._on_exposure_picked(f, c.itemData(i)))
        self.shutter_combo.activated.connect(self._on_shutter_chosen)
        self.cam_zoom_combo.activated.connect(
            lambda i: self._set_cam_zoom(self.cam_zoom_combo.itemData(i)))
        self.canvas.menu_requested.connect(self._canvas_menu)
        self.canvas.unmagnify_requested.connect(lambda: self._set_cam_zoom("1"))
        self.canvas.guide_menu_requested.connect(self._guide_menu)
        self.format_combo.activated.connect(
            lambda i: self._set_guide_format(self.format_combo.itemData(i) or None))
        if hasattr(self.usb, "zoom_changed"):
            self.usb.zoom_changed.connect(self._on_cam_zoom)
        self._zoom_spot_found.connect(self._on_zoom_spot)
        self.release_btn.clicked.connect(self._on_release_camera)
        self.light.state_changed.connect(self._on_light_state)
        self.light.telemetry_changed.connect(lambda t: self._refresh_light_status())
        if hasattr(self.light, "identity_changed"):
            self.light.identity_changed.connect(self._on_light_identity)
        self.light.ports_changed.connect(self._on_ports)
        self.rescan_btn.clicked.connect(self.light.rescan)
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        self.preset_combo.customContextMenuRequested.connect(self._preset_menu)
        self.save_as_btn.clicked.connect(self._on_save_as)
        self.save_btn.clicked.connect(self._on_save)
        self.shoot_btn.clicked.connect(self.shoot)
        self.film_base_combo.currentIndexChanged.connect(self._on_film_base_selected)
        self.film_base_combo.customContextMenuRequested.connect(self._film_base_menu)
        self.measure_btn.clicked.connect(self._on_measure_clicked)
        self.edit_btn.clicked.connect(self._on_edit_film_base)
        self.film_base_use.toggled.connect(
            lambda on: (_settings().setValue("live/film_base_use", bool(on)),
                        self._refresh_film_bases()))
        self._patch_read.connect(self._on_patch_read)
        self.meter_btn.clicked.connect(self._on_meter_clicked)
        self.use_frame_btn.clicked.connect(self._on_use_frame)
        self.frame_auto.toggled.connect(self._on_frame_auto)
        self.canvas.guide_changed.connect(lambda g: self._frame_changed())
        self.canvas.guide_changed.connect(lambda g: self._guide_placed_by_hand())
        self.canvas.guide_changed.connect(lambda g: self._push_params())
        # your guide, kept on the frame (spec/guide-follow-and-crop.md §1-2)
        self._tracker = guide_tracker.GuideTracker()
        self._settle = guide_tracker.SettleWatch()   # moved, then still: snap onto the frame
        self._guide_auto = _settings().value("live/guide_auto", True) not in (
            False, "false", "0", 0)
        self._guide_placed = False       # placed by hand this session
        self._last_base_find = 0.0       # find_frame (for the film base) once a second
        self._remembered_base = None     # (base8, key): film base seen, for the estimate
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(AUTO_GUIDE_MS)
        self._auto_timer.timeout.connect(self._auto_guide_tick)
        self._auto_timer.start()
        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(FRAME_ESTIMATE_MS)
        self._frame_timer.timeout.connect(self._update_frame_estimate)
        self._frame_timer.start()
        self.timing_btn.clicked.connect(self._timing_popup)
        self.scan_combo.activated.connect(self._on_scan_type_chosen)
        self.tether_btn.clicked.connect(self._on_tether_button)
        self.rail.state_changed.connect(self._on_rail_state)
        self.rail.info_changed.connect(lambda _i: self._refresh_rail())
        self.rail.status_changed.connect(lambda _s: self._refresh_rail())
        self.rail.move_done.connect(self._on_rail_done)
        self.rail.rail_error.connect(self._on_rail_error)
        self.rail.ports_changed.connect(self._on_rail_ports)
        self.rail_rescan_btn.clicked.connect(self.rail.rescan)
        self.rail_connect_btn.clicked.connect(self._on_rail_connect)
        self.rail_zero_btn.clicked.connect(self.rail_zero)
        self.rail_home_btn.clicked.connect(self.rail_home)
        self.rail_stop_btn.clicked.connect(self.rail_stop)
        self.rail_af_btn.clicked.connect(self.autofocus)
        self.rail_af_btn.customContextMenuRequested.connect(
            lambda p: self._rail_af_menu(self.rail_af_btn.mapToGlobal(p)))
        self._af_timer = QTimer(self)
        self._af_timer.setInterval(100)
        self._af_timer.timeout.connect(lambda: self._af is not None and self._af.tick())
        self._af_shot_timer = QTimer(self)
        self._af_shot_timer.setInterval(100)
        self._af_shot_timer.timeout.connect(self._af_shot_tick)
        self.rail_af_shoot.toggled.connect(self._on_af_shoot_toggled)
        self.rail_af_zoom.currentIndexChanged.connect(self._on_af_zoom_changed)
        for key, slot in (("[", lambda: self.rail_jog(-1, "fine")),
                          ("]", lambda: self.rail_jog(1, "fine")),
                          ("{", lambda: self.rail_jog(-1, "coarse")),
                          ("}", lambda: self.rail_jog(1, "coarse"))):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(slot)
        for key, slot in (("1", lambda: self.solo("r")), ("2", lambda: self.solo("g")),
                          ("3", lambda: self.solo("b")), ("4", lambda: self.solo("w")),
                          ("S", self.shoot), ("Esc", self._on_escape),
                          ("Z", self._step_cam_zoom),
                          ("F", lambda: self.canvas.zoom("4" if self.canvas.is_fit() else "fit"))):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(slot)
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(IDLE_CHECK_MS)
        self._idle_timer.timeout.connect(self._check_idle)
        self._idle_timer.start()
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._refresh_status_strip)
        self._status_timer.start()
        self._ring_timer = QTimer(self)
        self._ring_timer.setInterval(33)
        self._ring_timer.timeout.connect(self._tick_ring)
        if self._mw is not None and hasattr(self._mw, "tether_captured"):
            self._mw.tether_captured.connect(self._on_tether_captured)
        if self._mw is not None and hasattr(self._mw, "scan_type_changed"):
            self._mw.scan_type_changed.connect(self._apply_scan_type)

    # ------------------------------------------------------------------ #
    # opening and closing
    # ------------------------------------------------------------------ #
    def showEvent(self, e):
        super().showEvent(e)
        if not self._started:
            self._started = True
            QTimer.singleShot(0, self._start)

    def _start(self):
        port = str(_settings().value("live/port", "") or "")
        ident = str(_settings().value("live/port_id", "") or "")
        if ident and hasattr(self.light, "set_auto_ident"):
            self.light.set_auto_ident(ident)      # found wherever it's plugged in now
        if port:
            self.light.set_auto_port(port)
        self.light.rescan()                       # connect now, not on the next 3 s scan
        rport = str(_settings().value("live/rail_port", "") or "")
        if rport and rport != port:
            self.rail.set_auto_port(rport)
            self.rail.rescan()
        if hasattr(self.feed, "isRunning") and not self.feed.isRunning():
            self.feed.start()
        if hasattr(self.usb, "isRunning") and not self.usb.isRunning():
            self.usb.start()
        self._push_params()
        lf.request_camera_permission(QApplication.instance(), self._on_permission)
        if not self._asked_tether:
            self._asked_tether = True
            QTimer.singleShot(0, self.ask_hot_folder)

    def _on_permission(self, granted):
        if self._closing:
            return
        if granted is False:
            self.canvas.set_message(
                "Camera access was refused.\nAllow it in System Settings → Privacy & "
                "Security → Camera (for the app that runs FreeCCR), then Find Cameras.")
            return
        self.find_cameras()

    def find_cameras(self):
        """Capture devices (OpenCV) and USB cameras, both listed in the
        Camera combo; the remembered one is opened when it turns up."""
        key = str(_settings().value("live/camera_key", "") or "")
        pref = _settings().value("live/camera_index", None)
        try:
            pref = int(pref) if pref not in (None, "") else None
        except (TypeError, ValueError):
            pref = None
        self.canvas.set_message("Looking for cameras…")
        self._cv_found, self._usb_found = [], []
        if cb.split_key(key) is not None:
            self._want_key = key
            self.feed.find_cameras(None, self._video_format(), open_found=False)
        else:
            self._want_key = None
            self.feed.find_cameras(pref, self._video_format())
        self.usb.scan()
        hint = cb.install_hints()
        self.find_btn.setToolTip(
            "Look for capture devices (each is opened once to check it delivers a "
            "picture) and cameras on USB." + (f"\n{hint}" if hint else ""))

    def _video_format(self):
        f = str(_settings().value("live/video_format", "auto") or "auto")
        return f if f in lf.FORMATS else "auto"

    def closeEvent(self, e):
        self.shutdown()
        super().closeEvent(e)

    def shutdown(self):
        """Stop a sequence, turn the light off, close the port, stop the feed."""
        if self._closing:
            return
        self._closing = True
        QApplication.instance().removeEventFilter(self)
        for t in (self._idle_timer, self._status_timer, self._ring_timer, self._af_timer,
                  self._frame_timer, self._auto_timer, self._af_shot_timer):
            t.stop()
        self._af_shot = None
        self._af_then = None
        if self._af is not None and self._af.running:
            self._af.on_finished = None
            self._af.stop()
        if self.sequence.running:
            self.sequence.on_finished = None
            self.sequence.stop()
        if self._measure is not None:
            self._measure["cal"].cancel()
            self._measure_finish()
        self._save_view_state()
        st = _settings()
        st.setValue("live/geometry", self.saveGeometry())
        try:
            self.light.shutdown(light_off=True)
        except Exception as ex:
            print(f"live monitor: light shutdown: {ex}")
        try:
            self.rail.shutdown(stop=True)        # the motor stays on: it holds the camera
        except Exception as ex:
            print(f"live monitor: rail shutdown: {ex}")
        try:
            self.feed.stop()
            if hasattr(self.feed, "wait"):
                self.feed.wait(3000)
        except Exception as ex:
            print(f"live monitor: feed stop: {ex}")
        try:
            self.usb.stop()
            if hasattr(self.usb, "wait"):
                self.usb.wait(3000)
        except Exception as ex:
            print(f"live monitor: usb camera stop: {ex}")
        self.closed.emit()

    # ------------------------------------------------------------------ #
    # Hot Folder Tether
    # ------------------------------------------------------------------ #
    def _tether_state(self):
        mw = self._mw
        if mw is None:
            return False, "", 0
        active = bool(getattr(mw, "_tether_active", False))
        label = mw._tether_folder_label() if active and hasattr(mw, "_tether_folder_label") else ""
        return active, label, int(getattr(mw, "_tether_count", 0) or 0)

    def ask_hot_folder(self):
        """On opening: with Hot Folder Tether off, show the one window that
        starts it (spec/tether-start-window.md); with it on, nothing (the status
        strip shows it). Its "Don't ask when the monitor opens" is remembered."""
        if self._closing or self._mw is None:
            return
        if not _settings().value("live/ask_tether", True, type=bool):
            return
        active, _label, _count = self._tether_state()
        if not active:
            opts = self._start_hot_folder(from_monitor=True)
            if opts and opts.get("dont_ask"):
                _settings().setValue("live/ask_tether", False)
        self._refresh_status_strip()

    def _start_hot_folder(self, from_monitor=False):
        """The main window's start window, opened over Capture and light like
        it; its answers (None if it wasn't shown). Capture comes back to the
        front afterwards (the user, 2026-11-01: it was left behind the main
        window). The test seam."""
        try:
            opts = self._mw.enter_tethering(from_monitor=from_monitor, parent=self)
        except TypeError:                         # a main window without `parent`
            opts = self._mw.enter_tethering(from_monitor=from_monitor)
        self._to_front()
        return opts

    def _to_front(self):
        if self._closing:
            return
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(0, lambda: (not self._closing) and (self.raise_(),
                                                               self.activateWindow()))

    def _on_tether_button(self):
        if self._mw is None:
            return
        active, _label, _count = self._tether_state()
        if active:
            self._mw.toggle_tethering()           # Stop
        else:
            self._start_hot_folder()
        self._refresh_status_strip()

    def _on_tether_captured(self, path):
        """§8.3: after a Shoot, the first frame to arrive is checked against
        Hold."""
        if not self._await_exposure:
            return
        self._await_exposure = False
        if self._shutter_is_usb():
            return                       # a USB shot waits for its file: no Hold to check
        exp = cs.exposure_seconds(path)
        if exp and not cs.hold_covers(self._timing, exp):
            shown = f"1/{round(1 / exp)} s" if exp < 1 else f"{exp:g} s"
            self.capture_caption.setStyleSheet(
                f"color: {theme.WARN_TEXT}; font-size: {theme.FS_CAPTION}px;")
            self.capture_caption.setText(
                f"Hold {self._timing['hold_ms']} ms is shorter than the {shown} exposure "
                f"in {os.path.basename(path)}: the next colour lights while the shutter "
                "is open. Raise Hold in Timing.")

    # ------------------------------------------------------------------ #
    # toolbar
    # ------------------------------------------------------------------ #
    def _apply_toggles(self):
        for k, a in self.actions.items():
            a.blockSignals(True)
            a.setChecked(bool(self._toggles.get(k, False)) and k != "negative")
            a.blockSignals(False)
        self.canvas.guide_on = self.actions["guide"].isChecked()
        self.canvas.align_on = self.actions["align"].isChecked()
        self.canvas.sharp_on = self.actions["sharp"].isChecked()
        self._refresh_negative_enabled()
        self._push_params()

    def _on_toggle(self, key, on):
        if key != "negative":
            self._toggles[key] = bool(on)
            _settings().setValue("live/toggles", json.dumps(self._toggles))
        if key == "guide":
            self.canvas.guide_on = on
        elif key == "align":
            self.canvas.align_on = on
        elif key == "sharp":
            self.canvas.sharp_on = on
        elif key == "negative" and on:
            self._rebuild_negative()
        self.canvas.update()
        self._push_params()

    def _on_panel_toggled(self, on):
        self.scroll.setVisible(on)
        _settings().setValue("live/panel_hidden", not on)

    def _place(self, pop, global_pos):
        from utils.popup_placement import place_on_screen
        pop.adjustSize()
        place_on_screen(pop, global_pos)
        pop.show()
        self._popup = pop

    def _denoise_menu(self, pos):
        pop = OptionsPopup("Reduce noise", [
            ("strength", "Strength", lf.DENOISE_LIMITS["strength"],
             lambda v: f"{v} %",
             "How much each new frame counts: lower is quieter but slower to "
             "follow a change (it restarts by itself when the film moves)")],
            self._denoise_opts, lf.DENOISE_DEFAULT, self)
        pop.changed.connect(self._on_denoise_opts)
        self._place(pop, pos)

    def _on_denoise_opts(self, v):
        self._denoise_opts = lf.clean_options(v, lf.DENOISE_DEFAULT, lf.DENOISE_LIMITS)
        _settings().setValue("live/denoise", json.dumps(self._denoise_opts))
        self._push_params()

    def _edge_menu(self, pos):
        pop = OptionsPopup("Edge amplify", [
            ("amount", "Amount", lf.EDGE_LIMITS["amount"], lambda v: f"{v} %",
             "How strongly edges are lifted"),
            ("radius", "Radius", lf.EDGE_LIMITS["radius"], lambda v: f"{v / 10:.1f} px",
             "The size of the detail that is lifted")],
            self._edge_opts, lf.EDGE_DEFAULT, self)
        pop.changed.connect(self._on_edge_opts)
        self._place(pop, pos)

    def _on_edge_opts(self, v):
        self._edge_opts = lf.clean_options(v, lf.EDGE_DEFAULT, lf.EDGE_LIMITS)
        _settings().setValue("live/edge_amp", json.dumps(self._edge_opts))
        self._push_params()

    def _sharp_menu(self, pos):
        m = QMenu(self)
        m.addAction("Draw Area…", lambda: (self.actions["sharp"].setChecked(True),
                                           self.canvas.set_mode("area")))
        m.addAction("Whole View", self._sharp_whole_view)
        m.addAction("Reset Best", self._reset_sharp_best)
        m.popup(pos)
        self._popup = m

    def _negative_menu(self, pos):
        """Right-click Negative Preview: colour negative or black and white
        (the panel's Black & white box, which scan type B&W also sets)."""
        m = QMenu(self)
        for text, bw in (("Colour Negative", False), ("Black && White", True)):
            a = m.addAction(text)
            a.setCheckable(True)
            a.setChecked(self.neg_bw.isChecked() == bw)
            a.triggered.connect(lambda _c=False, v=bw: self.set_negative_kind(v))
        m.popup(pos)
        self._popup = m

    def set_negative_kind(self, bw):
        """Colour or black-and-white negative; picking one turns the preview
        on when it can be."""
        self.neg_bw.setChecked(bool(bw))
        a = self.actions["negative"]
        if a.isEnabled() and not a.isChecked():
            a.setChecked(True)

    def _sharp_whole_view(self):
        self.canvas.sharp_area = None
        self._save_view_state()
        self._push_params()
        self.canvas.update()

    def _reset_sharp_best(self):
        self._reset_best += 1
        self._push_params()

    def _guide_menu(self, pos):
        from core.film_formats import FILM_FORMATS
        m = QMenu(self)
        follow = m.addAction("Follow the Frame")
        follow.setCheckable(True)
        follow.setChecked(self._guide_auto)
        follow.setToolTip("The guide stays where you put it on the frame and moves with the "
                          "frame as focusing changes its size or position. When you wind on "
                          "and the new frame settles, it moves onto that frame, keeping the "
                          "margins you set inside the frame's edges.")
        follow.triggered.connect(lambda on: self.set_guide_auto(bool(on)))
        snap = m.addAction("Snap to the Frame", lambda: self._snap_guide(by_hand=True))
        snap.setEnabled(self._guide_auto)
        snap.setToolTip("Move the guide onto the frame in view now, keeping your margins.")
        m.addSeparator()
        fmt = self.canvas.guide.get("format")
        sub = m.addMenu("Format")
        a = sub.addAction("Free")
        a.setCheckable(True)
        a.setChecked(fmt is None)
        a.triggered.connect(lambda: self._set_guide_format(None))
        for key, label, _ratio, _cols in FILM_FORMATS:
            a = sub.addAction(label)
            a.setCheckable(True)
            a.setChecked(fmt == key)
            a.triggered.connect(lambda _c=False, k=key: self._set_guide_format(k))
        size = m.addMenu("Size")
        size.addAction("Larger", lambda: self._nudge_guide(scale=1.04))
        size.addAction("Smaller", lambda: self._nudge_guide(scale=1 / 1.04))
        move = m.addMenu("Position")
        for name, dx, dy in (("Up", 0, -0.01), ("Down", 0, 0.01), ("Left", -0.01, 0), ("Right", 0.01, 0)):
            move.addAction(name, lambda x=dx, y=dy: self._nudge_guide(dx=x, dy=y))
        rot = m.addMenu("Rotation")
        for name, da in (("+0.5°", 0.5), ("−0.5°", -0.5), ("+5°", 5.0), ("−5°", -5.0)):
            rot.addAction(name, lambda d=da: self._nudge_guide(dangle=d))
        rot.addAction("Straight", lambda: self._nudge_guide(angle=0.0))
        m.addSeparator()
        m.addAction("Fit the View to It", lambda: self.canvas.zoom("guide"))
        m.addAction("Reset Guide", lambda: self._set_guide(dict(GUIDE_DEFAULT)))
        m.popup(pos)
        self._popup = m

    def _sync_format_combo(self):
        """The toolbar's Format list shows the guide's format."""
        combo = getattr(self, "format_combo", None)
        if combo is None:
            return
        i = combo.findData(self.canvas.guide.get("format") or "")
        combo.blockSignals(True)
        combo.setCurrentIndex(max(0, i))
        combo.blockSignals(False)

    def _set_guide(self, g, by_hand=True):
        if not self.actions["guide"].isChecked():
            self.actions["guide"].setChecked(True)
        self.canvas.set_guide(g)
        self._sync_format_combo()
        self._save_view_state()
        self._push_params()                      # the histogram looks where it is now
        if by_hand:
            self._guide_placed_by_hand()

    # --- your guide, kept on the frame (spec/guide-follow-and-crop.md) -------
    def set_guide_auto(self, on):
        self._guide_auto = bool(on)
        _settings().setValue("live/guide_auto", self._guide_auto)
        self._tracker.reset()
        self.canvas.set_guide_waiting(None)
        if on:
            if not self.actions["guide"].isChecked():
                self.actions["guide"].setChecked(True)
            self._anchor_guide()
            self._note_caption("The frame guide stays on the frame as you focus.")

    def _anchor_guide(self):
        """Follow the guide as it is now: the opening seen now is its
        reference (or the next one seen, when none is in view)."""
        frame = None if self.magnified() else self.feed.last_full_frame()
        self._tracker.anchor(dict(self.canvas.guide), guide_tracker.find_opening(frame),
                             frame=frame)        # its margins inside the frame's edges
        self.canvas.set_guide_waiting(None)

    def _snap_guide(self, by_hand=False):
        """The picture moved and settled (or Snap to the Frame): move the guide
        onto the frame, keeping the margins you set inside the frame's edges
        (spec/guide-follow-and-crop.md revision 2). True when it moved."""
        if (not self._guide_auto or not self.actions["guide"].isChecked()
                or self.magnified() or self._sleeping is not None
                or self._measure is not None or self.sequence.running):
            return False
        frame = self.feed.last_full_frame()
        if frame is None:
            return False
        if not self._tracker.rel:
            if by_hand:
                self._note_caption("No edge of the frame showed beside the guide when it "
                                   "was placed, so it can't snap: place it on the frame again.")
            return False
        g = self._tracker.snap(frame, guide_tracker.find_opening(frame))
        if g is None:
            if by_hand:
                self._note_caption("The guide is on the frame (or the frame's edges can't "
                                   "be seen).")
            return False
        self.canvas.set_guide(dict(g, format=self.canvas.guide.get("format")))
        self.canvas.set_guide_waiting(None)
        self._push_params()
        self._note_caption("The frame guide moved onto the new frame.")
        return True

    def _guide_placed_by_hand(self):
        """You placed the guide: following (when on) keeps it there from now
        on, and the shots carry it to their crop."""
        self._guide_placed = True
        if self._guide_auto:
            self._anchor_guide()

    def _remembered_key(self):
        return (self._exposure.get("shutter"), self._exposure.get("iso"),
                tuple(sorted((k, int(v)) for k, v in self._levels.items())),
                str(self._cam_zoom))

    def _auto_guide_tick(self):
        """Keep the guide on the frame: find the carrier's opening in the
        newest full live picture and move the guide with it (4 times a second
        while it follows). The film base seen is remembered once a second."""
        if self._closing:
            return
        if (not self.actions["guide"].isChecked()
                or self.magnified() or self._sleeping is not None
                or self._measure is not None or self.sequence.running):
            self.canvas.set_guide_waiting(None)
            return
        frame = self.feed.last_full_frame()
        if frame is None:
            return
        now = time.monotonic()
        if now - self._last_base_find >= 1.0:
            self._last_base_find = now
            find = live_frame.find_frame(frame)
            if find.base8 is not None:
                self._remembered_base = (find.base8, self._remembered_key())
        if not self._guide_auto:
            self.canvas.set_guide_waiting(None)
            return
        if self._tracker.ref_guide is None:
            self._anchor_guide()
            return
        g = self._tracker.update(guide_tracker.find_opening(frame))
        if g is not None:
            self.canvas.set_guide(dict(g, format=self.canvas.guide.get("format")))
            self._push_params()                  # the histogram follows it too
        self.canvas.set_guide_waiting(self._tracker.reason or None)
        if self._settle.update(frame):
            self._snap_guide()                   # the picture moved and settled

    def _set_guide_format(self, key):
        g = dict(self.canvas.guide)
        g["format"] = key
        r = None
        if key:
            from core.film_formats import ratio
            r = ratio(key)
        geo = self.canvas.geometry_()
        if r and geo is not None:
            fw, fh = geo.full_w, geo.full_h
            wpx, hpx = g["w"] * fw, g["h"] * fh
            landscape = wpx >= hpx
            want = r if landscape else 1 / r
            area = wpx * hpx
            nw = math.sqrt(area * want)
            nh = nw / want
            g["w"], g["h"] = nw / fw, nh / fh
        self._set_guide(g)

    def _nudge_guide(self, scale=None, dx=0.0, dy=0.0, dangle=0.0, angle=None):
        g = dict(self.canvas.guide)
        if scale:
            g["w"] *= scale
            g["h"] *= scale
        g["cx"] += dx
        g["cy"] += dy
        g["angle"] = (g["angle"] + dangle) if angle is None else angle
        self._set_guide(clean_guide(g))

    def _align_menu(self, pos):
        m = QMenu(self)
        m.addAction("Place Line…", lambda: (self.actions["align"].setChecked(True),
                                            self.canvas.set_mode("line")))
        m.popup(pos)
        self._popup = m

    def _on_escape(self):
        if self.canvas.cancel_mode():
            self._pending_measure = None
            return
        if self._af_shot is not None:
            self._af_shot_cancel()
            return
        if self._measure is not None:
            self._measure_stop()
            return
        if self.sequence.running:
            self.sequence.stop()
            return
        if (self._af is not None and self._af.running) or self.rail.moving():
            self.rail_stop()

    # ------------------------------------------------------------------ #
    # the feed
    # ------------------------------------------------------------------ #
    def _push_params(self, *_):
        if self._closing:
            return
        w, h = self.canvas.device_size()
        # the Live histogram looks inside the frame guide, at the whole frame
        # (the window's zoom doesn't move it); it holds while magnified
        # (spec/frame-exposure.md §2)
        guide_on = self.actions["guide"].isChecked()
        hist_paused = guide_on and self.magnified()
        hist_area = dict(self.canvas.guide) if (guide_on and not hist_paused) else None
        caption = ("Live · paused (magnified)" if hist_paused
                   else "Live · frame guide" if hist_area else "Live")
        neg = self._neg_tables if (self.actions["negative"].isChecked()
                                   and self.actions["negative"].isEnabled()) else None
        self.feed.set_params(lf.ViewParams(
            canvas_w=w, canvas_h=h, scale=self.canvas.scale, cx=self.canvas.cx, cy=self.canvas.cy,
            denoise=self.actions["denoise"].isChecked(), denoise_opts=dict(self._denoise_opts),
            edge=self.actions["edge"].isChecked(), edge_opts=dict(self._edge_opts),
            sharp=self.actions["sharp"].isChecked(), sharp_area=self.canvas.sharp_area,
            false_colour=self.actions["fc"].isChecked(), negative=neg,
            bw=self.neg_bw.isChecked(), clip=self._clip_kind, reset_best=self._reset_best,
            hist_area=hist_area, hist_paused=hist_paused,
            frame_area=hist_area))           # a new frame is looked for there too
        self.histogram.set_caption(caption)
        self._sync_view_zoom()

    def _on_frame(self):
        r = self.feed.take()
        if r is None or self._closing:
            return
        self._last_frame_t = time.monotonic()
        self._live_trouble = None                  # a picture: live view is back
        reading = getattr(r, "reading", None)
        if reading is not None and self._af is not None and self._af.running:
            self._af.reading(reading)
        self.canvas.set_result(r)
        if getattr(r, "new_frame", False):
            self._frame_changed()                  # a metered reading was the old frame's
        if getattr(r, "new_frame", False) and time.monotonic() >= self._new_frame_quiet:
            # spec/sharpness-meter-v2.md §5 (not right after a magnification
            # change, which is a new picture too and already reset the best)
            self.canvas.flash_readout_note("New frame: best reset")
            self._note_caption("New frame: the sharpness best was reset.")
        self._last_fps = r.fps
        if r.hist is not None:
            self.histogram.set_data(r.hist)
        if r.activity:
            self._used()
        if (self.actions["negative"].isChecked() and self._neg_tables is None
                and self._neg["base"] is None):
            self._rebuild_negative()

    def _on_cameras(self, found):
        self._cv_found = list(found)
        self._rebuild_camera_combo()

    def _on_usb_cameras(self, found):
        self._usb_found = list(found)
        self._relocate_usb_keys()                # a re-plugged camera has a new port
        self._rebuild_camera_combo()
        self._rebuild_shutter_combo()
        keys = [k for k, _l in self._usb_found]
        shutter = str(_settings().value("live/shutter", "") or "")
        if self._want_key in keys:
            self.feed.open_camera(self._want_key, self._video_format())
        elif shutter in keys and not self.usb.is_open():
            self.usb.open(shutter)               # the remembered USB shutter, no view
        elif self._want_key is not None:
            # the remembered USB camera isn't there: fall back to a device
            self._want_key = None
            if self._cv_found and getattr(self.feed, "index", None) is None:
                self.feed.open_camera(int(self._cv_found[0][0]), self._video_format())
            elif not self._cv_found:
                self._on_feed_status("none", "")

    def _relocate_usb_keys(self):
        """The remembered camera (and shutter) under the port it has now:
        libgphoto2's port changes on every re-plug (`cb.relocate_key`)."""
        s = _settings()
        found = self._usb_found
        old_cam = self._want_key
        new_cam = None
        if old_cam is not None and cb.split_key(old_cam) is not None:
            new_cam = cb.relocate_key(old_cam, found,
                                      str(s.value("live/camera_label", "") or ""))
            if new_cam and new_cam != old_cam:
                print(f"usb camera: {dict(found).get(new_cam, new_cam)} is at {new_cam} "
                      f"now (was {old_cam})")
                self._want_key = new_cam
                s.setValue("live/camera_key", new_cam)
        shutter = str(s.value("live/shutter", "") or "")
        if shutter.startswith("usb:") and cb.split_key(shutter) is not None:
            if shutter == old_cam and new_cam:
                new_sh = new_cam
            else:
                new_sh = cb.relocate_key(shutter, found,
                                         str(s.value("live/shutter_label", "") or ""))
            if new_sh and new_sh != shutter:
                s.setValue("live/shutter", new_sh)

    def _rebuild_camera_combo(self):
        self.camera_combo.clear()
        for idx, w, h in self._cv_found:
            self.camera_combo.addItem(f"Camera {idx}  ·  {w}×{h}", idx)
        for key, label in self._usb_found:
            self.camera_combo.addItem(label, key)
        if self.camera_combo.count() == 0:
            self.camera_combo.addItem("None found", None)
            return
        cur = self._want_key or getattr(self.feed, "index", None)
        i = self.camera_combo.findData(cur)
        if i >= 0:
            self.camera_combo.setCurrentIndex(i)

    def _on_camera_chosen(self, i):
        key = self.camera_combo.itemData(i)
        if key is None:
            return
        if isinstance(key, str):
            label = dict(self._usb_found).get(key, "")
            _settings().setValue("live/camera_key", key)
            _settings().setValue("live/camera_label", label)
            _settings().setValue("live/shutter", key)      # its own shutter too
            _settings().setValue("live/shutter_label", label)
            self._want_key = key
            self.feed.open_camera(key, self._video_format())
            return
        _settings().setValue("live/camera_index", int(key))
        _settings().setValue("live/camera_key", "")
        self._want_key = None
        self.feed.open_camera(int(key), self._video_format())

    def _on_feed_status(self, kind, text):
        if kind == "searching":
            if self.canvas.result is None:
                self.canvas.set_message(text or "Looking for cameras…")
        elif kind == "open":
            if cb.split_key(text) is not None:
                _settings().setValue("live/camera_key", text)
                label = dict(self._usb_found).get(text, "")
                if label:                          # known again after a re-plug
                    _settings().setValue("live/camera_label", label)
            else:
                try:
                    _settings().setValue("live/camera_index", int(text))
                    _settings().setValue("live/camera_key", "")
                except (TypeError, ValueError):
                    pass
            self.canvas.set_message("Waiting for the first frame…")
            self._on_camera_connected()
        elif kind == "none" and self._usb_found:
            self.canvas.set_message(
                "No camera open.\nPick yours in the Camera list below.")
        elif kind == "none":
            self.canvas.set_message(
                "No camera found.\nCheck the capture device is plugged in and the camera "
                "is sending its HDMI picture, then Find Cameras.\nOn a Mac, the app that "
                "runs FreeCCR also needs camera access: System Settings → Privacy & "
                "Security → Camera.")
        elif kind == "lost":
            self.canvas.set_message("The camera stopped sending frames.\nFind Cameras to "
                                    "reconnect.")
        if kind in ("lost", "closed", "none"):
            self._camera_up = self.usb.is_open()
        self._refresh_status_strip()

    def _refresh_status_strip(self):
        if self._closing:
            return
        if not (self.sequence.running if hasattr(self, "sequence") else False):
            self._refresh_roll_note()
        bits = []
        size = getattr(self.feed, "size", None)
        if self.canvas.result is not None:
            g = self.canvas.result.geometry
            bits.append(f"{g.full_w}×{g.full_h}")
        elif size:
            bits.append(f"{size[0]}×{size[1]}")
        if self._last_fps:
            bits.append(f"{self._last_fps:.1f} fps")
        four = getattr(self.feed, "fourcc", "")
        if four:
            bits.append(four)
        if self._exposure and self.usb.is_open():
            bits.append(cb.exposure_text(self._exposure))
        self.camera_info.setText("  ·  ".join(bits))
        active, label, count = self._tether_state()
        folder = self._tether_folder() if active else None
        if folder != self._usb_dest and self._measure is None:
            self._usb_dest = folder
            self.usb.set_dest(folder)
        if self._mw is None:
            self.tether_label.setText("")
            self.tether_btn.setVisible(False)
        elif active:
            self.tether_label.setText(f"Hot folder: “{label}”, {count} captured")
            self.tether_btn.setText("Stop Hot Folder Tether")
        else:
            self.tether_label.setText("Hot folder: off")
            self.tether_btn.setText("Start Hot Folder Tether…")

    def _on_clip_hover(self, kind):
        self._clip_kind = kind or ""
        self._push_params()

    # ------------------------------------------------------------------ #
    # Negative preview
    # ------------------------------------------------------------------ #
    def _refresh_negative_enabled(self):
        a = self.actions["negative"]
        lit = self._levels.get("w", 0) > 0
        a.setEnabled(lit)
        a.setToolTip("A rough positive of the white-lit negative, for judging the frame "
                     "(FreeCCR converts the RAW). Controls: Negative Preview in the panel."
                     if lit else "Light the White LED to preview the positive")
        if not lit and a.isChecked():
            a.setChecked(False)

    def _on_neg_button(self, key):
        if key != "auto" and self._refuse_when_magnified("Picking"):
            return
        if key == "auto":
            self._neg.update(base=None, hi=None, mid=None)
            self._rebuild_negative(force_auto=True)
            return
        self._pick_target = key
        self.canvas.set_mode("pick")

    def _on_box_picked(self, rect):
        """A box dragged in a box pick (the film base): Measure reads the box;
        Negative Preview's base is its centre's colour, as for a click."""
        if self._pick_target != "base":
            cx = 0.5 * (rect[0] + rect[2])
            cy = 0.5 * (rect[1] + rect[3])
            g = self.canvas.geometry_()
            if g is not None:
                self._on_picked(cx * g.full_w, cy * g.full_h)
            return
        self._base_box = tuple(float(v) for v in rect)
        full = self.feed.last_full_frame()
        g = self.canvas.geometry_()
        if full is not None and getattr(full, "ndim", 0) >= 2:
            h, w = full.shape[:2]
        elif g is not None:
            w, h = g.full_w, g.full_h
        else:
            w = h = 1
        cx = 0.5 * (rect[0] + rect[2])
        cy = 0.5 * (rect[1] + rect[3])
        self._on_picked(cx * w, cy * h)

    def _on_picked(self, sx, sy):
        target, self._pick_target = self._pick_target, None
        if target is None:
            return
        bgr = self.feed.sample(sx, sy)
        if bgr is None:
            return
        if target == "base":
            full = self.feed.last_full_frame()
            if full is not None and getattr(full, "ndim", 0) >= 2:
                h, w = full.shape[:2]
                self._neg_base_pos = (sx / max(1, w), sy / max(1, h), w, h)
                self._base_click_used = False         # a new click: Measure may use it
            if self._pending_measure is not None:
                name, index = self._pending_measure
                self._pending_measure = None
                self._fresh_base_click = True
                QTimer.singleShot(0, lambda: self.start_measure(name, index))
        self._neg[target] = bgr
        if target == "base" and self._neg.get("hi") is None:
            self._rebuild_negative(force_auto=True, keep_base=True)
        else:
            self._rebuild_negative()

    def _rebuild_negative(self, *_, force_auto=False, keep_base=False):
        n = self._neg
        if force_auto or n["base"] is None or n["hi"] is None:
            frame = self.feed.last_full_frame()
            if frame is not None:
                guide = self.canvas.guide if self.canvas.guide_on else None
                base, hi, note = ln.auto_points(frame, guide)
                if not keep_base or n["base"] is None:
                    n["base"] = base
                    n["note"] = note
                if n["hi"] is None or force_auto:
                    n["hi"] = hi
        if ln.usable(n["base"], n["hi"]):
            self._neg_tables = ln.tables(
                n["base"], n["hi"], n["mid"],
                temperature=self.neg_sliders["temperature"].value(),
                tint=self.neg_sliders["tint"].value(),
                brightness=self.neg_sliders["brightness"].value())
            b, h = n["base"], n["hi"]
            txt = (f"Film base {b[2]:.0f} / {b[1]:.0f} / {b[0]:.0f}, highlight "
                   f"{h[2]:.0f} / {h[1]:.0f} / {h[0]:.0f} (R / G / B)")
            if n["mid"] is not None:
                txt += ", mid grey set"
            if n.get("note"):
                txt = n["note"] + ". " + txt
            self.neg_note.setText(txt)
        else:
            self._neg_tables = None
            self.neg_note.setText("Film base and highlight: no usable pair yet "
                                  "(sample them, or Auto with the film in view).")
        self._push_params()

    # ------------------------------------------------------------------ #
    # the light
    # ------------------------------------------------------------------ #
    def _on_ports(self, ports):
        cur = self.port_combo.currentText()
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        taken = self.rail.port if (hasattr(self, "rail") and self.rail.is_open()) else ""
        ports = [p for p in ports if p != taken]
        if ports:
            self.port_combo.addItems(ports)
            want = self.light.port or cur or str(_settings().value("live/port", "") or "")
            i = self.port_combo.findText(want)
            if i >= 0:
                self.port_combo.setCurrentIndex(i)
        else:
            self.port_combo.addItem("No ports found")
        self.port_combo.blockSignals(False)
        self._refresh_light_status()

    def _on_connect_clicked(self):
        if self.light.is_open():
            self.light.close_port()
            return
        port = self.port_combo.currentText()
        if port and port != "No ports found":
            self.light.open_port(port)

    def _on_light_identity(self, ident):
        """The answering light's USB identity: how it is found next time,
        wherever it is plugged in (spec/live-capture-monitor.md 2026-11-07)."""
        if ident:
            _settings().setValue("live/port_id", str(ident))

    def _on_light_state(self, state, port, detail):
        if state in ("ok", "waiting") and port:
            _settings().setValue("live/port", port)
            self.light.set_auto_port(port)
            i = self.port_combo.findText(port)
            if i >= 0:
                self.port_combo.setCurrentIndex(i)
        was_open = getattr(self, "_prev_light", "off") in ("ok", "waiting", "silent")
        if (state == "waiting" and not was_open and any(self._levels.values())
                and self._sleeping is None):
            # levels set before connecting go to the light; an untouched
            # panel (all 0) leaves the light as it is, and one off while idle
            # stays off until Wake
            self.light.set_levels(self._levels)
        if (state == "waiting" and not was_open and not any(self._levels.values())
                and (self._camera_up or self.light_on_connect)):
            # the light comes on as it connects (the user, 2026-11-09), or
            # because the camera came first (2026-11-01)
            self._light_on_for_camera(
                "The light came on (white, 100 %) as it connected."
                if not self._camera_up else None)
        self._prev_light = state
        self._refresh_light_status()
        self.rail.set_blocked([port] if state in ("ok", "waiting", "silent") and port else [])
        self._on_rail_ports(self.rail.ports)

    # --- the light comes on with the camera (the user, 2026-11-01) --------
    def _on_camera_connected(self):
        self._camera_up = True
        self._light_on_for_camera()

    def _light_on_for_camera(self, note=None):
        """White at 100 % when the light or a camera connects, if the light is
        there and dark: levels set by hand stay, idle sleep (its timer runs
        as before) and a running shot or measuring are left alone, and
        Settings can turn it off (`live/light_on_camera`)."""
        if not _settings().value("live/light_on_camera", True, type=bool):
            return
        if (self._closing or self._sleeping is not None or self.sequence.running
                or self._measure is not None or not self.light.is_open()
                or any(self._levels.values())):
            return
        self.set_levels(cl.exclusive(cl.OFF, "w", 255))
        self._note_caption(note or "The light came on (white, 100 %) with the camera.")

    def _refresh_light_status(self):
        state, detail = self.light.state, self.light.detail
        self.light_dot.setStyleSheet(f"color: {STATE_COLOURS.get(state, theme.BORDER_STRONG)}; "
                                     "font-size: 13px;")
        is_open = self.light.is_open()
        self.connect_btn.setText("Disconnect" if is_open else "Connect")
        avail = state != "unavailable"
        for w in (self.connect_btn, self.rescan_btn, self.port_combo):
            w.setEnabled(avail)
        if state == "ok":
            t = self.light.telemetry
            # An Open Light Protocol light names itself (spec/open-light-protocol.md)
            bits = [t["name"] if t.get("name") else "Responding"]
            if "vbus" in t:
                bits.append(f"{t['vbus']:.1f} V")
            if "temp" in t:
                bits.append(f"{t['temp']:.0f} °C")
            text = "  ·  ".join(bits)
        elif state == "off":
            text = "Not connected"
        else:
            text = detail or state
        if self._sleeping is not None and (getattr(self, "_sleep_state", None) or
                                           {"lit": True}).get("lit"):
            text += "  ·  off while idle"
        self.light_status.setText(text)
        self._refresh_shoot_enabled()

    def _set_sliders(self, levels):
        for k, s in self.led_sliders.items():
            s.blockSignals(True)
            s.setValue(int(levels.get(k, 0)))
            s.blockSignals(False)
            self.led_values[k].setText(f"{s.value() / 255 * 100:.1f} %")

    def set_levels(self, levels, send=True):
        self._levels = cl.clean_levels(levels)
        self._set_sliders(self._levels)
        self._refresh_negative_enabled()
        if send:
            self._wake()
            self.light.set_levels(self._levels)

    def _on_led_slider(self, ch, value):
        self.set_levels(cl.exclusive(self._levels, ch, value))

    def solo(self, ch):
        if self.sequence.running or self._sleeping is not None:
            return                       # off while idle: only Wake lights it
        self._used()
        self.set_levels(cl.solo(self._preset(), ch))

    # ------------------------------------------------------------------ #
    # presets
    # ------------------------------------------------------------------ #
    def _preset(self):
        d = self._presets
        return d["presets"][d["selected"]]

    def _save_presets(self):
        self._presets = lp.normalise(self._presets)
        _settings().setValue("live/presets", lp.dumps(self._presets))

    def _refresh_presets(self):
        d = self._presets = lp.normalise(self._presets)
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItems([p["name"] for p in d["presets"]])
        self.preset_combo.setCurrentIndex(d["selected"])
        self.preset_combo.blockSignals(False)
        pre = self._preset()
        for k, ring in self.rings.items():
            ring.set_value(pre.get(k, 0))

    def _on_preset_selected(self, i):
        if i < 0:
            return
        self._presets["selected"] = i
        self._save_presets()
        self._refresh_presets()

    def _on_save_as(self):
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:")
        if not ok or not name.strip():
            return
        self._presets = lp.add(self._presets, name.strip(), self._levels)
        self._save_presets()
        self._refresh_presets()

    def _on_save(self):
        name = self._preset()["name"]
        if QMessageBox.question(self, "Save Preset",
                                f"Overwrite “{name}” with the current LED levels?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) != QMessageBox.Yes:
            return
        self._presets = lp.overwrite(self._presets, self._presets["selected"], self._levels)
        self._save_presets()
        self._refresh_presets()

    def _preset_menu(self, pos):
        m = QMenu(self)
        m.addAction("Rename…", self._on_rename)
        d = m.addAction("Delete…", self._on_delete)
        d.setEnabled(len(self._presets["presets"]) > 1)
        m.addSeparator()
        m.addAction("Import…", self._on_import)
        m.popup(self.preset_combo.mapToGlobal(pos))
        self._popup = m

    def _on_rename(self):
        cur = self._preset()["name"]
        name, ok = QInputDialog.getText(self, "Rename Preset", "Preset name:", text=cur)
        if ok and name.strip():
            self._presets = lp.rename(self._presets, self._presets["selected"], name.strip())
            self._save_presets()
            self._refresh_presets()

    def _on_delete(self):
        name = self._preset()["name"]
        if QMessageBox.question(self, "Delete Preset", f"Delete “{name}”?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._presets = lp.delete(self._presets, self._presets["selected"])
        self._save_presets()
        self._refresh_presets()

    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Presets", "",
                                              "Presets (*.json);;All files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                other = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "Import Presets", f"Could not read the file:\n\n{e}")
            return
        self._presets, n = lp.import_from(self._presets, other)
        self._save_presets()
        self._refresh_presets()
        QMessageBox.information(self, "Import Presets",
                                f"Added {n} preset{'s' if n != 1 else ''}." if n
                                else "No new presets (every name was already here).")

    # ------------------------------------------------------------------ #
    # capture
    # ------------------------------------------------------------------ #
    SHOOT_UNZOOM_S = 8.0          # how long Shoot waits for the camera to be Whole again

    def shoot(self):
        if self.sequence.running:
            self.sequence.stop()
            return
        if self._af_shot is not None:
            self._af_shot_cancel()                  # a second press while it focuses
            return
        if self._shoot_when_whole is not None:
            self._shoot_when_whole = None           # a second press while waiting: cancel
            self._note_caption("Shoot cancelled.")
            return
        if self._measure is not None:
            return
        if self._sleeping is not None:
            return                       # the light is off while idle: Wake first
        if self._rail_busy():
            self._warn_caption("Not while the focus rail is moving: the shot would blur.")
            return
        if self.rail_af_shoot.isChecked():
            self._af_shot_start()                   # focus first, then the shot
            return
        if self.magnified() and self.usb.is_open():
            # A magnified camera got stuck shooting (the user, 2026-11-01):
            # back to Whole first, and shoot once the camera says it is there
            # (`_on_cam_zoom`), or give up after SHOOT_UNZOOM_S.
            token = object()
            self._shoot_when_whole = token
            self._note_caption("Back to the whole frame before the shot…")
            self._set_cam_zoom("1")
            QTimer.singleShot(int(self.SHOOT_UNZOOM_S * 1000),
                              lambda t=token: self._shoot_unzoom_timeout(t))
            return
        prepared = self._prepare_shot()
        if prepared is not None:
            self._start_shot(prepared)

    def _prepare_shot(self):
        """What the shot needs, taken now, at the whole frame: the scan type,
        the USB checks, the exposures and the frame guide's record
        (spec/focus-rail.md revision 2 C.2). None when it can't shoot (the
        caption says why)."""
        tri = self.scan_type() == "trichrome"
        self._used()
        usb = self._shutter_is_usb()
        exposures = rearm = None
        if usb:
            if tri and not self.light.is_open():
                self._warn_caption("Trichrome needs the Scanlight connected: it sets "
                                   "the red, green and blue.")
                return None
            if not self._ensure_hot_folder():
                self._warn_caption("Shooting over USB brings the files in through Hot "
                                   "Folder Tether: turn it on to shoot.")
                return None
            rearm = self._frame_exposures       # put back if the shot never happens
            exposures = self._shoot_exposures(tri)
        elif not self.light.is_open():
            return None
        return {"tri": tri, "usb": usb, "exposures": exposures, "rearm": rearm,
                "guide": self._shot_guide_record(),
                "note": getattr(self, "_frame_shot_note", "")}

    def _start_shot(self, p):
        """Start the sequence with what `_prepare_shot` took. True when it
        started."""
        tri, exposures = p["tri"], p["exposures"]
        if p["usb"]:
            if exposures:
                self._shoot_exp_before = {f: self._exposure.get(f) for f in ("shutter", "iso")
                                          if self._exposure.get(f)}
                self._hold_live(True)
            capture_guide.begin_shot(p["guide"])
            started = self.sequence.start(self._timing, self._preset(), tri,
                                          shutter=self._usb_shutter, exposures=exposures)
            if not started and exposures:
                self._hold_live(False)
                self._shoot_exp_before = None
        else:
            if not self.light.is_open():
                return False
            capture_guide.begin_shot(p["guide"])
            started = self.sequence.start(self._timing, self._preset(), tri)
        if not started:
            capture_guide.begin_shot(None)
            self._log_shot = None
        if started:
            self._begin_log_shot(p)
            self._await_exposure = True
            self.shoot_btn.setText("Stop   (Esc)")
            self.measure_btn.setEnabled(False)
            self.edit_btn.setEnabled(False)
            self._refresh_capture_caption()
            note = p["note"] or self._film_base_shoot_note(tri)
            if note:
                self._note_caption(note)
        return started

    # --- the capture log and file names (spec/capture-log-and-names.md) -----
    def capture_session(self):
        """(folder, {roll, format, film}) of the hot folder Capture shoots
        into, or (None, None) with no hot folder."""
        mw = self._mw
        if mw is None or not getattr(mw, "_tether_active", False):
            return None, None
        folder = getattr(mw, "_tether_folder", None)
        sess = getattr(mw, "capture_session", None)
        if not folder or not isinstance(sess, dict):
            return None, None
        return folder, sess

    def _begin_log_shot(self, p):
        folder, sess = self.capture_session()
        self._log_shot = None
        if folder is None:
            capture_log.clear()
            return
        roll = capture_log.clean_roll(sess.get("roll")) or capture_log.DEFAULT_ROLL
        shot = capture_log.next_shot(folder, roll)
        scan = "trichrome" if p["tri"] else self.scan_type()
        exps = p.get("exposures")
        entry = {"roll": roll, "shot": shot, "scan_type": scan,
                 "format": sess.get("format") or "", "film": sess.get("film") or "",
                 "route": "usb" if p["usb"] else "cable",
                 "exposures": ({k: {f: v.get(f) for f in ("shutter", "iso") if v.get(f)}
                                for k, v in exps.items() if isinstance(v, dict)}
                               if exps else None)}
        if p.get("guide") is not None:
            # the guide goes with the shot for good (spec/guide-in-log.md)
            g = capture_guide.save_for_log(folder, roll, shot, p["guide"])
            if g is not None:
                entry["guide"] = g
        capture_log.begin_shot(folder, entry)
        self._log_shot = entry
        self._refresh_roll_note(shot + 1)

    def _shot_file_stem(self, channel):
        """The name FreeCCR gives the file of `channel` in the shot running."""
        e = self._log_shot
        if not e:
            return None
        slot = {"r": "red", "g": "green", "b": "blue"}.get(channel) \
            or capture_log.single_slot(e["scan_type"])
        return capture_log.shot_stem(e["roll"], e["format"], e["film"], e["shot"], slot)

    def _refresh_roll_note(self, next_shot=None):
        folder, sess = self.capture_session()
        if folder is None:
            self.roll_note.setText("")
            return
        roll = capture_log.clean_roll(sess.get("roll")) or capture_log.DEFAULT_ROLL
        if next_shot is None:
            next_shot = capture_log.next_shot(folder, roll)
        bits = [f"Roll {roll}"]
        if sess.get("format"):
            from core import film_formats
            bits.append(film_formats.label(sess["format"]) or sess["format"])
        if sess.get("film"):
            bits.append(str(sess["film"]))
        bits.append(f"next shot {int(next_shot):03d}")
        self.roll_note.setText(" · ".join(bits))

    # --- autofocus before each shot (spec/focus-rail.md revision 2 C) -------
    AF_SHOT_SETTLE_S = 1.0        # a picture this long after the camera says it zoomed
    AF_SHOT_ZOOM_S = 10.0         # how long to wait for the camera to magnify

    def _on_af_shoot_toggled(self, on):
        _settings().setValue("live/rail_af_shoot", bool(on))
        self._refresh_rail()

    def _on_af_zoom_changed(self, _i):
        _settings().setValue("live/rail_af_zoom", self.rail_af_zoom.currentData() or "")

    def _af_shot_start(self):
        if not self.rail.is_ready():
            self._warn_caption("Autofocus before each shot is ticked, but the focus rail "
                               "isn't connected: connect it, or untick the box to shoot "
                               "without it.")
            return
        if time.monotonic() - self._last_frame_t > 2.0:
            self._warn_caption("Autofocus before the shot needs the live view: no picture "
                               "is coming in.")
            return
        prepared = self._prepare_shot()          # at the whole frame, before zooming
        if prepared is None:
            return
        want = str(self.rail_af_zoom.currentData() or "")
        if not (want and self._cam_zoom_action.isVisible()
                and self.cam_zoom_combo.findData(want) >= 0):
            want = ""                            # no magnifier, or "no zoom"
        st = self._af_shot = {"stage": "zoom", "want": want, "back": self._cam_zoom or "1",
                              "zoomed": False, "zoom_at": None,
                              "started": time.monotonic(), "prepared": prepared}
        self._refresh_shoot_enabled()
        if want and self._cam_zoom != want:
            st["zoomed"] = True
            self._note_caption(f"Autofocus before the shot: Camera zoom "
                               f"{self.cam_zoom_label(want)}…  (Shoot or Esc stops it)")
            self._set_cam_zoom(want)
            self._af_shot_timer.start()
        else:
            self._af_shot_focus()

    def _af_shot_tick(self):
        st = self._af_shot
        if st is None or st["stage"] != "zoom":
            self._af_shot_timer.stop()
            return
        if (st["zoom_at"] is not None
                and self._last_frame_t >= st["zoom_at"] + self.AF_SHOT_SETTLE_S):
            self._af_shot_timer.stop()
            self._af_shot_focus()                # the magnified picture is coming in
        elif time.monotonic() - st["started"] > self.AF_SHOT_ZOOM_S:
            self._af_shot_timer.stop()
            self._af_shot_fail("The camera didn't magnify")

    def _af_shot_focus(self):
        st = self._af_shot
        if st is None:
            return
        st["stage"] = "focus"
        self._note_caption("Focusing before the shot…  (Shoot or Esc stops it)")
        if not self.autofocus(then=self._af_shot_focused):
            why = (self.rail_caption.text() or "Autofocus couldn't start").rstrip(".")
            self._af_shot_fail(why)

    def _af_shot_focused(self, result):
        st = self._af_shot
        if st is None:
            return
        if not result.ok:
            self._af_shot_fail(result.message.rstrip("."))
            return
        if self.magnified():
            st["stage"] = "whole"
            self._note_caption(result.message.rstrip(".") + ": back to the whole frame "
                               "for the shot…")
            self._set_cam_zoom("1")
            QTimer.singleShot(int(self.SHOOT_UNZOOM_S * 1000),
                              lambda s=st: self._af_shot_unzoom_timeout(s))
        else:
            self._af_shot_shoot()

    def _af_shot_shoot(self):
        st, self._af_shot = self._af_shot, None
        if st is None:
            return
        self._refresh_shoot_enabled()
        if not self._start_shot(st["prepared"]):
            self._af_shot_rearm(st)
            if not self.sequence.running:
                self._warn_caption("Focused, but the shot couldn't start.")

    def _af_shot_unzoom_timeout(self, st):
        if self._af_shot is st and st["stage"] == "whole":
            self._af_shot = None
            self._af_shot_rearm(st)
            self._refresh_shoot_enabled()
            self._warn_caption("Focused, but the camera didn't go back to the whole frame, "
                               "so nothing was shot. Set Camera zoom to Whole, then Shoot "
                               "again.")

    def _af_shot_rearm(self, st):
        arm = st["prepared"].get("rearm")
        if arm and self._frame_exposures is None:
            self._frame_exposures = arm          # Use for This Frame still stands
            self._refresh_frame_line()

    def _af_shot_fail(self, why):
        """No shot: the sweep has gone back to where it started (or was
        stopped); the camera goes back to the zoom it had."""
        st, self._af_shot = self._af_shot, None
        if st is None:
            return
        self._af_shot_timer.stop()
        self._af_shot_rearm(st)
        if st["zoomed"]:
            if self._cam_zoom != st["back"]:
                self._set_cam_zoom(st["back"])
            else:
                self.usb.set_zoom(st["back"])    # its zoom request may still be on its way
        self._refresh_shoot_enabled()
        self._warn_caption(f"{why}. Nothing was shot.")
        self._rail_caption(f"{why}. Nothing was shot.")   # stays when the other is redrawn

    def _af_shot_cancel(self):
        if self._af_shot is None:
            return
        self._af_then = None                     # its result is no longer wanted
        if self._af is not None and self._af.running:
            self._af.stop("Autofocus stopped.")
            self.rail.stop()
        elif self.rail.moving():
            self.rail.stop()
        self._af_shot_fail("Shoot cancelled")

    def _shot_guide_record(self):
        """The guide this shot's files carry to their crop
        (spec/guide-follow-and-crop.md §3), or None: the guide must be shown
        and placed (by hand this session, or followed from an opening)."""
        if not self.actions["guide"].isChecked() or self.magnified():
            return None
        following = self._guide_auto and self._tracker.anchored
        if not (self._guide_placed or following):
            return None
        placed = self._tracker.ref_guide if following else self.canvas.guide
        if all(abs(float(placed.get(k, 0.0) or 0.0) - float(GUIDE_DEFAULT[k])) < 1e-6
               for k in ("cx", "cy", "w", "h", "angle")):
            return None                  # the untouched default box isn't a frame
        frame = self.feed.last_full_frame()
        if frame is None:
            return None
        return capture_guide.make_record(
            self.canvas.guide, frame,
            following=self._guide_auto and self._tracker.state == "following")

    def _shoot_unzoom_timeout(self, token):
        if self._shoot_when_whole is token:
            self._shoot_when_whole = None
            self._warn_caption("The camera didn't go back to the whole frame, so nothing "
                               "was shot. Set Camera zoom to Whole, then Shoot again.")

    def _on_step(self, channel, duration_ms):
        self._ring_anim = (channel, time.monotonic(), max(1, duration_ms))
        for ring in self.rings.values():
            ring.set_progress(None)
        if channel in self.rings:
            self._ring_timer.start()

    def _tick_ring(self):
        if not self._ring_anim:
            self._ring_timer.stop()
            return
        ch, t0, dur = self._ring_anim
        p = min(1.0, (time.monotonic() - t0) * 1000.0 / dur)
        if ch in self.rings:
            self.rings[ch].set_progress(p)

    def _on_sequence_finished(self, reason):
        capture_guide.end_shot()                 # late files still get it, for a while
        capture_log.end_shot(reason)             # and join its log entry
        if self._shoot_exp_before is not None:   # a film-base Shoot: put the camera back
            before, self._shoot_exp_before = self._shoot_exp_before, None
            self._hold_live(False)
            if before:
                self._send_exposure(before)
        if self._exp_held:                       # picked while it ran
            held, self._exp_held = self._exp_held, {}
            self._send_exposure(held)
        self._ring_anim = None
        self._ring_timer.stop()
        for ring in self.rings.values():
            ring.set_progress(None)
        self.shoot_btn.setText("Shoot   (S)")
        self.measure_btn.setEnabled(True)
        self._refresh_edit_enabled()
        self._refresh_light_status()
        if reason == "disconnected":
            self._warn_caption("Stopped: the Scanlight disconnected.")
        elif reason == "timeout":
            self._warn_caption(f"Stopped: no file from the camera after "
                               f"{int(cs.FILE_TIMEOUT_S)} s.")
        elif reason == "failed":
            self._warn_caption("Stopped: the camera didn't fire.")
        elif reason == "camera lost":
            self._warn_caption("Stopped: the camera disconnected.")
        # "no file": _on_usb_skipped says why
        self._refresh_shoot_enabled()

    # ------------------------------------------------------------------ #
    # film bases (spec/film-base-exposure.md)
    # ------------------------------------------------------------------ #
    def _hold_live(self, on):
        hold = getattr(self.usb, "pause_live", None)
        if hold is not None:
            hold(on)

    def _save_film_bases(self):
        self._bases = be.normalise(self._bases)
        _settings().setValue("live/film_bases", be.dumps(self._bases))

    def _film_base(self):
        return be.selected(self._bases)

    def _refresh_film_bases(self):
        d = self._bases = be.normalise(self._bases)
        self.film_base_combo.blockSignals(True)
        self.film_base_combo.clear()
        if d["bases"]:
            self.film_base_combo.addItems([b["name"] for b in d["bases"]])
            self.film_base_combo.setCurrentIndex(d["selected"])
        else:
            self.film_base_combo.addItem("No film base yet")
        self.film_base_combo.setEnabled(bool(d["bases"]) and self._measure is None)
        self.film_base_combo.blockSignals(False)
        self._refresh_edit_enabled()
        tri = self.scan_type() == "trichrome"
        base = self._film_base()
        self.film_base_note.setText(be.summary(base, tri))
        self.film_base_use.setEnabled(be.exposures_for(base, tri) is not None)

    def _refresh_edit_enabled(self):
        seq = getattr(self, "sequence", None)
        self.edit_btn.setEnabled(bool(self._bases["bases"]) and self._measure is None
                                 and not (seq is not None and seq.running))

    def _on_film_base_selected(self, i):
        if i < 0 or not self._bases["bases"]:
            return
        self._bases["selected"] = i
        self._save_film_bases()
        self._refresh_film_bases()

    def _film_base_menu(self, pos):
        if self._measure is not None:
            return
        m = QMenu(self)
        has = bool(self._bases["bases"])
        a = m.addAction("Measure Again", lambda: self.start_measure(
            self._film_base()["name"], self._bases["selected"]))
        a.setEnabled(has)
        m.addAction("Edit…", self._on_edit_film_base).setEnabled(
            has and not self.sequence.running)
        m.addAction("Rename…", self._on_rename_film_base).setEnabled(has)
        m.addAction("Delete…", self._on_delete_film_base).setEnabled(has)
        m.popup(self.film_base_combo.mapToGlobal(pos))
        self._popup = m

    def _ask_film_base_name(self, default):
        """The test seam: a name, or None for cancel."""
        name, ok = QInputDialog.getText(self, "Measure Film Base", "Name this film base "
                                        "(stock, batch):", text=default)
        return name.strip() if ok and name.strip() else None

    def _edit_film_base_dialog(self, base):
        """The test seam: (action, name, values) with action "save", "new",
        "delete" or None (cancelled)."""
        from widgets.film_base_dialog import FilmBaseEditDialog
        dlg = FilmBaseEditDialog(base, self._exp_choices.get("shutter") or [],
                                 self._exp_choices.get("iso") or [], self)
        self.light_dialog(dlg)
        if dlg.exec() != QDialog.Accepted or dlg.action is None:
            return None, "", {}
        name, values = dlg.values()
        return dlg.action, name, values

    def _on_edit_film_base(self):
        """spec/film-base-exposure.md revision 4: set a film base by hand."""
        base = self._film_base()
        if base is None or self._measure is not None or self.sequence.running:
            return
        action, name, values = self._edit_film_base_dialog(base)
        if action == "delete":
            self._on_delete_film_base()          # asks first
            return
        if action not in ("save", "new"):
            return
        colours = be.edited_colours(base["colours"], values)
        if action == "save":
            self._bases = be.edit(self._bases, self._bases["selected"], name, colours)
        else:
            self._bases = be.add(self._bases, be.entry(
                name or base["name"], colours, base.get("camera", ""), base.get("notes", ())))
        self._save_film_bases()
        self._refresh_film_bases()
        new = self._film_base()
        if new is not None:
            self._note_caption(f"Film base “{new['name']}” saved: "
                               f"{be.summary(new, self.scan_type() == 'trichrome')}")

    def _on_rename_film_base(self):
        base = self._film_base()
        if base is None:
            return
        name, ok = QInputDialog.getText(self, "Rename Film Base", "Name:", text=base["name"])
        if ok and name.strip():
            self._bases = be.rename(self._bases, self._bases["selected"], name.strip())
            self._save_film_bases()
            self._refresh_film_bases()

    def _confirm_delete_film_base(self, base):
        """The test seam: Yes / No, No the default."""
        return QMessageBox.question(
            self, "Delete Film Base",
            f"Delete “{base['name']}” and its measurements?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def _on_delete_film_base(self):
        base = self._film_base()
        if base is None:
            return
        if not self._confirm_delete_film_base(base):
            return
        self._bases = be.delete(self._bases, self._bases["selected"])
        self._save_film_bases()
        self._refresh_film_bases()

    def _shoot_exposures(self, tri):
        """The film base's exposures for a USB Shoot, or None. Use for This
        Frame's exposures go first, once (spec/frame-exposure.md §6); then,
        with "Per shot, scale exposure to the frame's density" ticked, the frame's own suggestion
        (revision 3), which also sets the note Shoot shows (`_frame_shot_note`)."""
        self._frame_shot_note = ""
        armed, self._frame_exposures = self._frame_exposures, None
        if armed:
            self._refresh_frame_line()
            return armed
        if self.frame_auto.isChecked():
            self._refresh_frame_line()              # the suggestion as it is now
            sug = self._frame_suggestion
            base = be.exposures_for(self._film_base(), tri)
            if sug is not None and sug.changes:
                self._frame_shot_note = ("Shooting at this frame's own exposures: "
                                         + sug.text().replace("This frame", "").strip(" :,")
                                         + ".")
                return {k: dict(v) for k, v in sug.exposures.items()}
            if base is not None:
                if sug is None:
                    why = (getattr(self, "_frame_reason", "") or "none yet").rstrip(".")
                    self._frame_shot_note = ("No estimate for this frame (" + why + "): "
                                             "shot at the film base's exposures.")
                return base
        if not self.film_base_use.isChecked():
            return None
        return be.exposures_for(self._film_base(), tri)

    def _on_frame_auto(self, on):
        _settings().setValue("live/frame_auto", bool(on))
        if on:
            self._frame_exposures = None            # the box does what Use did
        self._refresh_frame_line()

    def _film_base_shoot_note(self, tri):
        base = self._film_base()
        if base is None or not self.film_base_use.isChecked():
            return ""
        if self.sequence.exposures:
            return f"Shooting at “{base['name']}”: {be.summary(base, tri)}"
        if not self._shutter_is_usb():
            return ("The film base's exposures need the USB camera as the Shutter: "
                    "shooting at the camera's own settings.")
        if be.exposures_for(base, tri) is None:
            return (f"“{base['name']}” wasn't measured for this scan type: shooting at the "
                    "Exposure row's settings.")
        return ""

    # --- this frame's own exposure (spec/frame-exposure.md) ------------------
    def _frame_changed(self):
        """The frame in the guide changed (a new frame, the guide moved): a
        metered reading and an unused Use for This Frame were the old one's."""
        self._frame_est = []
        if self._frame_metered is not None or self._frame_exposures is not None:
            self._frame_metered = None
            self._frame_exposures = None
            self._refresh_frame_line()

    def _frame_context(self):
        """(film base, trichrome, shutters, isos) or a reason string."""
        tri = self.scan_type() == "trichrome"
        base = self._film_base()
        if be.exposures_for(base, tri) is None:
            kind = "trichrome" if tri else "one exposure"
            if not base:
                return (f"This frame: needs a film base measured for {kind} first "
                        "(Film base → Measure…).")
            return (f"This frame: “{base['name']}” was measured for "
                    f"{'one exposure' if tri else 'trichrome'}; Measure Again for {kind} "
                    "(right-click the film base).")
        shutters = self._exp_choices.get("shutter") or []
        if not shutters:
            return "This frame: needs a USB camera whose shutter speed FreeCCR can set."
        return base, tri, shutters, self._exp_choices.get("iso") or []

    def _update_frame_estimate(self):
        """Every second: the live estimate of this frame's exposure."""
        if self._closing or not self.film_base_box.isVisible():
            return
        if self._measure is not None or self.sequence.running:
            return
        if self._frame_metered is None:
            if not self.actions["guide"].isChecked():
                self._frame_est = []
            elif self.magnified():
                pass                                  # paused: the picture is a part
            else:
                remembered = None
                if (self._remembered_base is not None
                        and self._remembered_base[1] == self._remembered_key()):
                    remembered = self._remembered_base[0]
                est = fm.live_estimate(self.feed.last_full_frame(), dict(self.canvas.guide),
                                       self._neg_base_pos, remembered=remembered)
                self._frame_live_reason = est.reason
                self._frame_live_source = est.source
                if est.ok:
                    self._frame_est = (self._frame_est + [est.ratio])[-FRAME_ESTIMATES:]
                else:
                    self._frame_est = []
        self._refresh_frame_line()

    def _refresh_frame_line(self):
        ctx = self._frame_context()
        sug = None
        if isinstance(ctx, str):
            text = ctx
        elif self._frame_metered is not None:
            sug = self._frame_metered
            text = sug.text()
        elif not self.actions["guide"].isChecked():
            text = "This frame: turn the frame guide on and place it on the frame."
        elif self.magnified():
            text = "This frame: paused while magnified."
        elif self._frame_est:
            import numpy as np
            base, tri, shutters, isos = ctx
            ratio = float(np.median(self._frame_est))
            sug = fm.suggest(base, tri, fm.live_levels(base, tri, ratio), shutters, isos,
                             live=True)
            text = sug.text() if sug else "This frame: no estimate."
        else:
            text = "This frame: " + (getattr(self, "_frame_live_reason", "") or
                                     "waiting for the live view…")
        if sug is not None and sug.notes:
            text += " (" + "; ".join(sug.notes) + ")"
        if self._frame_exposures is not None:
            text += "  ·  the next Shoot uses it"
        self._frame_suggestion = sug
        # The line shows only an estimate; why there is none goes in the Use
        # button's tooltip and the Shoot note (the user, 2026-11-01: "The
        # warning ... is wrong. Don't think there needs this warning anyway"
        # — a 35 mm roll read as "the film base shows on only one side")
        self._frame_reason = "" if sug is not None else text.replace("This frame: ", "")
        shown = text if sug is not None else ""
        self.frame_note.setText(shown)
        self.frame_note.setVisible(bool(shown))
        if sug is not None and sug.live:
            self.frame_note.setToolTip(
                "Estimated from the live view, no photos: the frame's brightest part "
                "inside the guide against the film base " + {
                    "click": "you clicked (Negative Preview → Film Base).",
                    "remembered": "seen earlier between frames, under the same light and "
                                  "exposure. A Film Base click overrides it.",
                }.get(getattr(self, "_frame_live_source", ""),
                      "found beside the guide. A Film Base click overrides it."))
        else:
            self.frame_note.setToolTip("")
        busy = self._measure is not None or self.sequence.running
        usable = bool(sug is not None and sug.changes and not busy)
        self.use_frame_btn.setEnabled(usable)
        self.use_frame_btn.setVisible(not self.frame_auto.isChecked())   # it does Use's job
        self.use_frame_btn.setText("Using for This Frame" if self._frame_exposures
                                   else "Use for This Frame")
        self.use_frame_btn.setToolTip(
            self._USE_TIP if usable else
            "Nothing to use yet: " + (
                "this frame is within a sixth of a stop of the film base's exposures."
                if sug is not None else text.replace("This frame: ", "")))
        # Meter (RAW test shots) shows only with its setting (revision 2:
        # fewer buttons); a metering already running keeps it until it ends.
        # It stays clickable (it says what's missing when pressed); it is off
        # only while a shot, measuring or another metering is running
        metering = self._measure is not None and self._measure.get("kind") == "frame"
        self.meter_btn.setVisible(metering or self._meter_shots_on())
        self.meter_btn.setEnabled(metering or not busy)
        why = self._meter_blocker(ctx)
        self.meter_btn.setToolTip(self._METER_TIP + (f"\n\nFirst: {why}" if why else ""))

    _METER_TIP = ("One RAW test shot per colour at the film base's exposures, read "
                  "inside the frame guide: this frame's exposure exactly. The shots "
                  "aren't kept.")
    _USE_TIP = ("The next Shoot uses this frame's exposures (longer for a dense frame), "
                "then it goes back to the film base's. Only the shutter speed and ISO "
                "change, so the file records it and conversion follows.")

    @staticmethod
    def _meter_shots_on():
        """Settings → Capture → Show Meter (`live/frame_meter`, off)."""
        return _settings().value("live/frame_meter", False) in (True, "true", "1", 1)

    def _meter_blocker(self, ctx=None):
        """What Meter still needs, in words, or "" when it can go."""
        ctx = self._frame_context() if ctx is None else ctx
        if isinstance(ctx, str):
            return ctx.replace("This frame: ", "")
        if not self.usb.is_open():
            return "the USB camera open."
        if self.scan_type() == "trichrome" and not self.light.is_open():
            return "the Scanlight connected (it lights each colour)."
        if not self.actions["guide"].isChecked():
            return "the frame guide on, placed on the frame (toolbar → Frame guide)."
        if self.magnified():
            return "Camera zoom back to Whole."
        return ""

    def _on_use_frame(self):
        sug = self._frame_suggestion
        if self._frame_exposures is not None:
            self._frame_exposures = None              # a second click: back to the base's
        elif sug is not None and sug.changes:
            self._frame_exposures = {k: dict(v) for k, v in sug.exposures.items()}
            what = ", ".join((f"{k.upper()} " if k != "w" else "")
                             + f"{be.short_text(v['shutter'])} ISO {v['iso']}"
                             for k, v in sug.exposures.items())
            self._note_caption(f"The next Shoot uses this frame's exposures ({what}), "
                               "then the film base's again.")
        self._refresh_frame_line()

    def _on_meter_clicked(self):
        if self._measure is not None:
            if self._measure.get("kind") == "frame":
                self._measure_stop()
            return
        self.start_frame_meter()

    def start_frame_meter(self):
        """Meter (spec/frame-exposure.md §4): one RAW test shot per colour at
        the film base's exposures, read inside the guide."""
        if self._measure is not None or self.sequence.running:
            return False
        if self._sleeping is not None:
            self._warn_caption("Wake the light first.")
            return False
        if self._rail_busy():
            self._warn_caption("Not while the focus rail is moving.")
            return False
        ctx = self._frame_context()
        if isinstance(ctx, str):
            self._warn_caption(ctx.replace("This frame: ", "Metering: "))
            return False
        base, tri, shutters, isos = ctx
        if not self.usb.is_open():
            self._warn_caption("Metering needs the USB camera open.")
            return False
        if tri and not self.light.is_open():
            self._warn_caption("Metering for trichrome needs the Scanlight connected.")
            return False
        if not self.actions["guide"].isChecked():
            self._warn_caption("Metering reads inside the frame guide: turn it on and place "
                               "it on the frame.")
            return False
        if self._refuse_when_magnified("Metering"):
            return False
        cal = fm.FrameMetering(base, tri)
        if cal.done:
            self._warn_caption(cal.error or "Nothing to meter.")
            return False
        import tempfile
        self._used()
        self._measure_token += 1
        guide = dict(self.canvas.guide)
        self._measure = {
            "kind": "frame", "cal": cal, "name": "", "index": None,
            "rect": None, "where": "inside the frame guide",
            "read": self._read_frame, "read_arg": guide,
            "base": base, "tri": tri, "shutters": list(shutters), "isos": list(isos),
            "temp": tempfile.mkdtemp(prefix="freeccr-frame-meter-"),
            "levels": dict(self._levels),
            "exposure": {f: self._exposure.get(f) for f in ("shutter", "iso")
                         if self._exposure.get(f)},
            "in_flight": False, "stopping": False, "token": self._measure_token,
        }
        self._hold_live(True)
        self.usb.set_dest(self._measure["temp"])
        self.meter_btn.setText("Stop Metering")
        self._refresh_film_bases()
        self._refresh_frame_line()
        self._refresh_shoot_enabled()
        self._measure_next()
        return True

    def _frame_meter_finish(self, m, cal):
        self.meter_btn.setText("Meter")
        self._refresh_film_bases()
        if cal.error is None and cal.result:
            sug = fm.suggest(m["base"], m["tri"], cal.result, m["shutters"], m["isos"])
            if sug is None:
                self._warn_caption("Metering: the film base changed meanwhile.")
            else:
                if cal.clipped:
                    sug.notes.append("clipped in " + ", ".join(
                        be.NAMES.get(c, c) for c in cal.clipped) + ": meter again")
                self._frame_metered = sug
                self._frame_exposures = None
                self._note_caption(sug.text() + (" (" + "; ".join(sug.notes) + ")"
                                                 if sug.notes else ""))
        elif cal.error == "cancelled":
            self._note_caption("Metering stopped.")
        else:
            self._warn_caption(cal.error or "Metering didn't finish.")
        self._refresh_frame_line()

    def _on_measure_clicked(self):
        if self._measure is not None:
            self._measure_stop()
            return
        name = self._ask_film_base_name(be.default_name())
        if name:
            self.start_measure(name)

    def _film_base_patch(self):
        """(rect in frame fractions, where) for reading the base: the Film Base
        click, else a border found on the live picture with HIGH confidence
        (two edges agreeing), else (None, ""). A lone bright edge can be bare
        light round the film, which would expose the base a stop or more
        under, so anything less asks for the click."""
        fresh, self._fresh_base_click = getattr(self, "_fresh_base_click", False), False
        box, self._base_box = getattr(self, "_base_box", None), None
        if fresh and box is not None:
            return tuple(box), "the box you drew"
        if self._neg_base_pos is not None:
            fx, fy, w, h = self._neg_base_pos
            # an older click inside the frame guide is another roll's, or a gap
            # that has since been wound on into a frame (the user, 2026-11-01:
            # a new roll's Measure read the picture there and failed as
            # uneven): ask again. A click made for this Measure always counts;
            # an older one counts once (a Measure that used it, or failed,
            # asks next time: "if the first measure doesn't work and then I
            # click measure again, it doesn't allow me to pick").
            if fresh or not (getattr(self, "_base_click_used", False)
                             or getattr(self, "_ask_where", False)
                             or (self.actions["guide"].isChecked()
                                 and fm.inside_guide(dict(self.canvas.guide), fx, fy, w, h))):
                return be.patch_around(fx, fy, w, h), "where you clicked"
        if getattr(self, "_ask_where", False):
            return None, ""                          # the last Measure failed: ask where
        frame = self.feed.last_full_frame()
        if frame is None:
            return None, ""
        try:
            from core.auto_black_point import find_rebate
            res = find_rebate(frame)
        except Exception:
            return None, ""
        if res.rect and res.confidence == "high":
            return be.patch_inset(res.rect), f"the {res.side} border"
        return None, ""

    def start_measure(self, name, index=None):
        """Measure a film base: `index` set = Measure Again on that entry."""
        if self._measure is not None or self.sequence.running:
            return False
        if self._rail_busy():
            self._warn_caption("Not while the focus rail is moving.")
            return False
        if self._sleeping is not None:
            self._warn_caption("Wake the light first.")
            return False
        shutters = self._exp_choices.get("shutter") or []
        if not self.usb.is_open() or not shutters:
            self._warn_caption("Measuring needs a USB camera whose shutter speed FreeCCR "
                               "can set.")
            return False
        tri = self.scan_type() == "trichrome"
        if tri and not self.light.is_open():
            self._warn_caption("Measuring for trichrome needs the Scanlight connected: it "
                               "lights each colour.")
            return False
        if self._refuse_when_magnified("Measuring"):
            return False
        rect, where = self._film_base_patch()
        if rect is None:
            self._pending_measure = (name, index)
            self._pick_target = "base"
            # revision 5: the pointer carries the patch, so the click shows
            # exactly what will be read
            self.canvas.set_mode("pick", box=be.PATCH_SIDE,
                                 hint="Click the film base, or drag a box over it: the box is "
                                      "what's measured  ·  Esc cancels")
            self._note_caption("Click the film base in the picture (the clear border beside "
                               "the frame), or drag a box over it: a tall thin one fits the "
                               "gap between frames. Measuring starts after.  Esc cancels.")
            return False
        self._used()
        if tri:
            # each LED from full (the user's choice): the shutter does the
            # balancing, so no colour is slowed by a preset's dimmed LED
            colours = list(be.COLOURS)
            start = {c: cl.solo({c: 255}, c) for c in colours}
        else:
            colours = ["w"]
            start = {"w": dict(self._levels) if self.light.is_open() else None}
        first = self._exposure.get("shutter") or self.shutter_speed_combo.currentData()
        cal = be.Calibration(colours, start, shutters, self._exp_choices.get("iso") or [],
                             first, can_dim=self.light.is_open())
        if cal.done:
            self._warn_caption(cal.error or "Nothing to measure.")
            return False
        import tempfile
        self._measure_token += 1
        self._measure = {
            "cal": cal, "name": name, "index": index, "rect": rect, "where": where,
            "temp": tempfile.mkdtemp(prefix="freeccr-film-base-"),
            "levels": dict(self._levels),
            "exposure": {f: self._exposure.get(f) for f in ("shutter", "iso")
                         if self._exposure.get(f)},
            "in_flight": False, "stopping": False, "token": self._measure_token,
        }
        self._hold_live(True)
        self.usb.set_dest(self._measure["temp"])
        self.canvas.set_mark(rect, "Measuring the base here")
        self.measure_btn.setText("Stop Measuring")
        self._refresh_film_bases()
        self._refresh_shoot_enabled()
        self._measure_next()
        return True

    def _measure_next(self):
        m = self._measure
        if m is None:
            return
        cal = m["cal"]
        shot = cal.next_shot()
        if shot is None:
            self._measure_finish()
            return
        colour, levels, exp = shot
        if colour in be.COLOURS and not self.light.is_open():
            cal.failed("The Scanlight disconnected.")
            self._measure_finish()
            return
        if levels is not None and self.light.is_open():
            self.set_levels(levels)
        what = be.NAMES.get(colour, colour) if colour in be.COLOURS else "the film base"
        if m.get("kind") == "frame":
            what = (be.NAMES.get(colour, colour) if colour in be.COLOURS else "the frame")
        verb = "Metering" if m.get("kind") == "frame" else "Measuring"
        self._note_caption(f"{verb} {what} ({m['where']}): shot {cal.shots + 1} at "
                           f"{be.short_text(exp['shutter'])}, ISO {exp['iso']}…  Esc stops.")
        token = m["token"]
        QTimer.singleShot(cs.settle_ms(self._timing), lambda: self._measure_fire(token, exp))

    def _measure_fire(self, token, exp):
        m = self._measure
        if m is None or m["token"] != token:
            return
        if m["stopping"]:
            self._measure_finish()
            return
        m["in_flight"] = True
        m["shot"] = m.get("shot", 0) + 1
        shot = m["shot"]
        wait_ms = (cs.FILE_TIMEOUT_S + (be.seconds(exp.get("shutter")) or 0.0)) * 1000.0
        QTimer.singleShot(int(wait_ms), lambda: self._measure_timeout(token, shot))
        self.usb.fire(exp)

    def _measure_timeout(self, token, shot):
        m = self._measure
        if m is None or m["token"] != token or m.get("shot") != shot or not m["in_flight"]:
            return
        m["cal"].failed(f"No file from the camera after {int(cs.FILE_TIMEOUT_S)} s.")
        self._measure_finish()

    def _measure_landed(self, path):
        m = self._measure
        if not m["in_flight"]:
            return                               # not ours (a late file)
        m["in_flight"] = False
        token = m["token"]
        rect = m.get("read_arg", m["rect"])
        read = m.get("read") or self._read_patch

        m["last_path"] = path

        def work():
            try:
                out = read(path, rect)
            except Exception as e:
                out = e
            self._patch_read.emit(token, out)

        import threading
        threading.Thread(target=work, daemon=True).start()

    def _on_patch_read(self, token, reading):
        m = self._measure
        if m is None or m["token"] != token:
            return
        cal = m["cal"]
        if isinstance(reading, Exception):
            cal.failed(f"Couldn't read the test shot ({reading}).")
        elif m["stopping"]:
            cal.cancel()
        else:
            cal.landed(reading)
        path, m["last_path"] = m.get("last_path"), None
        if path:
            if cal.done and cal.error not in (None, "cancelled") and m.get("kind") != "frame":
                m["kept"] = self._keep_failed_shot(path)      # to look into
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass
        if cal.done:
            self._measure_finish()
        else:
            self._measure_next()

    def _keep_failed_shot(self, path):
        """Move the test shot of a failed Measure out of the temp folder (it is
        deleted with it) to `<app data>/measure-failed/`, so what it read can be
        looked into; its path, or None."""
        try:
            from core.catalog import default_catalog_path
            import shutil
            d = os.environ.get("FREECCR_MEASURE_KEEP") or os.path.join(
                os.path.dirname(default_catalog_path()), "measure-failed")
            os.makedirs(d, exist_ok=True)
            dest = os.path.join(d, time.strftime("%Y%m%d-%H%M%S-") + os.path.basename(path))
            shutil.move(path, dest)
            return dest
        except Exception as ex:
            print(f"measure: keeping the failed shot: {ex}")
            return None

    def _measure_stop(self):
        """Stop: at once, or once the shot on its way has landed (a shot is
        never abandoned, so its file can't reach the hot folder)."""
        m = self._measure
        if m is None:
            return
        if m["in_flight"]:
            m["stopping"] = True
            self._note_caption("Stopping after this shot…")
            return
        m["cal"].cancel()
        self._measure_finish()

    def _measure_finish(self):
        m, self._measure = self._measure, None
        if m is None:
            return
        self._measure_token += 1
        cal = m["cal"]
        if not cal.done:
            cal.cancel()
        # put back what measuring changed: the light, the camera's exposure, the
        # download folder, live view
        if self.light.is_open() and m["levels"] is not None and self._sleeping is None:
            self.set_levels(m["levels"])
        if m["exposure"] and self.usb.is_open():
            setter = getattr(self.usb, "set_exposure", None)
            if setter is not None:
                for f, v in m["exposure"].items():
                    setter(f, v)
        self.usb.set_dest(self._usb_dest)
        self._hold_live(False)
        self.canvas.set_mark(None)
        import shutil
        shutil.rmtree(m["temp"], ignore_errors=True)
        self.measure_btn.setText("Measure…")
        if m.get("kind") == "frame":
            self._frame_meter_finish(m, cal)
        elif cal.error is None and cal.result:
            camera = self._usb_model or ""
            if m["index"] is not None:
                self._bases = be.replace(self._bases, m["index"], cal.result, camera, cal.notes)
            else:
                self._bases = be.add(self._bases, be.entry(m["name"], cal.result, camera,
                                                           cal.notes))
            self._save_film_bases()
            self._refresh_film_bases()
            base = self._film_base()
            tri = self.scan_type() == "trichrome"
            self._note_caption(f"Film base “{base['name']}” measured in {cal.shots} shots: "
                               + be.summary(base, tri))
        else:
            self._refresh_film_bases()
            if cal.error == "cancelled":
                self._note_caption("Measuring stopped; nothing was saved.")
            else:
                kept = m.get("kept")
                self._warn_caption((cal.error or "Measuring didn't finish.")
                                   + (f" The test shot is kept: {kept}" if kept else ""))
        if m.get("kind") != "frame":
            self._base_click_used = True
            self._ask_where = cal.error not in (None, "cancelled")
        if self._exp_held:
            held, self._exp_held = self._exp_held, {}
            self._send_exposure(held)
        self._refresh_shoot_enabled()

    # ------------------------------------------------------------------ #
    # USB camera (spec/capture-routes.md)
    # ------------------------------------------------------------------ #
    def _warn_caption(self, text):
        self.capture_caption.setStyleSheet(
            f"color: {theme.WARN_TEXT}; font-size: {theme.FS_CAPTION}px;")
        self.capture_caption.setText(text)

    def _shutter_is_usb(self):
        """The Shutter row names the USB camera that is open (an entry is its
        key; "usb" is the open one, from older settings)."""
        data = self.shutter_combo.currentData()
        if not self.usb.is_open() or not isinstance(data, str):
            return False
        if data == "usb":
            return True
        return data.startswith("usb:") and getattr(self.usb, "key", data) == data

    def _refresh_shoot_enabled(self):
        if self._af_shot is not None:
            self.shoot_btn.setEnabled(True)      # a second press stops it
            self.shoot_btn.setText("Stop   (Esc)")
            self.shoot_btn.setToolTip("Focusing before the shot: press to stop "
                                      "(nothing is shot)")
            return
        if not self.sequence.running and self.shoot_btn.text() != "Shoot   (S)":
            self.shoot_btn.setText("Shoot   (S)")
        if self._af is not None and self._af.running:
            self.shoot_btn.setEnabled(False)
            self.shoot_btn.setToolTip("Autofocus is moving the camera")
            return
        if self._measure is not None:
            self.shoot_btn.setEnabled(False)
            self.shoot_btn.setToolTip("Measuring the film base")
            return
        if self.sequence.running:
            self.shoot_btn.setEnabled(True)
            self.shoot_btn.setToolTip("")
            return
        if self._shutter_is_usb():
            ok = self.scan_type() != "trichrome" or self.light.is_open()
            self.shoot_btn.setEnabled(ok)
            self.shoot_btn.setToolTip("" if ok else "Trichrome needs the Scanlight connected")
            return
        ok = self.light.is_open()
        self.shoot_btn.setEnabled(ok)
        self.shoot_btn.setToolTip("" if ok else "Connect the Scanlight to shoot, or "
                                  "choose a USB camera as the Shutter")

    def _tether_folder(self):
        mw = self._mw
        if mw is None:
            return None
        if hasattr(mw, "tether_folder"):
            return mw.tether_folder()
        return getattr(mw, "_tether_folder", None)

    def _ensure_hot_folder(self):
        """True once Hot Folder Tether is on (asking to turn it on if it is
        off); the USB camera is pointed at its folder."""
        active, label, count = self._tether_state()
        if not active and self._mw is not None:
            self._start_hot_folder()
            active, label, count = self._tether_state()
        if active:
            self._usb_dest = self._tether_folder()
            self.usb.set_dest(self._usb_dest)
        self._refresh_status_strip()
        return active

    def _rebuild_shutter_combo(self):
        """Scanlight, then every USB camera found (one can be the shutter while
        a capture device gives the picture: a camera with no USB live view)."""
        want = str(_settings().value("live/shutter", "scanlight") or "scanlight")
        open_key = getattr(self.usb, "key", None) if self.usb.is_open() else None
        items = [("Scanlight", "scanlight")]
        for key, label in self._usb_found:
            if key == open_key and self._usb_model:
                label = f"USB · {self._usb_model}"
            items.append((label, key))
        keys = [k for _l, k in items]
        if self.usb.is_open() and (open_key is None or open_key not in keys):
            items.append((f"USB · {self._usb_model or 'camera'}", open_key or "usb"))
            keys.append(open_key or "usb")
        if want == "usb":                        # older setting: the open camera
            want = open_key or "usb"
        self.shutter_combo.blockSignals(True)
        self.shutter_combo.clear()
        for label, key in items:
            self.shutter_combo.addItem(label, key)
        i = self.shutter_combo.findData(want)
        self.shutter_combo.setCurrentIndex(i if i >= 0 else 0)
        self.shutter_combo.blockSignals(False)
        self._refresh_shoot_enabled()
        self._refresh_capture_caption()

    def _on_shutter_chosen(self, i):
        key = self.shutter_combo.itemData(i)
        _settings().setValue("live/shutter", key)
        _settings().setValue("live/shutter_label", dict(self._usb_found).get(key, ""))
        if (isinstance(key, str) and key.startswith("usb:")
                and (getattr(self.usb, "key", None) != key or not self.usb.is_open())):
            self.usb.open(key)                   # as the shutter only: no live view asked
        self._refresh_shoot_enabled()
        self._refresh_capture_caption()

    def _on_usb_state(self, kind, text):
        holders = list(getattr(self.usb, "holders", None) or []) if kind == "busy" else []
        own = [h for h in holders if getattr(h, "own", False)]
        holders = [h for h in holders if not getattr(h, "own", False)]
        others = [h for h in holders if not h.system]
        # Release Camera ends macOS's own camera service: offered only when
        # that (or nothing we can name) holds it, never for another program
        self.release_btn.setVisible(kind == "busy" and sys.platform == "darwin"
                                    and not others and not own)
        if kind in ("opening", "open", "lost", "closed"):
            self._live_trouble = None           # a fresh connection's live view
        if kind == "open":
            self._on_camera_connected()
            self._usb_model = text
            if self._usb_dest:
                self.usb.set_dest(self._usb_dest)
            key = getattr(self.usb, "key", None)
            if text and key is not None:          # the model the camera reports
                self._usb_found = [(k, f"USB · {text}" if k == key and k.startswith("usb:gphoto2:")
                                    else lab) for k, lab in self._usb_found]
                i = self.camera_combo.findData(key)
                if i >= 0 and key.startswith("usb:gphoto2:"):
                    self.camera_combo.setItemText(i, f"USB · {text}")
        elif kind == "busy":
            # who has it (spec/camera-holder.md), when the system can tell
            if own:
                self.canvas.set_message(
                    "FreeCCR still has the camera from an earlier connection.\n"
                    "Unplug the camera's USB cable and plug it in again, then Find Cameras.")
            elif others:
                names = " and ".join(h.describe() for h in others)
                self.canvas.set_message(
                    f"{names} {'is' if len(others) == 1 else 'are'} using the camera, "
                    "so FreeCCR can't open it.\nQuit it (or close its tether window), "
                    "then Find Cameras.")
            elif sys.platform == "darwin" and holders:
                self.canvas.set_message(
                    f"macOS is holding the camera ({holders[0].describe()}), so FreeCCR "
                    "can't open it.\nIf Image Capture, Photos or Preview is open, quit it. "
                    "Otherwise Release Camera from Other Apps ends that hold (it may come back when the "
                    "camera is replugged).")
            elif sys.platform == "darwin":
                self.canvas.set_message(
                    "Another program has the camera, so FreeCCR can't open it.\nIf Image "
                    "Capture, Photos or Preview is open, quit it, then Find Cameras. "
                    "Otherwise Release Camera from Other Apps ends macOS's own hold.")
            elif holders:
                self.canvas.set_message(
                    f"{holders[0].describe()} is holding the camera, so FreeCCR can't "
                    "open it.\nUnmount the camera in the file manager (or quit it), "
                    "then Find Cameras.")
            else:
                self.canvas.set_message(
                    "Another program is using the camera.\nClose it (or its tether "
                    "window), then Find Cameras.")
        elif kind == "error":
            self.canvas.set_message(f"The camera didn't open.\n{text}")
        elif kind == "nopreview":
            self.canvas.set_message(
                "This camera has no live view over USB.\nIt can still shoot over USB "
                "(Shutter); choose a capture device for the picture.")
            return
        elif kind == "lost":
            self.canvas.set_message("The USB camera disconnected.\nFind Cameras to "
                                    "reconnect.")
        if kind in ("lost", "closed", "error", "busy", "open"):
            self.addon_badge.setVisible(False)       # an open camera is checked afresh
        if kind in ("lost", "closed", "error", "busy"):
            if self._measure is not None:
                self._measure["cal"].failed("The camera disconnected.")
                self._measure_finish()
            self._exposure = {}
            self._cam_zoom_action.setVisible(False)
            self._on_cam_zoom("1")
            self._on_exposure_choices({})
        self._rebuild_shutter_combo()
        if kind == "lost" and self.sequence.running and self.sequence.camera_shutter:
            self.sequence.abort("camera lost")      # after the rebuild: its caption stays
        self._refresh_status_strip()

    def _on_black_point(self):
        start = getattr(self._mw, "start_roll_black_point", None)
        if start is None:
            return
        if not start():
            self._warn_caption("Capture the film lead first, with the light on.")

    # Live view over USB that stopped (spec/film-base-exposure.md revision 7).
    # The next frame clears the message (the canvas drops it on a result) and
    # `_live_trouble` (None, "stopped", "restarting" or "gone").
    def _usb_is_the_picture(self):
        key = getattr(self.usb, "key", None)
        return bool(key) and self.camera_combo.currentData() == key

    def _on_live_paused(self, paused):
        if not paused or not self._usb_is_the_picture():
            return
        if getattr(self, "_live_trouble", None) is None:
            self._live_trouble = "stopped"
            self.canvas.set_message("Live view stopped.\nChecking the camera…")

    def _on_live_restarting(self, attempt, tries):
        if not self._usb_is_the_picture():
            return
        self._live_trouble = "restarting"
        self.canvas.set_message("Live view stopped.\nReconnecting the camera to restart "
                                f"it (try {attempt} of {tries})…")

    def _on_live_not_back(self):
        if not self._usb_is_the_picture():
            return
        self._live_trouble = "gone"
        self.canvas.set_message(
            "Live view has stopped on the camera, and reconnecting didn't restart it.\n"
            "Unplug the camera's USB cable and plug it back in, then Find Cameras.")

    def _on_usb_file(self, path):
        if self._measure is not None:
            self._measure_landed(path)
            return
        capture_guide.remember(path)             # the shot's own file: its guide
        capture_log.add_file(path)               # ... and its line in the log
        if self.sequence.running and self.sequence.camera_shutter:
            self.sequence.frame_done(path)
        elif not self.sequence.running:
            self.capture_caption.setStyleSheet(
                f"color: {theme.TEXT_MUTED}; font-size: {theme.FS_CAPTION}px;")
            self.capture_caption.setText(f"From the camera: {os.path.basename(path)}")

    def _on_usb_skipped(self, name):
        if self.sequence.running and self.sequence.camera_shutter:
            self.sequence.abort("no file")
        active, _label, _count = self._tether_state()
        if active:
            self._warn_caption(f"The camera took {name}, but it didn't arrive in the hot "
                               "folder: check the camera software saves shots to this "
                               "computer.")
        else:
            self._warn_caption(f"The camera took {name}, but Hot Folder Tether is off, so "
                               "it stays on the camera. Turn the hot folder on to bring "
                               "shots in.")

    def _on_fire_failed(self, text):
        if self._measure is not None:
            self._measure["cal"].failed(text)
            self._measure_finish()
            return
        self.sequence.fire_failed(text)
        if not self.sequence.running:
            self._warn_caption(text)

    def _on_exposure(self, exp):
        self._exposure = dict(exp or {})
        self._select_exposure()
        self._refresh_status_strip()

    # -- shutter speed and ISO (spec/usb-exposure-control.md) -------------
    EXP_FIELDS = (("shutter", "shutter_speed_combo"), ("iso", "iso_combo"))

    @staticmethod
    def exposure_label(field, value):
        """What a picker shows for one of the camera's values: ISO values it
        reports as negative numbers are its automatic modes."""
        v = str(value)
        if field == "iso":
            return f"Auto {v[1:]}" if re.fullmatch(r"-\d+", v) else f"ISO {v}"
        return v

    def _note_caption(self, text):
        self.capture_caption.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: {theme.FS_CAPTION}px;")
        self.capture_caption.setText(text)

    def _on_exposure_choices(self, choices):
        self._exp_choices = dict(choices or {})
        self._exp_waiting, self._exp_held = {}, {}
        for field, name in self.EXP_FIELDS:
            combo = getattr(self, name)
            vals = self._exp_choices.get(field) or []
            combo.blockSignals(True)
            combo.clear()
            for v in vals:
                combo.addItem(self.exposure_label(field, v), str(v))
            combo.blockSignals(False)
            combo.setVisible(bool(vals))
        self.exposure_row.setVisible(bool(self._exp_choices))
        self.film_base_box.setVisible(bool(self._exp_choices.get("shutter")))
        self._select_exposure()

    def _select_exposure(self):
        """The pickers show what the camera reports, except a field whose
        change is waiting (sent, or held for the end of a sequence)."""
        for field, name in self.EXP_FIELDS:
            if field in self._exp_waiting or field in self._exp_held:
                continue
            v = self._exposure.get(field)
            combo = getattr(self, name)
            i = combo.findData(str(v)) if v is not None else -1
            if i >= 0 and i != combo.currentIndex():
                combo.blockSignals(True)
                combo.setCurrentIndex(i)
                combo.blockSignals(False)

    def _on_exposure_picked(self, field, value):
        if value is None:
            return
        value = str(value)
        if (field not in self._exp_waiting and field not in self._exp_held
                and str(self._exposure.get(field)) == value):
            return                                # already set
        if self.sequence.running or self._measure is not None:
            self._exp_held[field] = value
            self._note_caption(f"{self.exposure_label(field, value)} is set when "
                               + ("measuring finishes." if self._measure is not None
                                  else "this shot finishes."))
            return
        self._send_exposure({field: value})

    def _send_exposure(self, wanted):
        setter = getattr(self.usb, "set_exposure", None)
        if setter is None or not self.usb.is_open():
            return
        for f, v in wanted.items():
            self._exp_waiting[f] = v
            setter(f, v)
        self._note_caption("Setting " + ", ".join(
            self.exposure_label(f, v) for f, v in wanted.items()) + "…")

    def _on_exposure_set(self, results):
        bad = []
        for f, res in (results or {}).items():
            ok, text, value = res
            if self._exp_waiting.get(f) == value:
                self._exp_waiting.pop(f, None)
            if not ok:
                bad.append(self.exposure_label(f, value) + (f" ({text})" if text else ""))
        if bad:
            self._warn_caption("The camera didn't take " + ", ".join(bad) + ".")
        elif not self._exp_waiting and self.capture_caption.text().startswith("Setting "):
            self.capture_caption.setText("")
        self._select_exposure()

    VIEW_ZOOMS = (("Fit", "fit"), ("Fit Guide", "guide"), ("2×", "2"), ("4×", "4"),
                  ("8×", "8"))

    def _on_view_zoom_chosen(self, i):
        key = self.zoom_combo.itemData(i)
        if key != "custom":
            self.canvas.zoom(key)

    def _sync_view_zoom(self):
        """The View list shows the zoom as it is (the wheel and double-click
        change it too): a preset when it is one, else its factor."""
        combo = self.zoom_combo
        custom = combo.findData("custom")
        if self.canvas.is_fit():
            target = combo.findData("fit")
        else:
            f = self.canvas.zoom_factor()
            target = next((combo.findData(k) for _l, k in self.VIEW_ZOOMS
                           if k not in ("fit", "guide") and abs(f / float(k) - 1) < 0.03), -1)
            if target < 0:
                text = f"{f:.1f}×"
                if custom < 0:
                    combo.addItem(text, "custom")
                    custom = combo.findData("custom")
                else:
                    combo.setItemText(custom, text)
                target = custom
        if custom >= 0 and target != custom:
            combo.removeItem(custom)
            target = combo.findData(combo.itemData(target)) if target >= 0 else target
        combo.blockSignals(True)
        combo.setCurrentIndex(max(0, target))
        combo.blockSignals(False)

    @staticmethod
    def cam_zoom_label(lv):
        lv = str(lv)
        return "Whole" if lv == "1" else ("Max" if lv == "16" else f"{lv}×")

    def _on_zoom_levels(self, levels):
        self.cam_zoom_combo.clear()
        for lv in levels or []:
            self.cam_zoom_combo.addItem(self.cam_zoom_label(lv), lv)
        self._cam_zoom_action.setVisible(bool(levels))
        self._refresh_rail()                     # the autofocus-before-shot zoom list
        self.cam_zoom_combo.setToolTip(
            "The camera's own live view magnifier, for focusing: it reads the sensor, "
            "so 8× shows about one sensor pixel per screen pixel. It zooms where the "
            "camera's focus point is (move it with the joystick). Z steps through.")
        if not levels:
            self._on_cam_zoom("1")

    # --- the camera's magnifier (spec/live-magnifier.md) -------------------
    def magnified(self):
        return self._cam_zoom not in (None, "1")

    def _set_cam_zoom(self, level):
        level = str(level)
        if level == self._cam_zoom:
            return
        if not self.magnified():
            self._zoom_whole = self.feed.last_full_frame()   # to find where it looks
        self.usb.set_zoom(level)

    def _canvas_menu(self, pos):
        """Right click on the picture: the camera's zoom levels (the user,
        2026-11-01), as the toolbar's Camera zoom list and Z offer them."""
        m = QMenu(self)
        levels = [self.cam_zoom_combo.itemData(i) for i in range(self.cam_zoom_combo.count())]
        if not levels or not self._cam_zoom_action.isVisible():
            a = m.addAction("Camera zoom: not offered by this camera")
            a.setEnabled(False)
        else:
            head = m.addAction("Camera Zoom")
            head.setEnabled(False)
            for lv in levels:
                a = m.addAction(self.cam_zoom_label(lv))
                a.setCheckable(True)
                a.setChecked(str(lv) == str(self._cam_zoom))
                a.triggered.connect(lambda _c=False, v=lv: self._set_cam_zoom(v))
        m.popup(pos)
        self._popup = m
        return m

    def _step_cam_zoom(self):
        n = self.cam_zoom_combo.count()
        if n < 2 or not self._cam_zoom_action.isVisible():
            return
        i = self.cam_zoom_combo.findData(self._cam_zoom)
        nxt = self.cam_zoom_combo.itemData((i + 1) % n if i >= 0 else 1)
        self._set_cam_zoom(nxt)

    def _on_cam_zoom(self, level):
        level = str(level or "1")
        was = self._cam_zoom
        self._cam_zoom = level
        st = self._af_shot
        if st is not None:
            if st["stage"] == "zoom" and level == st["want"] and st["zoom_at"] is None:
                st["zoom_at"] = time.monotonic()  # now wait for its picture
            elif st["stage"] == "whole" and level == "1":
                QTimer.singleShot(0, self._af_shot_shoot)
        if level == "1" and self._shoot_when_whole is not None:
            # Shoot waited for the whole frame: go on with it now
            self._shoot_when_whole = None
            QTimer.singleShot(0, self.shoot)
        i = self.cam_zoom_combo.findData(level)
        if i >= 0:
            self.cam_zoom_combo.blockSignals(True)
            self.cam_zoom_combo.setCurrentIndex(i)
            self.cam_zoom_combo.blockSignals(False)
        self.canvas.set_magnified(None if level == "1" else self.cam_zoom_label(level))
        if level != was and self._af is not None and self._af.running:
            self._af.stop("Autofocus stopped: the camera zoom changed.")
            self.rail.stop()
        if level != was:
            self._reset_sharp_best()             # a different picture scale
            # and again once the new picture is actually arriving (the
            # reconnect takes ~1 s; frames already on their way would
            # otherwise set the best at the old scale)
            QTimer.singleShot(1500, self._reset_sharp_best)
            self._new_frame_quiet = time.monotonic() + 4.0
        self._zoom_token += 1
        if level != "1":
            self.canvas.set_zoom_spot(None)
            if self._zoom_whole is not None:
                tok = self._zoom_token
                QTimer.singleShot(1200, lambda: self._find_zoom_spot(tok, level))
        elif was not in (None, "1") and self._zoom_spot is not None:
            fx, fy, mag = self._zoom_spot
            self.canvas.set_zoom_spot((fx, fy, mag))
            tok = self._zoom_token
            QTimer.singleShot(5000, lambda: tok == self._zoom_token
                              and self.canvas.set_zoom_spot(None))

    def _find_zoom_spot(self, token, level):
        if token != self._zoom_token or self._zoom_whole is None:
            return
        whole, zoomed = self._zoom_whole, self.feed.last_full_frame()
        try:
            mag = float(level)
        except ValueError:
            return
        if zoomed is None:
            return

        def work():
            from core import zoom_spot
            try:
                spot = zoom_spot.locate(whole, zoomed, mag)
            except Exception:
                spot = None
            self._zoom_spot_found.emit(token, None if spot is None else (*spot, mag))
        import threading
        threading.Thread(target=work, daemon=True).start()

    def _on_zoom_spot(self, token, spot):
        if spot is not None:
            self._zoom_spot = spot          # kept even if the level changed since

    def _refuse_when_magnified(self, what):
        if not self.magnified():
            return False
        self._warn_caption(f"{what}: go back to the whole frame first (Z, or Whole in "
                           "the magnifier list), as it records whole-frame positions.")
        return True

    ADDON_TEXT = (
        "FreeCCR only uses the camera's standard USB controls and doesn't need "
        "Magic Lantern. Magic Lantern is unofficial software that runs alongside "
        "the camera's own firmware: its developers say it can't be guaranteed safe "
        "and is used at your own risk, and a wrong setting written by it can stop a "
        "camera starting up.\n\nIf the camera misbehaves while tethered, start it "
        "without Magic Lantern: hold SET while switching it on, or use a card that "
        "doesn't have it.")

    def _on_addons(self, found):
        """Unofficial add-ons on the open camera's card (spec/capture-routes.md
        §12): a badge while it is open, and a warning once per camera."""
        if cb.MAGIC_LANTERN not in (found or []):
            return
        self.addon_badge.setText("Magic Lantern on card")
        self.addon_badge.setToolTip(self.ADDON_TEXT)
        self.addon_badge.setVisible(True)
        model = self._usb_model or "camera"
        if model in self._addon_warned:
            return
        self._addon_warned.add(model)
        if not _settings().value("live/warn_addons", True, type=bool):
            return
        if self._addon_notice(model, found):
            _settings().setValue("live/warn_addons", False)

    def _addon_notice(self, model, found):
        """The warning box; returns "don't warn again". The test seam."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Magic Lantern")
        box.setText(f"The card in the {model} has Magic Lantern on it.")
        box.setInformativeText(self.ADDON_TEXT)
        box.addButton("OK", QMessageBox.AcceptRole)
        cb_ = QCheckBox("Don't warn me about this again")
        box.setCheckBox(cb_)
        box.exec()
        return cb_.isChecked()

    def _on_release_camera(self):
        box = QMessageBox(self)
        box.setWindowTitle("Release Camera from Other Apps")
        box.setText("End macOS's hold on the camera?")
        box.setInformativeText(
            "macOS's own camera service (ptpcamerad) opens PTP cameras as soon as "
            "they are plugged in, which stops FreeCCR opening them. FreeCCR will end "
            "it and try again. macOS starts it again by itself when needed.")
        go = box.addButton("Release", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is go:
            self.release_btn.setVisible(False)
            self.canvas.set_message("Opening the camera…")
            self.usb.release_and_retry()

    def scan_type(self):
        return self.scan_combo.currentData()

    def _on_scan_type_chosen(self, i):
        t = self.scan_combo.itemData(i)
        if self._mw is not None and hasattr(self._mw, "set_scan_type"):
            self._mw.set_scan_type(t)        # persists and tells everyone
        else:
            from core.ccr_backend import ccr_backend
            ccr_backend.scan_type = t
            _settings().setValue("scan/type", t)
        self._apply_scan_type(t)

    def _apply_scan_type(self, t):
        i = self.scan_combo.findData(t)
        if i >= 0 and i != self.scan_combo.currentIndex():
            self.scan_combo.blockSignals(True)
            self.scan_combo.setCurrentIndex(i)
            self.scan_combo.blockSignals(False)
        self.neg_bw.setChecked(t == "bw")
        self._refresh_capture_caption()
        self._refresh_shoot_enabled()
        if hasattr(self, "film_base_note"):
            self._refresh_film_bases()

    def _refresh_capture_caption(self):
        tri = self.scan_type() == "trichrome"
        usb = self._shutter_is_usb()
        total = cs.cycle_ms(self._timing, tri, camera=usb) / 1000.0
        self.capture_caption.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: {theme.FS_CAPTION}px;")
        what = ("One triplet (red, green, blue)" if tri
                else "One shot, with the light as it is,")
        if usb:
            settle = cs.settle_ms(self._timing)
            self.capture_caption.setText(
                f"One triplet (red, green, blue) over USB, ≈ {total:.0f} s: each colour "
                f"is set {settle} ms before its shot, and each shot waits for its file."
                if tri else
                f"One shot over USB with the light as it is, ≈ {total:.0f} s: it waits "
                "for the file.")
            return
        self.capture_caption.setText(
            f"{what} ≈ {total:.1f} s. Hold after the pulse: "
            f"{self._timing['hold_ms']} ms; it must be longer than the camera's shutter time.")

    def _timing_popup(self):
        ms = lambda v: f"{v} ms"  # noqa: E731
        if self._shutter_is_usb():
            rows = [("focus_ms", "Settle", cs.TIMING_LIMITS["focus_ms"], ms,
                     "Wait between setting a colour and firing the camera"),
                    ("period_ms", "Period", cs.TIMING_LIMITS["period_ms"], ms,
                     "The least time from one shot's start to the next")]
            pop = OptionsPopup("USB shutter timing", rows, self._timing,
                               cs.TIMING_DEFAULT, self, step=10)
            pop.changed.connect(self._on_timing)
            self._place(pop, self.timing_btn.mapToGlobal(self.timing_btn.rect().bottomLeft()))
            return
        rows = [("focus_ms", "Focus", cs.TIMING_LIMITS["focus_ms"], ms,
                 "Focus (half-press) held before the pulse"),
                ("pulse_ms", "Pulse", cs.TIMING_LIMITS["pulse_ms"], ms,
                 "The shutter trigger pulse"),
                ("hold_ms", "Hold", cs.TIMING_LIMITS["hold_ms"], ms,
                 "Wait after the pulse before the next colour: longer than the "
                 "camera's shutter time"),
                ("period_ms", "Period", cs.TIMING_LIMITS["period_ms"], ms,
                 "The least time from one shot's start to the next")]
        pop = OptionsPopup("Shutter timing", rows, self._timing, cs.TIMING_DEFAULT, self, step=10)
        pop.changed.connect(self._on_timing)
        self._place(pop, self.timing_btn.mapToGlobal(self.timing_btn.rect().bottomLeft()))

    def _on_timing(self, v):
        self._timing = cs.clean_timing(v)
        _settings().setValue("live/timing", json.dumps(self._timing))
        self._refresh_capture_caption()

    # ------------------------------------------------------------------ #
    # idle: the light turns off (spec §7.5)
    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # the focus rail (spec/focus-rail.md)
    # ------------------------------------------------------------------ #
    def _rail_spm(self):
        return self.rail.steps_per_mm() or raf.ASSUMED_STEPS_PER_MM

    def _rail_busy(self):
        return ((self._af is not None and self._af.running) or self.rail.moving()
                or self._af_shot is not None)

    def _rail_caption(self, text):
        self.rail_caption.setText(text)

    def _on_rail_ports(self, ports):
        cur = self.rail_port_combo.currentText()
        taken = self.light.port if self.light.is_open() else ""
        ports = [p for p in (ports or []) if p != taken]
        self.rail_port_combo.blockSignals(True)
        self.rail_port_combo.clear()
        if ports:
            self.rail_port_combo.addItems(ports)
            want = self.rail.port or cur or str(_settings().value("live/rail_port", "") or "")
            i = self.rail_port_combo.findText(want)
            if i >= 0:
                self.rail_port_combo.setCurrentIndex(i)
        else:
            self.rail_port_combo.addItem("No ports found")
        self.rail_port_combo.blockSignals(False)
        self._refresh_rail()

    def _on_rail_connect(self):
        if self.rail.is_open():
            self.rail.close_port()
            return
        port = self.rail_port_combo.currentText()
        if port and port != "No ports found":
            self.rail.open_port(port)

    def _on_rail_state(self, state, port, detail):
        if state == "ok" and port:
            _settings().setValue("live/rail_port", port)
            self.rail.set_auto_port(port)
        if state != "ok":
            self._rail_zeroed = False
            if self._af is not None and self._af.running:
                self._af.stop("Autofocus stopped: the rail disconnected.")
        if hasattr(self, "port_combo"):
            self._on_ports(self.light.ports)          # the light's list leaves its port out
        self._on_rail_ports(self.rail.ports)

    def _refresh_rail(self):
        if not hasattr(self, "rail_caption"):    # the section isn't built yet
            return
        state = self.rail.state
        self.rail_dot.setStyleSheet(f"color: {STATE_COLOURS.get(state, theme.BORDER_STRONG)}; "
                                    "font-size: 13px;")
        self.rail_connect_btn.setText("Disconnect" if self.rail.is_open() else "Connect")
        avail = state != "unavailable"
        for w in (self.rail_connect_btn, self.rail_rescan_btn, self.rail_port_combo):
            w.setEnabled(avail)
        info, st = self.rail.info, self.rail.status
        spm = self.rail.steps_per_mm()
        if state == "ok":
            bits = [info.get("name") or "Rail"]
            if st.get("homed"):
                bits.append("homed")
            elif self._rail_zeroed:
                bits.append("zero set here")
            else:
                bits.append("position not set")
            lead = orp.lead_mm(spm, self.rail.steps_per_rev())
            if lead:
                bits.append(f"{lead:g} mm a turn")
            if not spm:
                bits.append(f"steps ({raf.ASSUMED_STEPS_PER_MM:g}/mm assumed for "
                            "jogs and autofocus)")
            if st.get("driver") is False:
                bits.append("its motor driver isn't answering")
            text = "  ·  ".join(bits)
        elif state == "off":
            text = "Not connected"
        else:
            text = self.rail.detail or state
        self.rail_status.setText(text)
        self.rail_pos.setText(orp.mm_text(st.get("pos"), spm) if state == "ok" else "—")
        spr = self.rail.steps_per_rev()
        for b in self.rail_jog_btns.values():
            sign, size, keys = b._jog
            um = self._rail_jog[f"{size}_um"]
            b.setText(orp.jog_text(um, sign))
            turn = orp.turn_text(um, spm, spr)
            b.setToolTip(f"Move the camera {'down' if sign < 0 else 'up'} by "
                         f"{um / 1000:g} mm" + (f", {turn} of the screw" if turn else "")
                         + f" ({keys}).\nUp is away from the rail's home end. "
                         "Right-click: the step sizes.")
        ready = self.rail.is_ready()
        running = self._af is not None and self._af.running
        for b in list(self.rail_jog_btns.values()) + [self.rail_zero_btn]:
            b.setEnabled(ready and not running)
        self.rail_home_btn.setEnabled(ready and not running
                                      and info.get("home", "none") != "none")
        self.rail_home_btn.setToolTip(
            "This rail has no home switch or stall homing: use Set Zero"
            if ready and info.get("home", "none") == "none"
            else "Run to the rail's end, on its switch or a stall, and call that 0")
        self.rail_stop_btn.setEnabled(ready)
        self.rail_af_btn.setEnabled(ready)
        # "Focusing…" while it runs (a click or Esc stops it): the row has no
        # room for a longer label in the panel's width
        self.rail_af_btn.setText("Focusing…" if running else "Autofocus")
        self.rail_af_zoom.setEnabled(self.rail_af_shoot.isChecked()
                                     and self._cam_zoom_action.isVisible())
        self._refresh_shoot_enabled()

    def _rail_can_move(self, what, shot=False):
        if self._sleeping is not None or self._closing:
            return False
        if self._af_shot is not None and not shot:
            self._rail_caption("Focusing before the shot: Shoot or Esc stops it.")
            return False
        if not self.rail.is_ready():
            self._rail_caption("Connect the focus rail first.")
            return False
        if self._af is not None and self._af.running:
            self._rail_caption("Autofocus is running: Esc or Stop stops it.")
            return False
        if self.sequence.running or self._measure is not None:
            self._rail_caption(f"{what}: not while shooting (the camera would move "
                               "during the shot).")
            return False
        return True

    def rail_jog(self, sign, size):
        if not self._rail_can_move("Jog"):
            return
        um = self._rail_jog[f"{size}_um"]
        steps = max(1, int(round(um * self._rail_spm() / 1000.0)))
        self.rail.move(int(sign) * steps)
        self._used()

    def rail_zero(self):
        if not self._rail_can_move("Set Zero"):
            return
        if self.rail.moving():
            self._rail_caption("Wait for the rail to stop.")
            return
        self.rail.zero()
        self._rail_zeroed = True
        self._rail_caption("This position is now 0.")
        self._refresh_rail()

    def rail_home(self):
        if not self._rail_can_move("Home"):
            return
        self._rail_home_id = self.rail.home()
        self._rail_caption("Homing…")
        self._refresh_rail()

    def rail_stop(self):
        if self._af_shot is not None:
            self._af_shot_cancel()               # focusing for a shot: no shot either
            return
        if self._af is not None and self._af.running:
            self._af.stop("Autofocus stopped.")
        self.rail.stop()

    def _on_rail_done(self, move_id, pos, flags):
        if self._af is not None and self._af.running:
            self._af.moved(move_id, pos, flags)
        elif self._rail_home_id is not None and move_id == self._rail_home_id:
            self._rail_home_id = None
            if flags.get("fail"):
                self._rail_caption(f"Homing failed: {flags['fail']}.")
            elif flags.get("stopped"):
                self._rail_caption("Homing stopped.")
            else:
                self._rail_zeroed = False
                self._rail_caption("Homed.")
        elif flags.get("limit"):
            self._rail_caption("Stopped at the rail's limit.")
        self._refresh_rail()

    def _on_rail_error(self, text):
        self._rail_caption(f"The rail said: {text}")

    def autofocus(self, then=None):
        """Start the sweep (spec/focus-rail.md §6), or stop it. `then(result)`
        is called when it ends (autofocus before a shot). True when it started."""
        if self._af is not None and self._af.running:
            self.rail_stop()
            return False
        if then is None and self._af_shot is not None:
            self._af_shot_cancel()               # the button while it focuses for a shot
            return False
        if not self._rail_can_move("Autofocus", shot=then is not None):
            return False
        if self.rail.moving():
            self._rail_caption("Wait for the rail to stop.")
            return False
        pos = self.rail.position()
        if pos is None:
            self.rail.ask_status()
            self._rail_caption("The rail hasn't said where it is yet: try again.")
            return False
        if time.monotonic() - self._last_frame_t > 2.0:
            self._rail_caption("Autofocus needs the live view: no picture is coming in.")
            return False
        if not self.actions["sharp"].isChecked():
            self.actions["sharp"].setChecked(True)       # it reads the sharpness meter
        info, st = self.rail.info, self.rail.status
        limits = None
        if st.get("homed") and (info.get("min") is not None or info.get("max") is not None):
            limits = (info.get("min"), info.get("max"))
        self._af = raf.Autofocus(self.rail.goto, self._rail_af_opts,
                                 steps_per_mm=self.rail.steps_per_mm(), limits=limits,
                                 on_progress=self._af_progress,
                                 on_finished=self._af_finished)
        self._af_tip = ""
        if self._cam_zoom_action.isVisible() and not self.magnified():
            self._af_tip = "  Reads best at Camera zoom 8× (Z)."
        self._new_frame_quiet = float("inf")          # a sweep changes the picture
        self._used()
        self._rail_caption("Autofocus: starting…" + self._af_tip)
        self._af_then = then
        self._af_timer.start()
        self._af.start(pos)
        self._refresh_rail()
        return True

    def _af_progress(self, text):
        self._used()
        self._rail_caption(text + getattr(self, "_af_tip", ""))

    def _af_finished(self, result):
        self._af_timer.stop()
        self._new_frame_quiet = time.monotonic() + 1.0
        self._rail_caption(result.message)
        self._note_caption(result.message)
        self._refresh_rail()
        then, self._af_then = self._af_then, None
        if then is not None:
            then(result)

    def _rail_jog_menu(self, pos):
        pop = OptionsPopup("Jog steps", [
            ("fine_um", "Fine", RAIL_JOG_LIMITS["fine_um"], lambda v: f"{v} µm",
             "[ and ] and the inner buttons"),
            ("coarse_um", "Coarse", RAIL_JOG_LIMITS["coarse_um"], lambda v: f"{v} µm",
             "{ and } and the outer buttons")],
            self._rail_jog, RAIL_JOG_DEFAULT, self)
        pop.changed.connect(self._on_rail_jog_opts)
        self._place(pop, pos)

    def _on_rail_jog_opts(self, v):
        self._rail_jog = lf.clean_options(v, RAIL_JOG_DEFAULT, RAIL_JOG_LIMITS)
        _settings().setValue("live/rail_jog", json.dumps(self._rail_jog))
        self._refresh_rail()

    def _rail_af_menu(self, pos):
        L = raf.LIMITS
        spm, spr = self.rail.steps_per_mm(), self.rail.steps_per_rev()

        def range_text(v):
            turn = orp.turn_text(v, spm, spr)
            return f"±{v / 1000:.2f} mm" + (f" · {turn}" if turn else "")
        pop = OptionsPopup("Autofocus", [
            ("range_um", "Range", L["range_um"], range_text,
             "How far either side of where it starts the coarse sweep looks"),
            ("coarse_um", "Coarse", L["coarse_um"], lambda v: f"{v} µm",
             "The coarse sweep's step: about half the depth of field"),
            ("fine_um", "Fine", L["fine_um"], lambda v: f"{v} µm",
             "The fine sweep's step, round the coarse best"),
            ("backlash_um", "Backlash", L["backlash_um"], lambda v: f"{v} µm",
             "The rail's play: every position is reached moving up, from at least "
             "this far below when it has to go down"),
            ("settle_ms", "Settle", L["settle_ms"], lambda v: f"{v} ms",
             "Wait after each move before reading (vibration, and frames already on "
             "their way)"),
            ("readings", "Readings", L["readings"], lambda v: str(v),
             "Readings per position (their median counts)")],
            self._rail_af_opts, raf.DEFAULTS, self,
            value_w=104 if orp.turn_text(1000, spm, spr) else 56)
        pop.changed.connect(self._on_rail_af_opts)
        self._place(pop, pos)

    def _on_rail_af_opts(self, v):
        self._rail_af_opts = raf.clean_options(v)
        _settings().setValue("live/rail_af", json.dumps(self._rail_af_opts))

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Resize and obj is getattr(self, "scroll", None):
            if getattr(self, "wake_overlay", None) is not None:
                self._place_wake_overlay()
            return False
        if t in (QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel):
            try:
                if obj.isWidgetType() and obj.window() is self:
                    self._used()
            except RuntimeError:
                pass
        return False

    def _used(self):
        # Use only counts towards the idle time: once the light is off for
        # being idle, Wake (and nothing else) turns it back on.
        self._last_use = time.monotonic()

    IDLE_DEFAULT_MIN = 8

    def _idle_minutes(self):
        st = _settings()
        try:
            # the old default of 5 (written whenever Settings closed with
            # Done) moves to 8 once: the user asked for 8 (2026-10-31)
            if not st.value("live/idle_default_8", False, type=bool):
                if int(st.value("live/idle_minutes", self.IDLE_DEFAULT_MIN)) == 5:
                    st.setValue("live/idle_minutes", self.IDLE_DEFAULT_MIN)
                st.setValue("live/idle_default_8", True)
            return max(0, int(st.value("live/idle_minutes", self.IDLE_DEFAULT_MIN)))
        except (TypeError, ValueError):
            return self.IDLE_DEFAULT_MIN

    def _camera_keys(self):
        """(the feed's source, a USB camera open as the shutter only)."""
        feed_key = getattr(self.feed, "index", None)
        usb_key = None
        if self.usb.is_open():
            usb_key = getattr(self.usb, "key", None)
            if usb_key == feed_key:
                usb_key = None                       # the feed reopens it
        return feed_key, usb_key

    def _check_idle(self):
        mins = self._idle_minutes()
        if (self._sleeping is not None or mins <= 0 or self.sequence.running
                or self._measure is not None):
            return
        lit = self.light.is_open() and any(self._levels.values())
        feed_key, usb_key = self._camera_keys()
        camera = feed_key is not None or usb_key is not None or self.usb.is_open()
        if not (lit or camera):
            return
        if time.monotonic() - self._last_use < mins * 60:
            return
        self._go_to_sleep(lit, camera, feed_key, usb_key,
                          f"after {mins} min without use")

    def sleep_for_quit(self):
        """The app's Quit? box is up: the light off and the camera let go
        while it asks (the user, 2026-11-01); True if anything was put to
        sleep, so `wake_after_quit` can put it back on No."""
        if self._closing or self._sleeping is not None:
            return False
        if self.sequence.running or self._measure is not None:
            return False                              # a shot in flight finishes first
        lit = self.light.is_open() and any(self._levels.values())
        feed_key, usb_key = self._camera_keys()
        camera = feed_key is not None or usb_key is not None or self.usb.is_open()
        if not (lit or camera):
            return False
        self._go_to_sleep(lit, camera, feed_key, usb_key, "while FreeCCR asked to quit")
        return True

    def wake_after_quit(self):
        self._wake()

    def _go_to_sleep(self, lit, camera, feed_key, usb_key, why):
        # Asleep: the light off and the camera let go (spec/live-capture-monitor.md,
        # revision 2026-10-31); Wake puts back exactly this
        self._sleeping = dict(self._levels)
        self._sleep_state = {"lit": lit, "feed": feed_key, "usb": usb_key}
        if lit:
            self.light.set_levels(dict(cl.OFF))       # off, not dimmed: spares the LEDs
        if camera:
            if feed_key is not None:
                self.feed.close_camera()
            if self.usb.is_open():
                self.usb.close()
            self.canvas.set_message("Asleep: Wake reconnects the camera.")
        what = ("The Scanlight turned off and the camera was let go" if lit and camera
                else "The Scanlight turned off" if lit else "The camera was let go")
        back = ("puts them back" if lit and camera
                else "puts it back at the levels it had" if lit else "reconnects it")
        self.wake_note.setText(f"{what} {why}. Wake {back}.")
        self._place_wake_overlay()
        self.wake_overlay.setVisible(True)
        self._refresh_light_status()

    def _wake(self):
        if self._sleeping is None:
            return
        state = getattr(self, "_sleep_state", None) or {"lit": True}
        self._sleeping = None
        self._sleep_state = None
        self.wake_overlay.setVisible(False)
        self._last_use = time.monotonic()
        if state.get("lit"):
            self.light.set_levels(self._levels)
        if state.get("feed") is not None:
            self.canvas.set_message("Reconnecting the camera…")
            self.feed.open_camera(state["feed"], self._video_format())
        if state.get("usb"):
            self.usb.open(state["usb"])               # the shutter-only camera
        self._refresh_light_status()

    # ------------------------------------------------------------------ #
    # view state
    # ------------------------------------------------------------------ #
    def _load_view_state(self):
        st = _settings()
        self.canvas.set_guide(_json_setting("live/guide", dict(GUIDE_DEFAULT)))
        self._sync_format_combo()
        try:
            self.canvas.align_x = min(1.0, max(0.0, float(st.value("live/align_x", 0.15))))
        except (TypeError, ValueError):
            pass
        area = _json_setting("live/sharp_area", None)
        if isinstance(area, list) and len(area) == 4:
            self.canvas.sharp_area = tuple(float(v) for v in area)
        hidden = st.value("live/panel_hidden", False, type=bool)
        if hidden:
            self.panel_action.setChecked(False)

    def _save_view_state(self):
        st = _settings()
        st.setValue("live/guide", json.dumps(self.canvas.guide))
        st.setValue("live/align_x", float(self.canvas.align_x))
        st.setValue("live/sharp_area", json.dumps(list(self.canvas.sharp_area))
                    if self.canvas.sharp_area else "")


class _UsbShutter:
    """The capture sequence's camera shutter: a USB camera, fired by the
    worker; its file landing calls the sequence's frame_done."""

    def __init__(self, win):
        self.win = win

    def fire(self, exposure=None):
        name = self.win._shot_file_stem(self.win.sequence.channel)
        if name:
            self.win.usb.fire(exposure or None, name=name)
        elif exposure:
            self.win.usb.fire(exposure)
        else:
            self.win.usb.fire()


class _SequenceLight:
    """What the capture sequence drives: the light, with the panel kept in
    step (the sliders show the colour being shot)."""

    def __init__(self, win):
        self.win = win

    def is_open(self):
        return self.win.light.is_open()

    def set_levels(self, lv):
        self.win.set_levels(lv)

    def focus(self, on):
        self.win.light.focus(on)

    def pulse(self, ms):
        self.win.light.pulse(ms)
