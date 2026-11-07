#!/usr/bin/env python3
"""The Live Capture Monitor window (spec/live-capture-monitor.md): opening
from the File menu, the Hot Folder Tether prompt, the LED panel, presets and
keys, a trichrome Shoot on a fake Scanlight, idle dimming, the Negative
preview, and closing (light off, port and camera released)."""

import os
import sys
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtCore import QObject, Signal, QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QSpinBox  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import capture_light as cl  # noqa: E402
from core import capture_sequence as cs  # noqa: E402
from core import live_feed as lf  # noqa: E402
from core.light_link import LightLink, LightClient  # noqa: E402
from widgets import live_monitor as lm  # noqa: E402
from test_live_capture_light import FakeDevice, FakeSerial  # noqa: E402


@pytest.fixture(autouse=True)
def iso_settings(tmp_path, monkeypatch):
    path = str(tmp_path / "live.ini")
    monkeypatch.setattr(lm, "_settings", lambda: QSettings(path, QSettings.IniFormat))
    from core.ccr_backend import ccr_backend
    monkeypatch.setattr(ccr_backend, "scan_type", "colour", raising=False)
    return path


class FakeFeed(QObject):
    frame_ready = Signal()
    cameras_found = Signal(list)
    status = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.params = None
        self.log = []
        self.running = False
        self.result = None
        self.full = None
        self.samples = {}
        self.index, self.size, self.fourcc = 1, (1920, 1080), ""

    def set_params(self, p):
        self.params = p

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def open_camera(self, i, fmt="auto"):
        self.log.append(("open", i, fmt))

    def close_camera(self):
        self.log.append(("close",))

    def find_cameras(self, pref=None, fmt="auto", open_found=True):
        self.log.append(("find", pref, fmt))

    def take(self):
        r, self.result = self.result, None
        return r

    def sample(self, sx, sy):
        return self.samples.get("any")

    def last_full_frame(self):
        return self.full

    def stop(self):
        self.running = False
        self.log.append(("stop",))

    def wait(self, ms=0):
        return True


def _light():
    dev = FakeDevice()
    made = []

    def factory():
        s = FakeSerial(dev)
        made.append(s)
        return s
    link = LightLink(serial_factory=factory, port_lister=lambda: ["/dev/cu.usbmodem1"],
                     sleep=lambda s: None, available=True)
    return LightClient(link=link, threaded=False), dev, made


def _monitor(mw=None, connect=True, usb=None):
    from core.usb_camera import UsbCamera
    feed = FakeFeed()
    light, dev, made = _light()
    mon = lm.LiveCaptureMonitor(main_window=mw, feed=feed, light=light,
                                usb=usb if usb is not None else UsbCamera(backends=[]))
    mon._start_hot_folder = lambda from_monitor=False: None
    mon.resize(1300, 820)
    mon.show()
    _app.processEvents()
    if connect:
        light.open_port("/dev/cu.usbmodem1")
        light.link.tick()
    return mon, feed, light, dev


def _close(mon):
    mon.close()
    _app.processEvents()


def test_led_groups_exclude_and_reach_the_light():
    mon, feed, light, dev = _monitor()
    try:
        assert light.is_open() and light.state == "ok"
        dev.written.clear()
        mon.led_sliders["w"].setValue(200)
        assert dev.written[-1] == cl.colour_packet({"w": 200})
        assert mon.actions["negative"].isEnabled()
        light.link._last_colour = -1e9
        mon.led_sliders["g"].setValue(100)
        assert mon.led_sliders["w"].value() == 0           # the other group goes off
        assert not mon.actions["negative"].isEnabled()     # no white: no positive
        assert mon.led_values["g"].text() == "39.2 %"
        # Infrared runs alone: up, everything else off; White up turns it off
        mon.led_sliders["w"].setValue(120)
        mon.led_sliders["ir"].setValue(80)
        assert mon._levels == {"r": 0, "g": 0, "b": 0, "w": 0, "ir": 80}
        assert mon.led_sliders["w"].value() == 0
        mon.led_sliders["w"].setValue(60)
        assert mon.led_sliders["ir"].value() == 0
        # compact rows, no explanation line under them
        assert mon.led_sliders["r"].height() == lm.LED_ROW_H
    finally:
        _close(mon)


