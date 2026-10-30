"""Cameras on their USB cable, for the Live Capture Monitor
(spec/capture-routes.md §5.1). No Qt.

Two open-source routes, chosen by what is installed:
- `Gphoto2Backend`: libgphoto2 through the `gphoto2` Python package (macOS,
  Linux). Live view, the shutter and the files over the camera's own USB
  connection; the files are downloaded into the hot folder.
- `DigiCamControlBackend`: digiCamControl's built-in web server on this
  computer (Windows). It already drives the camera on the normal driver;
  FreeCCR asks it for live view and to capture, and points its session
  folder at the hot folder.
- `FakeBackend`: a synthetic camera for tests (and FREECCR_FAKE_USB_CAMERA=1).

A backend is used from ONE thread only (libgphoto2's calls block and a camera
can't be shared between threads): `core.usb_camera.UsbCamera` owns them.

Every key is "usb:<backend>:<id>"; `split_key` takes it apart.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

RAW_EXTS = {".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2",
            ".raf", ".rw2", ".orf", ".pef", ".dng", ".3fr", ".fff", ".iiq",
            ".erf", ".mos", ".mrw", ".x3f", ".srw", ".rwl", ".gpr"}
QUIET_S = 0.3              # a shot's files (RAW + JPEG) arrive within this of each other
# Live view that answers "busy": a GFX 50S II refuses it for a while after
# connecting and after each shot, then gives frames (2026-10-27). So "busy" is
# retried, no more than every PREVIEW_BUSY_BACKOFF_S, and only a camera busy
# for PREVIEW_GIVE_UP_S without ever giving a frame counts as having no USB
# live view.
PREVIEW_BUSY_BACKOFF_S = 0.25
PREVIEW_GIVE_UP_S = 15.0
# While live view runs and no shot is on its way, libgphoto2's event check is
# made only this often (spec §17): on a Fujifilm each check also sets the
# body's priority mode and lists its storage, and checking between every
# frame held a GFX 50S II to 3.4 fps where it gives 50 without, and one a
# second to 36 (test 5). Nothing is missed: libgphoto2 announces any file it
# hasn't seen at the next check.
LIVE_EVENTS_EVERY_S = 3.0   # each check stalls the GFX 50S II's picture ~0.3 s (test 5)
LIVE_RECENT_S = 0.5        # live view was asked for this recently = it is running
SHOT_WAIT_S = 30.0         # after firing, check events continuously this long
# A fire right after (re)connecting: a GFX 50S II answered [-110] I/O in
# progress to one sent straight after the reconnect that ends live view to set
# the shutter speed (the user's first film-base measure, 2026-10-30) AND TOOK
# THE PICTURE. So: wait RECONNECT_SETTLE_S after a connection before firing,
# and treat "busy" from a fire as "maybe shot": look for the shot's file (an
# event, or the file waiting in the camera's own memory) for FIRE_CONFIRM_S
# plus the exposure before firing again (at most FIRE_BUSY_TRIES more), so a
# busy answer never takes a second picture.
RECONNECT_SETTLE_S = 1.0
FIRE_CONFIRM_S = 3.0
FIRE_BUSY_TRIES = 1
CONFIRM_POLL_MS = 200
HELD_EXTS = RAW_EXTS | {".jpg", ".jpeg", ".heif", ".hif", ".tif", ".tiff"}
# Live view that has given frames and then answers busy this long is
# reported as paused (`live_stalled`): a body holding a shot it couldn't clear.
LIVE_STALL_S = 5.0
LOST_AFTER_ERRORS = 3      # consecutive I/O failures before the camera counts as gone
DCC_PORT = 5513


class CameraBusy(Exception):
    """Another process holds the camera (on macOS: the system's camera
    service)."""


class CameraLost(Exception):
    """The camera is gone (unplugged, switched off, the bridge app closed)."""


@dataclass(frozen=True)
class CameraInfo:
    key: str           # "usb:<backend>:<id>"
    label: str         # shown in the Camera list
    model: str = ""


def make_key(backend, ident):
    return f"usb:{backend}:{ident}"


def split_key(key):
    """("gphoto2", "usb:020,007") from "usb:gphoto2:usb:020,007"; None if not
    a USB camera key."""
    if not isinstance(key, str) or not key.startswith("usb:"):
        return None
    parts = key.split(":", 2)
    if len(parts) != 3 or not parts[1]:
        return None
    return parts[1], parts[2]


def is_raw_name(name):
    return os.path.splitext(str(name))[1].lower() in RAW_EXTS


def keep_raw(names):
    """Of one shot's files, the ones to bring in: a RAW wins over a
    same-name JPEG (the hot folder's own rule, applied before anything is
    written so no stray JPEG lands as a frame of its own)."""
    groups = {}
    for n in names:
        groups.setdefault(os.path.splitext(os.path.basename(n))[0].lower(), []).append(n)
    out = []
    for n in names:
        same = groups[os.path.splitext(os.path.basename(n))[0].lower()]
        if is_raw_name(n) or not any(is_raw_name(m) for m in same):
            out.append(n)
    return out


MAGIC_LANTERN = "Magic Lantern"


def is_live_view_file(name):
    """A Fujifilm body's live view frame (`LIVE0001.JPG` on its own store):
    libgphoto2 announces it like a new file (test 5, spec §18). Never a shot."""
    stem, ext = os.path.splitext(os.path.basename(name or ""))
    return ext.lower() in (".jpg", ".jpeg") and stem.upper().startswith("LIVE") \
        and stem[4:].isdigit()


def on_memory_card(folder):
    """Is this camera folder on a memory card? Cards keep files under DCIM
    (the DCF layout); a body's own memory, like a Fujifilm's tether buffer
    (`/store_10000001`), does not. FreeCCR clears shots only from the
    latter (spec §20)."""
    return any(part.upper() == "DCIM" for part in (folder or "").split("/"))


def addons_on_card(files, folders):
    """Unofficial camera add-ons whose files are at the top of a card:
    Magic Lantern installs autoexec.bin and an ML folder (its install guide).
    `files` / `folders` are the names at the card's top level."""
    f = {str(n).lower() for n in files}
    d = {str(n).lower() for n in folders}
    return [MAGIC_LANTERN] if "autoexec.bin" in f and "ml" in d else []


