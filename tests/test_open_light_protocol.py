#!/usr/bin/env python3
"""Open Light Protocol (spec/open-light-protocol.md): the text helpers, and
FreeCCR's light link driving the MicroPython reference firmware end to end
through a fake serial port, next to an unchanged Scanlight."""

import importlib.util
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
sys.path.insert(0, HERE)

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import capture_light as cl  # noqa: E402
from core import olp  # noqa: E402
from core.light_link import LightLink  # noqa: E402
from test_live_capture_light import Clock  # noqa: E402

FIRMWARE = os.path.join(HERE, "..", "examples", "open_light", "micropython", "main.py")


def _firmware():
    spec = importlib.util.spec_from_file_location("olp_firmware", FIRMWARE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # main() isn't run: no __main__
    return mod


class FakeHardware:
    def __init__(self, chans=("R", "G", "B", "W", "IR"), shutter=True, focus=True):
        self.chans, self._shutter, self._focus = set(chans), shutter, focus
        self.levels, self.pulses, self.focused = {}, [], []

    def has(self, k):
        return k in self.chans

    def has_shutter(self):
        return self._shutter

    def has_focus(self):
        return self._focus

    def set(self, k, v):
        self.levels[k] = v

    def pulse(self, ms):
        self.pulses.append(ms)

    def focus(self, on):
        self.focused.append(on)

    def status(self):
        return {"TEMP": 31.5, "VBUS": 12.1}


class FirmwareSerial:
    """A serial port with the reference firmware on the other end."""
    def __init__(self, light):
        self.light = light
        self.port = self.baudrate = self.timeout = self.write_timeout = None
        self.dtr = self.rts = None
        self.is_open = False
        self._out = bytearray()
        self.sent = []

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def reset_input_buffer(self):
        self._out.clear()

    @property
    def in_waiting(self):
        return len(self._out)

    def read(self, n):
        out = bytes(self._out[:n])
        del self._out[:n]
        return out

    def write(self, b):
        self.sent.append(bytes(b))
        for reply in self.light.feed(b):
            self._out.extend((reply + "\n").encode())
        return len(b)


# --------------------------------------------------------------------------- #
# the text helpers
# --------------------------------------------------------------------------- #
def test_lines_freeccr_sends():
    assert olp.set_line({"r": 200, "w": 999, "ir": -3}) == b"SET R=200 G=0 B=0 W=255 IR=0\n"
    assert olp.shutter_line(300) == b"SHUTTER 300\n"
    assert olp.shutter_line(0) == b"SHUTTER 1\n" and olp.shutter_line(1e9) == b"SHUTTER 10000\n"
    assert olp.focus_line(True) == b"FOCUS 1\n" and olp.focus_line(False) == b"FOCUS 0\n"


def test_replies_freeccr_understands():
    kind, info = olp.parse_reply("OLP 1 CH=R,G,B SHUTTER=1 FOCUS=0 NAME=Bench light v2")
    assert kind == "hello" and info == {"version": 1, "channels": ["r", "g", "b"],
                                        "shutter": True, "focus": False,
                                        "name": "Bench light v2"}
    assert olp.parse_reply("STATUS TEMP=31.5 VBUS=12.10 FAN=40") == (
        "status", {"temp": 31.5, "vbus": 12.1})
    assert olp.parse_reply("ok") == ("ok", "")
    assert olp.parse_reply("ERR no shutter") == ("err", "no shutter")
    assert olp.parse_reply("OLP x") is None and olp.parse_reply("hello") is None


def test_line_parser_drops_binary_and_joins_split_lines():
    p = olp.LineParser()
    assert p.feed(bytes([0xFE, 1, 4, 0, 0, 0x13, 0x88]) + b"STA") == []
    assert p.feed(b"TUS TEMP=30\r\nOK\n") == ["STATUS TEMP=30", "OK"]


# --------------------------------------------------------------------------- #
# the reference firmware on its own
# --------------------------------------------------------------------------- #
def test_reference_firmware_answers_and_ignores_another_protocols_bytes():
    fw = _firmware()
    hw = FakeHardware(chans=("R", "G", "B"), focus=False)
    light = fw.OpenLight(hw, name="Test light")
    # the Scanlight's firmware request and a colour packet go first: dropped
    junk = cl.fw_version_packet() + cl.colour_packet({"r": 3, "g": 10})
    replies = light.feed(junk + b"HELLO\n")
    assert replies[-1] == "OLP 1 CH=R,G,B SHUTTER=1 FOCUS=0 NAME=Test light"
    assert light.feed(b"SET R=200 G=5 W=99\n") == ["OK"]
    assert hw.levels == {"R": 200, "G": 5}              # no W on this light
    assert light.feed(b"SET R=x\n") == ["ERR bad value R=x"]
    assert light.feed(b"SHUTTER 250\n") == ["OK"] and hw.pulses == [250]
    assert light.feed(b"FOCUS 1\n") == ["ERR no focus"]
    assert light.feed(b"OFF\n") == ["OK"] and hw.levels == {"R": 0, "G": 0, "B": 0}
    assert light.feed(b"STATUS\n") == ["STATUS TEMP=31.5 VBUS=12.1"]
    assert light.feed(b"DANCE\n") == ["ERR unknown DANCE"]


# --------------------------------------------------------------------------- #
# FreeCCR's link driving it
# --------------------------------------------------------------------------- #
def _link_to(light):
    made = []

    def factory():
        made.append(FirmwareSerial(light))
        return made[-1]
    clock = Clock()
    link = LightLink(serial_factory=factory, port_lister=lambda: ["/dev/ttyACM0"],
                     clock=clock, sleep=lambda s: None, available=True)
    states, tele = [], []
    link.state_changed.connect(lambda s, p, d: states.append(s))
    link.telemetry_changed.connect(lambda t: tele.append(dict(t)))
    return link, clock, made, states, tele


def test_freeccr_drives_an_open_light_end_to_end():
    fw = _firmware()
    hw = FakeHardware()
    link, clock, made, states, tele = _link_to(fw.OpenLight(hw, name="Pico RGB"))
    link.set_levels({"w": 120})                          # before it has answered
    link.open_port("/dev/ttyACM0")
    ser = made[-1]
    assert ser.sent[:2] == [cl.fw_version_packet(), olp.HELLO]
    link.tick()                                          # reads "OLP 1 ..."
    assert link.dialect() == "olp" and states[-1] == "ok"
    assert link.light_info()["name"] == "Pico RGB"
    assert hw.levels["W"] == 120                         # resent in its own words
    clock.t += 0.3
    link.tick()                                          # the STATUS reply
    assert tele and tele[-1]["name"] == "Pico RGB" and tele[-1]["temp"] == 31.5
    clock.t += 0.05
    link.set_levels({"r": 200, "g": 10})
    assert ser.sent[-1] == b"SET R=200 G=10 B=0 W=0 IR=0\n"
    assert (hw.levels["R"], hw.levels["G"], hw.levels["W"]) == (200, 10, 0)
    link.focus(True)
    link.pulse(300)
    assert hw.focused == [True] and hw.pulses == [300]
    link.shutdown(True)
    assert ser.sent[-1] == olp.OFF and hw.levels["R"] == 0 and not link.is_open()


def test_a_scanlight_is_unchanged():
    """No OLP reply: the binary protocol as before (the existing light tests
    hold the rest)."""
    from test_live_capture_light import FakeDevice, FakeSerial
    dev = FakeDevice()
    made = []
    link = LightLink(serial_factory=lambda: (made.append(FakeSerial(dev)), made[-1])[1],
                     port_lister=lambda: [], clock=Clock(), sleep=lambda s: None,
                     available=True)
    link.open_port("/dev/cu.usbmodem1")
    link.tick()
    assert link.dialect() == "scanlight" and link.light_info() == {}
    dev.written.clear()
    link.set_levels({"w": 50})
    link.pulse(200)
    assert dev.written == [cl.colour_packet({"w": 50}), cl.shutter_packet(200)]


def test_the_monitor_names_an_open_light():
    from test_live_monitor import _monitor, _close
    mon, feed, light, dev = _monitor()
    try:
        light._on_state("ok", "/dev/ttyACM0", "Responding")
        light._on_telemetry({"name": "Pico RGB", "vbus": 12.1, "temp": 31.0})
        mon._refresh_light_status()
        assert mon.light_status.text().startswith("Pico RGB  ·  12.1 V  ·  31 °C")
    finally:
        _close(mon)