def test_solo_rings_and_keys_only_in_this_window_and_not_while_typing():
    mon, feed, light, dev = _monitor()
    try:
        pre = mon._preset()
        mon.rings["b"].click()
        assert mon._levels == cl.solo(pre, "b")
        mon.canvas.setFocus()
        QTest.keyClick(mon.canvas, Qt.Key_1)
        assert mon._levels == cl.solo(pre, "r")
        spin = QSpinBox(mon)
        spin.show()
        spin.setFocus()
        spin.selectAll()
        _app.processEvents()
        QTest.keyClick(spin, Qt.Key_2)
        assert mon._levels == cl.solo(pre, "r")            # the digit went into the box
        assert spin.value() == 2
    finally:
        _close(mon)


def test_shoot_runs_the_trichrome_sequence_on_the_light():
    mon, feed, light, dev = _monitor()
    try:
        mon._timing = cs.clean_timing({"focus_ms": 0, "pulse_ms": 10, "hold_ms": 0, "period_ms": 0})
        mon._on_scan_type_chosen(mon.scan_combo.findData("trichrome"))
        pre = mon._preset()
        dev.written.clear()
        QTest.keyClick(mon.canvas, Qt.Key_S)
        assert mon.sequence.running and mon.shoot_btn.text().startswith("Stop")
        t0 = time.time()
        while mon.sequence.running and time.time() - t0 < 5:
            _app.processEvents()
            light.link._last_colour = -1e9
            light.link.tick()
            time.sleep(0.005)
        assert not mon.sequence.running and mon.shoot_btn.text().startswith("Shoot")
        w = [p for p in dev.written if p != cl.fw_version_packet()]
        colours = [p for p in w if p[1] == cl.H2D_SET_COLOUR]
        assert colours[0] == cl.colour_packet(cl.solo(pre, "r"))
        assert cl.colour_packet(cl.solo(pre, "g")) in colours
        assert cl.colour_packet(cl.solo(pre, "b")) in colours
        assert colours[-1] == cl.colour_packet({"w": pre["w"]})
        assert sum(1 for p in w if p[1] == cl.H2D_SHUTTER) == 3
    finally:
        _close(mon)


def test_shoot_needs_the_light():
    mon, feed, light, dev = _monitor(connect=False)
    try:
        assert not mon.shoot_btn.isEnabled()
        mon.shoot()
        assert not mon.sequence.running
    finally:
        _close(mon)


def test_hold_shorter_than_the_exposure_is_flagged(monkeypatch):
    mon, feed, light, dev = _monitor()
    try:
        monkeypatch.setattr(cs, "exposure_seconds", lambda p: 0.25)
        mon._await_exposure = True
        mon._on_tether_captured("/x/DSCF0001.RAF")
        txt = mon.capture_caption.text()
        assert "200 ms" in txt and "1/4 s" in txt and "DSCF0001.RAF" in txt
        mon._on_tether_captured("/x/DSCF0002.RAF")          # only the first is checked
        assert "DSCF0001" in mon.capture_caption.text()
    finally:
        _close(mon)


def test_idle_turns_the_light_off_and_only_wake_turns_it_on(iso_settings):
    """Idle: the light goes OFF (not dimmed) and an overlay covers the whole
    control panel; clicks, keys, the 1-4 solos and Shoot don't light it;
    Wake puts back exactly the levels it had (spec §7.5)."""
    mon, feed, light, dev = _monitor()
    try:
        lm._settings().setValue("live/idle_minutes", 1)
        mon.led_sliders["w"].setValue(220)
        mon._last_use -= 61
        mon._check_idle()
        assert mon._sleeping == {"r": 0, "g": 0, "b": 0, "w": 220, "ir": 0}
        assert "off while idle" in mon.light_status.text()
        light.link._last_colour = -1e9
        light.link.tick()
        assert dev.written[-1] == cl.colour_packet(dict(cl.OFF))
        assert mon.wake_overlay.isVisible()
        assert mon.wake_overlay.geometry() == mon.scroll.rect()
        assert "1 min" in mon.wake_note.text()
        n = len(dev.written)
        mon._used()                                        # a click or key
        mon.solo("r")                                      # the 1-4 keys
        mon.shoot()                                        # S
        light.link._last_colour = -1e9
        light.link.tick()
        assert mon._sleeping is not None and not mon.sequence.running
        assert all(w == cl.colour_packet(dict(cl.OFF)) for w in dev.written[n:])
        mon.wake_btn.click()
        light.link._last_colour = -1e9
        light.link.tick()
        assert mon._sleeping is None and not mon.wake_overlay.isVisible()
        assert dev.written[-1] == cl.colour_packet({"w": 220})
        assert "off while idle" not in mon.light_status.text()
        lm._settings().setValue("live/idle_minutes", 0)       # never
        mon._last_use -= 10_000
        mon._check_idle()
        assert mon._sleeping is None
    finally:
        _close(mon)


