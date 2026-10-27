#!/usr/bin/env python3
"""The USB camera route of the Live Capture Monitor (spec/capture-routes.md):
the libgphoto2 backend against a fake `gphoto2` module, the digiCamControl
backend against a local fake web server, the worker feeding LiveFeed, the
event-driven shutter in the capture sequence, Windows port ranking, and the
monitor's Camera list, Shutter row and USB Shoot."""

import http.server
import json
import os
import sys
import threading
import time
import types
import urllib.parse

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtCore import QObject, Signal, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import camera_backends as cb  # noqa: E402
from core import capture_light as cl  # noqa: E402
from core import capture_sequence as cs  # noqa: E402
from core import live_feed as lf  # noqa: E402
from core.usb_camera import UsbCamera, UsbFrameSource  # noqa: E402
from test_live_capture_light import Clock, FakeLight, Timers, PRESET  # noqa: E402


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    _app.processEvents()
    return cond()


# --------------------------------------------------------------------------- #
# a fake libgphoto2
# --------------------------------------------------------------------------- #
class GPhoto2Error(Exception):
    def __init__(self, code):
        super().__init__(f"[{code}] fake error")
        self.code = code


class _List:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def get_name(self, i):
        return self.items[i][0]

    def get_value(self, i):
        return self.items[i][1]


class _File:
    def __init__(self, data):
        self.data = data

    def get_data_and_size(self):
        return memoryview(self.data)

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self.data)


class _Widget:
    def __init__(self, children=None, value=None):
        self.children = children or {}
        self.value = value

    def get_child_by_name(self, name):
        if name not in self.children:
            raise GPhoto2Error(-2)
        return self.children[name]

    def get_value(self):
        return self.value

    def set_value(self, v):
        self.value = v


def fake_gp(cameras=(("Canon EOS 5D Mark III", "usb:001,004"),), shot=("IMG_0001.CR2", "IMG_0001.JPG"),
            config=None, card=None):
    gp = types.SimpleNamespace()
    gp.GPhoto2Error = GPhoto2Error
    gp.GP_EVENT_UNKNOWN, gp.GP_EVENT_TIMEOUT, gp.GP_EVENT_FILE_ADDED = 0, 1, 2
    gp.GP_FILE_TYPE_NORMAL = 1
    gp.GP_ERROR_IO_USB_CLAIM, gp.GP_ERROR_IO_USB_FIND = -53, -52
    gp.GP_ERROR_CAMERA_BUSY, gp.GP_ERROR_IO = -110, -7
    gp.GP_ERROR, gp.GP_ERROR_NOT_SUPPORTED, gp.GP_CAPTURE_IMAGE = -1, -6, 0
    # fuji: like libgphoto2 2.5.34 with a GFX 50S II, the trigger call fails
    # while live view runs; the capture call ends live view and returns a file
    gp.state = types.SimpleNamespace(init_error=None, preview_errors=[], events=[],
                                     fuji=False, in_live=False, captures=0,
                                     capture_error=None, capture_echo=False,
                                     event_checks=0,
                                     shot=list(shot), fired=0, inited=False, exited=False,
                                     config=config if config is not None else _Widget({
                                         "shutterspeed": _Widget(value="1/60"),
                                         "iso": _Widget(value="100"),
                                         "f-number": _Widget(value="f/8"),
                                         "eoszoom": _Widget(value="1")}),
                                     set_configs=0, card=card)
    st = gp.state

    class Camera:
        @staticmethod
        def autodetect():
            return _List(list(cameras))

        def set_port_info(self, p):
            self.port = p

        def set_abilities(self, a):
            self.abilities = a

        def init(self):
            if st.init_error is not None:
                raise GPhoto2Error(st.init_error)
            st.inited = True

        def exit(self):
            st.exited = True

        def capture_preview(self):
            st.in_live = True                  # started even when the frame then fails
            if st.preview_errors:
                raise GPhoto2Error(st.preview_errors.pop(0))
            ok, buf = cv2.imencode(".jpg", np.full((30, 40, 3), 90, np.uint8))
            return _File(buf.tobytes())

        def trigger_capture(self):
            if st.fuji and st.in_live:
                raise GPhoto2Error(gp.GP_ERROR)
            st.fired += 1
            for n in st.shot:
                st.events.append((gp.GP_EVENT_FILE_ADDED,
                                  types.SimpleNamespace(folder="/store/DCIM", name=n)))

        def capture(self, kind):
            if st.capture_error is not None:
                raise GPhoto2Error(st.capture_error)
            st.in_live = False
            st.captures += 1
            first = types.SimpleNamespace(folder="/store/DCIM", name=st.shot[0])
            rest = st.shot[1:] + (st.shot[:1] if st.capture_echo else [])
            for n in rest:                     # RAW + JPEG: the other one as an event
                st.events.append((gp.GP_EVENT_FILE_ADDED,
                                  types.SimpleNamespace(folder="/store/DCIM", name=n)))
            return first

        def wait_for_event(self, ms):
            st.event_checks += 1
            if st.events:
                return st.events.pop(0)
            return gp.GP_EVENT_TIMEOUT, None

        def file_get(self, folder, name, kind):
            return _File(f"{folder}/{name}".encode())

        def get_config(self):
            return st.config

        def set_config(self, cfg):
            st.set_configs += 1

        # the card: {"/": [stores], "/store": (files, folders)}; None = no listing
        def folder_list_folders(self, path):
            if st.card is None:
                raise GPhoto2Error(-6)
            if path == "/":
                return _List([(n, None) for n in st.card["/"]])
            return _List([(n, None) for n in st.card[path][1]])

        def folder_list_files(self, path):
            if st.card is None:
                raise GPhoto2Error(-6)
            return _List([(n, None) for n in st.card[path][0]])

    class PortInfoList:
        def load(self):
            pass

        def lookup_path(self, addr):
            return 0

        def __getitem__(self, i):
            return "port"

    class CameraAbilitiesList(PortInfoList):
        def lookup_model(self, m):
            return 0

    gp.Camera, gp.PortInfoList, gp.CameraAbilitiesList = Camera, PortInfoList, CameraAbilitiesList
    return gp


def _gp_backend(tmp_path, **kw):
    gp = fake_gp(**kw)
    clock = Clock()
    b = cb.Gphoto2Backend(gp_module=gp, clock=clock)
    return b, gp, clock


def test_keys_and_raw_preference():
    assert cb.split_key("usb:gphoto2:usb:020,007") == ("gphoto2", "usb:020,007")
    assert cb.split_key(3) is None and cb.split_key("usb:") is None
    assert cb.keep_raw(["A.CR2", "A.JPG", "B.JPG"]) == ["A.CR2", "B.JPG"]
    assert cb.exposure_text({"shutter": "1/60", "iso": "100", "aperture": "f/8"}) == \
        "1/60 s  ·  ISO 100  ·  f/8"


