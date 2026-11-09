#!/usr/bin/env python3
"""The focus rail (spec/focus-rail.md): the Open Rail Protocol's lines, the
link against a fake rail, the autofocus sweep on a simulated rail with play
and a noisy focus curve, and the Live Capture Monitor's Focus Rail section."""

import itertools
import math
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import orp  # noqa: E402
from core import rail_autofocus as raf  # noqa: E402
from core.rail_link import RailLink, RailClient  # noqa: E402


# --------------------------------------------------------------------------- #
# the protocol's lines
# --------------------------------------------------------------------------- #
def test_lines_freeccr_sends():
    assert orp.HELLO == b"\nHELLO\n"
    assert orp.goto_line(1500, 7) == b"GOTO 1500 ID=7\n"
    assert orp.goto_line(12.6) == b"GOTO 13\n"
    assert orp.move_line(-200, 8) == b"MOVE -200 ID=8\n"
    assert orp.home_line(9) == b"HOME ID=9\n"
    assert orp.speed_line(3200, 20000) == b"SPEED 3200 ACCEL=20000\n"
    assert orp.enable_line(False) == b"ENABLE 0\n"


def test_replies_freeccr_understands():
    kind, h = orp.parse_reply("ORP 1 STEPS_PER_MM=400 MIN=0 MAX=40000 HOME=SWITCH "
                              "NAME=Bench focus rail")
    assert kind == "hello"
    assert h == {"version": 1, "steps_per_mm": 400.0, "steps_per_rev": 0, "min": 0,
                 "max": 40000, "home": "switch", "name": "Bench focus rail"}
    assert orp.parse_reply("ORP 1 STEPS_PER_MM=800 STEPS_PER_REV=3200")[1][
        "steps_per_rev"] == 3200
    assert orp.parse_reply("orp 1 home=stall")[1]["home"] == "stall"
    assert orp.parse_reply("ORP 1")[1]["steps_per_mm"] == 0.0
    assert orp.parse_reply("STATUS POS=-12 MOVING=1 HOMED=0 ENABLED=1 DRIVER=0") == (
        "status", {"pos": -12, "moving": True, "homed": False, "enabled": True,
                   "driver": False})
    assert orp.parse_reply("DONE ID=7 POS=1500") == (
        "done", {"id": 7, "pos": 1500, "stopped": False, "limit": False, "fail": ""})
    d = orp.parse_reply("DONE POS=3 STOPPED=1 FAIL=no switch found")[1]
    assert d["id"] is None and d["stopped"] and d["fail"] == "no switch found"
    assert orp.parse_reply("OK") == ("ok", "")
    assert orp.parse_reply("ERR homing") == ("err", "homing")
    # a light on the port answers in the Open Light Protocol
    kind, info = orp.parse_reply("OLP 1 CH=R,G,B NAME=Pico light")
    assert kind == "light" and info["name"] == "Pico light"
    assert orp.parse_reply("rst:0x1 (POWERON_RESET),boot:0x13") is None   # boot chatter


def test_ports_rank_an_esp32_first_and_a_light_last():
    ports = [("/dev/cu.usbmodem1101", 0x2E8A), ("/dev/cu.Bluetooth-Incoming-Port", None),
             ("/dev/cu.usbserial-0001", 0x10C4), ("/dev/cu.usbmodem2201", 0x303A),
             ("COM7", 0x0483)]
    ranked = sorted(ports, key=lambda p: orp.rank_port(*p))
    assert [p for p, _v in ranked] == ["/dev/cu.usbmodem2201", "/dev/cu.usbserial-0001",
                                       "COM7", "/dev/cu.usbmodem1101",
                                       "/dev/cu.Bluetooth-Incoming-Port"]


def test_position_and_jog_text():
    assert orp.mm_text(4938, 400) == "12.345 mm"
    assert orp.mm_text(-8, 400) == "-0.020 mm"
    assert orp.mm_text(17, 0) == "17 steps"
    assert orp.jog_text(20, 1) == "+0.02"
    assert orp.jog_text(200, -1) == "−0.2"
    assert orp.jog_text(5, 1) == "+0.005"
    # a 4 mm ball screw at 3200 steps a turn: 20 µm is 1.8°, 1 mm a quarter turn
    assert orp.turn_text(20, 800, 3200) == "1.8°"
    assert orp.turn_text(1000, 800, 3200) == "90°"
    assert orp.turn_text(20, 800, 0) == ""
    assert orp.lead_mm(800, 3200) == 4.0 and orp.lead_mm(0, 3200) is None