def unique_path(folder, name):
    """`folder/name`, or `folder/stem_1.ext`, `_2`… if that is taken (a new card
    restarts the camera's numbering)."""
    stem, ext = os.path.splitext(os.path.basename(name))
    path = os.path.join(folder, stem + ext)
    k = 1
    while os.path.exists(path) or os.path.exists(path + ".part"):
        path = os.path.join(folder, f"{stem}_{k}{ext}")
        k += 1
    return path


def exposure_text(exp):
    bits = []
    s = (exp or {}).get("shutter")
    if s:
        s = str(s)
        bits.append(s if s.endswith("s") or s.lower() in ("bulb", "auto") else f"{s} s")
    iso = (exp or {}).get("iso")
    if iso:
        bits.append(f"ISO {iso}" if not str(iso).upper().startswith("ISO") else str(iso))
    ap = (exp or {}).get("aperture")
    if ap:
        ap = str(ap)
        bits.append(ap if ap.lower().startswith("f") else f"f/{ap}")
    return "  ·  ".join(bits)


# --------------------------------------------------------------------------- #
# libgphoto2
# --------------------------------------------------------------------------- #
def _gphoto2_debug_log(gp):
    """FREECCR_GPHOTO2_LOG=<file>: libgphoto2's own debug log, for finding out
    what a camera answered (once per process)."""
    path = os.environ.get("FREECCR_GPHOTO2_LOG")
    if not path or gp is None or _GP_LOG:
        return
    try:
        import logging
        log = logging.getLogger("gphoto2")
        handler = logging.FileHandler(os.path.expanduser(path), mode="w")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.DEBUG)
        _GP_LOG.append(gp.check_result(gp.use_python_logging(mapping={
            gp.GP_LOG_ERROR: logging.WARNING,
            gp.GP_LOG_VERBOSE: logging.INFO,
            gp.GP_LOG_DEBUG: logging.DEBUG})))
        print(f"usb camera: libgphoto2 debug log -> {path}")
    except Exception as e:
        print(f"usb camera: no libgphoto2 log ({e})")


_GP_LOG = []


def _import_gphoto2():
    try:
        import gphoto2 as gp
        return gp
    except Exception:
        return None


# python-gphoto2 crashes the whole process (a NULL pointer read, SIGSEGV) when
# get_value() meets a text / radio / menu setting the camera left without a
# value: its NULL check tests the wrong pointer (seen on a Fujifilm GFX 50S II,
# 2026-10-27). No Python exception is raised, so a try can't catch it. String
# settings are therefore read through libgphoto2's own gp_widget_get_value,
# with the NULL checked here.
_GP_LIB = {}


def _gp_library(gp):
    """The libgphoto2 the gphoto2 package itself loaded (same file, so the
    same library instance), or None."""
    key = id(gp)
    if key in _GP_LIB:
        return _GP_LIB[key]
    lib = None
    base = os.path.dirname(getattr(gp, "__file__", "") or "")
    if base:
        import ctypes
        import glob
        cands = (sorted(glob.glob(os.path.join(base, ".dylibs", "libgphoto2.*dylib")))
                 + sorted(glob.glob(os.path.join(base, "libgphoto2", "libgphoto2.so*")))
                 + sorted(glob.glob(os.path.join(base, "libgphoto2", "libgphoto2*.dylib"))))
        for c in cands:
            if "port" in os.path.basename(c):
                continue
            try:
                lib = ctypes.CDLL(c)
                lib.gp_widget_get_value.restype = ctypes.c_int
                lib.gp_widget_get_value.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                break
            except (OSError, AttributeError):
                lib = None
    _GP_LIB[key] = lib
    return lib


def _text_at(lib, address):
    """The string value of the libgphoto2 widget at `address`, or None when
    it has none."""
    import ctypes
    out = ctypes.c_char_p()
    if lib.gp_widget_get_value(ctypes.c_void_p(address), ctypes.byref(out)) < 0:
        return None
    return out.value.decode("utf-8", "replace") if out.value else None


def widget_value(gp, widget):
    """A setting's value without the crash: strings via libgphoto2 itself
    (None when empty, or when that library can't be reached), numbers via
    python-gphoto2 (they have no pointer to be NULL)."""
    this = getattr(widget, "this", None)
    if this is None:
        return widget.get_value()            # not a libgphoto2 widget (tests' fakes)
    kind = widget.get_type()
    if kind not in (gp.GP_WIDGET_TEXT, gp.GP_WIDGET_RADIO, gp.GP_WIDGET_MENU):
        return widget.get_value()
    lib = _gp_library(gp)
    if lib is None:
        return None
    return _text_at(lib, int(this))


