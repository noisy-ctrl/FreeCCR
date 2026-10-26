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
import sys
import time

from PySide6.QtCore import Qt, QTimer, Signal, QSettings, QEvent, QPointF, QRectF
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QPainter, QPen, QColor, QFont
from PySide6.QtWidgets import (QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel,
                               QPushButton, QComboBox, QToolBar, QCheckBox, QSizePolicy,
                               QScrollArea, QSlider, QMenu, QInputDialog, QMessageBox,
                               QFileDialog, QApplication)

from core import camera_backends as cb
from core import capture_light as cl
from core import capture_sequence as cs
from core import light_presets as lp
from core import live_feed as lf
from core import live_negative as ln
from ui import theme
from widgets.histogram_widget import HistogramWidget
from widgets.live_canvas import LiveCanvas, clean_guide, GUIDE_DEFAULT
from widgets.sliders_panel import CollapsibleSection, GradientSlider

CHANNEL_COLOURS = {"r": theme.CH_R, "g": theme.CH_G, "b": theme.CH_B,
                   "w": "#e8e8e8", "ir": "#b06a7e"}
RING_KEYS = (("r", "R", "1"), ("g", "G", "2"), ("b", "B", "3"), ("w", "W", "4"))
LED_VALUE_W = 52
IDLE_CHECK_MS = 10_000
BREATH_MS = 100
BREATH_LOW, BREATH_HIGH, BREATH_PERIOD_S = 0.05, 0.25, 4.0
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

    def __init__(self, title, rows, values, defaults, parent=None, step=1):
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
            val.setFixedWidth(56)
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
            pen = QPen(QColor(255, 255, 255), 4)
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

    def __init__(self, main_window=None, feed=None, light=None, usb=None, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Live Capture Monitor")
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self._mw = main_window
        self._closing = False
        self._asked_tether = False
        self._started = False
        self._levels = dict(cl.OFF)
        self._sleeping = None            # the levels to restore while dimmed
        self._breath_t0 = 0.0
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
        self._addon_warned = set()       # cameras warned about this session

        st = _settings()
        self._toggles = _json_setting("live/toggles", {"denoise": True})
        self._denoise_opts = lf.clean_options(_json_setting("live/denoise", {}),
                                              lf.DENOISE_DEFAULT, lf.DENOISE_LIMITS)
        self._edge_opts = lf.clean_options(_json_setting("live/edge_amp", {}),
                                           lf.EDGE_DEFAULT, lf.EDGE_LIMITS)
        self._timing = cs.clean_timing(_json_setting("live/timing", {}))
        self._presets = lp.normalise(st.value("live/presets", ""))

        self.feed = feed if feed is not None else lf.LiveFeed()
        if light is None:
            from core.light_link import LightClient
            light = LightClient()
        self.light = light
        if usb is None:
            from core.usb_camera import UsbCamera
            usb = UsbCamera()
        self.usb = usb
        if hasattr(self.feed, "sources"):
            self.feed.sources["usb"] = self.usb.frame_source
        self.sequence = cs.CaptureSequence(_SequenceLight(self), on_step=self._on_step,
                                           on_finished=self._on_sequence_finished)
        self._usb_shutter = _UsbShutter(self)

        self._build()
        self._load_view_state()
        self._wire()
        self._apply_toggles()
        self._refresh_presets()
        self._apply_scan_type(self.scan_type())
        self._on_ports(self.light.ports)          # the link may have reported already
        self._refresh_light_status()
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
        toggle("negative", "Negative",
               "A rough positive of the white-lit negative, for judging the "
               "frame (FreeCCR converts the RAW). Its controls are in the "
               "panel under Negative Preview.")
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.zoom_combo = QComboBox()
        for label, key in (("Fit", "fit"), ("Fit Guide", "guide"), ("1:1", "1:1"), ("2:1", "2:1")):
            self.zoom_combo.addItem(label, key)
        self.zoom_combo.setToolTip("Zoom. The wheel zooms about the pointer, drag pans, "
                                   "double-click switches Fit / 1:1 (F).")
        self.zoom_combo.activated.connect(lambda i: self.canvas.zoom(self.zoom_combo.itemData(i)))
        tb.addWidget(self.zoom_combo)
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
        self.cam_zoom_combo = QComboBox()
        self.cam_zoom_combo.setToolTip("The camera's own live view magnification, "
                                       "for focusing")
        self.cam_zoom_combo.setVisible(False)
        r.addWidget(self.cam_zoom_combo)
        self.release_btn = QPushButton("Release Camera")
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
        for k in cl.CHANNELS:
            row = QHBoxLayout()
            row.setSpacing(theme.GAP_TIGHT)
            lab = QLabel(cl.NAMES[k])
            lab.setFixedWidth(theme.LABEL_COL_W)
            lab.setFixedHeight(theme.CONTROL_H)
            lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            s = GradientSlider(("#262626", CHANNEL_COLOURS[k]), Qt.Horizontal)
            s.setRange(0, 255)
            s.reset_value = 0
            s.setFixedHeight(theme.CONTROL_H)
            s.setToolTip(f"{cl.NAMES[k]} LED. Double-click: off.")
            v = QLabel("0.0 %")
            v.setFixedWidth(LED_VALUE_W)
            v.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            s.valueChanged.connect(lambda val, ch=k: self._on_led_slider(ch, val))
            row.addWidget(lab)
            row.addWidget(s, 1)
            row.addWidget(v)
            lay.addLayout(row)
            self.led_sliders[k], self.led_values[k] = s, v
        lay.addWidget(_muted("Red, Green and Blue turn White and Infrared off (and the "
                             "other way round): the light can't run both groups."))
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
        self.shoot_btn = QPushButton("Shoot   (S)")
        self.shoot_btn.setFixedHeight(theme.CONTROL_H_LG)
        theme.style_button(self.shoot_btn, "primary")
        lay.addWidget(self.shoot_btn)
        self.capture_caption = _muted("")
        lay.addWidget(self.capture_caption)

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

    def _wire(self):
        c = self.canvas
        c.view_changed.connect(self._push_params)
        c.picked.connect(self._on_picked)
        c.area_drawn.connect(lambda a: (self._push_params(), self._save_view_state()))
        c.line_placed.connect(lambda x: self._save_view_state())
        c.guide_changed.connect(lambda g: self._save_view_state())
        c.pointer_moved.connect(self._used)
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
        self.usb.fire_failed.connect(self._on_fire_failed)
        self.shutter_combo.activated.connect(self._on_shutter_chosen)
        self.cam_zoom_combo.activated.connect(
            lambda i: self.usb.set_zoom(self.cam_zoom_combo.itemData(i)))
        self.release_btn.clicked.connect(self._on_release_camera)
        self.light.state_changed.connect(self._on_light_state)
        self.light.telemetry_changed.connect(lambda t: self._refresh_light_status())
        self.light.ports_changed.connect(self._on_ports)
        self.rescan_btn.clicked.connect(self.light.rescan)
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        self.preset_combo.customContextMenuRequested.connect(self._preset_menu)
        self.save_as_btn.clicked.connect(self._on_save_as)
        self.save_btn.clicked.connect(self._on_save)
        self.shoot_btn.clicked.connect(self.shoot)
        self.timing_btn.clicked.connect(self._timing_popup)
        self.scan_combo.activated.connect(self._on_scan_type_chosen)
        self.tether_btn.clicked.connect(self._on_tether_button)
        for key, slot in (("1", lambda: self.solo("r")), ("2", lambda: self.solo("g")),
                          ("3", lambda: self.solo("b")), ("4", lambda: self.solo("w")),
                          ("S", self.shoot), ("Esc", self._on_escape),
                          ("F", lambda: self.canvas.zoom("1:1" if self.canvas.is_fit() else "fit"))):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(slot)
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(IDLE_CHECK_MS)
        self._idle_timer.timeout.connect(self._check_idle)
        self._idle_timer.start()
        self._breath_timer = QTimer(self)
        self._breath_timer.setInterval(BREATH_MS)
        self._breath_timer.timeout.connect(self._breathe)
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
        if port:
            self.light.set_auto_port(port)
            self.light.rescan()                   # connect now, not on the next 3 s scan
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
        for t in (self._idle_timer, self._breath_timer, self._status_timer, self._ring_timer):
            t.stop()
        if self.sequence.running:
            self.sequence.on_finished = None
            self.sequence.stop()
        self._save_view_state()
        st = _settings()
        st.setValue("live/geometry", self.saveGeometry())
        try:
            self.light.shutdown(light_off=True)
        except Exception as ex:
            print(f"live monitor: light shutdown: {ex}")
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
        if self._closing or self._mw is None:
            return
        if not _settings().value("live/ask_tether", True, type=bool):
            return
        active, label, count = self._tether_state()
        choice, dont_ask = self._hot_folder_choice(active, label, count)
        if dont_ask:
            _settings().setValue("live/ask_tether", False)
        if choice == "start":
            if active:
                self._mw.stop_tethering()
            self._mw.enter_tethering()
        self._refresh_status_strip()

    def _hot_folder_choice(self, active, label, count):
        """("start" | "keep", don't-ask-again). The test seam."""
        box = QMessageBox(self)
        box.setWindowTitle("Hot Folder Tether")
        if active:
            box.setText(f"Hot Folder Tether is watching “{label}” ({count} captured).")
            box.setInformativeText("Close that session and start a new one for this scan?")
            start = box.addButton("Start New…", QMessageBox.AcceptRole)
            keep = box.addButton("Keep Current", QMessageBox.RejectRole)
        else:
            box.setText("Turn on Hot Folder Tether?")
            from core import scan_type as st
            box.setInformativeText(
                "The frames your camera's tether app saves into a folder are loaded "
                f"into FreeCCR as you shoot.\n\nScanning: {st.LABELS[st.clean(self.scan_type())]} "
                "(change it under Capture).")
            start = box.addButton("Turn On…", QMessageBox.AcceptRole)
            keep = box.addButton("Not Now", QMessageBox.RejectRole)
        box.setDefaultButton(start)
        cb = QCheckBox("Don't ask when the monitor opens")
        box.setCheckBox(cb)
        box.exec()
        return ("start" if box.clickedButton() is start else "keep"), cb.isChecked()

    def _on_tether_button(self):
        if self._mw is None:
            return
        self._mw.toggle_tethering()
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

    def _set_guide(self, g):
        if not self.actions["guide"].isChecked():
            self.actions["guide"].setChecked(True)
        self.canvas.set_guide(g)
        self._save_view_state()

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
            return
        if self.sequence.running:
            self.sequence.stop()

    # ------------------------------------------------------------------ #
    # the feed
    # ------------------------------------------------------------------ #
    def _push_params(self, *_):
        if self._closing:
            return
        w, h = self.canvas.device_size()
        neg = self._neg_tables if (self.actions["negative"].isChecked()
                                   and self.actions["negative"].isEnabled()) else None
        self.feed.set_params(lf.ViewParams(
            canvas_w=w, canvas_h=h, scale=self.canvas.scale, cx=self.canvas.cx, cy=self.canvas.cy,
            denoise=self.actions["denoise"].isChecked(), denoise_opts=dict(self._denoise_opts),
            edge=self.actions["edge"].isChecked(), edge_opts=dict(self._edge_opts),
            sharp=self.actions["sharp"].isChecked(), sharp_area=self.canvas.sharp_area,
            false_colour=self.actions["fc"].isChecked(), negative=neg,
            bw=self.neg_bw.isChecked(), clip=self._clip_kind, reset_best=self._reset_best))
        if self.canvas.is_fit():
            self.zoom_combo.setCurrentIndex(0)

    def _on_frame(self):
        r = self.feed.take()
        if r is None or self._closing:
            return
        self.canvas.set_result(r)
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
        self._rebuild_camera_combo()
        keys = [k for k, _l in self._usb_found]
        if self._want_key in keys:
            self.feed.open_camera(self._want_key, self._video_format())
        elif self._want_key is not None:
            # the remembered USB camera isn't there: fall back to a device
            self._want_key = None
            if self._cv_found and getattr(self.feed, "index", None) is None:
                self.feed.open_camera(int(self._cv_found[0][0]), self._video_format())
            elif not self._cv_found:
                self._on_feed_status("none", "")

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
            _settings().setValue("live/camera_key", key)
            _settings().setValue("live/shutter", "usb")    # its own shutter too
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
            else:
                try:
                    _settings().setValue("live/camera_index", int(text))
                    _settings().setValue("live/camera_key", "")
                except (TypeError, ValueError):
                    pass
            self.canvas.set_message("Waiting for the first frame…")
        elif kind == "none":
            self.canvas.set_message(
                "No camera found.\nCheck the capture device is plugged in and the camera "
                "is sending its HDMI picture, then Find Cameras.\nOn a Mac, the app that "
                "runs FreeCCR also needs camera access: System Settings → Privacy & "
                "Security → Camera.")
        elif kind == "lost":
            self.canvas.set_message("The camera stopped sending frames.\nFind Cameras to "
                                    "reconnect.")
        self._refresh_status_strip()

    def _refresh_status_strip(self):
        if self._closing:
            return
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
        if folder != self._usb_dest:
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
        if key == "auto":
            self._neg.update(base=None, hi=None, mid=None)
            self._rebuild_negative(force_auto=True)
            return
        self._pick_target = key
        self.canvas.set_mode("pick")

    def _on_picked(self, sx, sy):
        target, self._pick_target = self._pick_target, None
        if target is None:
            return
        bgr = self.feed.sample(sx, sy)
        if bgr is None:
            return
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

    def _on_light_state(self, state, port, detail):
        if state in ("ok", "waiting") and port:
            _settings().setValue("live/port", port)
            self.light.set_auto_port(port)
            i = self.port_combo.findText(port)
            if i >= 0:
                self.port_combo.setCurrentIndex(i)
        was_open = getattr(self, "_prev_light", "off") in ("ok", "waiting", "silent")
        if state == "waiting" and not was_open and any(self._levels.values()):
            # levels set before connecting go to the light; an untouched
            # panel (all 0) leaves the light as it is
            self.light.set_levels(self._levels)
        self._prev_light = state
        self._refresh_light_status()

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
            bits = ["Responding"]
            if "vbus" in t:
                bits.append(f"{t['vbus']:.1f} V")
            if "temp" in t:
                bits.append(f"{t['temp']:.0f} °C")
            text = "  ·  ".join(bits)
        elif state == "off":
            text = "Not connected"
        else:
            text = detail or state
        if self._sleeping is not None:
            text += "  ·  dimmed while idle"
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
        if self.sequence.running:
            return
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
    def shoot(self):
        if self.sequence.running:
            self.sequence.stop()
            return
        tri = self.scan_type() == "trichrome"
        self._used()
        if self._shutter_is_usb():
            if tri and not self.light.is_open():
                self._warn_caption("Trichrome needs the Scanlight connected: it sets "
                                   "the red, green and blue.")
                return
            if not self._ensure_hot_folder():
                self._warn_caption("Shooting over USB brings the files in through Hot "
                                   "Folder Tether: turn it on to shoot.")
                return
            started = self.sequence.start(self._timing, self._preset(), tri,
                                          shutter=self._usb_shutter)
        else:
            if not self.light.is_open():
                return
            started = self.sequence.start(self._timing, self._preset(), tri)
        if started:
            self._await_exposure = True
            self.shoot_btn.setText("Stop   (Esc)")
            self._refresh_capture_caption()

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
        self._ring_anim = None
        self._ring_timer.stop()
        for ring in self.rings.values():
            ring.set_progress(None)
        self.shoot_btn.setText("Shoot   (S)")
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
    # USB camera (spec/capture-routes.md)
    # ------------------------------------------------------------------ #
    def _warn_caption(self, text):
        self.capture_caption.setStyleSheet(
            f"color: {theme.WARN_TEXT}; font-size: {theme.FS_CAPTION}px;")
        self.capture_caption.setText(text)

    def _shutter_is_usb(self):
        return self.shutter_combo.currentData() == "usb" and self.usb.is_open()

    def _refresh_shoot_enabled(self):
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
            choice, _dont = self._hot_folder_choice(False, label, count)
            if choice == "start":
                self._mw.enter_tethering()
            active, label, count = self._tether_state()
        if active:
            self._usb_dest = self._tether_folder()
            self.usb.set_dest(self._usb_dest)
        self._refresh_status_strip()
        return active

    def _rebuild_shutter_combo(self):
        want = str(_settings().value("live/shutter", "scanlight") or "scanlight")
        self.shutter_combo.blockSignals(True)
        self.shutter_combo.clear()
        self.shutter_combo.addItem("Scanlight", "scanlight")
        if self.usb.is_open():
            self.shutter_combo.addItem(f"USB · {self._usb_model or 'camera'}", "usb")
        i = self.shutter_combo.findData(want)
        self.shutter_combo.setCurrentIndex(i if i >= 0 else 0)
        self.shutter_combo.blockSignals(False)
        self._refresh_shoot_enabled()
        self._refresh_capture_caption()

    def _on_shutter_chosen(self, i):
        _settings().setValue("live/shutter", self.shutter_combo.itemData(i))
        self._refresh_shoot_enabled()
        self._refresh_capture_caption()

    def _on_usb_state(self, kind, text):
        self.release_btn.setVisible(kind == "busy" and sys.platform == "darwin")
        if kind == "open":
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
            self.canvas.set_message(
                "macOS is holding the camera, so FreeCCR can't open it.\nRelease "
                "Camera ends that hold (it may come back when the camera is replugged)."
                if sys.platform == "darwin" else
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
            self._exposure = {}
            self.cam_zoom_combo.setVisible(False)
        self._rebuild_shutter_combo()
        if kind == "lost" and self.sequence.running and self.sequence.camera_shutter:
            self.sequence.abort("camera lost")      # after the rebuild: its caption stays
        self._refresh_status_strip()

    def _on_usb_file(self, path):
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
        self.sequence.fire_failed(text)
        if not self.sequence.running:
            self._warn_caption(text)

    def _on_exposure(self, exp):
        self._exposure = dict(exp or {})
        self._refresh_status_strip()

    def _on_zoom_levels(self, levels):
        self.cam_zoom_combo.clear()
        for lv in levels or []:
            self.cam_zoom_combo.addItem(f"{lv}×", lv)
        self.cam_zoom_combo.setVisible(bool(levels))

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
        box.setWindowTitle("Release Camera")
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
    # idle dimming
    # ------------------------------------------------------------------ #
    def eventFilter(self, obj, ev):
        t = ev.type()
        if t in (QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel):
            try:
                if obj.isWidgetType() and obj.window() is self:
                    self._used()
            except RuntimeError:
                pass
        return False

    def _used(self):
        self._last_use = time.monotonic()
        if self._sleeping is not None:
            self._wake()

    def _idle_minutes(self):
        try:
            return max(0, int(_settings().value("live/idle_minutes", 5)))
        except (TypeError, ValueError):
            return 5

    def _check_idle(self):
        mins = self._idle_minutes()
        if (self._sleeping is not None or mins <= 0 or self.sequence.running
                or not self.light.is_open() or not any(self._levels.values())):
            return
        if time.monotonic() - self._last_use >= mins * 60:
            self._sleeping = dict(self._levels)
            self._breath_t0 = time.monotonic()
            self._breath_timer.start()
            self._refresh_light_status()

    def _breathe(self):
        if self._sleeping is None:
            self._breath_timer.stop()
            return
        phase = ((time.monotonic() - self._breath_t0) % BREATH_PERIOD_S) / BREATH_PERIOD_S
        k = BREATH_LOW + (BREATH_HIGH - BREATH_LOW) * 0.5 * (1 - math.cos(2 * math.pi * phase))
        lv = {c: (int(round(255 * k)) if v > 0 else 0) for c, v in self._sleeping.items()}
        self.light.set_levels(lv)

    def _wake(self):
        if self._sleeping is None:
            return
        self._sleeping = None
        self._breath_timer.stop()
        self.light.set_levels(self._levels)
        self._refresh_light_status()

    # ------------------------------------------------------------------ #
    # view state
    # ------------------------------------------------------------------ #
    def _load_view_state(self):
        st = _settings()
        self.canvas.set_guide(_json_setting("live/guide", dict(GUIDE_DEFAULT)))
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

    def fire(self):
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
