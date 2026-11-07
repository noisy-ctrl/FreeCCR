"""Which program is holding the camera (spec/camera-holder.md). Qt-free.

When libgphoto2 can't claim a USB camera, another program has it. This finds
out which, read only:
- macOS: Apple's `ioreg` lists the USB devices; a program that claimed one
  for itself shows on it (or its interface) as
  `"UsbExclusiveOwner" = "pid 512, ptpcamerad"`.
- Linux: the process with the port's `/dev/bus/usb/BBB/DDD` open.
Nothing is opened, changed or ended. Any failure gives [].
"""
import os
import re
import subprocess
import sys
from dataclasses import dataclass

IOREG = ["ioreg", "-r", "-c", "IOUSBHostDevice", "-l", "-w", "0"]
TIMEOUT_S = 5

# macOS's own camera service (Image Capture, Photos and Preview use it) and
# the GNOME desktop's camera mounter: holders the user didn't open themselves.
SYSTEM = {"ptpcamerad": "macOS's own camera service",
          "ptpcamera": "macOS's own camera service",
          "mscamerad": "macOS's own camera service",
          "image capture": "macOS's Image Capture",
          "photos": "macOS's Photos",
          "gvfs-gphoto2-vo": "the desktop's camera mounter",
          "gvfs-gphoto2-volume-monitor": "the desktop's camera mounter",
          "gvfsd-gphoto2": "the desktop's camera mounter"}

# USB vendor ids of camera makers (to pick the camera among held devices)
CAMERA_VENDORS = frozenset({0x04A9, 0x04B0, 0x054C, 0x04CB, 0x07B4, 0x04DA,
                            0x0A17, 0x25FB, 0x1A98, 0x1003})

_OWNER = re.compile(r"pid\s+(\d+)\s*,\s*(.+)")
_PROP = re.compile(r'"([^"]+)"\s*=\s*(.+)$')
_PORT = re.compile(r"usb:(\d+),(\d+)")


@dataclass
class Holder:
    pid: int
    name: str
    system: bool = False
    own: bool = False             # FreeCCR's own process (an earlier connection)

    def describe(self):
        if self.own:
            return f"FreeCCR itself (process {self.pid})"
        what = SYSTEM.get(self.name.lower())
        base = f"“{self.name}” (process {self.pid})"
        return f"{base}, {what}" if what else base


def _value(raw):
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    try:
        return int(raw)
    except ValueError:
        return raw


def parse_ioreg(text):
    """One dict per USB device: vendor, product, name, location, address,
    owners [(pid, name)], its interfaces' owners included."""
    devices = []
    cur = None
    for line in (text or "").splitlines():
        if "+-o " in line and "<class " in line:
            if "<class IOUSBHostDevice" in line or "<class IOUSBDevice" in line:
                cur = {"vendor": None, "product": None, "name": "", "location": None,
                       "address": None, "owners": []}
                devices.append(cur)
            continue
        if cur is None:
            continue
        m = _PROP.search(line)
        if not m:
            continue
        key, val = m.group(1), _value(m.group(2))
        if key == "UsbExclusiveOwner":
            o = _OWNER.search(str(val))
            if o:
                owner = (int(o.group(1)), o.group(2).strip())
                if owner not in cur["owners"]:
                    cur["owners"].append(owner)
        elif key == "idVendor" and cur["vendor"] is None and isinstance(val, int):
            cur["vendor"] = val
        elif key == "idProduct" and cur["product"] is None and isinstance(val, int):
            cur["product"] = val
        elif key in ("USB Product Name", "kUSBProductString") and not cur["name"]:
            cur["name"] = str(val)
        elif key == "locationID" and cur["location"] is None and isinstance(val, int):
            cur["location"] = val
        elif key == "USB Address" and cur["address"] is None and isinstance(val, int):
            cur["address"] = val
    return devices


def _words(s):
    return {w for w in re.split(r"[^a-z0-9]+", str(s or "").lower()) if len(w) >= 2}


def pick_device(devices, port="", model="", me=None):
    """The camera's entry among `devices`. Its port names it outright, held
    or not: a camera whose entry shows no owner has a holder we can't see
    (Image Capture's hold doesn't show), and another device's owner is never
    taken for it (2026-11-07: a device FreeCCR itself had open was named as
    the camera's holder while Image Capture had the camera). Without the port,
    only devices held by someone other than `me` (FreeCCR's process) count."""
    m = _PORT.search(str(port or ""))
    if m:
        bus, addr = int(m.group(1)), int(m.group(2))
        for d in devices:
            if (d["location"] is not None and d["address"] is not None
                    and (d["location"] >> 24) == bus and d["address"] == addr):
                return d
    held = [d for d in devices if any(pid != me for pid, _n in d["owners"])]
    if not held:
        return None
    want = _words(model) - {"usb", "camera"}
    if want:
        named = [d for d in held if want & _words(d["name"])]
        if len(named) == 1:
            return named[0]
    makers = [d for d in held if d["vendor"] in CAMERA_VENDORS]
    if len(makers) == 1:
        return makers[0]
    # nothing else to go on: the only held device, when a camera service
    # holds it (any other lone holder may have any other USB device)
    if len(held) == 1 and any(n.lower() in SYSTEM for _p, n in held[0]["owners"]):
        return held[0]
    return None


def _is_webcam_service(name):
    return name.lower() in ("vdcassistant", "applecameraassistant", "avconferenced")


def _holders(owners, me=None):
    """Holders from (pid, name) owners: FreeCCR's own process only when
    nobody else holds it (then it is an earlier connection of ours)."""
    others = [(pid, name) for pid, name in owners if pid != me]
    if not others:
        return [Holder(pid, name, own=True) for pid, name in owners if pid == me]
    return [Holder(pid, name, name.lower() in SYSTEM) for pid, name in others]


def mac_holders(port="", model="", run=subprocess.run, me=None):
    me = os.getpid() if me is None else me
    try:
        r = run(IOREG, capture_output=True, text=True, timeout=TIMEOUT_S)
    except Exception:
        return []
    if getattr(r, "returncode", 1) != 0:
        return []
    d = pick_device(parse_ioreg(r.stdout), port, model, me=me)
    return _holders(d["owners"], me) if d else []


def linux_holders(port, proc="/proc", dev="/dev/bus/usb"):
    m = _PORT.search(str(port or ""))
    if not m:
        return []
    target = os.path.join(dev, f"{int(m.group(1)):03d}", f"{int(m.group(2)):03d}")
    out = []
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return []
    me = os.getpid()
    for p in pids:
        if int(p) == me:
            continue
        fd_dir = os.path.join(proc, p, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue                       # another user's process: not ours to read
        hit = False
        for fd in fds:
            try:
                if os.readlink(os.path.join(fd_dir, fd)) == target:
                    hit = True
                    break
            except OSError:
                continue
        if not hit:
            continue
        try:
            with open(os.path.join(proc, p, "comm")) as f:
                name = f.read().strip() or p
        except OSError:
            name = p
        out.append(Holder(int(p), name, name.lower() in SYSTEM))
    return out


def find(port="", model="", platform=None, **kw):
    """Who holds the camera on `port` (libgphoto2's "usb:BBB,DDD"): a list of
    Holder, [] when it can't be told. Never raises."""
    platform = platform or sys.platform
    try:
        if platform == "darwin":
            return mac_holders(port, model, **kw)
        if platform.startswith("linux"):
            return linux_holders(port, **kw)
    except Exception:
        return []
    return []


def describe(holders):
    return "; ".join(h.describe() for h in holders or [])