def test_a_belt_makes_the_steps_per_turn_fractional():
    """spec/focus-rail.md revision 2 A: 30 teeth on the motor and 20 on the
    screw at 3200 motor steps a turn give 2133.33 a screw turn; the user's
    swapped pulleys (20 on the motor, 30 on the screw) give 4800."""
    h = orp.parse_reply("ORP 1 STEPS_PER_MM=533.333 STEPS_PER_REV=2133.33 HOME=NONE "
                        "NAME=DIY Focus Rail")[1]
    assert h["steps_per_rev"] == 2133.33 and h["steps_per_mm"] == 533.333
    assert f"{orp.lead_mm(h['steps_per_mm'], h['steps_per_rev']):g}" == "4"
    assert orp.turn_text(20, 533.333, 2133.33) == "1.8°"
    assert orp.turn_text(20, 1200, 4800) == "1.8°" and orp.lead_mm(1200, 4800) == 4.0
    assert orp.parse_reply("ORP 1 STEPS_PER_REV=lots")[1]["steps_per_rev"] == 0.0


# --------------------------------------------------------------------------- #
# a fake rail on a fake port
# --------------------------------------------------------------------------- #
class FakeRail:
    """Answers the Open Rail Protocol; moves finish on finish()."""

    def __init__(self, ignore_hellos=0, needs_dtr=None, light=False, silent=False,
                 home="NONE", spm="400", spr="3200"):
        self.spm, self.spr = spm, spr
        self.ignore_hellos = ignore_hellos
        self.needs_dtr = needs_dtr
        self.light = light
        self.silent = silent
        self.home = home
        self.lines = []
        self.out = bytearray()
        self.pos = 0
        self.target = 0
        self.moving = False
        self.mid = None
        self.homed = False
        self.dtr = None

    def handle(self, line):
        self.lines.append(line)
        if self.silent or (self.needs_dtr is not None and self.dtr != self.needs_dtr):
            return
        parts = line.split()
        cmd = parts[0].upper()
        ids = [p[3:] for p in parts[1:] if p.upper().startswith("ID=")]
        if cmd == "HELLO":
            if self.ignore_hellos:
                self.ignore_hellos -= 1
                return
            if self.light:
                self.say("OLP 1 CH=R,G,B,W SHUTTER=1 FOCUS=0 NAME=Bench light")
            else:
                self.say(f"ORP 1 STEPS_PER_MM={self.spm} STEPS_PER_REV={self.spr} MIN=0 "
                         f"MAX=40000 HOME={self.home} NAME=Fake Rail")
        elif cmd == "STATUS":
            self.status()
        elif cmd in ("GOTO", "MOVE"):
            v = int(parts[1])
            self.target = v if cmd == "GOTO" else (self.target if self.moving else self.pos) + v
            self.moving = True
            self.mid = int(ids[0]) if ids else None
            self.say("OK")
        elif cmd == "STOP":
            self.say("OK")
            if self.moving:
                self.target = self.pos
                self.finish(stopped=True)
        elif cmd == "ZERO":
            self.pos = self.target = 0
            self.homed = False
            self.say("OK")
        elif cmd == "HOME":
            if self.home == "NONE":
                self.say("ERR no home")
            else:
                self.say("OK")
                self.mid = int(ids[0]) if ids else None
                self.moving = True
                self.target = 200
                self.homed = True
        else:
            self.say(f"ERR unknown {cmd}")

    def say(self, text):
        self.out += (text + "\n").encode()

    def status(self):
        self.say(f"STATUS POS={self.pos} MOVING={int(self.moving)} HOMED={int(self.homed)} "
                 "ENABLED=1")

    def finish(self, stopped=False):
        self.pos = self.target
        self.moving = False
        idp = f" ID={self.mid}" if self.mid is not None else ""
        self.say(f"DONE{idp} POS={self.pos}" + (" STOPPED=1" if stopped else ""))
        self.status()


