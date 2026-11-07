#!/usr/bin/env python3
"""The focus rail's reference firmware (spec/focus-rail.md §7): its protocol
and motion logic (examples/focus_rail/arduino/FocusRail/RailCore.h) compiled
on this computer with a simulated motor and end switch
(examples/focus_rail/host/rail_host.cpp), then FreeCCR's own link driving it
end to end. Skipped without a C++ compiler."""

import os
import shutil
import subprocess
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, os.path.join(ROOT, 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.rail_link import RailLink  # noqa: E402

FIRMWARE = os.path.join(ROOT, "examples", "focus_rail")
CXX = shutil.which("g++") or shutil.which("clang++")


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    if CXX is None:
        pytest.skip("no C++ compiler")
    exe = str(tmp_path_factory.mktemp("rail") / "rail_host")
    r = subprocess.run([CXX, "-std=c++11", "-O1", "-Wall", "-Werror",
                        "-I", os.path.join(FIRMWARE, "arduino", "FocusRail"),
                        os.path.join(FIRMWARE, "host", "rail_host.cpp"), "-o", exe],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return exe


def run(exe, lines, mode="SWITCH", moving_status=False, args=()):
    data = lines if isinstance(lines, bytes) else ("\n".join(lines) + "\n").encode()
    out = subprocess.run([exe, mode, *args], input=data, capture_output=True,
                         timeout=30).stdout.decode()
    return [ln for ln in out.splitlines()
            if ln != "#ok" and (moving_status or "MOVING=1" not in ln)]


def test_hello_status_and_other_bytes_ignored(host):
    out = run(host, b"\xfe\x02\x00\nHELLO\r\nSTATUS\nhello\nFOO 1\n\n")
    assert out == ["ORP 1 STEPS_PER_MM=400 STEPS_PER_REV=3200 MIN=0 MAX=40000 HOME=SWITCH "
                   "NAME=Host Rail (simulated)",
                   "STATUS POS=0 MOVING=0 HOMED=0 ENABLED=1 DRIVER=1",
                   "ORP 1 STEPS_PER_MM=400 STEPS_PER_REV=3200 MIN=0 MAX=40000 HOME=SWITCH "
                   "NAME=Host Rail (simulated)",
                   "ERR unknown FOO"]


def test_a_belt_drive_reports_its_steps_through_the_sketchs_arithmetic(host):
    """spec/focus-rail.md revision 2 A: `orp::screwStepsPerTurn`, which
    FocusRail.ino uses, through the host build."""
    tail = " HOME=NONE NAME=Host Rail (simulated)"
    swapped = run(host, ["HELLO"], mode="NONE",
                  args=("--belt", "20", "30", "--lead", "4", "--microsteps", "16"))
    assert swapped == ["ORP 1 STEPS_PER_MM=1200 STEPS_PER_REV=4800 MIN=0 MAX=40000" + tail]
    first = run(host, ["HELLO"], mode="NONE", args=("--belt", "30", "20", "--lead", "4"))
    assert first == ["ORP 1 STEPS_PER_MM=533.333 STEPS_PER_REV=2133.33 MIN=0 MAX=40000"
                     + tail]
    from core import orp
    h = orp.parse_reply(first[0])[1]
    assert f"{orp.lead_mm(h['steps_per_mm'], h['steps_per_rev']):g}" == "4"


def test_goto_move_and_a_retarget(host):
    out = run(host, ["GOTO 1000 ID=1", "#tick 100", "MOVE 500 ID=2", "#tick 2000",
                     "MOVE -8", "#tick 100", "GOTO x", "MOVE 5 ID=z"])
    assert out == ["OK", "OK", "DONE ID=2 POS=1500",
                   "STATUS POS=1500 MOVING=0 HOMED=0 ENABLED=1 DRIVER=1",
                   "OK", "DONE POS=1492",
                   "STATUS POS=1492 MOVING=0 HOMED=0 ENABLED=1 DRIVER=1",
                   "ERR bad move", "ERR bad move"]


def test_status_lines_while_moving(host):
    out = run(host, ["GOTO 4000 ID=1", "#tick 1100"], moving_status=True)
    moving = [ln for ln in out if "MOVING=1" in ln]
    assert len(moving) == 4          # every 200 ms of a 1 s move
    assert out[-2:] == ["DONE ID=1 POS=4000",
                        "STATUS POS=4000 MOVING=0 HOMED=0 ENABLED=1 DRIVER=1"]


def test_stop_and_zero(host):
    out = run(host, ["GOTO 4000 ID=3", "#tick 100", "ZERO", "STOP", "#tick 10",
                     "ZERO", "STATUS", "STOP"])
    assert out[:3] == ["OK", "ERR moving", "OK"]
    assert out[3] == "DONE ID=3 POS=400 STOPPED=1"
    assert out[5:] == ["OK", "STATUS POS=0 MOVING=0 HOMED=0 ENABLED=1 DRIVER=1", "OK"]


def test_soft_limits_only_once_homed_and_home_on_the_switch(host):
    out = run(host, ["GOTO -300 ID=1", "#tick 200", "#switch -1000", "HOME ID=5",
                     "GOTO 10", "#tick 3000", "GOTO -300 ID=6", "#tick 500",
                     "GOTO 50000 ID=7", "#tick 12000"])
    assert out[:3] == ["OK", "DONE ID=1 POS=-300",
                       "STATUS POS=-300 MOVING=0 HOMED=0 ENABLED=1 DRIVER=1"]
    assert out[3:7] == ["OK", "ERR homing", "DONE ID=5 POS=200",
                        "STATUS POS=200 MOVING=0 HOMED=1 ENABLED=1 DRIVER=1"]
    assert out[7:9] == ["OK", "DONE ID=6 POS=0 LIMIT=1"]
    assert out[10:12] == ["OK", "DONE ID=7 POS=40000 LIMIT=1"]


def test_homing_failures_and_stop(host):
    out = run(host, ["HOME ID=1", "#tick 8000"])
    assert out[0] == "OK" and out[1].startswith("DONE ID=1 POS=")
    assert out[1].endswith("FAIL=no switch found")
    assert "HOMED=0" in out[2]
    out = run(host, ["#switch -100000", "HOME ID=2", "#tick 50", "STOP", "#tick 20"])
    assert out[0] == "OK" and out[1] == "OK"
    assert out[2].startswith("DONE ID=2 ") and out[2].endswith("STOPPED=1")
    assert "HOMED=0" in out[3]
    assert run(host, ["HOME"], mode="NONE") == ["ERR no home"]
    out = run(host, ["HELLO", "#stall -700", "HOME ID=4", "#tick 3000"], mode="STALL")
    assert "HOME=STALL" in out[0]
    assert out[1:] == ["OK", "DONE ID=4 POS=200",
                       "STATUS POS=200 MOVING=0 HOMED=1 ENABLED=1 DRIVER=1"]


def test_enable_and_speed(host):
    out = run(host, ["SPEED 0", "SPEED 8000 ACCEL=x", "SPEED 8000 ACCEL=40000",
                     "ENABLE 2", "ENABLE 0", "GOTO 5", "ENABLE 1"])
    assert out == ["ERR bad SPEED", "ERR bad SPEED", "OK", "ERR bad ENABLE", "OK",
                   "STATUS POS=0 MOVING=0 HOMED=0 ENABLED=0 DRIVER=1",
                   "ERR motor off (ENABLE 1)", "OK",
                   "STATUS POS=0 MOVING=0 HOMED=0 ENABLED=1 DRIVER=1"]


class ProcPort:
    """A pyserial-like port onto the host build, running in real time."""

    def __init__(self, exe):
        self.exe = exe
        self.port = self.baudrate = self.timeout = self.write_timeout = None
        self.dtr = self.rts = self.exclusive = None
        self.proc = None
        self._buf = bytearray()

    def open(self):
        self.proc = subprocess.Popen([self.exe, "SWITCH", "--realtime"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, bufsize=0)
        os.set_blocking(self.proc.stdout.fileno(), False)

    def _pump(self):
        try:
            d = os.read(self.proc.stdout.fileno(), 65536)
        except BlockingIOError:
            d = b""
        self._buf += d or b""

    @property
    def in_waiting(self):
        self._pump()
        return len(self._buf)

    def read(self, n):
        d = bytes(self._buf[:n])
        del self._buf[:n]
        return d

    def reset_input_buffer(self):
        self._pump()
        self._buf.clear()

    def write(self, b):
        self.proc.stdin.write(b)
        self.proc.stdin.flush()
        return len(b)

    def close(self):
        if self.proc is not None:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)


def test_freeccr_drives_the_reference_firmware_end_to_end(host):
    ports = []

    def factory():
        p = ProcPort(host)
        ports.append(p)
        return p
    link = RailLink(serial_factory=factory, port_lister=lambda: ["sim"], available=True,
                    sleep=lambda s: None)
    got = {"state": [], "done": [], "status": []}
    link.state_changed.connect(lambda s, p, d: got["state"].append((s, d)))
    link.move_done.connect(lambda i, p, f: got["done"].append((i, p, f)))
    link.status_changed.connect(lambda st: got["status"].append(st))

    def until(cond, secs=5.0):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            link.tick()
            if cond():
                return True
            time.sleep(0.005)
        return False
    try:
        link.open_port("sim")
        assert until(lambda: got["state"] and got["state"][-1][0] == "ok")
        assert link.info()["steps_per_mm"] == 400.0 and link.info()["home"] == "switch"
        assert link.info()["steps_per_rev"] == 3200
        link.goto(800, 11)
        assert until(lambda: got["done"])
        assert got["done"][-1][:2] == (11, 800)
        link.move(-8, 12)
        assert until(lambda: len(got["done"]) == 2)
        assert got["done"][-1][:2] == (12, 792)
        ports[0].write(b"#switch -400\n")
        link.home(13)
        assert until(lambda: len(got["done"]) == 3)
        assert got["done"][-1][:2] == (13, 200) and not got["done"][-1][2]["fail"]
        assert until(lambda: got["status"][-1].get("homed") is True)
        link.goto(-50, 14)
        assert until(lambda: len(got["done"]) == 4)
        assert got["done"][-1][1] == 0 and got["done"][-1][2]["limit"]
    finally:
        link.shutdown()