def test_gphoto2_lists_opens_and_previews(tmp_path):
    b, gp, clock = _gp_backend(tmp_path)
    cams = b.list_cameras()
    assert [(c.key, c.label) for c in cams] == [
        ("usb:gphoto2:usb:001,004", "USB · Canon EOS 5D Mark III")]
    assert b.open("usb:001,004", str(tmp_path)) == "Canon EOS 5D Mark III"
    assert gp.state.inited
    data = b.preview()
    assert data[:2] == b"\xff\xd8"
    b.close()
    assert gp.state.exited


def test_gphoto2_shot_brings_in_only_the_raw_once_the_shot_is_quiet(tmp_path):
    b, gp, clock = _gp_backend(tmp_path)
    b.list_cameras()
    b.open("usb:001,004", str(tmp_path))
    b.fire()
    assert b.poll(0.0) == []                       # both files seen, quiet spell not over
    clock.t += cb.QUIET_S + 0.01
    ev = b.poll(0.0)
    assert ev == [("file", str(tmp_path / "IMG_0001.CR2"))]
    assert sorted(os.listdir(tmp_path)) == ["IMG_0001.CR2"]          # no JPEG, no .part
    # the camera's numbering repeats: the next file doesn't overwrite
    b.fire()
    b.poll(0.0)
    clock.t += cb.QUIET_S + 0.01
    assert b.poll(0.0) == [("file", str(tmp_path / "IMG_0001_1.CR2"))]


def test_gphoto2_jpeg_only_and_nowhere_to_put_it(tmp_path):
    b, gp, clock = _gp_backend(tmp_path, shot=("IMG_0002.JPG",))
    b.list_cameras()
    b.open("usb:001,004", None)
    b.fire()
    b.poll(0.0)
    clock.t += cb.QUIET_S + 0.01
    assert b.poll(0.0) == [("skipped", "IMG_0002.JPG")]
    b.set_dest(str(tmp_path))
    b.fire()
    b.poll(0.0)
    clock.t += cb.QUIET_S + 0.01
    assert b.poll(0.0) == [("file", str(tmp_path / "IMG_0002.JPG"))]


def test_gphoto2_busy_and_lost(tmp_path):
    b, gp, clock = _gp_backend(tmp_path)
    b.list_cameras()
    gp.state.init_error = -53
    with pytest.raises(cb.CameraBusy):
        b.open("usb:001,004", None)
    gp.state.init_error = None
    b.open("usb:001,004", None)
    # a camera busy mid-capture is not an error; repeated I/O failures are lost
    gp.state.preview_errors = [-110, -110, -7, -7]
    for _ in range(4):
        assert b.preview() is None
        clock.t += cb.PREVIEW_BUSY_BACKOFF_S + 0.01
    assert not gp.state.preview_errors
    gp.state.preview_errors = [-7]
    with pytest.raises(cb.CameraLost):
        b.preview()
    gp.state.preview_errors = [-52]                # USB device gone: lost at once
    b2, gp2, _c = _gp_backend(tmp_path)
    b2.list_cameras()
    b2.open("usb:001,004", None)
    gp2.state.preview_errors = [-52]
    with pytest.raises(cb.CameraLost):
        b2.preview()


def test_gphoto2_exposure_and_zoom(tmp_path):
    b, gp, clock = _gp_backend(tmp_path)
    b.list_cameras()
    b.open("usb:001,004", None)
    assert b.exposure() == {"shutter": "1/60", "iso": "100", "aperture": "f/8"}
    assert b.zoom_levels() == ["1", "5", "10"]
    b.set_zoom("5")
    assert gp.state.config.children["eoszoom"].value == "5" and gp.state.set_configs == 1
    b3, gp3, _c = _gp_backend(tmp_path, config=_Widget({"iso": _Widget(value="200")}))
    b3.list_cameras()
    b3.open("usb:001,004", None)
    assert b3.exposure() == {"iso": "200"} and b3.zoom_levels() == []


def test_real_gphoto2_has_what_the_backend_calls():
    gp = pytest.importorskip("gphoto2")
    for name in ("GPhoto2Error", "GP_EVENT_FILE_ADDED", "GP_EVENT_TIMEOUT", "GP_FILE_TYPE_NORMAL",
                 "GP_ERROR_IO_USB_CLAIM", "GP_ERROR_IO_USB_FIND", "GP_ERROR_CAMERA_BUSY",
                 "PortInfoList", "CameraAbilitiesList"):
        assert hasattr(gp, name), name
    for name in ("autodetect", "init", "exit", "capture_preview", "trigger_capture",
                 "wait_for_event", "file_get", "get_config", "set_config",
                 "set_port_info", "set_abilities"):
        assert hasattr(gp.Camera, name), name
    abil = gp.CameraAbilitiesList()
    abil.load()
    for model in ("Canon EOS 5D Mark III", "Canon EOS 5D Mark IV"):
        a = abil[abil.lookup_model(model)]
        assert a.operations & gp.GP_OPERATION_CAPTURE_PREVIEW
        assert a.operations & gp.GP_OPERATION_TRIGGER_CAPTURE


def test_macos_release_runs_only_on_a_mac(monkeypatch):
    ran = []
    monkeypatch.setattr(cb.sys, "platform", "linux")
    assert not cb.release_macos_camera_service(run=lambda *a, **k: ran.append(a))
    assert ran == []
    monkeypatch.setattr(cb.sys, "platform", "darwin")
    cb.release_macos_camera_service(
        run=lambda cmd, **k: (ran.append(cmd), types.SimpleNamespace(returncode=0))[1])
    assert ran == [["killall", "ptpcamerad"], ["killall", "PTPCamera"]]


# --------------------------------------------------------------------------- #
# a fake digiCamControl web server
# --------------------------------------------------------------------------- #
class _DccState:
    def __init__(self, folder):
        self.folder = folder
        self.camera = "CAM123"
        self.last = "-"
        self.live = False
        self.n = 0
        self.json = True
        self.raw_and_jpeg = False