class FakePort:
    def __init__(self, rail, fail_open=False):
        self.rail = rail
        self.fail_open = fail_open
        self.port = self.baudrate = self.timeout = self.write_timeout = None
        self.dtr = self.rts = self.exclusive = None
        self.is_open = False
        self._buf = bytearray()

    def open(self):
        if self.fail_open:
            raise OSError("busy")
        self.is_open = True
        self.rail.dtr = self.dtr

    def close(self):
        self.is_open = False

    def reset_input_buffer(self):
        self.rail.out.clear()

    @property
    def in_waiting(self):
        return len(self.rail.out)

    def read(self, n):
        d = bytes(self.rail.out[:n])
        del self.rail.out[:n]
        return d

    def write(self, b):
        self._buf += b
        while b"\n" in self._buf:
            line, _, rest = bytes(self._buf).partition(b"\n")
            self._buf = bytearray(rest)
            text = orp.clean_line(line.decode("ascii", "ignore"))
            if text:
                self.rail.handle(text)
        return len(b)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _link(rail, ports=("/dev/cu.usbserial-0001",), clock=None):
    made = []

    def factory():
        p = FakePort(rail)
        made.append(p)
        return p
    link = RailLink(serial_factory=factory, port_lister=lambda: list(ports),
                    clock=clock or Clock(), sleep=lambda s: None, available=True)
    return link, made


def _record(link):
    got = {"state": [], "info": [], "status": [], "done": [], "err": []}
    link.state_changed.connect(lambda s, p, d: got["state"].append((s, d)))
    link.info_changed.connect(lambda i: got["info"].append(i))
    link.status_changed.connect(lambda st: got["status"].append(st))
    link.move_done.connect(lambda i, p, f: got["done"].append((i, p, f)))
    link.rail_error.connect(lambda t: got["err"].append(t))
    return got


def test_the_link_talks_to_a_rail():
    rail = FakeRail()
    link, made = _link(rail)
    got = _record(link)
    link.open_port("/dev/cu.usbserial-0001")
    assert made[0].dtr is True and made[0].rts is False      # no ESP32 reset
    assert made[0].exclusive is True
    link.tick()
    link.tick()                                    # the STATUS it asked for
    assert got["state"][-1] == ("ok", "Fake Rail")
    assert got["info"][-1]["steps_per_mm"] == 400.0
    assert got["status"][-1]["pos"] == 0
    link.goto(1200, 5)
    link.tick()
    assert rail.lines[-1] == "GOTO 1200 ID=5"
    rail.finish()
    link.tick()
    assert got["done"][-1][:2] == (5, 1200)
    assert got["status"][-1] == {"pos": 1200, "moving": False, "homed": False,
                                 "enabled": True}
    link.move(-8, -1)
    link.tick()
    assert rail.lines[-1] == "MOVE -8"
    link.home(3)
    link.tick()
    assert got["err"] == ["no home"]
    assert rail.lines[-1] == "STATUS"                       # a refused move: where is it
    link.zero()
    assert rail.lines[-2:] == ["ZERO", "STATUS"]


def test_hello_is_repeated_until_a_rebooting_board_answers():
    rail = FakeRail(ignore_hellos=2)
    clock = Clock()
    link, made = _link(rail, clock=clock)
    got = _record(link)
    link.open_port("/dev/cu.usbserial-0001")
    for _ in range(3):
        link.tick()
        clock.t += 0.7
    link.tick()
    assert rail.lines.count("HELLO") == 3
    assert got["state"][-1][0] == "ok"


def test_a_silent_port_tries_dtr_off_once_then_says_so():
    rail = FakeRail(needs_dtr=False)
    clock = Clock()
    link, made = _link(rail, clock=clock)
    got = _record(link)
    link.open_port("/dev/cu.usbserial-0001")
    clock.t += 3.0
    link.tick()                                    # reopened with DTR off
    assert len(made) == 2 and made[1].dtr is False
    link.tick()
    assert got["state"][-1][0] == "ok"
    rail2 = FakeRail(silent=True)
    link2, made2 = _link(rail2, clock=clock)
    got2 = _record(link2)
    link2.open_port("/dev/cu.usbserial-0001")
    clock.t += 3.0
    link2.tick()
    clock.t += 3.0
    link2.tick()
    assert got2["state"][-1][0] == "silent"
    assert len(made2) == 2