def test_idle_lets_go_of_the_camera_and_wake_reconnects_it(iso_settings):
    """2026-10-31: the camera times out with the light, 8 minutes by default."""
    mon, feed, light, dev = _monitor()
    try:
        st = lm._settings()
        st.remove("live/idle_minutes")
        st.remove("live/idle_default_8")
        assert mon._idle_minutes() == 8
        st.setValue("live/idle_minutes", 5)               # the old default, saved
        st.remove("live/idle_default_8")
        assert mon._idle_minutes() == 8                   # moved to 8, once
        st.setValue("live/idle_minutes", 5)
        assert mon._idle_minutes() == 5                   # a 5 chosen afterwards stays
        st.setValue("live/idle_minutes", 1)
        # the light unlit, the camera connected: the camera alone sleeps
        mon.led_sliders["w"].setValue(0)
        feed.log.clear()
        mon._last_use -= 61
        mon._check_idle()
        assert mon._sleeping is not None and ("close",) in feed.log
        assert "camera was let go" in mon.wake_note.text()
        assert "off while idle" not in mon.light_status.text()
        assert "Wake reconnects" in mon.canvas.message
        mon.wake_btn.click()
        assert mon._sleeping is None and feed.log[-1][:2] == ("open", feed.index)
        # lit and connected: both, and both come back
        mon.led_sliders["w"].setValue(150)
        feed.log.clear()
        mon._last_use -= 61
        mon._check_idle()
        assert "turned off and the camera was let go" in mon.wake_note.text()
        assert ("close",) in feed.log
        mon.wake_btn.click()
        light.link._last_colour = -1e9
        light.link.tick()
        assert dev.written[-1] == cl.colour_packet({"w": 150})
        assert feed.log[-1][:2] == ("open", feed.index)
    finally:
        _close(mon)


def test_the_wake_overlay_follows_the_panel_size(iso_settings):
    mon, feed, light, dev = _monitor()
    try:
        lm._settings().setValue("live/idle_minutes", 1)
        mon.led_sliders["w"].setValue(100)
        mon._last_use -= 61
        mon._check_idle()
        mon.resize(mon.width(), mon.height() - 120)
        _app.processEvents()
        assert mon.wake_overlay.geometry() == mon.scroll.rect()
        assert mon.wake_btn.isVisible()
    finally:
        _close(mon)


def test_frames_histogram_and_the_negative_preview():
    mon, feed, light, dev = _monitor()
    try:
        frame = np.full((540, 960, 3), (62, 128, 214), np.uint8)
        frame[60:480, 80:880] = (25, 55, 110)
        feed.full = frame
        proc = lf.Processor()
        feed.result = proc.process(frame, lf.ViewParams(canvas_w=960, canvas_h=540))
        feed.frame_ready.emit()
        _app.processEvents()
        assert mon.canvas.result is not None
        assert mon.histogram._data is not None
        mon.led_sliders["w"].setValue(200)
        mon.actions["negative"].setChecked(True)
        assert mon._neg_tables is not None and feed.params.negative is not None
        assert "Film base" in mon.neg_note.text()
        # an eyedropper sample replaces the base
        feed.samples["any"] = (60.0, 125.0, 210.0)
        mon._on_neg_button("base")
        assert mon.canvas.mode == "pick"
        mon.canvas.picked.emit(100.0, 100.0)
        assert mon._neg["base"] == (60.0, 125.0, 210.0)
        mon.neg_sliders["temperature"].setValue(40)
        assert feed.params.negative is not None
        mon.led_sliders["w"].setValue(0)
        assert not mon.actions["negative"].isChecked() and feed.params.negative is None
    finally:
        _close(mon)


