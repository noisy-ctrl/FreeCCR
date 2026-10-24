#!/usr/bin/env python3
"""The Scanlight link, the capture sequence and the presets
(spec/live-capture-monitor.md §7-9). No hardware: a fake serial port, a fake
clock and a fake light."""

import os
import struct
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core import capture_light as cl  # noqa: E402
from core import capture_sequence as cs  # noqa: E402
from core import light_presets as lp  # noqa: E402
from core.light_link import LightLink, LightClient  # noqa: E402


# --------------------------------------------------------------------------- #
# protocol
# --------------------------------------------------------------------------- #
def test_packets_are_exact():
    assert cl.colour_packet({"r": 255, "g": 10, "b": 0, "w": 0, "ir": 3}) == \
        bytes([0xFE, 0, 6, 255, 10, 0, 0, 3, 0])
    assert cl.colour_packet({"w": 300.4}, save=True) == bytes([0xFE, 0, 6, 0, 0, 0, 255, 0, 1])
    assert cl.fw_version_packet() == bytes([0xFE, 2, 0])
    assert cl.shutter_packet(300) == bytes([0xFE, 3, 1, 30])
    assert cl.shutter_packet(99999) == bytes([0xFE, 3, 1, 255])
    assert cl.focus_packet(True) == bytes([0xFE, 7, 1, 1])
    assert cl.focus_packet(False) == bytes([0xFE, 7, 1, 0])


def _tele(header, value):
    return bytes([0xFE, header, 4]) + struct.pack(">i", int(round(value * 1000)))


def test_parser_handles_split_junk_and_several_at_once():
    p = cl.PacketParser()
    a, b = _tele(cl.D2H_VBUS, 5.123), _tele(cl.D2H_TEMP, 38.5)
    assert p.feed(a[:2]) == []
    assert p.feed(a[2:5]) == []
    got = p.feed(a[5:] + b"\x01\x02junk" + b + a)
    assert [cl.telemetry(h, d) for h, d in got] == [
        ("vbus", 5.123), ("temp", 38.5), ("vbus", 5.123)]
    assert cl.telemetry(9, b"\0\0\0\0") is None
    assert cl.telemetry(cl.D2H_VBUS, b"\0") is None


def test_port_ranking_puts_usb_first_and_macs_own_last():
    names = ["/dev/cu.debug-console", "/dev/cu.Bluetooth-Incoming-Port",
             "/dev/cu.usbmodem1101", "/dev/cu.other"]
    assert sorted(names, key=cl.port_rank) == [
        "/dev/cu.usbmodem1101", "/dev/cu.other",
        "/dev/cu.Bluetooth-Incoming-Port", "/dev/cu.debug-console"]


def test_channel_groups_exclude_each_other():
    lv = {"r": 0, "g": 0, "b": 0, "w": 200, "ir": 0}
    assert cl.exclusive(lv, "g", 120) == {"r": 0, "g": 120, "b": 0, "w": 0, "ir": 0}
    assert cl.exclusive({"r": 9, "g": 9, "b": 9, "w": 0, "ir": 0}, "ir", 50) == \
        {"r": 0, "g": 0, "b": 0, "w": 0, "ir": 50}
    # lowering a channel to 0 leaves the other group alone
    assert cl.exclusive(lv, "r", 0)["w"] == 200
    # a set with both keeps RGB, like the light
    assert cl.clean_levels({"r": 5, "w": 9}) == {"r": 5, "g": 0, "b": 0, "w": 0, "ir": 0}
    assert cl.solo({"r": 200, "g": 150, "b": 90, "w": 255}, "g") == \
        {"r": 0, "g": 150, "b": 0, "w": 0, "ir": 0}


# --------------------------------------------------------------------------- #
# the link, with a fake port
# --------------------------------------------------------------------------- #
class FakeDevice:
    """The light at the far end: answers only with the DTR it likes."""
    def __init__(self, wants_dtr=True, answers=True):
        self.wants_dtr = wants_dtr
        self.answers = answers
        self.written = []
        self.unplugged = False