def test_a_light_on_the_port_is_named_and_let_go():
    rail = FakeRail(light=True)
    link, made = _link(rail)
    got = _record(link)
    link.open_port("/dev/cu.usbserial-0001")
    link.tick()
    state, detail = got["state"][-1]
    assert state == "error" and "Bench light" in detail and "not a rail" in detail
    assert not link.is_open()
    link.scan()                                    # not reopened by itself
    assert not link.is_open()


def test_the_lights_port_is_never_opened():
    rail = FakeRail()
    link, made = _link(rail)
    got = _record(link)
    link.set_blocked(["/dev/cu.usbserial-0001"])
    link.set_auto_port("/dev/cu.usbserial-0001")
    link.scan()
    assert not made
    link.open_port("/dev/cu.usbserial-0001")
    assert not made and got["state"][-1][0] == "error"


def test_an_unplugged_rail_is_lost_and_comes_back():
    rail = FakeRail()
    link, made = _link(rail)
    got = _record(link)
    link.set_auto_port("/dev/cu.usbserial-0001")
    link.scan()
    link.tick()
    assert got["state"][-1][0] == "ok"

    def boom(*a):
        raise OSError("gone")
    made[0].read = boom
    rail.say("STATUS POS=1")
    link.tick()
    assert got["state"][-1][0] == "lost"
    link.scan()                                    # the remembered port is back
    link.tick()
    assert got["state"][-1][0] == "ok" and len(made) == 2


def test_the_client_numbers_moves_and_knows_where_the_rail_is():
    rail = FakeRail()
    link, _made = _link(rail)
    client = RailClient(link=link, threaded=False)
    client.open_port("/dev/cu.usbserial-0001")
    link.tick()
    link.tick()
    assert client.is_ready() and client.position() == 0
    a = client.goto(400)
    b = client.move(8)
    assert b == a + 1 and client.moving()
    link.tick()
    rail.finish()
    link.tick()
    assert not client.moving() and client.position() == 408
    client.shutdown()


# --------------------------------------------------------------------------- #
# autofocus on a simulated rail
# --------------------------------------------------------------------------- #
class SimRail:
    """A lead screw with play: the carriage trails the motor by half the play
    in the direction it last moved. Focus is a Gaussian of the carriage's
    position over a grain floor, with multiplicative noise."""

    def __init__(self, peak_um, play_um=50.0, spm=400.0, sigma_um=150.0, noise=0.02,
                 seed=3, start=0, flat=False):
        self.spm = spm
        self.peak = peak_um
        self.half_play = play_um * spm / 1000.0 / 2.0
        self.sigma = sigma_um
        self.noise = noise
        self.rng = np.random.default_rng(seed)
        self.pos = start
        self.carriage = float(start)
        self.last_dir = 0
        self.flat = flat

    def move(self, target):
        if target > self.pos:
            self.last_dir = 1
            self.carriage = max(self.carriage, target - self.half_play)
        elif target < self.pos:
            self.last_dir = -1
            self.carriage = min(self.carriage, target + self.half_play)
        self.pos = target

    def carriage_um(self):
        return self.carriage * 1000.0 / self.spm

    def score(self):
        if self.flat:
            base = 1.0
        else:
            x = self.carriage_um() - self.peak
            base = 0.08 + math.exp(-0.5 * (x / self.sigma) ** 2)
        return base * (1.0 + self.noise * self.rng.standard_normal())