def _dcc_server(state):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, body, ctype="text/plain"):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _resp(self, v):
            if state.json:
                return self._reply(json.dumps({"response": v, "success": True}), "application/json")
            return self._reply(v if isinstance(v, str) else "\n".join(v))

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items()}
            if u.path == "/liveview.jpg":
                ok, buf = cv2.imencode(".jpg", np.full((20, 30, 3), 50, np.uint8))
                return self._reply(buf.tobytes(), "image/jpeg")
            if "CMD" in q:
                state.live = q["CMD"] == "LiveViewWnd_Show"
                return self._reply("OK")
            cmd, p1, p2 = q.get("slc"), q.get("param1", ""), q.get("param2", "")
            if cmd == "list" and p1 == "cameras":
                return self._resp([state.camera])
            if cmd == "get":
                return self._resp({"camera": state.camera, "session.folder": state.folder,
                                   "lastcaptured": state.last, "shutterspeed": "1/125",
                                   "iso": "200", "aperture": "5.6"}.get(p1, ""))
            if cmd == "set":
                if p1 == "session.folder":
                    state.folder = p2
                elif p1 == "camera":
                    state.camera = p2
                return self._resp("OK")
            if cmd == "capture":
                state.n += 1
                stem = f"DSC_{state.n:04d}"
                names = [stem + ".NEF", stem + ".JPG"] if state.raw_and_jpeg else [stem + ".NEF"]
                for n in names:
                    with open(os.path.join(state.folder, n), "wb") as f:
                        f.write(b"x")
                state.last = names[-1]           # it reports the LAST file
                return self._resp("OK")
            return self._resp("")

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.mark.parametrize("as_json", [True, False])
def test_digicamcontrol_backend(tmp_path, as_json):
    old = tmp_path / "dcc_session"
    hot = tmp_path / "hot"
    old.mkdir()
    hot.mkdir()
    state = _DccState(str(old))
    state.json = as_json
    srv = _dcc_server(state)
    try:
        c = Clock()
        b = cb.DigiCamControlBackend(port=srv.server_address[1], clock=c,
                                     sleep=lambda dt: setattr(c, "t", c.t + dt))
        assert b.available()
        cams = b.list_cameras()
        assert [(c.key, c.label) for c in cams] == [("usb:dcc:CAM123", "digiCamControl · CAM123")]
        b.open("CAM123", str(hot))
        assert state.folder == str(hot)                    # its session folder = the hot folder
        b.start_live()
        assert state.live
        assert b.preview()[:2] == b"\xff\xd8"
        assert b.poll(0.0) == []
        b.fire()
        assert b.poll(1.0) == [("file", os.path.join(str(hot), "DSC_0001.NEF"))]
        state.raw_and_jpeg = True                          # reports the JPEG: the RAW is the shot
        b.fire()
        assert b.poll(1.0) == [("file", os.path.join(str(hot), "DSC_0002.NEF"))]
        state.last = "DSC_9999.NEF"                        # kept on the camera: never arrives
        assert b.poll(1.0) == []
        assert b.poll(b.ARRIVE_S + 1) == [("skipped", "DSC_9999.NEF")]
        assert b.exposure() == {"shutter": "1/125", "iso": "200", "aperture": "5.6"}
        b.close()
        assert state.folder == str(old) and not state.live  # put back as it was
    finally:
        srv.shutdown()
    b2 = cb.DigiCamControlBackend(port=srv.server_address[1])
    assert not b2.available()
    with pytest.raises(cb.CameraLost):
        b2.poll(0.0)


# --------------------------------------------------------------------------- #
# the worker and LiveFeed
# --------------------------------------------------------------------------- #
def test_worker_feeds_livefeed_and_saves_shots(tmp_path):
    fake = cb.FakeBackend()
    usb = UsbCamera(backends=[fake])
    feed = lf.LiveFeed()
    feed.sources["usb"] = usb.frame_source
    found, states, saved, exposures = [], [], [], []
    usb.cameras_found.connect(found.append)
    usb.state.connect(lambda k, t: states.append(k))
    usb.file_saved.connect(saved.append)
    usb.exposure_changed.connect(exposures.append)
    frames = []
    feed.frame_ready.connect(lambda: frames.append(feed.take()))
    usb.start()
    feed.start()
    try:
        usb.scan()
        assert _wait(lambda: found)
        key = found[0][0][0]
        assert found[0] == [("usb:fake:0", "USB · Test Camera")]
        usb.set_dest(str(tmp_path))
        feed.open_camera(key)
        assert _wait(lambda: any(f is not None for f in frames))
        r = [f for f in frames if f is not None][-1]
        assert (r.geometry.full_w, r.geometry.full_h) == (600, 400)
        assert "open" in states and fake.live
        assert _wait(lambda: exposures)
        usb.fire()
        assert _wait(lambda: saved)
        assert os.path.exists(saved[0]) and saved[0].startswith(str(tmp_path))
        # unplugged: the worker says so and the feed reports a lost camera
        fstates = []
        feed.status.connect(lambda k, t: fstates.append(k))
        fake.lost = True
        assert _wait(lambda: "lost" in states)
        assert _wait(lambda: "lost" in fstates, timeout=lf.LOST_AFTER_S + 3)
    finally:
        feed.stop()
        feed.wait(3000)
        usb.stop()
        usb.wait(3000)


def test_frame_source_counts_opening_as_alive():
    usb = UsbCamera(backends=[cb.FakeBackend()])
    src = usb.frame_source("usb:fake:0")           # worker not running yet
    assert isinstance(src, UsbFrameSource)
    assert src.alive()                              # opening: a pause, not a lost camera
    assert src.read() == (False, None)
    assert usb.frame_source(4) is None
    assert usb.frame_source("usb:nope") is None


# --------------------------------------------------------------------------- #
# the sequence with a camera shutter
# --------------------------------------------------------------------------- #
class FakeShutter:
    def __init__(self, clock, light):
        self.clock, self.light = clock, light
        self.fired = []

    def fire(self):
        self.fired.append(self.clock.t)
        self.light.log.append(("fire",))


def _useq():
    clock = Clock()
    timers = Timers(clock)
    light = FakeLight()
    done = []
    seq = cs.CaptureSequence(light, schedule=timers.schedule, clock=clock,
                             on_finished=done.append)
    return seq, light, clock, timers, done, FakeShutter(clock, light)


TIMING = {"focus_ms": 150, "pulse_ms": 300, "hold_ms": 200, "period_ms": 0}


def test_usb_single_shot_needs_no_light():
    seq, light, clock, timers, done, sh = _useq()
    light.open = False
    t0 = clock.t
    assert seq.start(TIMING, PRESET, trichrome=False, shutter=sh)
    timers.run(until=t0 + 0.1)
    assert sh.fired == []                            # settling
    timers.run(until=t0 + 0.2)
    assert sh.fired == [pytest.approx(t0 + 0.15)]
    assert not done                                  # waits for the file
    seq.frame_done("/hot/A.CR2")
    assert done == ["done"]
    assert ("focus", True) not in light.log and not [e for e in light.log if e[0] == "pulse"]


def test_usb_trichrome_waits_for_each_file_and_needs_the_light():
    seq, light, clock, timers, done, sh = _useq()
    light.open = False
    assert not seq.start(TIMING, PRESET, trichrome=True, shutter=sh)
    assert done == ["disconnected"]
    done.clear()
    light.open = True
    assert seq.start(TIMING, PRESET, trichrome=True, shutter=sh)
    for _ in range(3):
        timers.run(until=clock.t + 0.2)
        clock.t += 1.5                               # the camera takes its time
        seq.frame_done("f")
    timers.run(until=clock.t + 0.2)
    assert done == ["done"]
    levels = [e[1] for e in light.log if e[0] == "levels"]
    assert levels == [cl.solo(PRESET, "r"), cl.solo(PRESET, "g"), cl.solo(PRESET, "b"),
                      {"r": 0, "g": 0, "b": 0, "w": 230, "ir": 0}]
    order = [e[0] for e in light.log]
    assert order == ["levels", "fire"] * 3 + ["levels"]   # colour set before each fire