class FakeSerial:
    def __init__(self, dev, fail_open=False):
        self.dev = dev
        self.fail_open = fail_open
        self.port = None
        self.baudrate = None
        self.timeout = None
        self.write_timeout = None
        self.dtr = None
        self.rts = None
        self.is_open = False
        self._out = bytearray()

    def open(self):
        if self.fail_open:
            raise OSError("busy")
        self.is_open = True

    def close(self):
        self.is_open = False

    def reset_input_buffer(self):
        self._out.clear()

    @property
    def in_waiting(self):
        if self.dev.unplugged:
            raise OSError("device gone")
        if self.dev.answers and self.dtr == self.dev.wants_dtr:
            self._out.extend(_tele(cl.D2H_VBUS, 5.1) + _tele(cl.D2H_TEMP, 38.0))
        return len(self._out)

    def read(self, n):
        out = bytes(self._out[:n])
        del self._out[:n]
        return out

    def write(self, b):
        if self.dev.unplugged:
            raise OSError("device gone")
        self.dev.written.append(bytes(b))
        return len(b)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _link(dev, ports=("/dev/cu.usbmodem1",), fail_open=False):
    clock = Clock()
    made = []

    def factory():
        s = FakeSerial(dev, fail_open=fail_open)
        made.append(s)
        return s
    link = LightLink(serial_factory=factory, port_lister=lambda: list(ports),
                     clock=clock, sleep=lambda s: None, available=True)
    states = []
    link.state_changed.connect(lambda s, p, d: states.append(s))
    tele = []
    link.telemetry_changed.connect(lambda t: tele.append(t))
    return link, clock, made, states, tele


def test_connect_asks_for_the_firmware_and_hears_telemetry():
    dev = FakeDevice()
    link, clock, made, states, tele = _link(dev)
    link.open_port("/dev/cu.usbmodem1")
    s = made[-1]
    assert s.dtr is True and s.rts is False and s.port == "/dev/cu.usbmodem1"
    assert dev.written[0] == cl.fw_version_packet()
    assert states[-1] == "waiting"
    link.tick()
    assert states[-1] == "ok"
    assert tele and tele[-1] == {"vbus": 5.1, "temp": 38.0}


def test_silent_port_retries_dtr_exactly_once():
    dev = FakeDevice(wants_dtr=False)          # only talks with DTR off
    link, clock, made, states, _ = _link(dev)
    link.open_port("/dev/cu.usbmodem1")
    link.tick()
    assert states[-1] == "waiting"
    clock.t += 2.6
    link.tick()                                  # silent: reopens with DTR off
    assert len(made) == 2 and made[-1].dtr is False
    link.tick()
    assert states[-1] == "ok"


def test_silent_both_ways_says_so_and_stops_trying():
    dev = FakeDevice(answers=False)
    link, clock, made, states, _ = _link(dev)
    link.open_port("/dev/cu.usbmodem1")
    for _ in range(2):
        clock.t += 2.6
        link.tick()
    assert states[-1] == "silent" and len(made) == 2
    for _ in range(5):
        clock.t += 2.6
        link.tick()
    assert len(made) == 2                       # never loops


def test_unplug_disconnects_and_reconnects_when_back():
    dev = FakeDevice()
    ports = ["/dev/cu.usbmodem1"]
    link, clock, made, states, _ = _link(dev, ports=ports)
    link.set_auto_port("/dev/cu.usbmodem1")
    link.open_port("/dev/cu.usbmodem1")
    link.tick()
    assert states[-1] == "ok"
    dev.unplugged = True
    ports.clear()
    link.tick()
    assert states[-1] == "lost" and not link.is_open()
    link.scan()
    assert not link.is_open()                   # not back yet
    dev.unplugged = False
    ports.append("/dev/cu.usbmodem1")
    link.scan()
    assert link.is_open() and states[-1] == "waiting"