def _run(sim, opts=None, limits=None, start=0, stop_after=None, readings=True):
    clock = Clock()
    ids = itertools.count(1)
    pending = []
    seen = []          # (pos, the rail's last direction) at each reading taken

    def goto(pos):
        mid = next(ids)
        pending.append((mid, pos))
        return mid
    done = []
    af = raf.Autofocus(goto, opts, steps_per_mm=sim.spm, limits=limits, clock=clock,
                       on_finished=done.append)
    af.start(start)
    for step in range(4000):
        if not af.running:
            break
        if stop_after is not None and step == stop_after:
            af.stop()
            break
        if pending:
            mid, pos = pending.pop(0)
            sim.move(pos)
            af.moved(mid, pos)
            continue
        w = af._wait
        if w and w[0] == "read":
            clock.t += 0.1
            if readings:
                before = len(w[3])
                af.reading(sim.score())
                if len(w[3]) > before or af._wait is not w:
                    seen.append((sim.pos, sim.last_dir))
            else:
                af.tick()
    return af, done, seen


@pytest.mark.parametrize("peak_um", [300.0, -620.0, 35.0, 980.0, 1250.0, -1300.0])
def test_autofocus_finds_the_peak_from_below_every_time(peak_um):
    sim = SimRail(peak_um)
    af, done, seen = _run(sim)
    assert done and done[0].ok, done
    # within half a fine step of the true peak, measured at the carriage
    assert abs(sim.carriage_um() - peak_um) <= raf.DEFAULTS["fine_um"] / 2 + 1, (
        sim.carriage_um(), peak_um)
    # every reading and the final position were reached moving up
    assert all(d == 1 for _p, d in seen), seen
    assert sim.last_dir == 1
    assert "Focused at" in done[0].message


def test_autofocus_with_more_play_than_set_still_lands_and_without_play_too():
    for play in (0.0, 200.0):
        sim = SimRail(410.0, play_um=play)
        af, done, seen = _run(sim, opts={"backlash_um": 250})
        assert done[0].ok
        assert abs(sim.carriage_um() - 410.0) <= 11


def test_autofocus_respects_the_soft_limits():
    sim = SimRail(150.0, start=200)
    af, done, seen = _run(sim, limits=(0, 40000), start=200)
    assert done[0].ok
    assert min(p for p, _d in seen) >= 0
    assert all(t >= 0 for _f, t in af.moves)


def test_a_flat_sweep_fails_and_goes_back():
    sim = SimRail(0.0, flat=True, start=1000)
    af, done, _seen = _run(sim, start=1000)
    assert not done[0].ok and "barely changed" in done[0].message
    assert sim.pos == 1000 and sim.last_dir == 1


def test_a_peak_beyond_the_range_fails_and_goes_back():
    sim = SimRail(2600.0, sigma_um=600.0)          # still rising at the far end
    af, done, _seen = _run(sim)
    assert not done[0].ok and "No focus peak" in done[0].message
    assert sim.pos == 0 and sim.last_dir == 1


def test_stop_and_a_missing_live_view():
    sim = SimRail(300.0)
    af, done, _seen = _run(sim, stop_after=6)
    assert not af.running and done and not done[0].ok and "stopped" in done[0].message
    sim = SimRail(300.0)
    af, done, _seen = _run(sim, readings=False)
    assert not done[0].ok and "No sharpness readings" in done[0].message


def test_fit_peak_and_past_peak():
    pts = [(p, math.exp(-0.5 * ((p - 13.0) / 10.0) ** 2)) for p in range(0, 40, 8)]
    assert abs(raf.fit_peak(pts) - 13) <= 1
    assert raf.fit_peak([(5, 1.0)]) == 5
    assert raf.fit_peak([(0, 1.0), (8, 1.0), (16, 1.0)]) == 0   # no peak: the best
    assert not raf.past_peak([(0, 1), (1, 2)])
    assert raf.past_peak([(0, 1), (1, 3), (2, 2), (3, 2)])
    assert not raf.past_peak([(0, 1), (1, 3), (2, 2.9), (3, 2)])


# --------------------------------------------------------------------------- #
# the monitor's Focus Rail section
# --------------------------------------------------------------------------- #
from test_live_monitor import FakeFeed, _light, _close, iso_settings  # noqa: E402,F401
from widgets import live_monitor as lm  # noqa: E402
from core import live_feed as lf  # noqa: E402