def test_usb_timeout_stop_and_failure():
    seq, light, clock, timers, done, sh = _useq()
    assert seq.start(TIMING, PRESET, trichrome=True, shutter=sh)
    timers.run()                                     # nothing lands: the timeout fires
    assert done == ["timeout"] and len(sh.fired) == 1
    done.clear()
    assert seq.start(TIMING, PRESET, trichrome=True, shutter=sh)
    timers.run(until=clock.t + 0.2)
    seq.stop()                                       # mid-shot: never abandoned
    assert done == [] and seq.running
    seq.frame_done("f")
    assert done == ["stopped"] and len(sh.fired) == 2
    done.clear()
    assert seq.start(TIMING, PRESET, trichrome=False, shutter=sh)
    timers.run(until=clock.t + 0.2)
    seq.fire_failed("no")
    assert done == ["failed"]
    timers.run()                                     # the stale timeout does nothing
    assert done == ["failed"]


def test_ports_ranked_by_usb_id():
    ports = [("COM1", None, "Communications Port"), ("COM3", 0x0403, "USB Serial Port"),
             ("COM5", 0x2E8A, "USB Serial Device"), ("/dev/cu.Bluetooth-Incoming-Port", None, "")]
    ranked = sorted(ports, key=lambda p: cl.port_info_rank(*p))
    assert [p[0] for p in ranked] == ["COM5", "COM3", "COM1", "/dev/cu.Bluetooth-Incoming-Port"]
    assert cl.port_info_rank("/dev/cu.usbmodem1101")[0] == 0


# --------------------------------------------------------------------------- #
# the monitor
# --------------------------------------------------------------------------- #
class FakeUsb(QObject):
    addons_found = Signal(list)
    cameras_found = Signal(list)
    state = Signal(str, str)
    file_saved = Signal(str)
    file_skipped = Signal(str)
    exposure_changed = Signal(dict)
    zoom_levels = Signal(list)
    fired = Signal()
    fire_failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.log = []
        self.status = "closed"
        self.dest = None

    def frame_source(self, key, fmt=None):
        return None

    def scan(self):
        self.log.append("scan")

    def open(self, key):
        self.log.append(("open", key))

    def set_live(self, on):
        pass

    def fire(self):
        self.log.append("fire")

    def set_zoom(self, lv):
        self.log.append(("zoom", lv))

    def set_dest(self, d):
        self.dest = d

    def release_and_retry(self):
        self.log.append("release")

    def is_open(self):
        return self.status == "open"

    def stop(self):
        self.log.append("stop")

    def opened(self, model="Canon EOS 5D Mark III"):
        self.status = "open"
        self.state.emit("open", model)


class _MW(QObject):
    tether_captured = Signal(str)

    def __init__(self, folder):
        super().__init__()
        self._tether_active = False
        self._tether_count = 0
        self.folder = folder

    def _tether_folder_label(self):
        return "Roll"

    def tether_folder(self):
        return self.folder if self._tether_active else None

    def enter_tethering(self):
        self._tether_active = True

    def stop_tethering(self):
        self._tether_active = False


@pytest.fixture
def mon_usb(tmp_path, monkeypatch):
    from widgets import live_monitor as lm
    from test_live_monitor import FakeFeed, _light
    path = str(tmp_path / "live.ini")
    monkeypatch.setattr(lm, "_settings", lambda: QSettings(path, QSettings.IniFormat))
    from core.ccr_backend import ccr_backend
    monkeypatch.setattr(ccr_backend, "scan_type", "colour", raising=False)
    feed = FakeFeed()
    light, dev, made = _light()
    usb = FakeUsb()
    mw = _MW(str(tmp_path))
    mon = lm.LiveCaptureMonitor(main_window=mw, feed=feed, light=light, usb=usb)
    asked = []
    mon._hot_folder_choice = lambda *a: (asked.append(a), ("start", False))[1]
    mon.resize(1300, 820)
    mon.show()
    _app.processEvents()
    yield mon, feed, light, usb, mw, asked, lm
    mon.close()
    _app.processEvents()


def test_monitor_lists_usb_cameras_and_shoots_over_usb(mon_usb):
    mon, feed, light, usb, mw, asked, lm = mon_usb
    asked.clear()
    mon._on_cameras([(0, 1920, 1080)])
    usb.cameras_found.emit([("usb:gphoto2:usb:001,004", "USB · Canon EOS 5D Mark III")])
    _app.processEvents()
    labels = [mon.camera_combo.itemText(i) for i in range(mon.camera_combo.count())]
    assert labels == ["Camera 0  ·  1920×1080", "USB · Canon EOS 5D Mark III"]
    # choosing the USB camera opens it in the feed and makes it the shutter
    mon._on_camera_chosen(1)
    assert feed.log[-1] == ("open", "usb:gphoto2:usb:001,004", "auto")
    assert lm._settings().value("live/shutter") == "usb:gphoto2:usb:001,004"
    assert not mon.shoot_btn.isEnabled()             # no Scanlight, no USB camera yet
    usb.opened()
    usb.exposure_changed.emit({"shutter": "1/60", "iso": "100", "aperture": "8"})
    _app.processEvents()
    assert mon.shutter_combo.currentText() == "USB · Canon EOS 5D Mark III"
    assert mon.shoot_btn.isEnabled()                 # a single shot doesn't need the light
    assert "1/60 s  ·  ISO 100  ·  f/8" in mon.camera_info.text()
    assert "over USB" in mon.capture_caption.text()
    # Shoot with the hot folder off: asks, turns it on, points the camera at it, fires
    mon.shoot()
    assert asked and mw._tether_active and usb.dest == mw.folder
    assert _wait(lambda: "fire" in usb.log)
    assert mon.sequence.running
    usb.file_saved.emit(os.path.join(mw.folder, "IMG_0001.CR2"))
    _app.processEvents()
    assert not mon.sequence.running and mon.shoot_btn.text() == "Shoot   (S)"