class Gphoto2Backend:
    name = "gphoto2"
    SHUTTER_KEYS = ("shutterspeed", "shutterspeed2", "exposuretime")
    ISO_KEYS = ("iso", "isospeed")
    APERTURE_KEYS = ("aperture", "f-number", "fnumber")
    ZOOM_KEY = "eoszoom"
    ZOOM_LEVELS = ["1", "5", "10"]

    def __init__(self, gp_module=None, clock=time.monotonic, sleep=time.sleep):
        self.gp = gp_module if gp_module is not None else _import_gphoto2()
        self.clock = clock
        self.sleep = sleep
        self.camera = None
        self._ready_at = 0.0          # no fire before this (just connected)
        self._use_capture = False     # the trigger was refused: capture call from now on
        self._shutter_s = 0.0         # the shutter speed last read or set, seconds
        _gphoto2_debug_log(self.gp)
        self.dest = None
        self._ident = None           # the open camera's port
        self._exp_keys = {}          # field -> the setting that worked (per connection)
        self.reconnects = 0          # exposure pauses (spec/usb-exposure-control.md)
        self._models = {}            # address -> model, from the last scan
        self._pending = []           # (folder, name) added, waiting for the shot's quiet
        self._last_added = None
        self._errors = 0
        self._zoom_ok = None
        self.no_preview = False      # the camera has no live view over USB
        self._busy_since = None      # first "busy" of the current run of them
        self._busy_until = 0.0       # don't ask again before this
        self._preview_frames = 0
        self._reset_shot_state()

    def _reset_shot_state(self):
        self._live_asked = False     # live view asked of the camera since the last shot
        self._last_preview = None    # when live view was last wanted
        self._next_events = 0.0      # throttled event check, while live view runs
        self._shot_until = 0.0       # a shot is on its way until its file or this
        self._shot_files = []        # (folder, name) already taken for this shot

    def available(self):
        return self.gp is not None

    def install_hint(self):
        return "Install the gphoto2 Python package (pip install gphoto2) for USB cameras."

    def list_cameras(self):
        if self.gp is None:
            return []
        try:
            cams = self.gp.Camera.autodetect()
        except Exception:
            return []
        out = []
        for i in range(cams.count()):
            model, addr = cams.get_name(i), cams.get_value(i)
            self._models[addr] = model
            out.append(CameraInfo(make_key(self.name, addr), f"USB · {model}", model))
        return out

    def _code(self, e):
        return getattr(e, "code", None)

    def _connect(self, ident):
        """A fresh, initialised Camera on `ident`'s port (open and the
        exposure reconnect share this)."""
        gp = self.gp
        cam = gp.Camera()
        try:
            ports = gp.PortInfoList()
            ports.load()
            cam.set_port_info(ports[ports.lookup_path(ident)])
            model = self._models.get(ident)
            if model:
                abil = gp.CameraAbilitiesList()
                abil.load()
                cam.set_abilities(abil[abil.lookup_model(model)])
            cam.init()
        except gp.GPhoto2Error as e:
            if self._code(e) == gp.GP_ERROR_IO_USB_CLAIM:
                raise CameraBusy(str(e))
            raise CameraLost(str(e))
        return cam

    def open(self, ident, dest=None):
        self.close()
        self.dest = dest
        cam = self._connect(ident)
        self.camera = cam
        self._ident = ident
        self._ready_at = self.clock() + RECONNECT_SETTLE_S
        self._use_capture = False
        self._exp_keys = {}
        self._pending, self._last_added, self._errors = [], None, 0
        self._zoom_ok = None
        self.no_preview = False
        self._busy_since = None
        self._busy_until = 0.0
        self._preview_frames = 0
        self._reset_shot_state()
        return self.reported_model() or self._models.get(ident, "")

    def reported_model(self):
        """The model the camera itself reports. libgphoto2 matches a camera
        to its table by USB id, and Fujifilm reuses ids across models (a
        GFX 50S II matches the GFX100 entry), so the table name can be
        another camera's."""
        try:
            cfg = self.camera.get_config()
            model = str(widget_value(self.gp, cfg.get_child_by_name("cameramodel")) or "").strip()
        except Exception:
            return ""
        try:
            maker = str(widget_value(self.gp, cfg.get_child_by_name("manufacturer")) or "").split()
        except Exception:
            maker = []
        if model and maker and maker[0].lower() not in model.lower():
            model = f"{maker[0]} {model}"
        return model

    def close(self):
        cam, self.camera = self.camera, None
        if cam is not None:
            try:
                cam.exit()
            except Exception:
                pass

    def set_dest(self, dest):
        self.dest = dest

    def start_live(self):
        pass                          # libgphoto2 starts live view on the first preview

    def stop_live(self):
        if self.camera is None:
            return
        self._last_preview = None     # events are checked at the full rate again
        # Canon EOS keeps the mirror up and the screen dark until told
        # otherwise; ending live view is a config value there, absent elsewhere.
        # One setting, not the whole configuration (~150 on a Fujifilm). A
        # Fujifilm has no such setting: its live view ends with the next shot
        # (libgphoto2's capture call), which is why `_live_asked` stays set.
        try:
            single = getattr(self.camera, "get_single_config", None)
            if single is not None:
                w = single("viewfinder")
                w.set_value(0)
                self.camera.set_single_config("viewfinder", w)
            else:
                cfg = self.camera.get_config()
                w = cfg.get_child_by_name("viewfinder")
                w.set_value(0)
                self.camera.set_config(cfg)
        except Exception:
            pass

    def _failed(self, e):
        gp = self.gp
        code = self._code(e)
        if code == gp.GP_ERROR_CAMERA_BUSY:
            return                    # mid-capture; try again next time
        self._errors += 1
        if self._errors >= LOST_AFTER_ERRORS or code == gp.GP_ERROR_IO_USB_FIND:
            raise CameraLost(str(e))

    def preview(self):
        if self.camera is None:
            raise CameraLost("not open")
        now = self.clock()
        self._last_preview = now
        if now < self._busy_until:
            return None                       # it said busy a moment ago
        # Set before asking: a request that starts the camera's live view and
        # then fails still leaves it running, and the next shot must end it.
        self._live_asked = True
        try:
            cf = self.camera.capture_preview()
            data = bytes(memoryview(cf.get_data_and_size()))
        except self.gp.GPhoto2Error as e:
            code = self._code(e)
            if code == getattr(self.gp, "GP_ERROR_NOT_SUPPORTED", -6):
                self.no_preview = True
                return None
            if code == self.gp.GP_ERROR_CAMERA_BUSY:
                if self._busy_since is None:
                    self._busy_since = now
                self._busy_until = now + PREVIEW_BUSY_BACKOFF_S
                if (not self._preview_frames
                        and now - self._busy_since >= PREVIEW_GIVE_UP_S):
                    self.no_preview = True    # shooting over USB still works
                return None
            self._failed(e)
            return None
        self._errors = 0
        self._busy_since = None
        self._busy_until = 0.0
        self._preview_frames += 1
        return data

    def fire(self):
        """Take a picture; its file(s) come through `poll`.

        libgphoto2's full capture call (`_capture_call`) when live view was
        asked for since the last shot (it ends live view first; the trigger
        call doesn't, which a Fujifilm refuses: spec §17), always on a
        Fujifilm, and on any camera once its trigger call was refused as busy;
        otherwise the trigger call, which doesn't hold this thread.

        Never straight after connecting (RECONNECT_SETTLE_S). "Busy" from the
        fire may still have taken the picture (GFX 50S II), so the shot's file
        is looked for (`_confirm_shot`) before it is fired again."""
        gp = self.gp
        if self.camera is None:
            raise CameraLost("not open")
        wait = self._ready_at - self.clock()
        if wait > 0:
            self.sleep(wait)
        self._shot_files = []
        start = self.clock()
        tries = 0
        taken = None
        while True:
            try:
                taken = None
                if self._capture_call():
                    try:
                        taken = self.camera.capture(getattr(gp, "GP_CAPTURE_IMAGE", 0))
                    except gp.GPhoto2Error as e:
                        if self._code(e) != getattr(gp, "GP_ERROR_NOT_SUPPORTED", -6):
                            raise
                        taken = None
                        self.camera.trigger_capture()
                else:
                    self.camera.trigger_capture()
                break
            except gp.GPhoto2Error as e:
                if self._code(e) != gp.GP_ERROR_CAMERA_BUSY:
                    self._failed(e)
                    raise (CameraLost(str(e)) if self._errors >= LOST_AFTER_ERRORS
                           else RuntimeError(str(e)))
                if self._confirm_shot(FIRE_CONFIRM_S + self._shutter_s):
                    print(f"usb camera: the camera answered busy ({e}) but took the shot "
                          f"({self.clock() - start:.2f} s)")
                    break
                if tries >= FIRE_BUSY_TRIES:
                    raise RuntimeError(str(e))     # busy never counts towards "lost"
                tries += 1
                again = "" if self._capture_call() else " with the capture call"
                self._use_capture = True
                print(f"usb camera: the camera answered busy ({e}) and no shot came; "
                      f"firing again{again}")
        self._errors = 0
        self._live_asked = False
        self._shot_until = self.clock() + SHOT_WAIT_S
        if taken is not None:
            self._add_file(taken.folder, taken.name)

    def _capture_call(self):
        """The full capture call rather than the trigger call? With live view
        asked for since the last shot; always on a Fujifilm: the user's GFX
        50S II log (2026-10-30) has two capture-call shots in a row working
        and the trigger call that followed refused as busy, every time; and on
        any camera once its trigger call was refused as busy with no shot."""
        model = (self._models.get(self._ident) or "").lower()
        return bool(self._live_asked or self._use_capture or "fuji" in model)

    def _confirm_shot(self, seconds):
        """After a busy answer to a fire: did the camera take the picture?
        True once its file is seen, as an event or waiting in the camera's
        own memory (then it is queued like any shot's file)."""
        gp = self.gp
        end = self.clock() + max(0.5, seconds)
        next_list = self.clock() + 1.0
        while self.clock() < end:
            t0 = self.clock()
            try:
                kind, data = self.camera.wait_for_event(CONFIRM_POLL_MS)
            except gp.GPhoto2Error:
                kind, data = None, None
            if kind == gp.GP_EVENT_FILE_ADDED and not is_live_view_file(data.name):
                self._add_file(data.folder, data.name)
                return True
            spent = self.clock() - t0
            if kind != gp.GP_EVENT_FILE_ADDED and spent < CONFIRM_POLL_MS / 2000.0:
                self.sleep(CONFIRM_POLL_MS / 1000.0 - spent)   # the wait returned early
            if self.clock() >= next_list:
                next_list = self.clock() + 1.0
                if self._take_held():
                    return True
        return self._take_held()

    def _take_held(self):
        held = self.held_files()
        for folder, name in held:
            self._add_file(folder, name)
        return bool(held)

    def held_files(self):
        """Shots waiting in the camera's OWN memory (a store with no DCIM
        folder: never a card), as (folder, name). A body in tether mode keeps
        each shot there until the computer takes it (spec §19-20)."""
        if self.camera is None:
            return []

        def names(lst):
            return [lst.get_name(i) for i in range(lst.count())]
        out = []
        try:
            stores = names(self.camera.folder_list_folders("/"))
        except Exception:
            return []
        for store in stores:
            top = "/" + store.strip("/")
            try:
                if any(f.upper() == "DCIM" for f in names(self.camera.folder_list_folders(top))):
                    continue                       # a memory card: never touched
                files = names(self.camera.folder_list_files(top))
            except Exception:
                continue
            for n in files:
                if (os.path.splitext(n)[1].lower() in HELD_EXTS
                        and not is_live_view_file(n) and (top, n) not in self._pending):
                    out.append((top, n))
        return out

    def collect_held(self):
        """Bring in shots the camera is holding (live view stuck busy after a
        shot whose file never came): queued like a shot's files. The count."""
        n = len(self.held_files())
        if n and self._take_held():
            self._shot_until = self.clock() + SHOT_WAIT_S
        return n

    def _add_file(self, folder, name):
        # A file the capture call returned might be announced again as an
        # event: one download per file per shot's group. (Not across groups:
        # some bodies name every shot alike, e.g. capt0000.jpg.)
        if (folder, name) in self._shot_files:
            return
        self._shot_files.append((folder, name))
        self._pending.append((folder, name))
        self._last_added = self.clock()

    def _events_due(self):
        """Throttle the event check while live view runs and no shot is on
        its way (LIVE_EVENTS_EVERY_S)."""
        now = self.clock()
        live = self._last_preview is not None and now - self._last_preview < LIVE_RECENT_S
        if not live or self._pending or now < self._shot_until:
            return True
        if now < self._next_events:
            return False
        self._next_events = now + LIVE_EVENTS_EVERY_S
        return True

    def poll(self, timeout_s):
        """Events since the last call: ("file", path) for each file brought
        into dest, ("skipped", name) for one there was nowhere to put."""
        gp = self.gp
        if self.camera is None:
            raise CameraLost("not open")
        if not self._events_due():
            return []
        end = self.clock() + max(0.0, timeout_s)
        wait_ms = max(1, int(timeout_s * 1000))
        while True:
            try:
                kind, data = self.camera.wait_for_event(wait_ms)
            except gp.GPhoto2Error as e:
                self._failed(e)
                break
            self._errors = 0
            if kind == gp.GP_EVENT_FILE_ADDED:
                if not is_live_view_file(data.name):
                    self._add_file(data.folder, data.name)
                wait_ms = 1
                continue
            if kind == gp.GP_EVENT_TIMEOUT or self.clock() >= end:
                break
            wait_ms = 1
        out = []
        if self._pending and self.clock() - (self._last_added or 0) >= QUIET_S:
            self._shot_until = 0.0            # the shot's files are in
            self._shot_files = []
            pending, self._pending = self._pending, []
            keep = set(keep_raw([n for _f, n in pending]))
            saved_all = bool(self.dest)
            for folder, name in pending:
                if name not in keep:
                    continue
                if not self.dest:
                    out.append(("skipped", name))
                    continue
                path = self._download(folder, name)
                if path:
                    out.append(("file", path))
                else:
                    saved_all = False
            if saved_all:
                # the other half of a RAW + JPEG pair, once its RAW is safe:
                # a body holding it still counts the transfer as unfinished
                for folder, name in pending:
                    if name not in keep:
                        self._clear(folder, name)
        return out

    def _clear(self, folder, name):
        """Delete a shot from the camera's own memory (never a card). A body
        in tether mode holds each shot until the computer deletes it, and
        until then refuses live view (GFX 50S II, test 6, spec §19-20); the
        protocol's normal end to a transfer, which libgphoto2's command line
        also does after capturing and downloading."""
        if on_memory_card(folder):
            return False
        try:
            self.camera.file_delete(folder, name)
            return True
        except self.gp.GPhoto2Error as e:
            print(f"usb camera: couldn't clear {name} from the camera: {e}")
            return False

    def live_stalled(self):
        """Live view gave frames, then has answered busy for LIVE_STALL_S."""
        return bool(self._preview_frames and self._busy_since is not None
                    and self.clock() - self._busy_since >= LIVE_STALL_S)

    def _download(self, folder, name):
        gp = self.gp
        target = unique_path(self.dest, name)
        part = target + ".part"
        try:
            cf = self.camera.file_get(folder, name, gp.GP_FILE_TYPE_NORMAL)
            got = len(memoryview(cf.get_data_and_size()))
            cf.save(part)
            os.replace(part, target)
        except gp.GPhoto2Error as e:
            try:
                os.remove(part)
            except OSError:
                pass
            # One file that can't be fetched (gone again, like a live view
            # frame) is not a lost camera; repeated failures or no USB are.
            self._failed(e)
            print(f"usb camera: couldn't download {name}: {e}")
            return None
        self._errors = 0
        if self._saved_whole(folder, name, target, got):
            self._clear(folder, name)
        return target

    def _saved_whole(self, folder, name, target, got):
        """The file on disk is every byte received, and as long as the camera
        says the file is: only then may the camera's copy go."""
        try:
            on_disk = os.path.getsize(target)
        except OSError:
            return False
        if on_disk != got or got <= 0:
            print(f"usb camera: {name}: saved {on_disk} of {got} bytes; left on the camera")
            return False
        size = None
        try:
            info = self.camera.file_get_info(folder, name).file
            if info.fields & getattr(self.gp, "GP_FILE_INFO_SIZE", 1 << 2):
                size = int(info.size)
        except Exception:
            size = None                       # no size reported: the save check stands
        if size is not None and size != on_disk:
            print(f"usb camera: {name}: the camera says {size} bytes, saved {on_disk}; "
                  "left on the camera")
            return False
        return True

    def addons(self):
        """Unofficial add-ons found on the camera's card(s) (spec §12). A
        listing that fails is no finding."""
        if self.camera is None:
            return []

        def names(lst):
            return [lst.get_name(i) for i in range(lst.count())]
        found = []
        try:
            stores = names(self.camera.folder_list_folders("/"))
        except Exception:
            return []
        for store in stores:
            top = "/" + store.strip("/")
            try:
                files = names(self.camera.folder_list_files(top))
                folders = names(self.camera.folder_list_folders(top))
            except Exception:
                continue
            for a in addons_on_card(files, folders):
                if a not in found:
                    found.append(a)
        return found

    def exposure(self):
        """Shutter, ISO, aperture. One setting at a time where libgphoto2 can
        (reading the whole configuration is ~150 properties on a Fujifilm,
        which stalls live view every time it is done)."""
        if self.camera is None:
            return {}
        single = getattr(self.camera, "get_single_config", None)
        if single is not None:
            out = {}
            for field, keys in (("shutter", self.SHUTTER_KEYS), ("iso", self.ISO_KEYS),
                                ("aperture", self.APERTURE_KEYS)):
                for k in keys:
                    try:
                        v = widget_value(self.gp, single(k))
                    except Exception:
                        continue
                    if v not in (None, ""):
                        out[field] = str(v)
                        break
            self._note_shutter(out.get("shutter"))
            return out
        try:
            cfg = self.camera.get_config()
        except Exception:
            return {}
        out = {}
        for field, keys in (("shutter", self.SHUTTER_KEYS), ("iso", self.ISO_KEYS),
                            ("aperture", self.APERTURE_KEYS)):
            for k in keys:
                try:
                    v = widget_value(self.gp, cfg.get_child_by_name(k))
                except Exception:
                    continue
                if v not in (None, ""):
                    out[field] = str(v)
                    break
        return out

    # -- setting the exposure (spec/usb-exposure-control.md) -----------------
    EXPOSURE_FIELDS = (("shutter", SHUTTER_KEYS), ("iso", ISO_KEYS))

    def _setting(self, key):
        """The camera's setting `key` as a widget (one setting, not the whole
        configuration)."""
        single = getattr(self.camera, "get_single_config", None)
        if single is not None:
            return single(key)
        return self.camera.get_config().get_child_by_name(key)

    def exposure_choices(self):
        """{"shutter": [values], "iso": [values]}: what the camera offers for
        each field it lets the computer set (at least two choices), in its
        own order. The setting used is remembered per connection."""
        if self.camera is None:
            return {}
        out = {}
        for field, keys in self.EXPOSURE_FIELDS:
            for k in keys:
                try:
                    w = self._setting(k)
                    n = int(w.count_choices())
                    vals = [str(w.get_choice(i)) for i in range(n)]
                except Exception:
                    continue
                if len(vals) >= 2:
                    out[field] = vals
                    self._exp_keys[field] = k
                    break
        return out

    def shot_pending(self):
        """A shot is on its way (fired, files not all in yet)."""
        return bool(self._pending) or self.clock() < self._shot_until

    def _put(self, field, value):
        """Set one field and read it back: (ok, text)."""
        key = self._exp_keys.get(field)
        if key is None:
            return False, "the camera has no such setting"
        try:
            w = self._setting(key)
            w.set_value(str(value))
            single = getattr(self.camera, "set_single_config", None)
            if single is not None:
                single(key, w)
            else:
                cfg = self.camera.get_config()
                cfg.get_child_by_name(key).set_value(str(value))
                self.camera.set_config(cfg)
            got = widget_value(self.gp, self._setting(key))
        except self.gp.GPhoto2Error as e:
            return False, str(e)
        except Exception as e:
            return False, str(e)
        if str(got) != str(value):
            return False, f"it reads {got}"
        if field == "shutter":
            self._note_shutter(value)
        return True, ""

    def _note_shutter(self, value):
        if value:
            from core.base_exposure import seconds
            t = seconds(value)
            if t is not None:
                self._shutter_s = t

    def _reconnect(self):
        """End live view the only way libgphoto2 can on a Fujifilm without
        shooting: disconnect and connect again. The shot state, the hot
        folder and the frame count stay; live view starts again on the next
        preview."""
        cam, self.camera = self.camera, None
        if cam is not None:
            try:
                cam.exit()
            except Exception:
                pass
        self.camera = self._connect(self._ident)
        self._ready_at = self.clock() + RECONNECT_SETTLE_S
        self.reconnects += 1
        self._live_asked = False
        self._last_preview = None
        self._busy_since = None
        self._busy_until = 0.0

    def set_exposure(self, wanted):
        """Set {field: value}; {field: (ok, text)}. A camera that refuses
        while its live view runs (a Fujifilm answers Device Busy to every
        setting) gets one reconnect, then the refused fields again."""
        if self.camera is None:
            raise CameraLost("not open")
        if not self._exp_keys:
            self.exposure_choices()
        results = {f: self._put(f, v) for f, v in wanted.items()}
        failed = [f for f, (ok, _t) in results.items() if not ok
                  and self._exp_keys.get(f) is not None]
        if failed and self._live_asked:
            self._reconnect()
            for f in failed:
                results[f] = self._put(f, wanted[f])
        return results

    def zoom_levels(self):
        if self.camera is None:
            return []
        if self._zoom_ok is None:
            try:
                self.camera.get_config().get_child_by_name(self.ZOOM_KEY)
                self._zoom_ok = True
            except Exception:
                self._zoom_ok = False
        return list(self.ZOOM_LEVELS) if self._zoom_ok else []

    def set_zoom(self, level):
        if self.camera is None or not self.zoom_levels():
            return
        try:
            cfg = self.camera.get_config()
            cfg.get_child_by_name(self.ZOOM_KEY).set_value(str(level))
            self.camera.set_config(cfg)
        except Exception as e:
            print(f"usb camera: zoom {level}: {e}")