def test_user_disconnect_stays_disconnected():
    """(The reference reconnected to the remembered port within 3 s of the
    user pressing Disconnect.)"""
    dev = FakeDevice()
    link, clock, made, states, _ = _link(dev)
    link.set_auto_port("/dev/cu.usbmodem1")
    link.open_port("/dev/cu.usbmodem1")
    link.close_port()
    assert states[-1] == "off"
    link.scan()
    link.scan()
    assert not link.is_open()
    link.open_port("/dev/cu.usbmodem1")
    assert link.is_open()


def test_open_failure_is_reported():
    link, clock, made, states, _ = _link(FakeDevice(), fail_open=True)
    link.open_port("/dev/cu.usbmodem1")
    assert states[-1] == "error" and not link.is_open()


def test_colour_writes_are_coalesced_and_the_last_wins():
    dev = FakeDevice()
    link, clock, made, states, _ = _link(dev)
    link.open_port("/dev/cu.usbmodem1")
    dev.written.clear()
    link.set_levels({"w": 10})                   # goes out at once
    for v in (20, 30, 40):
        link.set_levels({"w": v})                # within 16 ms: held
    assert dev.written == [cl.colour_packet({"w": 10})]
    clock.t += 0.02
    link.tick()
    assert dev.written[-1] == cl.colour_packet({"w": 40}) and len(dev.written) == 2


def test_focus_and_pulse_flush_the_colour_first():
    dev = FakeDevice()
    link, clock, made, states, _ = _link(dev)
    link.open_port("/dev/cu.usbmodem1")
    link.set_levels({"r": 1})
    link.set_levels({"r": 200})
    dev.written.clear()
    link.focus(True)
    link.pulse(300)
    assert dev.written == [cl.colour_packet({"r": 200}), cl.focus_packet(True),
                           cl.shutter_packet(300)]


def test_shutdown_turns_the_light_off_then_closes():
    dev = FakeDevice()
    link, clock, made, states, _ = _link(dev)
    link.open_port("/dev/cu.usbmodem1")
    link.set_levels({"w": 200})
    dev.written.clear()
    link.shutdown(True)
    assert dev.written == [cl.colour_packet(cl.OFF)]
    assert not made[-1].is_open and not link.is_open()


def test_without_pyserial_the_link_says_so():
    link = LightLink(available=False)
    states = []
    link.state_changed.connect(lambda s, p, d: states.append((s, d)))
    link.start()
    assert states[-1][0] == "unavailable" and "pyserial" in states[-1][1]
    link.open_port("/dev/cu.x")                  # harmless
    assert not link.is_open()


def test_client_on_its_own_thread_round_trips():
    dev = FakeDevice()
    made = []

    def factory():
        s = FakeSerial(dev)
        made.append(s)
        return s
    link = LightLink(serial_factory=factory, port_lister=lambda: ["/dev/cu.usbmodem1"],
                     sleep=lambda s: None, available=True)
    client = LightClient(link=link, threaded=True)
    try:
        client.open_port("/dev/cu.usbmodem1")
        client.set_levels({"w": 128})
        import time
        t0 = time.time()
        while client.state != "ok" and time.time() - t0 < 3:
            _app.processEvents()
            time.sleep(0.01)
        assert client.state == "ok" and client.is_open()
    finally:
        client.shutdown(light_off=True)
    assert dev.written[-1] == cl.colour_packet(cl.OFF)
    assert not made[-1].is_open


# --------------------------------------------------------------------------- #
# the sequence, with a fake clock and light
# --------------------------------------------------------------------------- #
class FakeLight:
    def __init__(self):
        self.log = []
        self.open = True

    def is_open(self):
        return self.open

    def set_levels(self, lv):
        self.log.append(("levels", dict(lv)))

    def focus(self, on):
        self.log.append(("focus", on))

    def pulse(self, ms):
        self.log.append(("pulse", ms))


