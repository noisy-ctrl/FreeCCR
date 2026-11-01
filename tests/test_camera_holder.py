#!/usr/bin/env python3
"""Which program is holding the camera (spec/camera-holder.md): macOS's ioreg
read, the Linux /proc read, UsbCamera keeping what it found on a busy open, and
the Live Capture Monitor naming it."""

import os
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import camera_backends as cb  # noqa: E402
from core import camera_holder as ch  # noqa: E402

# Shaped like `ioreg -r -c IOUSBHostDevice -l -w 0`: a webcam held through its
# interface by its own service, a hub, and the camera held by ptpcamerad.
IOREG = """\
+-o FaceTime HD Camera@14200000  <class IOUSBHostDevice, id 0x100000a1b, registered, matched, active, busy 0 (5 ms), retain 30>
  | {
  |   "sessionID" = 1234567
  |   "USB Product Name" = "FaceTime HD Camera"
  |   "idVendor" = 1452
  |   "idProduct" = 34068
  |   "locationID" = 337641472
  |   "USB Address" = 2
  | }
  |
  +-o VDC@0  <class IOUSBHostInterface, id 0x100000a2c, registered, matched, active, busy 0 (0 ms), retain 9>
      {
        "bInterfaceNumber" = 0
        "UsbExclusiveOwner" = "pid 162, VDCAssistant"
      }

+-o USB3.1 Hub@02100000  <class IOUSBHostDevice, id 0x100000b10, registered, matched, active, busy 0 (2 ms), retain 22>
  | {
  |   "USB Product Name" = "USB3.1 Hub"
  |   "idVendor" = 3034
  |   "locationID" = 34603008
  |   "USB Address" = 1
  | }
  |
  +-o GFX 50S II@02110000  <class IOUSBHostDevice, id 0x100000b44, registered, matched, active, busy 0 (12 ms), retain 28>
    | {
    |   "USB Product Name" = "GFX 50S II"
    |   "idVendor" = 1227
    |   "idProduct" = 12345
    |   "locationID" = 34668544
    |   "USB Address" = 4
    | }
    |
    +-o GFX 50S II@0  <class IOUSBHostInterface, id 0x100000b55, registered, matched, active, busy 0 (0 ms), retain 8>
        {
          "UsbExclusiveOwner" = "pid 512, ptpcamerad"
        }
"""


def test_ioreg_is_read_into_devices_with_their_owners():
    devs = ch.parse_ioreg(IOREG)
    assert [d["name"] for d in devs] == ["FaceTime HD Camera", "USB3.1 Hub", "GFX 50S II"]
    assert devs[0]["owners"] == [(162, "VDCAssistant")]      # from its interface
    assert devs[1]["owners"] == []
    gfx = devs[2]
    assert gfx["owners"] == [(512, "ptpcamerad")]
    assert gfx["vendor"] == 0x04CB and gfx["address"] == 4 and gfx["location"] >> 24 == 2


def test_the_camera_is_picked_by_port_then_name_then_maker():
    devs = ch.parse_ioreg(IOREG)
    assert ch.pick_device(devs, "usb:002,004")["name"] == "GFX 50S II"
    assert ch.pick_device(devs, "usb:009,009", "Fujifilm GFX 50S II")["name"] == "GFX 50S II"
    assert ch.pick_device(devs, "", "")["name"] == "GFX 50S II"          # Fujifilm's id
    # two held devices and nothing to tell them apart: nothing claimed
    two = [dict(d, vendor=None, name="Thing") for d in devs if d["owners"]]
    assert ch.pick_device(two, "usb:009,009", "") is None
    # the webcam alone held: not taken for the camera
    cam_only = [d for d in devs if d["name"] == "FaceTime HD Camera"]
    assert ch.pick_device(cam_only, "", "") is None