def _rail_monitor(rail=None, ports=("/dev/cu.usbmodem1", "/dev/cu.usbserial-0001")):
    from core.usb_camera import UsbCamera
    rail = rail or FakeRail()
    clock = Clock()
    link, made = _link(rail, ports=ports, clock=clock)
    client = RailClient(link=link, threaded=False)
    feed = FakeFeed()
    light, dev, _m = _light()
    mon = lm.LiveCaptureMonitor(feed=feed, light=light, usb=UsbCamera(backends=[]),
                                rail=client)
    mon.light_on_connect = False
    mon._start_hot_folder = lambda from_monitor=False: None
    mon.resize(1300, 820)
    mon.show()
    _app.processEvents()
    light.open_port("/dev/cu.usbmodem1")
    light.link.tick()
    return mon, feed, client, rail


def test_the_rail_list_leaves_out_the_lights_port_and_jogs_move_it():
    mon, feed, client, rail = _rail_monitor()
    try:
        client.link.scan(force_emit=True)
        items = [mon.rail_port_combo.itemText(i) for i in range(mon.rail_port_combo.count())]
        assert items == ["/dev/cu.usbserial-0001"]
        mon.rail_connect_btn.click()
        client.link.tick()
        client.link.tick()
        assert "Fake Rail" in mon.rail_status.text()
        assert "position not set" in mon.rail_status.text()
        assert "8 mm a turn" in mon.rail_status.text()
        assert "0.9° of the screw" in mon.rail_jog_btns["up_fine"].toolTip()
        assert "(])" in mon.rail_jog_btns["up_fine"].toolTip()
        from PySide6.QtCore import QPoint
        mon._rail_af_menu(QPoint(10, 10))                 # Range as an angle too
        assert mon._popup.values_lbl["range_um"].text() == "±1.00 mm · 45°"
        mon._popup.close()
        assert mon.rail_pos.text() == "0.000 mm"
        assert mon.rail_jog_btns["up_fine"].text() == "+0.02"
        assert mon.rail_jog_btns["down_coarse"].text() == "−0.2"
        mon.rail_jog_btns["up_fine"].click()
        assert rail.lines[-1].startswith("MOVE 8 ")
        mon.canvas.setFocus()
        QTest.keyClick(mon.canvas, Qt.Key_BracketLeft)
        assert rail.lines[-1].startswith("MOVE -8 ")
        mon.rail_jog_btns["up_coarse"].click()
        assert rail.lines[-1].startswith("MOVE 80 ")
        rail.finish()
        client.link.tick()
        assert mon.rail_pos.text() == "0.200 mm"
        mon.rail_zero_btn.click()
        assert rail.lines[-2] == "ZERO"
        client.link.tick()
        assert "zero set here" in mon.rail_status.text()
        assert not mon.rail_home_btn.isEnabled()          # HOME=NONE
        # the light's list leaves out the rail's port
        items = [mon.port_combo.itemText(i) for i in range(mon.port_combo.count())]
        assert "/dev/cu.usbserial-0001" not in items
    finally:
        _close(mon)
    assert not client.link.is_open()


def _frame(score):
    from core.live_feed import FrameResult, resolve_view
    from PySide6.QtGui import QImage
    img = QImage(8, 8, QImage.Format_RGB888)
    return FrameResult(img, resolve_view(8, 8, 8, 8, 1.0, 0.5, 0.5), reading=score)


def test_autofocus_from_the_monitor_runs_on_readings_and_esc_stops_it():
    mon, feed, client, rail = _rail_monitor()
    try:
        mon.rail.open_port("/dev/cu.usbserial-0001")
        client.link.tick()
        client.link.tick()
        mon.autofocus()
        assert "live view" in mon.rail_caption.text()       # no picture yet
        feed.result = _frame(1.0)
        mon._on_frame()
        mon.actions["sharp"].setChecked(False)
        mon.autofocus()
        assert mon.actions["sharp"].isChecked()             # it reads the meter
        assert mon._af.running and mon.rail_af_btn.text() == "Focusing…"
        assert not mon.shoot_btn.isEnabled()
        assert rail.lines[-1].startswith("GOTO ") and " ID=" in rail.lines[-1]
        # drive it: moves finish, readings follow the fake rail's position
        clock = Clock()
        mon._af._clock = clock
        sim = SimRail(260.0, play_um=0.0, noise=0.0)
        for _ in range(400):
            if not mon._af.running:
                break
            if rail.moving:
                rail.finish()
                sim.move(rail.pos)
                client.link.tick()
                continue
            clock.t += 0.1
            feed.result = _frame(sim.score())
            mon._on_frame()
        assert not mon._af.running and mon._af.result.ok
        assert "Focused at" in mon.rail_caption.text()
        assert abs(sim.carriage_um() - 260.0) <= 11
        assert mon.shoot_btn.isEnabled()
        # again, and Esc stops it
        feed.result = _frame(1.0)
        mon._on_frame()
        mon.autofocus()
        assert mon._af.running
        mon._on_escape()
        assert not mon._af.running and rail.lines[-1] == "STOP"
        assert "stopped" in mon.rail_caption.text()
    finally:
        _close(mon)