class Timers:
    def __init__(self, clock):
        self.clock = clock
        self.queue = []

    def schedule(self, ms, fn):
        self.queue.append((self.clock.t + ms / 1000.0, len(self.queue), fn))

    def run(self, until=None):
        while self.queue:
            self.queue.sort(key=lambda e: (e[0], e[1]))
            due, _, fn = self.queue[0]
            if until is not None and due > until:
                return
            self.queue.pop(0)
            self.clock.t = max(self.clock.t, due)
            fn()


PRESET = {"name": "P", "r": 210, "g": 160, "b": 180, "w": 230, "ir": 0}


def _seq():
    clock = Clock()
    timers = Timers(clock)
    light = FakeLight()
    steps, done = [], []
    seq = cs.CaptureSequence(light, schedule=timers.schedule, clock=clock,
                             on_step=lambda c, d: steps.append((c, d, clock.t)),
                             on_finished=done.append)
    return seq, light, clock, timers, steps, done


def test_trichrome_order_values_and_timing():
    seq, light, clock, timers, steps, done = _seq()
    t0 = clock.t
    timing = {"focus_ms": 200, "pulse_ms": 300, "hold_ms": 200, "period_ms": 1000}
    assert seq.start(timing, PRESET, trichrome=True)
    timers.run()
    assert done == ["done"]
    assert [s[0] for s in steps] == ["r", "g", "b"]
    lit = [e[1] for e in light.log if e[0] == "levels"]
    assert lit == [cl.solo(PRESET, "r"), cl.solo(PRESET, "g"), cl.solo(PRESET, "b"),
                   {"r": 0, "g": 0, "b": 0, "w": 230, "ir": 0}]
    kinds = [e[0] for e in light.log]
    assert kinds == ["levels", "focus", "pulse", "focus"] * 3 + ["levels"]
    # shots start Period apart (one shot takes 700 ms < 1000)
    starts = [s[2] - t0 for s in steps]
    assert starts == pytest.approx([0.0, 1.0, 2.0])
    assert clock.t - t0 == pytest.approx(cs.cycle_ms(timing) / 1000.0)
    assert steps[0][1] == cs.shot_ms(timing) == 700


def test_colour_leads_the_pulse_even_with_no_focus_time():
    seq, light, clock, timers, steps, done = _seq()
    seq.start({"focus_ms": 0, "pulse_ms": 100, "hold_ms": 0, "period_ms": 0},
              PRESET, trichrome=False)
    t0 = clock.t
    timers.run(until=t0 + 0.049)
    assert ("pulse", 100) not in light.log
    timers.run()
    assert ("pulse", 100) in light.log and done == ["done"]
    assert not any(e[0] == "levels" for e in light.log)     # a single shot leaves the light


def test_stop_before_the_pulse_fires_nothing_and_returns_to_white():
    seq, light, clock, timers, steps, done = _seq()
    seq.start(cs.TIMING_DEFAULT, PRESET, trichrome=True)
    seq.stop()                                    # during the first focus wait
    timers.run()
    assert done == ["stopped"]
    assert not any(e[0] == "pulse" for e in light.log)
    assert light.log[-2] == ("focus", False)
    assert light.log[-1] == ("levels", {"r": 0, "g": 0, "b": 0, "w": 230, "ir": 0})


def test_stop_after_the_pulse_lets_it_finish():
    seq, light, clock, timers, steps, done = _seq()
    seq.start(cs.TIMING_DEFAULT, PRESET, trichrome=True)
    timers.run(until=clock.t + 0.25)              # the red pulse has fired
    assert ("pulse", 300) in light.log
    seq.stop()
    assert seq.running                            # hold still running
    timers.run()
    assert done == ["stopped"]
    assert sum(1 for e in light.log if e[0] == "pulse") == 1
    assert ("focus", False) in light.log
    assert light.log[-1][0] == "levels" and light.log[-1][1]["w"] == 230