def test_monitor_usb_trichrome_needs_the_light_and_lost_camera_stops(mon_usb, monkeypatch):
    mon, feed, light, usb, mw, asked, lm = mon_usb
    from core.ccr_backend import ccr_backend
    lm._settings().setValue("live/shutter", "usb")
    usb.opened()
    _app.processEvents()
    monkeypatch.setattr(ccr_backend, "scan_type", "trichrome", raising=False)
    mon._apply_scan_type("trichrome")
    assert mon.scan_type() == "trichrome"
    assert not mon.shoot_btn.isEnabled()
    light.open_port("/dev/cu.usbmodem1")
    light.link.tick()
    _app.processEvents()
    assert mon.shoot_btn.isEnabled()
    mw._tether_active = True
    mon.shoot()
    assert mon.sequence.running
    usb.status = "lost"
    usb.state.emit("lost", "unplugged")
    _app.processEvents()
    assert not mon.sequence.running
    assert "camera disconnected" in mon.capture_caption.text()
    assert mon.shutter_combo.count() == 1            # back to the Scanlight only


def test_monitor_busy_camera_offers_release_on_a_mac(mon_usb, monkeypatch):
    mon, feed, light, usb, mw, asked, lm = mon_usb
    monkeypatch.setattr(lm.sys, "platform", "darwin")
    usb.state.emit("busy", "Could not claim the USB device")
    _app.processEvents()
    assert mon.release_btn.isVisibleTo(mon)
    monkeypatch.setattr(lm.sys, "platform", "win32")
    usb.state.emit("busy", "x")
    _app.processEvents()
    assert not mon.release_btn.isVisibleTo(mon)
    usb.zoom_levels.emit(["1", "5", "10"])
    _app.processEvents()
    assert mon.cam_zoom_combo.isVisibleTo(mon) and mon.cam_zoom_combo.count() == 3
    mon.cam_zoom_combo.activated.emit(1)
    assert ("zoom", "5") in usb.log


def test_remembered_usb_camera_is_opened_when_found(mon_usb):
    mon, feed, light, usb, mw, asked, lm = mon_usb
    lm._settings().setValue("live/camera_key", "usb:gphoto2:usb:001,004")
    feed.log.clear()
    mon.find_cameras()
    assert feed.log[-1][0] == "find" and "scan" in usb.log
    mon._on_cameras([(0, 1920, 1080)])
    usb.cameras_found.emit([("usb:gphoto2:usb:001,004", "USB · Canon EOS 5D Mark III")])
    _app.processEvents()
    assert feed.log[-1] == ("open", "usb:gphoto2:usb:001,004", "auto")
    assert mon.camera_combo.currentText() == "USB · Canon EOS 5D Mark III"


# --------------------------------------------------------------------------- #
# the real libgphoto2, against its own virtual camera
# --------------------------------------------------------------------------- #
_REAL_SCRIPT = r'''
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
from core import camera_backends as cb
b = cb.Gphoto2Backend()
cams = b.list_cameras()
out = {"cams": [(c.key, c.label) for c in cams]}
ident = cb.split_key(cams[0].key)[1]
out["model"] = b.open(ident, sys.argv[2])
out["preview"] = b.preview()
out["no_preview"] = b.no_preview
out["exposure"] = b.exposure()
b.fire()
files, end = [], time.monotonic() + 10
while not files and time.monotonic() < end:
    files = [v for k, v in b.poll(0.2) if k == "file"]
out["files"] = files
b.close()
print(json.dumps(out))
'''