def test_jogging_is_refused_while_shooting_and_shoot_while_the_rail_moves():
    mon, feed, client, rail = _rail_monitor()
    try:
        mon.rail.open_port("/dev/cu.usbserial-0001")
        client.link.tick()
        client.link.tick()
        mon.rail_jog(1, "fine")
        assert client.moving()
        mon.shoot()
        assert not mon.sequence.running
        assert "focus rail" in mon.capture_caption.text()
        rail.finish()
        client.link.tick()
        mon.sequence.running = True
        n = len(rail.lines)
        mon.rail_jog(1, "fine")
        assert len(rail.lines) == n and "not while shooting" in mon.rail_caption.text()
        mon.sequence.running = False
    finally:
        _close(mon)


def test_the_panel_still_fits_with_the_rail_section_open(iso_settings):
    lm._settings().setValue("live/rail_open", True)
    mon, feed, client, rail = _rail_monitor()
    try:
        assert mon.rail_section._content.isVisible()
        assert mon.panel.minimumSizeHint().width() <= lm.theme.PANEL_W
    finally:
        _close(mon)


# --------------------------------------------------------------------------- #
# autofocus before each shot (spec/focus-rail.md revision 2 C)
# --------------------------------------------------------------------------- #
def _connected_monitor(magnifier=True):
    mon, feed, client, rail = _rail_monitor()
    mon.rail.open_port("/dev/cu.usbserial-0001")
    client.link.tick()
    client.link.tick()
    feed.result = _frame(1.0)
    mon._on_frame()
    order = []
    mon.AF_SHOT_SETTLE_S = 0.0
    if magnifier:
        mon._on_zoom_levels(["1", "2.5", "4", "8", "16"])
        mon.usb.set_zoom = lambda lv: order.append(f"zoom {lv}")
    real_guide = mon._shot_guide_record
    mon._shot_guide_record = lambda: (order.append("prepared"), real_guide())[1]
    mon.rail_af_shoot.setChecked(True)
    return mon, feed, client, rail, order


def _drive_af(mon, feed, client, rail, sim, steps=600):
    clock = Clock()
    mon._af._clock = clock
    for _ in range(steps):
        if mon._af is None or not mon._af.running:
            break
        if rail.moving:
            rail.finish()
            sim.move(rail.pos)
            client.link.tick()
            continue
        clock.t += 0.1
        feed.result = _frame(sim.score())
        mon._on_frame()


def _magnify(mon, feed, level):
    """The camera says it zoomed, then its first picture at that zoom comes."""
    mon._on_cam_zoom(level)
    feed.result = _frame(1.0)
    mon._on_frame()
    mon._af_shot_tick()