def release_macos_camera_service(run=subprocess.run):
    """End the hold macOS's own camera service has on a PTP camera, so
    libgphoto2 can claim it (it may come back when the camera is replugged).
    Returns True if anything was ended."""
    if sys.platform != "darwin":
        return False
    ended = False
    for proc in ("ptpcamerad", "PTPCamera"):
        try:
            r = run(["killall", proc], capture_output=True, timeout=5)
            ended = ended or r.returncode == 0
        except Exception:
            pass
    return ended


# --------------------------------------------------------------------------- #
# digiCamControl (Windows)
# --------------------------------------------------------------------------- #
def _parse(text):
    """digiCamControl's answers: JSON when they parse, else plain text."""
    t = (text or "").strip()
    try:
        v = json.loads(t)
    except (ValueError, TypeError):
        return t
    if isinstance(v, dict):
        for k in ("response", "Response", "value", "Value", "result", "Result"):
            if k in v:
                return v[k]
        return v
    return v


def _as_list(v):
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v or "").strip()
    if not s or s.upper() == "OK":
        return []
    for sep in ("\r\n", "\n", ";", ","):
        if sep in s:
            return [x.strip() for x in s.split(sep) if x.strip()]
    return [s]


class DigiCamControlBackend:
    name = "dcc"

    def __init__(self, port=DCC_PORT, opener=None, clock=time.monotonic, sleep=time.sleep):
        self.base = f"http://127.0.0.1:{int(port)}"
        self._open_url = opener or urllib.request.urlopen
        self.clock = clock
        self.sleep = sleep
        self.camera = None
        self.dest = None
        self._old_folder = None
        self._last = None
        self._errors = 0
        self._arriving = []          # (folder, name, deadline): reported, not on disk yet
        self._next_check = 0.0

    def install_hint(self):
        return ("On Windows, install digiCamControl and turn on its web server "
                "(Settings → Webserver) for USB cameras.")

    # -- HTTP --------------------------------------------------------------
    def _get(self, path, timeout=1.0, binary=False):
        try:
            with self._open_url(self.base + path, timeout=timeout) as r:
                data = r.read()
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise CameraLost(f"digiCamControl isn't answering ({e})")
        return data if binary else data.decode("utf-8", "replace")

    def _slc(self, cmd, p1="", p2="", timeout=1.0):
        q = urllib.parse.urlencode({"slc": cmd, "param1": p1, "param2": p2})
        return _parse(self._get("/?" + q, timeout))

    def _do(self, cmd):
        self._get("/?" + urllib.parse.urlencode({"CMD": cmd}))

    # -- backend -----------------------------------------------------------
    def available(self):
        try:
            self._get("/?" + urllib.parse.urlencode({"slc": "get", "param1": "camera",
                                                    "param2": ""}), timeout=0.4)
            return True
        except CameraLost:
            return False

    def list_cameras(self):
        try:
            serials = _as_list(self._slc("list", "cameras"))
        except CameraLost:
            return []
        if not serials:
            try:
                cur = str(self._slc("get", "camera") or "").strip()
            except CameraLost:
                return []
            serials = [cur] if cur and cur != "-" else []
        return [CameraInfo(make_key(self.name, s), f"digiCamControl · {s}", s)
                for s in serials]

    def open(self, ident, dest=None):
        self.close()
        self._slc("set", "camera", ident)
        try:
            self._old_folder = str(self._slc("get", "session.folder") or "") or None
        except CameraLost:
            self._old_folder = None
        self.camera = ident
        self.set_dest(dest)
        self._last = str(self._slc("get", "lastcaptured") or "")
        self._errors = 0
        self._arriving, self._next_check = [], 0.0
        return ident

    def close(self):
        if self.camera is None:
            return
        self.camera = None
        try:
            self._do("LiveViewWnd_Hide")
            if self._old_folder:
                self._slc("set", "session.folder", self._old_folder)
        except CameraLost:
            pass

    def set_dest(self, dest):
        self.dest = dest
        if self.camera is not None and dest:
            self._slc("set", "session.folder", dest)

    def start_live(self):
        self._do("LiveViewWnd_Show")

    def stop_live(self):
        try:
            self._do("LiveViewWnd_Hide")
        except CameraLost:
            pass

    def preview(self):
        try:
            data = self._get("/liveview.jpg", timeout=1.0, binary=True)
        except CameraLost:
            self._errors += 1
            if self._errors >= LOST_AFTER_ERRORS:
                raise
            return None
        self._errors = 0
        return data if data[:2] == b"\xff\xd8" else None

    def fire(self):
        r = self._slc("capture", timeout=5.0)
        if isinstance(r, str) and r and r.upper() != "OK" and "error" in r.lower():
            raise RuntimeError(r)

    CHECK_EVERY_S = 0.25        # how often digiCamControl is asked for its last shot
    ARRIVE_S = 5.0              # a reported file must appear on disk within this

    def poll(self, timeout_s):
        """("file", path) once a new shot's file is on disk (the RAW of a
        RAW + JPEG pair); ("skipped", name) when there is no folder to look in
        or the file never appears (digiCamControl set to keep shots on the
        camera only)."""
        end = self.clock() + max(0.0, timeout_s)
        out = []
        while True:
            now = self.clock()
            if now >= getattr(self, "_next_check", 0.0):
                self._next_check = now + self.CHECK_EVERY_S
                v = str(self._slc("get", "lastcaptured") or "").strip()
                if v and v != "-" and v != self._last:
                    self._last = v
                    name = os.path.basename(v)
                    folder = os.path.dirname(v) if os.path.isabs(v) else (self.dest or "")
                    if not folder:
                        out.append(("skipped", name))
                    else:
                        self._arriving.append((folder, name, now + self.ARRIVE_S))
            keep = []
            for folder, name, deadline in self._arriving:
                path = self._prefer_raw(folder, name)
                if os.path.exists(path):
                    out.append(("file", path))
                elif now >= deadline:
                    out.append(("skipped", name))
                else:
                    keep.append((folder, name, deadline))
            self._arriving = keep
            if out or self.clock() >= end:
                return out
            self.sleep(min(self.CHECK_EVERY_S, max(0.0, end - self.clock())))

    @staticmethod
    def _prefer_raw(folder, name):
        """digiCamControl reports the LAST file of a shot; with RAW + JPEG
        that can be the JPEG. The shot is the RAW beside it."""
        path = os.path.join(folder, name)
        if is_raw_name(name):
            return path
        stem = os.path.splitext(name)[0].lower()
        try:
            for e in os.scandir(folder):
                if os.path.splitext(e.name)[0].lower() == stem and is_raw_name(e.name):
                    return e.path
        except OSError:
            pass
        return path

    def addons(self):
        return []                    # its web interface can't list the card

    def exposure(self):
        out = {}
        for field, key in (("shutter", "shutterspeed"), ("iso", "iso"), ("aperture", "aperture")):
            try:
                v = self._slc("get", key)
            except CameraLost:
                return out
            v = str(v or "").strip()
            if v and v.upper() != "OK" and v != "-":
                out[field] = v
        return out

    def zoom_levels(self):
        return []

    def set_zoom(self, level):
        pass

    # Setting exposure is libgphoto2-only for now (spec/usb-exposure-control.md).
    def exposure_choices(self):
        return {}

    def shot_pending(self):
        return False

    def set_exposure(self, wanted):
        return {f: (False, "not over this connection") for f in wanted}


