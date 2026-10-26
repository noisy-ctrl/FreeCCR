"""The USB camera worker of the Live Capture Monitor
(spec/capture-routes.md §5.2).

`UsbCamera` is a `WorkerThread` and the ONLY code that touches a USB camera
(through `core.camera_backends`): commands are queued from the GUI, results
come back as signals. Live view frames are decoded into a one-slot,
latest-wins mailbox; `frame_source(key)` hands `LiveFeed` a capture-like
object over that mailbox, so the live picture's processing is the same as
for an HDMI capture device.
"""
import threading
import time

import cv2
import numpy as np
from PySide6.QtCore import Signal

from core import camera_backends as cb
from utils.qt_threads import WorkerThread

EXPOSURE_EVERY_S = 3.0
FRAME_WAIT_S = 0.2          # a source read waits this long for a new frame


class UsbCamera(WorkerThread):
    cameras_found = Signal(list)        # [(key, label), ...]
    state = Signal(str, str)            # opening/open/busy/lost/closed/error, text
    file_saved = Signal(str)            # a shot's file is in the hot folder
    file_skipped = Signal(str)          # a shot's file had nowhere to go
    exposure_changed = Signal(dict)
    zoom_levels = Signal(list)
    addons_found = Signal(list)         # unofficial add-ons on the card (spec §12)
    fired = Signal()
    fire_failed = Signal(str)

    def __init__(self, backends=None, clock=time.monotonic, parent=None):
        super().__init__(parent)
        self._backends = backends
        self._clock = clock
        self._lock = threading.Lock()
        self._cmds = []
        self._wake = threading.Event()
        self._stop = False
        self._frame_cv = threading.Condition()
        self._frame = None
        self._seq = 0
        self._live = False
        self.backend = None
        self.key = None
        self.model = ""
        self.dest = None
        self.status = "closed"           # read by sources and the GUI

    # -- GUI side ---------------------------------------------------------
    def scan(self):
        self._command(("scan",))

    def open(self, key):
        if key != self.key or self.status not in ("opening", "open"):
            # Seen at once by a source made for this key (LiveFeed's thread):
            # opening a camera takes a while and isn't a lost one.
            self.key, self.status = key, "opening"
        self._command(("open", key))

    def close(self):
        self._command(("close",))

    def set_live(self, on):
        self._command(("live", bool(on)))

    def fire(self):
        self._command(("fire",))

    def set_zoom(self, level):
        self._command(("zoom", str(level)))

    def set_dest(self, folder):
        self._command(("dest", folder or None))

    def release_and_retry(self):
        self._command(("release",))

    def stop(self):
        self._stop = True
        self._command(("stop",))
        with self._frame_cv:
            self._frame_cv.notify_all()

    def is_open(self):
        return self.status == "open"

    def frame_source(self, key, fmt=None):
        """A capture-like object for LiveFeed (`read`, `get`, `release`,
        `alive`), or None if `key` isn't a USB camera."""
        if cb.split_key(key) is None:
            return None
        if self.key != key or self.status != "open":
            self.open(key)
        self.set_live(True)
        return UsbFrameSource(self, key)

    def latest(self, after_seq, timeout):
        """(seq, frame) newer than `after_seq`, waiting up to `timeout`; the
        current one if nothing newer came."""
        with self._frame_cv:
            if self._seq <= after_seq and not self._stop:
                self._frame_cv.wait(timeout)
            return self._seq, self._frame

    def _command(self, cmd):
        with self._lock:
            self._cmds.append(cmd)
        self._wake.set()

    # -- worker side ------------------------------------------------------
    def _all_backends(self):
        if self._backends is None:
            self._backends = cb.default_backends()
        return self._backends

    def _backend_named(self, name):
        for b in self._all_backends():
            if b.name == name:
                return b
        return None

    def run(self):
        next_exposure = 0.0
        while not self._stop:
            with self._lock:
                cmds, self._cmds = self._cmds, []
            for cmd in cmds:
                try:
                    self._handle(cmd)
                except Exception as e:      # a command never kills the worker
                    print(f"usb camera: {cmd[0]}: {e}")
            if self._stop:
                break
            if self.backend is None or self.status != "open":
                self._wake.wait(0.1)
                self._wake.clear()
                continue
            try:
                if self._live and not getattr(self.backend, "no_preview", False):
                    data = self.backend.preview()
                    if data:
                        self._publish(data)
                    elif getattr(self.backend, "no_preview", False):
                        self.state.emit("nopreview", self.model)
                events = self.backend.poll(0.01 if self._live else 0.1)
                for kind, value in events:
                    if kind == "file":
                        self.file_saved.emit(value)
                    elif kind == "skipped":
                        self.file_skipped.emit(value)
                now = self._clock()
                if now >= next_exposure:
                    next_exposure = now + EXPOSURE_EVERY_S
                    exp = self.backend.exposure()
                    if exp:
                        self.exposure_changed.emit(exp)
            except cb.CameraLost as e:
                self._lose(str(e))
        self._shut()

    def _handle(self, cmd):
        kind = cmd[0]
        if kind == "stop":
            self._stop = True
        elif kind == "scan":
            found = []
            for b in self._all_backends():
                try:
                    if b.available():
                        found.extend(b.list_cameras())
                except Exception as e:
                    print(f"usb camera: {b.name} scan: {e}")
            self.cameras_found.emit([(c.key, c.label) for c in found])
        elif kind == "open":
            self._open(cmd[1])
        elif kind == "close":
            self._shut()
            self.status = "closed"
            self.state.emit("closed", "")
        elif kind == "live":
            self._live = cmd[1]
            if self.backend is not None and self.status == "open":
                (self.backend.start_live if self._live else self.backend.stop_live)()
        elif kind == "fire":
            if self.backend is None or self.status != "open":
                self.fire_failed.emit("The camera isn't open.")
                return
            try:
                self.backend.fire()
                self.fired.emit()
            except cb.CameraLost as e:
                self._lose(str(e))
                self.fire_failed.emit("The camera disconnected.")
            except Exception as e:
                self.fire_failed.emit(f"The camera didn't fire ({e}).")
        elif kind == "zoom":
            if self.backend is not None:
                self.backend.set_zoom(cmd[1])
        elif kind == "dest":
            self.dest = cmd[1]
            if self.backend is not None and self.status == "open":
                self.backend.set_dest(self.dest)
        elif kind == "release":
            cb.release_macos_camera_service()
            time.sleep(0.3)
            if self.key:
                self._open(self.key)

    def _open(self, key):
        parts = cb.split_key(key)
        if parts is None:
            return
        self._shut()
        backend = self._backend_named(parts[0])
        self.key = key
        if backend is None:
            self.status = "error"
            self.state.emit("error", "That camera's connection isn't available here.")
            return
        self.status = "opening"
        self.state.emit("opening", "Opening the camera…")
        try:
            model = backend.open(parts[1], self.dest)
        except cb.CameraBusy as e:
            self.status = "busy"
            self.state.emit("busy", str(e))
            return
        except Exception as e:
            self.status = "error"
            self.state.emit("error", str(e))
            return
        self.backend = backend
        self.model = model or ""
        self.status = "open"
        if self._live:
            try:
                backend.start_live()
            except cb.CameraLost as e:
                self._lose(str(e))
                return
        self.state.emit("open", self.model)
        self.zoom_levels.emit(list(backend.zoom_levels()))
        try:
            found = list(backend.addons()) if hasattr(backend, "addons") else []
        except Exception:
            found = []
        if found:
            self.addons_found.emit(found)

    def _publish(self, data):
        frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return
        with self._frame_cv:
            self._frame = frame
            self._seq += 1
            self._frame_cv.notify_all()

    def _lose(self, text):
        self._shut()
        self.status = "lost"
        self.state.emit("lost", text)
        with self._frame_cv:
            self._frame_cv.notify_all()

    def _shut(self):
        b, self.backend = self.backend, None
        if b is not None:
            try:
                b.close()
            except Exception:
                pass
        with self._frame_cv:
            self._frame = None
            self._frame_cv.notify_all()


class UsbFrameSource:
    """What LiveFeed reads a USB camera through: OpenCV's capture shape."""

    def __init__(self, worker, key):
        self.worker = worker
        self.key = key
        self._seq = -1
        self._last = None

    def alive(self):
        """While the camera is opening or open, a missing frame is a pause
        (opening, capturing), not a lost camera."""
        return self.worker.key == self.key and self.worker.status in ("opening", "open")

    def read(self):
        """(True, frame) for a NEW frame; (False, None) when none came within
        FRAME_WAIT_S (check `alive()` before calling that a lost camera)."""
        if not self.alive():
            return False, None
        seq, frame = self.worker.latest(self._seq, FRAME_WAIT_S)
        if frame is None or seq == self._seq:
            return False, None
        self._seq = seq
        self._last = frame
        return True, frame

    def get(self, prop):
        f = self._last
        if f is None:
            return 0
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return f.shape[1]
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return f.shape[0]
        return 0

    def set(self, prop, value):
        return False

    def isOpened(self):
        return self.alive()

    def release(self):
        # The view stops; the camera stays open for the shutter.
        if self.worker.key == self.key:
            self.worker.set_live(False)