def test_real_libgphoto2_virtual_camera(tmp_path):
    """libgphoto2's own virtual camera (its `vusb` driver, shipped in the
    gphoto2 wheels): the backend's real calls, end to end."""
    gp = pytest.importorskip("gphoto2")
    vusb = os.path.join(os.path.dirname(gp.__file__), "libgphoto2", "vusb")
    if not os.path.isdir(vusb):
        pytest.skip("this gphoto2 build has no virtual camera")
    import subprocess
    vdir = tmp_path / "vcam"
    vdir.mkdir()
    for i in range(2):
        cv2.imwrite(str(vdir / f"IMG_{i:04d}.JPG"), np.full((20, 30, 3), 60 * i, np.uint8))
    dest = tmp_path / "hot"
    dest.mkdir()
    env = dict(os.environ, VCAMERADIR=str(vdir))
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    r = subprocess.run([sys.executable, "-c", _REAL_SCRIPT, src, str(dest)], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["cams"] and out["cams"][0][1].startswith("USB · ")
    assert out["preview"] is None and out["no_preview"]          # it has no live view
    assert out["exposure"].get("shutter") and out["exposure"].get("aperture")
    assert len(out["files"]) == 1
    f = out["files"][0]
    assert os.path.dirname(f) == str(dest) and os.path.getsize(f) > 0
    assert not [n for n in os.listdir(dest) if n.endswith(".part")]


def test_monitor_stops_when_a_shot_never_arrives(mon_usb):
    mon, feed, light, usb, mw, asked, lm = mon_usb
    lm._settings().setValue("live/shutter", "usb")
    usb.opened()
    _app.processEvents()
    mw._tether_active = True
    mon.shoot()
    assert _wait(lambda: "fire" in usb.log)
    usb.file_skipped.emit("DSC_0001.NEF")
    _app.processEvents()
    assert not mon.sequence.running
    assert "didn't arrive in the hot folder" in mon.capture_caption.text()
    assert mon.shoot_btn.isEnabled()



# --------------------------------------------------------------------------- #
# Magic Lantern on the card (spec §12)
# --------------------------------------------------------------------------- #
def test_magic_lantern_is_recognised_by_its_two_items():
    assert cb.addons_on_card(["autoexec.bin"], ["DCIM", "ML"]) == ["Magic Lantern"]
    assert cb.addons_on_card(["AUTOEXEC.BIN"], ["ml", "MISC"]) == ["Magic Lantern"]
    assert cb.addons_on_card(["autoexec.bin"], ["DCIM"]) == []      # one of the two isn't enough
    assert cb.addons_on_card([], ["ML"]) == []


def test_gphoto2_lists_every_card_for_add_ons(tmp_path):
    card = {"/": ["store_00010001", "store_00020001"],
            "/store_00010001": ([], ["DCIM"]),
            "/store_00020001": (["AUTOEXEC.BIN"], ["DCIM", "ML"])}
    b, gp, clock = _gp_backend(tmp_path, card=card)
    b.list_cameras()
    b.open("usb:001,004", None)
    assert b.addons() == ["Magic Lantern"]
    b2, gp2, _c = _gp_backend(tmp_path, card=None)                  # no listing: no finding
    b2.list_cameras()
    b2.open("usb:001,004", None)
    assert b2.addons() == []
    assert cb.DigiCamControlBackend().addons() == []


_REAL_ML_SCRIPT = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from core import camera_backends as cb
b = cb.Gphoto2Backend()
cams = b.list_cameras()
b.open(cb.split_key(cams[0].key)[1], None)
print(json.dumps(b.addons()))
b.close()
'''


def test_real_libgphoto2_finds_magic_lantern_on_the_card(tmp_path):
    gp = pytest.importorskip("gphoto2")
    if not os.path.isdir(os.path.join(os.path.dirname(gp.__file__), "libgphoto2", "vusb")):
        pytest.skip("this gphoto2 build has no virtual camera")
    import subprocess
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    out = {}
    for with_ml in (True, False):
        vdir = tmp_path / f"vcam_{with_ml}"
        (vdir / "DCIM" / "100CANON").mkdir(parents=True)
        cv2.imwrite(str(vdir / "DCIM" / "100CANON" / "IMG_0001.JPG"), np.zeros((8, 8, 3), np.uint8))
        if with_ml:
            (vdir / "ML" / "modules").mkdir(parents=True)
            (vdir / "autoexec.bin").write_bytes(b"x")
        r = subprocess.run([sys.executable, "-c", _REAL_ML_SCRIPT, src],
                           env=dict(os.environ, VCAMERADIR=str(vdir)),
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr[-2000:]
        out[with_ml] = json.loads(r.stdout.strip().splitlines()[-1])
    assert out == {True: ["Magic Lantern"], False: []}


def test_worker_reports_add_ons_after_opening():
    fake = cb.FakeBackend()
    fake.card_addons = ["Magic Lantern"]
    usb = UsbCamera(backends=[fake])
    got = []
    usb.addons_found.connect(got.append)
    usb.start()
    try:
        usb.open("usb:fake:0")
        assert _wait(lambda: got)
        assert got == [["Magic Lantern"]]
    finally:
        usb.stop()
        usb.wait(3000)


def test_monitor_warns_once_and_shows_a_badge(mon_usb):
    mon, feed, light, usb, mw, asked, lm = mon_usb
    shown = []
    mon._addon_notice = lambda model, found: (shown.append(model), False)[1]
    usb.opened("Canon EOS 5D Mark III")
    _app.processEvents()
    usb.addons_found.emit(["Magic Lantern"])
    _app.processEvents()
    assert shown == ["Canon EOS 5D Mark III"]
    assert mon.addon_badge.isVisibleTo(mon) and mon.addon_badge.text() == "Magic Lantern on card"
    assert "own risk" in mon.addon_badge.toolTip() and "hold SET" in mon.addon_badge.toolTip()
    assert "Canon" not in mon.ADDON_TEXT                 # the maker isn't named
    usb.addons_found.emit(["Magic Lantern"])             # same camera again: no second box
    _app.processEvents()
    assert shown == ["Canon EOS 5D Mark III"]
    usb.status = "closed"
    usb.state.emit("closed", "")
    _app.processEvents()
    assert not mon.addon_badge.isVisibleTo(mon)
    # "don't warn me again" turns the box off; the badge still shows
    mon._addon_notice = lambda model, found: (shown.append(model), True)[1]
    usb.opened("Canon EOS 5D Mark IV")
    usb.addons_found.emit(["Magic Lantern"])
    _app.processEvents()
    assert shown[-1] == "Canon EOS 5D Mark IV"
    assert lm._settings().value("live/warn_addons", True, type=bool) is False
    mon._addon_warned.clear()
    usb.addons_found.emit(["Magic Lantern"])
    _app.processEvents()
    assert shown[-1] == "Canon EOS 5D Mark IV" and len(shown) == 2
    assert mon.addon_badge.isVisibleTo(mon)



def test_the_camera_s_own_model_name_wins_over_the_table(tmp_path):
    """Fujifilm reuses USB ids: libgphoto2 matches a GFX 50S II to its GFX100
    entry. The name shown is the one the camera reports."""
    cfg = _Widget({"cameramodel": _Widget(value="GFX 50S II"),
                   "manufacturer": _Widget(value="FUJIFILM")})
    b, gp, clock = _gp_backend(tmp_path, cameras=(("Fujifilm GFX100", "usb:001,009"),), config=cfg)
    assert b.list_cameras()[0].label == "USB · Fujifilm GFX100"
    assert b.open("usb:001,009", None) == "FUJIFILM GFX 50S II"
    canon = _Widget({"cameramodel": _Widget(value="Canon EOS 5D Mark III"),
                     "manufacturer": _Widget(value="Canon Inc.")})
    b2, gp2, _c = _gp_backend(tmp_path, config=canon)
    b2.list_cameras()
    assert b2.open("usb:001,004", None) == "Canon EOS 5D Mark III"    # no "Canon Canon"
    b3, gp3, _c = _gp_backend(tmp_path, config=_Widget({}))           # nothing reported
    b3.list_cameras()
    assert b3.open("usb:001,004", None) == "Canon EOS 5D Mark III"


def test_monitor_relabels_the_camera_with_its_own_name(mon_usb):
    mon, feed, light, usb, mw, asked, lm = mon_usb
    key = "usb:gphoto2:usb:001,009"
    usb.cameras_found.emit([(key, "USB · Fujifilm GFX100")])
    _app.processEvents()
    usb.key = key
    usb.opened("FUJIFILM GFX 50S II")
    _app.processEvents()
    i = mon.camera_combo.findData(key)
    assert mon.camera_combo.itemText(i) == "USB · FUJIFILM GFX 50S II"
    lm._settings().setValue("live/shutter", "usb")
    mon._rebuild_shutter_combo()
    assert mon.shutter_combo.currentText() == "USB · FUJIFILM GFX 50S II"


# --------------------------------------------------------------------------- #
# reading settings without python-gphoto2's NULL-string crash
# --------------------------------------------------------------------------- #
def test_empty_text_setting_reads_as_none_not_a_crash():
    """python-gphoto2's get_value() reads a NULL string and the process dies
    (SIGSEGV, seen on a GFX 50S II). An empty libgphoto2 text widget, made
    directly in the library, reads as None through the guard."""
    gp = pytest.importorskip("gphoto2")
    import ctypes
    lib = cb._gp_library(gp)
    assert lib is not None
    lib.gp_widget_new.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p]
    lib.gp_widget_set_value.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    w = ctypes.c_void_p()
    assert lib.gp_widget_new(gp.GP_WIDGET_TEXT, b"empty", ctypes.byref(w)) >= 0
    assert cb._text_at(lib, w.value) is None             # no value: None, no crash
    assert lib.gp_widget_set_value(w, ctypes.c_char_p(b"GFX50S II")) >= 0
    assert cb._text_at(lib, w.value) == "GFX50S II"
    lib.gp_widget_free.argtypes = [ctypes.c_void_p]
    lib.gp_widget_free(w)


def test_fake_widgets_still_read_directly():
    w = _Widget(value="1/125")
    assert cb.widget_value(fake_gp(), w) == "1/125"


_REAL_WALK = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import gphoto2 as gp
from core import camera_backends as cb
cam = gp.Camera(); cam.init()
seen, same, empty = 0, 0, 0
def walk(w):
    global seen, same, empty
    if w.get_type() not in (gp.GP_WIDGET_WINDOW, gp.GP_WIDGET_SECTION, gp.GP_WIDGET_BUTTON):
        seen += 1
        v = cb.widget_value(gp, w)
        if v is None:
            empty += 1
        elif v == w.get_value():        # safe: the guard found a value
            same += 1
    for i in range(w.count_children()):
        walk(w.get_child(i))
walk(cam.get_config())
cam.exit()
print(json.dumps({"seen": seen, "same": same, "empty": empty}))
'''


def test_guarded_reads_match_on_the_virtual_camera(tmp_path):
    gp = pytest.importorskip("gphoto2")
    if not os.path.isdir(os.path.join(os.path.dirname(gp.__file__), "libgphoto2", "vusb")):
        pytest.skip("this gphoto2 build has no virtual camera")
    import subprocess
    vdir = tmp_path / "vcam"
    vdir.mkdir()
    cv2.imwrite(str(vdir / "IMG_0001.JPG"), np.zeros((8, 8, 3), np.uint8))
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    r = subprocess.run([sys.executable, "-c", _REAL_WALK, src], env=dict(os.environ, VCAMERADIR=str(vdir)),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["seen"] > 10 and out["same"] + out["empty"] == out["seen"]



# --------------------------------------------------------------------------- #
# the GFX 50S II case (2026-10-27): USB shooting works, USB live view doesn't
# --------------------------------------------------------------------------- #
def test_live_view_busy_is_retried_and_only_given_up_after_a_long_while(tmp_path):
    """A GFX 50S II answers live view "busy" for a while after connecting and
    after each shot, then gives frames: keep asking (not too often), and only
    a camera busy for PREVIEW_GIVE_UP_S with no frame ever has none."""
    b, gp, clock = _gp_backend(tmp_path)
    b.list_cameras()
    b.open("usb:001,004", None)
    gp.state.preview_errors = [-110] * 100
    assert b.preview() is None
    left = len(gp.state.preview_errors)
    assert b.preview() is None and len(gp.state.preview_errors) == left   # backing off
    clock.t += cb.PREVIEW_BUSY_BACKOFF_S + 0.01
    assert b.preview() is None and len(gp.state.preview_errors) == left - 1
    clock.t += 5.0
    gp.state.preview_errors = []                     # ready: frames from now on
    assert b.preview() is not None and not b.no_preview
    # never a frame, busy past the give-up time: no USB live view
    b2, gp2, c2 = _gp_backend(tmp_path)
    b2.list_cameras()
    b2.open("usb:001,004", None)
    gp2.state.preview_errors = [-110] * 1000
    t0 = c2.t
    while c2.t - t0 < cb.PREVIEW_GIVE_UP_S + 1:
        b2.preview()
        c2.t += cb.PREVIEW_BUSY_BACKOFF_S + 0.01
    assert b2.no_preview
    # one that HAS given frames is only busy around a shot: never given up
    b3, gp3, c3 = _gp_backend(tmp_path)
    b3.list_cameras()
    b3.open("usb:001,004", None)
    assert b3.preview() is not None
    gp3.state.preview_errors = [-110] * 1000
    for _ in range(100):
        b3.preview()
        c3.t += 1.0
    assert not b3.no_preview


def test_exposure_reads_one_setting_at_a_time_when_it_can(tmp_path):
    b, gp, clock = _gp_backend(tmp_path)
    b.list_cameras()
    b.open("usb:001,004", None)
    asked = []

    def single(name):
        asked.append(name)
        return gp.state.config.get_child_by_name(name)
    b.camera.get_single_config = single
    b.camera.get_config = lambda: (_ for _ in ()).throw(AssertionError("whole config read"))
    assert b.exposure() == {"shutter": "1/60", "iso": "100", "aperture": "f/8"}
    assert asked[:2] == ["shutterspeed", "iso"] and "f-number" in asked


def test_usb_shutter_with_a_capture_device_for_the_picture(mon_usb):
    """HDMI for the picture, the camera's USB for the shutter and files."""
    mon, feed, light, usb, mw, asked, lm = mon_usb
    key = "usb:gphoto2:usb:001,003"
    mon._on_cameras([(0, 1920, 1080)])
    usb.cameras_found.emit([(key, "USB · Fuji Fujifilm GFX100")])
    _app.processEvents()
    feed.log.clear()
    labels = [mon.shutter_combo.itemText(i) for i in range(mon.shutter_combo.count())]
    assert labels == ["Scanlight", "USB · Fuji Fujifilm GFX100"]   # listed before it opens
    mon.shutter_combo.setCurrentIndex(1)
    mon.shutter_combo.activated.emit(1)
    assert ("open", key) in usb.log                     # opened as the shutter...
    assert not [e for e in feed.log if e[0] == "open"]  # ...not as the picture
    usb.key = key
    usb.opened("FUJIFILM GFX50S II")
    _app.processEvents()
    assert mon.shutter_combo.currentText() == "USB · FUJIFILM GFX50S II"
    assert mon._shutter_is_usb() and mon.shoot_btn.isEnabled()
    mw._tether_active = True
    mon.shoot()
    assert _wait(lambda: "fire" in usb.log)
    usb.file_saved.emit(os.path.join(mw.folder, "DSCF0001.raf"))
    _app.processEvents()
    assert not mon.sequence.running


def test_remembered_usb_shutter_opens_without_the_view(mon_usb):
    mon, feed, light, usb, mw, asked, lm = mon_usb
    key = "usb:gphoto2:usb:001,003"
    lm._settings().setValue("live/camera_key", "")
    lm._settings().setValue("live/shutter", key)
    mon.find_cameras()
    feed.log.clear()
    usb.log.clear()
    mon._on_cameras([(0, 1920, 1080)])
    usb.cameras_found.emit([(key, "USB · Fuji Fujifilm GFX100")])
    _app.processEvents()
    assert ("open", key) in usb.log
    assert not [e for e in feed.log if e[0] == "open" and e[1] == key]


# --------------------------------------------------------------------------- #
# shooting while live view runs (spec §17, the GFX 50S II's test 4)
# --------------------------------------------------------------------------- #
def _fuji_backend(tmp_path, **kw):
    b, gp, clock = _gp_backend(tmp_path, **kw)
    gp.state.fuji = True
    b.list_cameras()
    b.open("usb:001,004", str(tmp_path))
    return b, gp, clock


def _bring_in(b, clock):
    """Poll through a shot's quiet spell; the files it brought in."""
    out = b.poll(0.0)
    clock.t += cb.QUIET_S + 0.01
    return out + b.poll(0.0)


def test_a_shot_during_live_view_ends_it_first(tmp_path):
    """libgphoto2's trigger call shoots without ending a Fujifilm's live
    view, which the camera refuses: after live view, the capture call."""
    b, gp, clock = _fuji_backend(tmp_path, shot=("DSCF0001.RAF",))
    assert b.preview() is not None
    with pytest.raises(GPhoto2Error):                  # what test 4 hit
        b.camera.trigger_capture()
    b.fire()
    assert gp.state.captures == 1 and gp.state.fired == 0 and not gp.state.in_live
    assert _bring_in(b, clock) == [("file", str(tmp_path / "DSCF0001.RAF"))]
    # live view again, then the next shot ends it again
    assert b.preview() is not None
    b.fire()
    assert gp.state.captures == 2
    assert _bring_in(b, clock) == [("file", str(tmp_path / "DSCF0001_1.RAF"))]
    # no live view since the shot: the trigger call, as before (tested on the body)
    b.fire()
    assert gp.state.fired == 1 and gp.state.captures == 2
    assert _bring_in(b, clock) == [("file", str(tmp_path / "DSCF0001_2.RAF"))]


def test_live_view_asked_but_still_busy_still_counts(tmp_path):
    """A request that started the camera's live view but got no frame leaves
    it running: the next shot must still end it."""
    b, gp, clock = _fuji_backend(tmp_path, shot=("DSCF0002.RAF",))
    gp.state.preview_errors = [-110]
    assert b.preview() is None and gp.state.in_live
    b.fire()
    assert gp.state.captures == 1 and gp.state.fired == 0
    # stopping the view doesn't end a Fujifilm's live view either
    clock.t += 1.0
    b.preview()
    b.stop_live()
    b.fire()
    assert gp.state.captures == 2


def test_capture_call_raw_and_jpeg_and_echoes(tmp_path):
    """The capture call returns one file; the other of a RAW + JPEG pair
    arrives as an event and the RAW is kept. A file announced again by an
    event in the same shot is downloaded once."""
    b, gp, clock = _fuji_backend(tmp_path, shot=("DSCF0003.JPG", "DSCF0003.RAF"))
    b.preview()
    b.fire()
    assert _bring_in(b, clock) == [("file", str(tmp_path / "DSCF0003.RAF"))]
    (tmp_path / "x").mkdir()
    b2, gp2, c2 = _fuji_backend(tmp_path / "x", shot=("DSCF0004.RAF",))
    gp2.state.capture_echo = True
    b2.preview()
    b2.fire()
    assert _bring_in(b2, c2) == [("file", str(tmp_path / "x" / "DSCF0004.RAF"))]
    assert os.listdir(tmp_path / "x") == ["DSCF0004.RAF"]
    # a later shot with the same name is a new file, not an echo
    b2.preview()
    b2.fire()
    assert _bring_in(b2, c2) == [("file", str(tmp_path / "x" / "DSCF0004_1.RAF"))]


def test_capture_call_not_supported_falls_back_to_trigger(tmp_path):
    b, gp, clock = _gp_backend(tmp_path, shot=("IMG_0001.CR2",))
    b.list_cameras()
    b.open("usb:001,004", str(tmp_path))
    gp.state.capture_error = -6
    b.preview()
    b.fire()
    assert gp.state.fired == 1
    assert _bring_in(b, clock) == [("file", str(tmp_path / "IMG_0001.CR2"))]
    gp.state.capture_error = -7                        # a real failure is reported
    b.preview()
    with pytest.raises(RuntimeError):
        b.fire()


def test_event_checks_are_throttled_while_live_view_runs(tmp_path):
    """FreeCCR's loop is preview, then poll. Live and no shot on its way:
    about one event check a second (each costs a Fujifilm a priority-mode
    write and a storage listing). A shot on its way, or no live view: every
    poll checks."""
    b, gp, clock = _fuji_backend(tmp_path, shot=("DSCF0005.RAF",))

    def loop(seconds, live=True):
        end = clock.t + seconds
        got = []
        while clock.t < end:
            if live:
                b.preview()
            got += b.poll(0.01)
            clock.t += 0.02                            # 50 fps
        return got

    gp.state.event_checks = 0
    loop(3.0)
    assert 3 <= gp.state.event_checks <= 4
    b.fire()                                           # capture call: file pending
    gp.state.event_checks = 0
    got = loop(cb.QUIET_S + 0.1)
    assert got == [("file", str(tmp_path / "DSCF0005.RAF"))]
    assert gp.state.event_checks >= int(cb.QUIET_S / 0.02)   # continuous until it's in
    gp.state.event_checks = 0
    loop(2.0)
    assert gp.state.event_checks <= 3                  # throttled again
    # a triggered shot (no live view asked since): continuous until its file
    gp.state.fuji = False
    b.stop_live()
    b.fire()
    gp.state.event_checks = 0
    got = loop(1.0, live=False)
    assert got == [("file", str(tmp_path / "DSCF0005_1.RAF"))]
    assert gp.state.event_checks >= 50                 # no live view: every poll


def test_worker_does_not_spin_when_live_view_is_busy():
    """Live view backing off and the event check throttled both return at
    once: the worker pauses instead of spinning."""
    calls = {"preview": 0}

    class Busy(cb.FakeBackend):
        def preview(self):
            calls["preview"] += 1
            return None

        def poll(self, timeout_s):
            return []

    w = UsbCamera(backends=[Busy()])
    w.start()
    try:
        w.open(cb.make_key("fake", "0"))
        w.set_live(True)
        assert _wait(lambda: w.status == "open")
        time.sleep(0.2)
        n0 = calls["preview"]
        time.sleep(0.5)
        assert calls["preview"] - n0 <= 0.5 / usb_idle() + 5
    finally:
        w.stop()
        w.wait(3000)


def usb_idle():
    from core import usb_camera
    return usb_camera.IDLE_LIVE_S


_REAL_LIVE_SHOT = r'''
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
from core import camera_backends as cb
b = cb.Gphoto2Backend()
cams = b.list_cameras()
b.open(cb.split_key(cams[0].key)[1], sys.argv[2])
b._live_asked = True          # as after a live view frame (the virtual camera has none)
b.fire()
files, end = [], time.monotonic() + 3
while time.monotonic() < end:
    files += [v for k, v in b.poll(0.1) if k == "file"]
print(json.dumps({"files": files, "listing": sorted(os.listdir(sys.argv[2]))}))
b.close()
'''


def test_real_libgphoto2_capture_call_brings_the_file_in_once(tmp_path):
    gp = pytest.importorskip("gphoto2")
    vusb = os.path.join(os.path.dirname(gp.__file__), "libgphoto2", "vusb")
    if not os.path.isdir(vusb):
        pytest.skip("this gphoto2 build has no virtual camera")
    import subprocess
    vdir = tmp_path / "vcam"
    vdir.mkdir()
    cv2.imwrite(str(vdir / "IMG_0000.JPG"), np.full((20, 30, 3), 60, np.uint8))
    dest = tmp_path / "hot"
    dest.mkdir()
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    r = subprocess.run([sys.executable, "-c", _REAL_LIVE_SHOT, src, str(dest)],
                       env=dict(os.environ, VCAMERADIR=str(vdir)),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert len(out["files"]) == 1 and len(out["listing"]) == 1     # once, no .part
    assert os.path.getsize(out["files"][0]) > 0