def test_the_light_map_is_idempotent_and_leaves_other_colours_alone():
    """Revision 2026-11-01 (3): recolouring a stylesheet twice = once, so the
    monitor can recolour every descendant after building."""
    from ui import theme as T
    assert not set(T.LIGHT_MAP.values()) & set(T.LIGHT_MAP)     # no light value is a key
    qss = (f"color: {T.TEXT_MUTED}; background: rgba(30, 30, 30, 248); "
           f"border: 1px solid {T.BORDER.upper()}; x: #333; y: {T.DANGER}; z: #123456")
    once = T.light_qss_text(qss)
    assert once == T.light_qss_text(once)
    assert "color: #555555" in once and "rgba(212, 212, 212, 248)" in once
    assert "#a8a8a8" in once and "x: #ececec" in once
    assert T.DANGER in once and "#123456" in once                 # meaning and others kept
    assert T.light_colour(T.TEXT) == "#111111" and T.light_colour("not a colour") == "not a colour"
    assert lm.theme.TEXT_MUTED == "#555555" and lm.theme.PANEL_W == T.PANEL_W


def test_the_monitor_is_light_grey_with_black_text():
    mon, feed, light, dev = _monitor()
    try:
        from PySide6.QtGui import QPalette
        from ui import theme as T
        t = T.LIGHT_TOKENS
        assert mon.palette().color(QPalette.Window).name() == t["window"]
        assert mon.palette().color(QPalette.WindowText).name() == t["text"]
        assert mon.panel.palette().color(QPalette.Window).name() == t["window"]
        assert t["surface"] in mon.styleSheet() and "QLabel, QCheckBox" in mon.styleSheet()
        # a section header made by another module: recoloured
        sec_qss = mon.rail_section._toggle_btn.styleSheet()
        assert T.SURFACE not in sec_qss and T.TEXT not in sec_qss
        # the primary button
        assert T.SURFACE_ACTIVE not in mon.shoot_btn.styleSheet()
        assert t["surface_active"] in mon.shoot_btn.styleSheet()
        # captions set at run time
        mon._warn_caption("careful")
        assert T.light_colour(T.WARN_TEXT) in mon.capture_caption.styleSheet()
        mon._note_caption("fine")
        assert t["text_muted"] in mon.capture_caption.styleSheet()
        # the picture's surround: a neutral mid grey, its message dark
        assert mon.canvas.surround.name() == "#808080"
        assert mon.canvas.message_colour.name() == t["text"]
        img = mon.canvas.grab().toImage()
        assert img.pixelColor(5, 5).name() == "#808080"
        # on screen: the panel reads light, its text dark
        import numpy as np
        mon.resize(1200, 900)
        mon.show()
        _app.processEvents()
        shot = mon.panel.grab().toImage().convertToFormat(img.Format.Format_RGB888)
        w, h = shot.width(), shot.height()
        arr = np.frombuffer(shot.constBits(), np.uint8, shot.bytesPerLine() * h)
        arr = arr.reshape(h, shot.bytesPerLine())[:, :w * 3].reshape(h, w, 3)
        assert np.median(arr) >= 200          # light (the dark scope is a small part)
        dark = (arr.max(axis=2) < 60).mean()
        assert dark > 0.002                          # black text is there
    finally:
        _close(mon)


def test_negative_preview_is_named_so_and_its_right_click_picks_colour_or_bw():
    mon, feed, light, dev = _monitor()
    try:
        a = mon.actions["negative"]
        assert a.text() == "Negative Preview"
        mon.led_sliders["w"].setValue(200)
        from PySide6.QtCore import QPoint
        mon._negative_menu(QPoint(10, 10))
        acts = mon._popup.actions()
        assert [x.text() for x in acts] == ["Colour Negative", "Black && White"]
        assert acts[0].isChecked() and not acts[1].isChecked()
        acts[1].trigger()
        assert mon.neg_bw.isChecked() and a.isChecked()     # picked: the preview comes on
        assert feed.params.bw
        mon._popup.close()
        mon._negative_menu(QPoint(10, 10))
        assert mon._popup.actions()[1].isChecked()
        mon._popup.actions()[0].trigger()
        assert not mon.neg_bw.isChecked() and not feed.params.bw
        mon._popup.close()
    finally:
        _close(mon)


