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
def _import_gphoto2():
    try:
        import gphoto2 as gp
        return gp
    except Exception:
        return None


class Gphoto2Backend:
    name = "gphoto2"
    SHUTTER_KEYS = ("shutterspeed", "shutterspeed2", "exposuretime")
    ISO_KEYS = ("iso", "isospeed")
    APERTURE_KEYS = ("aperture", "f-number", "fnumber")
    ZOOM_KEY = "eoszoom"
    ZOOM_LEVELS = ["1", "5", "10"]

    def __init__(self, gp_module=None, clock=time.monotonic):
        self.gp = gp_module if gp_module is not None else _import_gphoto2()
        self.clock = clock
        self.camera = None
        self.dest = None
        self._models = {}            # address -> model, from the last scan
        self._pending = []           # (folder, name) added, waiting for the shot's quiet
        self._last_added = None
        self._errors = 0
        self._zoom_ok = None
        self.no_preview = False      # the camera has no live view over USB

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

    def open(self, ident, dest=None):
        gp = self.gp
        self.close()
        self.dest = dest
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
        self.camera = cam
        self._pending, self._last_added, self._errors = [], None, 0
        self._zoom_ok = None
        self.no_preview = False
        return self._models.get(ident, "")

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
        # Canon EOS keeps the mirror up and the screen dark until told
        # otherwise; ending live view is a config value there, absent elsewhere.
        try:
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
        try:
            cf = self.camera.capture_preview()
            data = bytes(memoryview(cf.get_data_and_size()))
        except self.gp.GPhoto2Error as e:
            if self._code(e) == getattr(self.gp, "GP_ERROR_NOT_SUPPORTED", -6):
                self.no_preview = True
                return None
            self._failed(e)
            return None
        self._errors = 0
        return data

    def fire(self):
        if self.camera is None:
            raise CameraLost("not open")
        try:
            self.camera.trigger_capture()
        except self.gp.GPhoto2Error as e:
            self._failed(e)
            raise CameraLost(str(e)) if self._errors >= LOST_AFTER_ERRORS else RuntimeError(str(e))
        self._errors = 0

    def poll(self, timeout_s):
        """Events since the last call: ("file", path) for each file brought
        into dest, ("skipped", name) for one there was nowhere to put."""
        gp = self.gp
        if self.camera is None:
            raise CameraLost("not open")
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
                self._pending.append((data.folder, data.name))
                self._last_added = self.clock()
                wait_ms = 1
                continue
            if kind == gp.GP_EVENT_TIMEOUT or self.clock() >= end:
                break
            wait_ms = 1
        out = []
        if self._pending and self.clock() - (self._last_added or 0) >= QUIET_S:
            pending, self._pending = self._pending, []
            keep = set(keep_raw([n for _f, n in pending]))
            for folder, name in pending:
                if name not in keep:
                    continue
                if not self.dest:
                    out.append(("skipped", name))
                    continue
                out.append(("file", self._download(folder, name)))
        return out

    def _download(self, folder, name):
        gp = self.gp
        target = unique_path(self.dest, name)
        part = target + ".part"
        try:
            cf = self.camera.file_get(folder, name, gp.GP_FILE_TYPE_NORMAL)
            cf.save(part)
            os.replace(part, target)
        except gp.GPhoto2Error as e:
            try:
                os.remove(part)
            except OSError:
                pass
            raise CameraLost(str(e))
        return target

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
        if self.camera is None:
            return {}
        try:
            cfg = self.camera.get_config()
        except Exception:
            return {}
        out = {}
        for field, keys in (("shutter", self.SHUTTER_KEYS), ("iso", self.ISO_KEYS),
                            ("aperture", self.APERTURE_KEYS)):
            for k in keys:
                try:
                    v = cfg.get_child_by_name(k).get_value()
                except Exception:
                    continue
                if v not in (None, ""):
                    out[field] = str(v)
                    break
        return out

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
        return {"shutter": "1/60", "iso": "100", "aperture": "8"}

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