# --------------------------------------------------------------------------- #
# a synthetic camera
# --------------------------------------------------------------------------- #
class FakeBackend:
    """A camera that makes a moving test picture and writes a small JPEG per
    shot. For tests, and for trying the route without a camera
    (FREECCR_FAKE_USB_CAMERA=1)."""
    name = "fake"

    def __init__(self, cameras=("Test Camera",), clock=time.monotonic, file_ext=".jpg"):
        self.cameras = list(cameras)
        self.clock = clock
        self.file_ext = file_ext
        self.camera = None
        self.dest = None
        self.live = False
        self.fired = 0
        self.lost = False
        self.busy = False
        self._due = []                # (time, name)
        self.zoom = "1"
        self.frames = 0
        self.card_addons = []
        # exposure (spec/usb-exposure-control.md): refused while live view
        # runs, like a GFX 50S II; a reconnect ends live view
        self.shutters = ["1/250", "1/125", "1/60", "1/30", "1/15"]
        self.isos = ["100", "200", "400", "-1"]
        self.settings = {"shutter": "1/60", "iso": "100"}
        self.refuse_in_live = True
        self.reconnects = 0
        self.set_calls = []
        self.pending_shot = False

    def available(self):
        return True

    def install_hint(self):
        return ""

    def list_cameras(self):
        return [CameraInfo(make_key(self.name, str(i)), f"USB · {m}", m)
                for i, m in enumerate(self.cameras)]

    def open(self, ident, dest=None):
        if self.busy:
            raise CameraBusy("held by another process")
        self.camera = ident
        self.dest = dest
        return self.cameras[int(ident)] if str(ident).isdigit() else ident

    def close(self):
        self.camera = None

    def set_dest(self, dest):
        self.dest = dest

    def start_live(self):
        self.live = True

    def stop_live(self):
        self.live = False

    def preview(self):
        if self.lost:
            raise CameraLost("unplugged")
        import cv2
        import numpy as np
        self.frames += 1
        h, w = 400, 600
        x = np.linspace(0, 255, w, dtype=np.float32)[None, :].repeat(h, 0)
        img = np.dstack([x, np.roll(x, self.frames * 7, axis=1), 255 - x]).astype(np.uint8)
        ok, buf = cv2.imencode(".jpg", img)
        return buf.tobytes() if ok else None

    def fire(self):
        if self.lost:
            raise CameraLost("unplugged")
        self.fired += 1
        self._due.append((self.clock() + 0.05, f"FAKE{self.fired:04d}{self.file_ext}"))

    def poll(self, timeout_s):
        if self.lost:
            raise CameraLost("unplugged")
        out, keep = [], []
        for t, name in self._due:
            if self.clock() < t:
                keep.append((t, name))
            elif not self.dest:
                out.append(("skipped", name))
            else:
                import cv2
                import numpy as np
                path = unique_path(self.dest, name)
                cv2.imwrite(path, np.full((40, 60, 3), 128, np.uint8))
                out.append(("file", path))
        self._due = keep
        return out

    def addons(self):
        return list(self.card_addons)

    def exposure(self):
        return {"shutter": self.settings["shutter"], "iso": self.settings["iso"],
                "aperture": "8"}

    def exposure_choices(self):
        return {"shutter": list(self.shutters), "iso": list(self.isos)}

    def shot_pending(self):
        return bool(self.pending_shot or self._due)

    def set_exposure(self, wanted):
        self.set_calls.append(dict(wanted))
        if self.live and self.refuse_in_live:
            self.reconnects += 1          # ends live view; preview starts it again
        out = {}
        for f, v in wanted.items():
            ok = v in (self.shutters if f == "shutter" else self.isos)
            if ok:
                self.settings[f] = v
            out[f] = (ok, "" if ok else "not offered")
        return out

    def zoom_levels(self):
        return ["1", "5", "10"]

    def set_zoom(self, level):
        self.zoom = str(level)


def default_backends():
    """The backends this computer can use, best first."""
    out = []
    if os.environ.get("FREECCR_FAKE_USB_CAMERA") == "1":
        out.append(FakeBackend())
    gp = Gphoto2Backend()
    if gp.available():
        out.append(gp)
    if sys.platform == "win32" or os.environ.get("FREECCR_DCC") == "1":
        try:
            from PySide6.QtCore import QSettings
            port = int(QSettings("FreeCCR", "FreeCCR").value("live/dcc_port", DCC_PORT) or DCC_PORT)
        except Exception:
            port = DCC_PORT
        out.append(DigiCamControlBackend(port))
    return out


def install_hints():
    """What to install for USB cameras on this computer."""
    if sys.platform == "win32":
        return DigiCamControlBackend().install_hint()
    if _import_gphoto2() is None:
        return Gphoto2Backend(gp_module=None).install_hint()
    return ""