def test_shoot_magnifies_focuses_goes_back_to_whole_then_shoots(iso_settings):
    mon, feed, client, rail, order = _connected_monitor()
    try:
        mon.shoot()
        assert order == ["prepared", "zoom 8"]           # the shot taken at the whole frame
        assert mon._af_shot["stage"] == "zoom" and not mon.sequence.running
        assert mon.shoot_btn.isEnabled() and mon.shoot_btn.text().startswith("Stop")
        assert mon._rail_busy()                          # Measure, Meter, jogs refused
        n = len(rail.lines)
        mon.rail_jog(1, "fine")
        assert len(rail.lines) == n and "before the shot" in mon.rail_caption.text()
        _magnify(mon, feed, "8")
        assert mon._af is not None and mon._af.running   # focusing at 8×
        assert rail.lines[-1].startswith("GOTO ")
        sim = SimRail(260.0, play_um=0.0, noise=0.0)
        _drive_af(mon, feed, client, rail, sim)
        assert mon._af.result.ok and abs(sim.carriage_um() - 260.0) <= 11
        assert order[-1] == "zoom 1" and not mon.sequence.running   # back to Whole first
        mon._on_cam_zoom("1")
        _app.processEvents()
        assert mon.sequence.running and mon._af_shot is None
        assert mon.shoot_btn.text().startswith("Stop")
        assert order.count("prepared") == 1
    finally:
        mon.sequence.stop()
        _close(mon)


def test_without_a_magnifier_it_focuses_then_shoots(iso_settings):
    mon, feed, client, rail, order = _connected_monitor(magnifier=False)
    try:
        assert not mon.rail_af_zoom.isEnabled()          # nothing to zoom
        mon.shoot()
        assert mon._af is not None and mon._af.running
        _drive_af(mon, feed, client, rail, SimRail(-120.0, play_um=0.0, noise=0.0))
        _app.processEvents()
        assert mon.sequence.running and order == ["prepared"]
    finally:
        mon.sequence.stop()
        _close(mon)


def test_no_focus_found_shoots_nothing_and_goes_back_to_whole(iso_settings):
    mon, feed, client, rail, order = _connected_monitor()
    try:
        mon.shoot()
        _magnify(mon, feed, "8")
        _drive_af(mon, feed, client, rail, SimRail(0.0, play_um=0.0, flat=True))
        assert not mon._af.result.ok and mon._af_shot is None
        assert mon.capture_caption.text().startswith("Focus barely changed")
        assert mon.capture_caption.text().endswith("Nothing was shot.")
        _app.processEvents()
        assert not mon.sequence.running and order[-1] == "zoom 1"
        assert mon.rail_caption.text().endswith("Nothing was shot.")
        assert mon.shoot_btn.text().startswith("Shoot")
    finally:
        _close(mon)


def test_shoot_again_or_esc_stops_it_and_nothing_is_shot(iso_settings):
    mon, feed, client, rail, order = _connected_monitor()
    try:
        mon.shoot()
        _magnify(mon, feed, "8")
        assert mon._af.running
        mon.shoot()                                       # a second press
        assert not mon._af.running and rail.lines[-1] == "STOP"
        assert mon._af_shot is None and not mon.sequence.running
        assert order[-1] == "zoom 1"
        assert "Shoot cancelled. Nothing was shot." == mon.capture_caption.text()
        assert mon.shoot_btn.text().startswith("Shoot")
        # Esc while it is still magnifying
        client.link.tick()                               # the STOP's DONE
        mon._on_cam_zoom("1")
        order.clear()
        mon.shoot()
        assert order == ["prepared", "zoom 8"]
        mon._on_escape()
        assert mon._af_shot is None and order[-1] == "zoom 1"
        mon._on_cam_zoom("8")                            # the camera's late report
        mon._af_shot_tick()
        assert mon._af is None or not mon._af.running
    finally:
        _close(mon)


def test_the_box_needs_the_rail_and_is_remembered(iso_settings):
    mon, feed, client, rail = _rail_monitor()
    try:
        feed.result = _frame(1.0)
        mon._on_frame()
        mon.rail_af_shoot.setChecked(True)
        mon.rail_af_zoom.setCurrentIndex(mon.rail_af_zoom.findData("16"))
        mon.shoot()
        assert not mon.sequence.running and mon._af_shot is None
        assert "isn't connected" in mon.capture_caption.text()
        assert lm._settings().value("live/rail_af_shoot", type=bool) is True
        assert lm._settings().value("live/rail_af_zoom") == "16"
    finally:
        _close(mon)
    mon2, *_rest = _rail_monitor()
    try:
        assert mon2.rail_af_shoot.isChecked()
        assert mon2.rail_af_zoom.currentData() == "16"
    finally:
        _close(mon2)