def test_closing_turns_the_light_off_and_releases_everything():
    mon, feed, light, dev = _monitor()
    mon.led_sliders["w"].setValue(180)
    dev.written.clear()
    _close(mon)
    assert dev.written == [cl.colour_packet(cl.OFF)]
    assert not light.link.is_open()
    assert ("stop",) in feed.log


def test_the_quit_box_turns_the_light_off_and_lets_the_camera_go(iso_settings):
    """2026-11-01: while the app asks Quit?, Capture is asleep (light off,
    camera let go); No wakes it exactly as Wake does."""
    mon, feed, light, dev = _monitor()
    try:
        mon.led_sliders["w"].setValue(200)
        feed.log.clear()
        assert mon.sleep_for_quit()
        light.link._last_colour = -1e9
        light.link.tick()
        assert dev.written[-1] == cl.colour_packet(dict(cl.OFF))
        assert ("close",) in feed.log
        assert "while FreeCCR asked to quit" in mon.wake_note.text()
        assert not mon.sleep_for_quit()                    # already asleep
        mon.wake_after_quit()
        light.link._last_colour = -1e9
        light.link.tick()
        assert mon._sleeping is None and dev.written[-1] == cl.colour_packet({"w": 200})
        assert feed.log[-1][:2] == ("open", feed.index)
    finally:
        _close(mon)


def test_the_main_windows_quit_box_sleeps_capture_and_no_wakes_it():
    from ui.main_window import MainWindow
    from PySide6.QtWidgets import QMessageBox
    from PySide6.QtGui import QCloseEvent

    class Mon:
        def __init__(self):
            self.calls = []

        def sleep_for_quit(self):
            self.calls.append("sleep")
            return True

        def wake_after_quit(self):
            self.calls.append("wake")

    class Stub:
        _live_monitor = Mon()
        _confirm_quit = staticmethod(lambda: QMessageBox.No)
    stub = Stub()
    ev = QCloseEvent()
    MainWindow.closeEvent(stub, ev)
    assert stub._live_monitor.calls == ["sleep", "wake"] and not ev.isAccepted()


def test_the_format_list_sets_the_guides_format_and_follows_the_menu():
    """2026-11-01: "a 'Format' drop down window to the left of the Camera Zoom
    drop down. There you select format and it changes the frame guide"."""
    mon, feed, light, dev = _monitor()
    try:
        combo = mon.format_combo
        assert combo.itemText(0) == "Free" and combo.findData("645") > 0
        bar = [mon.toolbar.widgetForAction(a) for a in mon.toolbar.actions()]
        fmt_at = bar.index(mon.toolbar.widgetForAction(mon._format_action))
        assert fmt_at < bar.index(mon.toolbar.widgetForAction(mon._cam_zoom_action))
        mon.set_guide_auto(False)
        i = combo.findData("645")
        combo.activated.emit(i)
        g = mon.canvas.guide
        assert g["format"] == "645"
        geo = mon.canvas.geometry_()
        if geo is not None:
            wpx, hpx = g["w"] * geo.full_w, g["h"] * geo.full_h
            assert max(wpx, hpx) / min(wpx, hpx) == pytest.approx(56 / 41.5, rel=1e-3)
        mon._set_guide_format("6x6")                    # the guide menu's Format
        assert combo.currentData() == "6x6"
        mon._set_guide_format(None)
        assert combo.currentText() == "Free"
        # a right click on the outline opens the guide's menu
        from PySide6.QtCore import QPoint
        mon.canvas.guide_menu_requested.emit(QPoint(10, 10))
        assert mon._popup.actions()[0].text() == "Follow the Frame"
        mon._popup.close()
    finally:
        _close(mon)


def test_the_panel_fits_its_width():
    mon, feed, light, dev = _monitor()
    try:
        assert mon.panel.minimumSizeHint().width() <= lm.theme.PANEL_W
    finally:
        _close(mon)