def test_mac_holders_run_ioreg_and_never_raise():
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return types.SimpleNamespace(returncode=0, stdout=IOREG)
    hs = ch.find("usb:002,004", "GFX 50S II", platform="darwin", run=run)
    assert calls[0][0] == ch.IOREG and calls[0][1]["timeout"] == ch.TIMEOUT_S
    assert hs == [ch.Holder(512, "ptpcamerad", True)]
    assert hs[0].describe() == "“ptpcamerad” (process 512), macOS's own camera service"
    assert ch.Holder(77, "Some App").describe() == "“Some App” (process 77)"

    def missing(cmd, **kw):
        raise FileNotFoundError("ioreg")
    assert ch.find("usb:002,004", platform="darwin", run=missing) == []
    assert ch.find("usb:002,004", platform="darwin",
                   run=lambda c, **k: types.SimpleNamespace(returncode=1, stdout="")) == []
    assert ch.find("usb:002,004", platform="win32") == []


def test_linux_names_the_process_with_the_device_file_open(tmp_path):
    dev = tmp_path / "dev"
    (dev / "002").mkdir(parents=True)
    node = dev / "002" / "004"
    node.write_text("")
    proc = tmp_path / "proc"
    for pid, comm, links in (("1234", "gvfs-gphoto2-vo", [str(node)]),
                             ("88", "editor", [str(tmp_path / "other")]),
                             ("self", "x", [])):
        (proc / pid / "fd").mkdir(parents=True)
        (proc / pid / "comm").write_text(comm + "\n")
        for i, target in enumerate(links):
            os.symlink(target, proc / pid / "fd" / str(i))
    hs = ch.find("usb:002,004", platform="linux", proc=str(proc), dev=str(dev))
    assert hs == [ch.Holder(1234, "gvfs-gphoto2-vo", True)]
    assert ch.find("usb:002,005", platform="linux", proc=str(proc), dev=str(dev)) == []
    assert ch.find("not a port", platform="linux", proc=str(proc), dev=str(dev)) == []


class BusyBackend:
    name = "fake"
    _models = {"usb:001,004": "Fujifilm GFX 50S II"}

    def available(self):
        return True

    def open(self, ident, dest=None):
        raise cb.CameraBusy("Could not claim the USB device")

    def close(self):
        pass


def test_a_busy_open_keeps_who_holds_the_camera(monkeypatch):
    from core.usb_camera import UsbCamera
    seen = []
    monkeypatch.setattr(ch, "find", lambda port, model: seen.append((port, model))
                        or [ch.Holder(900, "OtherApp")])
    usb = UsbCamera(backends=[BusyBackend()])
    states = []
    usb.state.connect(lambda k, t: states.append((k, list(usb.holders))))
    usb._open("usb:fake:usb:001,004")
    assert seen == [("usb:001,004", "Fujifilm GFX 50S II")]
    assert states[-1] == ("busy", [ch.Holder(900, "OtherApp")])

    def broken(port, model):
        raise RuntimeError("boom")
    monkeypatch.setattr(ch, "find", broken)
    usb._open("usb:fake:usb:001,004")
    assert usb.status == "busy" and usb.holders == []


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from widgets import live_monitor as lm
    import test_live_monitor as tlm
    path = str(tmp_path / "live.ini")
    monkeypatch.setattr(lm, "_settings", lambda: QSettings(path, QSettings.IniFormat))
    from core.ccr_backend import ccr_backend
    monkeypatch.setattr(ccr_backend, "scan_type", "colour", raising=False)
    mon, feed, light, dev = tlm._monitor()
    yield mon, lm
    tlm._close(mon)


def test_the_monitor_names_the_holder(monitor, monkeypatch):
    mon, lm = monitor
    monkeypatch.setattr(lm.sys, "platform", "darwin")
    mon.usb.holders = [ch.Holder(900, "OtherApp")]
    mon.usb.state.emit("busy", "Could not claim the USB device")
    _app.processEvents()
    assert "“OtherApp” (process 900) is using the camera" in mon.canvas.message
    assert not mon.release_btn.isVisibleTo(mon)        # never offered for another program
    mon.usb.holders = [ch.Holder(512, "ptpcamerad", True)]
    mon.usb.state.emit("busy", "Could not claim the USB device")
    _app.processEvents()
    assert "macOS is holding the camera (“ptpcamerad” (process 512)" in mon.canvas.message
    assert mon.release_btn.isVisibleTo(mon)
    mon.usb.holders = []
    mon.usb.state.emit("busy", "x")
    _app.processEvents()
    assert mon.canvas.message.startswith("macOS is holding the camera, so")