def test_disconnect_mid_sequence_stops_it():
    seq, light, clock, timers, steps, done = _seq()
    seq.start(cs.TIMING_DEFAULT, PRESET, trichrome=True)
    timers.run(until=clock.t + 0.9)               # after the red shot
    light.open = False
    timers.run()
    assert done == ["disconnected"]
    assert sum(1 for e in light.log if e[0] == "pulse") == 1


def test_start_without_a_light_refuses():
    seq, light, clock, timers, steps, done = _seq()
    light.open = False
    assert not seq.start(cs.TIMING_DEFAULT, PRESET)
    assert done == ["disconnected"] and light.log == []


def test_a_second_start_while_running_is_ignored():
    seq, light, clock, timers, steps, done = _seq()
    assert seq.start(cs.TIMING_DEFAULT, PRESET)
    assert not seq.start(cs.TIMING_DEFAULT, PRESET)
    timers.run()
    assert done == ["done"] and len(steps) == 3


def test_period_waits_rather_than_dropping():
    seq, light, clock, timers, steps, done = _seq()
    seq.start({"focus_ms": 0, "pulse_ms": 10, "hold_ms": 0, "period_ms": 2000},
              PRESET, trichrome=False)
    timers.run()
    t1 = clock.t
    seq.start({"focus_ms": 0, "pulse_ms": 10, "hold_ms": 0, "period_ms": 2000},
              PRESET, trichrome=False)
    timers.run()
    assert len(steps) == 2 and steps[1][2] - steps[0][2] == pytest.approx(2.0)
    assert done == ["done", "done"] and clock.t > t1


def test_hold_must_cover_the_exposure():
    t = {"hold_ms": 200}
    assert cs.hold_covers(t, 1 / 250)
    assert not cs.hold_covers(t, 0.25)
    assert cs.hold_covers(t, None)
    assert cs.clean_timing({"pulse_ms": 99999, "focus_ms": -5})["pulse_ms"] == 2550
    assert cs.clean_timing({"focus_ms": -5})["focus_ms"] == 0


# --------------------------------------------------------------------------- #
# presets
# --------------------------------------------------------------------------- #
def test_presets_normalise_add_rename_delete():
    d = lp.normalise(None)
    assert d == {"presets": [lp.DEFAULT], "selected": 0}
    d = lp.add(d, "Portra", {"r": 200, "g": 150, "b": 120, "w": 230})
    assert lp.names(d) == ["Preset 1", "Portra"] and d["selected"] == 1
    d = lp.add(d, "portra", {"r": 1})
    assert lp.names(d)[-1] == "portra 2"
    d = lp.rename(d, 2, "Ektar")
    assert lp.names(d)[-1] == "Ektar"
    assert lp.names(lp.rename(d, 2, "PORTRA")) == lp.names(d)      # taken
    d = lp.overwrite(d, 1, {"r": 9, "g": 9, "b": 9, "w": 9, "ir": 9})
    assert d["presets"][1] == {"name": "Portra", "r": 9, "g": 9, "b": 9, "w": 9, "ir": 9}
    d["selected"] = 2
    d = lp.delete(d, 1)
    assert lp.names(d) == ["Preset 1", "Ektar"] and d["selected"] == 1
    d = lp.delete(lp.delete(d, 0), 0)
    assert lp.names(d) == ["Ektar"]                                  # never empty
    assert lp.normalise(lp.dumps(d)) == d


def test_presets_import_the_other_apps_file():
    other = {"presets": [{"name": "Preset 1", "r": 1, "g": 2, "b": 3, "w": 4, "ir": 0},
                         {"name": "Night", "r": 300, "g": "x", "b": 5, "w": 0, "ir": 0},
                         {"name": ""}],
             "selected_index": 1}
    d, n = lp.import_from(lp.normalise(None), other)
    assert n == 1 and lp.names(d) == ["Preset 1", "Night"]
    assert d["presets"][1] == {"name": "Night", "r": 255, "g": 0, "b": 5, "w": 0, "ir": 0}
    assert lp.normalise(other)["selected"] == 1
    assert lp.import_from(d, "not json") == (d, 0)