def test_presets_save_as_and_persist(monkeypatch, iso_settings):
    mon, feed, light, dev = _monitor()
    try:
        from PySide6.QtWidgets import QInputDialog
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Portra", True))
        mon.led_sliders["r"].setValue(210)
        mon._on_save_as()
        assert mon.preset_combo.currentText() == "Portra"
        assert mon.rings["r"].value == 210
        stored = lm._settings().value("live/presets")
        assert "Portra" in stored
    finally:
        _close(mon)


# --------------------------------------------------------------------------- #
# with FreeCCR's main window
# --------------------------------------------------------------------------- #
class _MW(QObject):
    tether_captured = Signal(str)

    def __init__(self, active=False):
        super().__init__()
        self._tether_active = active
        self._tether_count = 7
        self.calls = []

    def _tether_folder_label(self):
        return "Roll 12"

    def stop_tethering(self):
        self.calls.append("stop")
        self._tether_active = False

    def enter_tethering(self, from_monitor=False):
        self.calls.append(("enter", from_monitor))
        self._tether_active = True
        return {"start": True, "dont_ask": False}

    def toggle_tethering(self):
        self.calls.append("toggle")


@pytest.mark.parametrize("active,want", [
    (False, [("enter", True)]),       # off: the one start window, from the monitor
    (True, [])])                      # on: nothing asked (the status strip shows it)
def test_hot_folder_on_opening(active, want):
    mw = _MW(active)
    mon, feed, light, dev = _monitor(mw)
    try:
        del mon._start_hot_folder                  # the real one: calls the main window
        mon.ask_hot_folder()
        assert mw.calls == want
    finally:
        _close(mon)


def test_dont_ask_again_is_remembered():
    mw = _MW()
    mon, feed, light, dev = _monitor(mw)
    try:
        asked = []
        mon._start_hot_folder = lambda from_monitor=False: (
            asked.append(from_monitor) or {"start": False, "dont_ask": True})
        mon.ask_hot_folder()
        mon.ask_hot_folder()
        assert asked == [True]
    finally:
        _close(mon)


def test_status_strip_shows_the_hot_folder():
    mw = _MW(active=True)
    mon, feed, light, dev = _monitor(mw)
    try:
        mon._refresh_status_strip()
        assert mon.tether_label.text() == "Hot folder: “Roll 12”, 7 captured"
        assert mon.tether_btn.text() == "Stop Hot Folder Tether"
        mon.tether_btn.click()
        assert mw.calls == ["toggle"]
    finally:
        _close(mon)


def test_file_menu_opens_one_monitor(monkeypatch):
    from ui.main_window import MainWindow
    made = []

    class Light(LightClient):
        def __init__(self):
            link = LightLink(serial_factory=lambda: FakeSerial(FakeDevice()),
                             port_lister=lambda: [], sleep=lambda s: None, available=True)
            super().__init__(link=link, threaded=False)

    real = lm.LiveCaptureMonitor

    def make(main_window=None):
        m = real(main_window=main_window, feed=FakeFeed(), light=Light())
        m._start_hot_folder = lambda from_monitor=False: None
        made.append(m)
        return m
    monkeypatch.setattr(lm, "LiveCaptureMonitor", make)
    w = MainWindow()
    assert w.live_monitor_action.text() == "Capture…"
    acts = [a.text() for a in w.menuBar().actions()[0].menu().actions()]
    assert acts.index("Capture…") == acts.index("Hot Folder Tether…") + 1
    w.live_monitor_action.trigger()
    w.live_monitor_action.trigger()
    assert len(made) == 1 and w._live_monitor is made[0]
    made[0].close()
    _app.processEvents()
    assert w._live_monitor is None


def test_reconnects_to_the_remembered_port_on_opening():
    lm._settings().setValue("live/port", "/dev/cu.usbmodem1")
    mon, feed, light, dev = _monitor(connect=False)
    try:
        for _ in range(5):
            _app.processEvents()
        assert light.is_open() and light.port == "/dev/cu.usbmodem1"
        assert ("find", None, "auto") in feed.log or any(e[0] == "find" for e in feed.log)
    finally:
        _close(mon)


def test_clicking_the_sharpness_meter_resets_the_best():
    mon, feed, light, dev = _monitor()
    try:
        before = mon._reset_best
        mon.canvas.meter_clicked.emit()
        assert mon._reset_best == before + 1
        assert feed.params.reset_best == before + 1
    finally:
        _close(mon)


def test_the_light_comes_on_white_when_a_camera_connects(iso_settings):
    mon, feed, light, dev = _monitor()
    try:
        assert not any(mon._levels.values())
        dev.written.clear()
        feed.status.emit("open", "1")
        _app.processEvents()
        assert mon._levels == {"r": 0, "g": 0, "b": 0, "w": 255, "ir": 0}
        assert dev.written[-1] == cl.colour_packet({"w": 255})
        assert mon.led_sliders["w"].value() == 255
        # levels set by hand stay
        mon.led_sliders["w"].setValue(0)
        mon.led_sliders["r"].setValue(90)
        mon.usb.state.emit("open", "Some camera")
        _app.processEvents()
        assert mon._levels["r"] == 90 and mon._levels["w"] == 0
        # turned off in Settings: nothing
        mon.led_sliders["r"].setValue(0)
        lm._settings().setValue("live/light_on_camera", False)
        feed.status.emit("open", "1")
        assert not any(mon._levels.values())
    finally:
        _close(mon)


def test_a_light_connected_after_the_camera_comes_on_too_but_not_asleep(iso_settings):
    mon, feed, light, dev = _monitor(connect=False)
    try:
        feed.status.emit("open", "1")                 # no light yet: nothing to do
        assert not any(mon._levels.values())
        light.open_port("/dev/cu.usbmodem1")
        light.link.tick()
        _app.processEvents()
        assert mon._levels["w"] == 255
        # asleep: the camera reopening at Wake doesn't light it by itself
        mon.led_sliders["w"].setValue(0)
        mon._sleeping = {}
        feed.status.emit("open", "1")
        assert not any(mon._levels.values())
        mon._sleeping = None
    finally:
        _close(mon)


def test_the_start_window_is_light_and_capture_comes_back_to_the_front():
    """Start Hot Folder Tether from Capture: the window is light like Capture,
    and Capture is raised afterwards rather than left behind the main window
    (the user, 2026-11-01)."""
    from ui import theme
    from widgets.tether_start_dialog import TetherStartDialog

    class MW:
        _tether_active = False
        calls = []

        def enter_tethering(self, from_monitor=False, parent=None):
            self.calls.append((from_monitor, parent))
            dlg = TetherStartDialog(parent, n_open=0, folder="", scan_type="colour",
                                    from_monitor=from_monitor)
            parent.light_dialog(dlg)
            self.dlg = dlg
            return {"start": False}
    mw = MW()
    mon, feed, light, dev = _monitor(mw=mw)
    try:
        raised = []
        mon.raise_ = lambda: raised.append(1)
        lm.LiveCaptureMonitor._start_hot_folder(mon, from_monitor=True)
        assert mw.calls == [(True, mon)]
        pal = mw.dlg.palette()
        assert pal.color(pal.ColorRole.Window).name().lower() == theme.LIGHT_TOKENS["window"].lower()
        assert raised                                  # now ...
        n = len(raised)
        _app.processEvents()
        assert len(raised) > n                         # ... and once more after the event loop
    finally:
        _close(mon)


def test_the_lights_usb_identity_is_remembered_and_handed_back(iso_settings):
    """spec/live-capture-monitor.md revision 2026-11-07: found wherever it is
    plugged in next time."""
    ident = "2e8a:000a:E6614C311B7A9C2D"
    mon, feed, light, dev = _monitor(connect=False)
    try:
        light.link._identify = lambda: {"/dev/cu.usbmodem1": ident}
        light.open_port("/dev/cu.usbmodem1")
        light.link.tick()
        assert light.state == "ok"
        assert lm._settings().value("live/port_id") == ident
    finally:
        _close(mon)
    mon2, feed2, light2, dev2 = _monitor(connect=False)
    try:
        _app.processEvents()
        assert light2.link._auto_ident == ident
    finally:
        _close(mon2)
