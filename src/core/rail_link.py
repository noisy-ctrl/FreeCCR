"""The connection to a focus rail that speaks the Open Rail Protocol
(spec/focus-rail.md §4, docs/open-rail-protocol.md).

`RailLink` owns the rail's serial port and is the only thing that touches
it, on its own thread (`RailClient` puts it there), in the `LightLink`
pattern: non-blocking reads on a 15 ms tick, a check that the rail answers,
reconnecting to the remembered port after an unplug. Serial object, port
lister, clock and sleep are injectable, so it is tested without hardware.
"""
import time

from PySide6.QtCore import QObject, QTimer, Signal, Slot, Qt, QMetaObject

from core import capture_light as cl
from core import orp

STATES_OPEN = ("waiting", "ok", "silent")


def _pyserial_factory():
    return cl.serial.Serial()


class RailLink(QObject):
    """States: "unavailable" (no pyserial), "off", "error" (could not open,
    or the port has a light on it), "waiting" (open, no ORP answer yet),
    "ok" (the rail answered HELLO), "silent" (open, no answer with DTR
    either way), "lost" (an I/O error closed it; it reconnects when the
    port comes back)."""

    state_changed = Signal(str, str, str)       # state, port, detail
    info_changed = Signal(object)                # the HELLO's fields
    status_changed = Signal(object)              # {"pos", "moving", "homed", "enabled"}
    move_done = Signal(int, int, object)         # id (-1: none), pos, flags
    rail_error = Signal(str)                     # an ERR line
    ports_changed = Signal(list)

    TICK_MS = 15
    SCAN_MS = 3000
    HELLO_EVERY_S = 0.6       # a board that reset on open misses the first HELLO
    LINK_CHECK_S = 2.5
    SETTLE_S = 0.08
    BAUD = 115200

    def __init__(self, serial_factory=None, port_lister=None,
                 clock=time.monotonic, sleep=time.sleep, available=None):
        super().__init__()
        self._available = cl.SERIAL_AVAILABLE if available is None else bool(available)
        self._factory = serial_factory or _pyserial_factory
        self._lister = port_lister or orp.list_ports
        self._clock = clock
        self._sleep = sleep
        self._ser = None
        self._port = ""
        self._auto_port = ""
        self._blocked = set()
        self._user_closed = False
        self._parser = orp.LineParser()
        self._dtr = True
        self._retried = False
        self._opened_at = 0.0
        self._hello_at = -1e9
        self._answered = False
        self._info = {}
        self._status = {}
        self._ports = []
        self._state = ("off", "", "")
        self._tick_timer = None
        self._scan_timer = None

    # ------------------------------------------------------------------ #
    # lifecycle (on the link's own thread)
    # ------------------------------------------------------------------ #
    @Slot()
    def start(self):
        if not self._available:
            self._set_state("unavailable", "", "The focus rail needs the pyserial package")
            return
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(self.TICK_MS)
        self._tick_timer.timeout.connect(self.tick)
        self._tick_timer.start()
        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(self.SCAN_MS)
        self._scan_timer.timeout.connect(self.scan)
        self._scan_timer.start()
        self.state_changed.emit(*self._state)
        self.scan()

    @Slot(bool)
    def shutdown(self, stop=True):
        """Stop for good: a moving rail is stopped (it decelerates), then the
        port closes. The motor stays energised: the rail holds the camera."""
        for t in (self._tick_timer, self._scan_timer):
            if t is not None:
                t.stop()
        if self._ser is not None and stop and self._status.get("moving"):
            self._write(orp.STOP)
        self._close_quietly()
        self._auto_port = ""

    # ------------------------------------------------------------------ #
    # commands (queued from the GUI)
    # ------------------------------------------------------------------ #
    @Slot(str)
    def open_port(self, port):
        self._user_closed = False
        self._retried = False
        self._open(port, assert_dtr=True)

    @Slot()
    def close_port(self):
        self._user_closed = True
        self._close_quietly()
        self._set_state("off", self._port, "")

    @Slot(str)
    def set_auto_port(self, port):
        self._auto_port = port or ""

    @Slot(object)
    def set_blocked(self, ports):
        """Ports another link holds (the light's): never opened here."""
        self._blocked = {p for p in (ports or []) if p}

    @Slot()
    def rescan(self):
        self.scan(force_emit=True)

    @Slot(int, int)
    def goto(self, pos, move_id):
        self._send(orp.goto_line(pos, move_id if move_id >= 0 else None))

    @Slot(int, int)
    def move(self, steps, move_id):
        self._send(orp.move_line(steps, move_id if move_id >= 0 else None))

    @Slot()
    def stop(self):
        self._send(orp.STOP)

    @Slot()
    def zero(self):
        self._send(orp.ZERO)
        self._send(orp.STATUS)

    @Slot(int)
    def home(self, move_id):
        self._send(orp.home_line(move_id if move_id >= 0 else None))

    @Slot(int, int)
    def speed(self, steps_per_s, accel):
        self._send(orp.speed_line(steps_per_s, accel if accel > 0 else None))

    @Slot()
    def ask_status(self):
        self._send(orp.STATUS)

    # ------------------------------------------------------------------ #
    # the tick and the port scan
    # ------------------------------------------------------------------ #
    @Slot()
    def tick(self):
        ser = self._ser
        if ser is None:
            return
        try:
            n = ser.in_waiting
            chunk = ser.read(n) if n else b""
        except Exception as e:  # unplugged, mostly
            self._lose(e)
            return
        if chunk:
            for line in self._parser.feed(chunk):
                self._on_line(line)
                if self._ser is None:
                    return
        if self._ser is None:
            return
        now = self._clock()
        if self._state[0] == "waiting":
            if now - self._opened_at >= self.LINK_CHECK_S:
                if not self._retried:
                    # Silent: the firmware may want DTR the other way (or the
                    # board is still booting). Try the other way once.
                    self._retried = True
                    self._open(self._port, assert_dtr=not self._dtr)
                    return
                self._set_state("silent", self._port,
                                "Port open but no rail answers: is it the right port?")
            elif now - self._hello_at >= self.HELLO_EVERY_S:
                self._hello_at = now
                self._write(orp.HELLO)

    @Slot()
    def scan(self, force_emit=False):
        try:
            ports = list(self._lister())
        except Exception:
            ports = []
        if ports != self._ports or force_emit:
            self._ports = ports
            self.ports_changed.emit(list(ports))
        if (self._ser is None and self._auto_port and not self._user_closed
                and self._auto_port in ports and self._auto_port not in self._blocked):
            self._retried = False
            self._open(self._auto_port, assert_dtr=True)

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def is_open(self):
        return self._ser is not None

    def info(self):
        return dict(self._info)

    def status(self):
        return dict(self._status)

    def _open(self, port, assert_dtr):
        if not self._available:
            return
        self._close_quietly()
        self._port = port
        if port in self._blocked:
            self._set_state("error", port, f"{port} is the light's port")
            return
        try:
            ser = self._factory()
            ser.port = port
            ser.baudrate = self.BAUD
            ser.timeout = 0
            try:
                ser.write_timeout = 0.5
            except Exception:
                pass
            try:
                ser.exclusive = True           # no second program on the rail
            except Exception:
                pass
            # DTR on, RTS off, set BEFORE open: an ESP32 dev board's
            # auto-reset circuit resets the chip on RTS-without-DTR, and a
            # native-USB board only sends with DTR on (spec §2).
            ser.dtr = bool(assert_dtr)
            ser.rts = False
            ser.open()
        except Exception as e:
            self._set_state("error", port, f"Could not open {port}: {e}")
            return
        self._ser = ser
        self._dtr = bool(assert_dtr)
        self._parser = orp.LineParser()
        self._answered = False
        self._info = {}
        self._status = {}
        try:
            self._sleep(self.SETTLE_S)
            ser.reset_input_buffer()
        except Exception:
            pass
        self._opened_at = self._clock()
        self._hello_at = self._opened_at
        self._set_state("waiting", port, "Waiting for the rail…")
        self._write(orp.HELLO)

    def _on_line(self, line):
        reply = orp.parse_reply(line)
        if reply is None:
            return
        kind, data = reply
        if kind == "hello":
            self._answered = True
            self._info = dict(data)
            self.info_changed.emit(dict(self._info))
            name = data.get("name") or "Rail"
            self._set_state("ok", self._port, name)
            self._write(orp.STATUS)
        elif kind == "light":
            port = self._port
            self._close_quietly()
            self._user_closed = True
            name = data.get("name") or "a light"
            self._set_state("error", port, f"That port is a light ({name}), not a rail")
        elif kind == "status":
            if data:
                self._status.update(data)
                self.status_changed.emit(dict(self._status))
        elif kind == "done":
            if data.get("pos") is not None:
                self._status["pos"] = data["pos"]
            self._status["moving"] = False
            self.status_changed.emit(dict(self._status))
            mid = data.get("id")
            self.move_done.emit(-1 if mid is None else int(mid),
                                int(data.get("pos") or 0), dict(data))
        elif kind == "err":
            if self._answered:
                self.rail_error.emit(str(data))
                self._write(orp.STATUS)          # a refused move: still where it was

    def _send(self, line):
        if self._ser is None or self._state[0] != "ok":
            return False
        return self._write(line)

    def _write(self, pkt):
        if self._ser is None:
            return False
        try:
            self._ser.write(pkt)
            return True
        except Exception as e:
            self._lose(e)
            return False

    def _lose(self, err):
        self._close_quietly()
        self._set_state("lost", self._port, "Disconnected (unplugged?)")

    def _close_quietly(self):
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    def _set_state(self, state, port, detail):
        new = (state, port or "", detail or "")
        if new != self._state:
            self._state = new
            self.state_changed.emit(*new)


class RailClient(QObject):
    """The GUI's handle on a `RailLink`: puts it on its own thread and
    forwards commands as queued signals. `threaded=False` (tests) keeps the
    link on the caller's thread."""

    state_changed = Signal(str, str, str)
    info_changed = Signal(object)
    status_changed = Signal(object)
    move_done = Signal(int, int, object)
    rail_error = Signal(str)
    ports_changed = Signal(list)

    _do_open = Signal(str)
    _do_close = Signal()
    _do_auto = Signal(str)
    _do_blocked = Signal(object)
    _do_rescan = Signal()
    _do_goto = Signal(int, int)
    _do_move = Signal(int, int)
    _do_stop = Signal()
    _do_zero = Signal()
    _do_home = Signal(int)
    _do_speed = Signal(int, int)
    _do_status = Signal()

    def __init__(self, link=None, threaded=True, parent=None):
        super().__init__(parent)
        self.link = link or RailLink()
        self.state, self.port, self.detail = "off", "", ""
        self.info = {}
        self.status = {}
        self.ports = []
        self._ids = 0
        self._thread = None
        self.link.state_changed.connect(self._on_state)
        self.link.info_changed.connect(self._on_info)
        self.link.status_changed.connect(self._on_status)
        self.link.move_done.connect(self.move_done)
        self.link.rail_error.connect(self.rail_error)
        self.link.ports_changed.connect(self._on_ports)
        for sig, slot in ((self._do_open, self.link.open_port),
                          (self._do_close, self.link.close_port),
                          (self._do_auto, self.link.set_auto_port),
                          (self._do_blocked, self.link.set_blocked),
                          (self._do_rescan, self.link.rescan),
                          (self._do_goto, self.link.goto),
                          (self._do_move, self.link.move),
                          (self._do_stop, self.link.stop),
                          (self._do_zero, self.link.zero),
                          (self._do_home, self.link.home),
                          (self._do_speed, self.link.speed),
                          (self._do_status, self.link.ask_status)):
            sig.connect(slot)
        if threaded:
            from utils.qt_threads import new_thread
            self._thread = new_thread()
            self.link.moveToThread(self._thread)
            self._thread.started.connect(self.link.start)
            self._thread.start()
        else:
            self.link.start()

    def _on_state(self, state, port, detail):
        self.state, self.port, self.detail = state, port, detail
        if state not in STATES_OPEN:
            self.info = {}
            self.status = {}
        self.state_changed.emit(state, port, detail)

    def _on_info(self, info):
        self.info = dict(info)
        self.info_changed.emit(self.info)

    def _on_status(self, st):
        self.status = dict(st)
        self.status_changed.emit(self.status)

    def _on_ports(self, ports):
        self.ports = list(ports)
        self.ports_changed.emit(self.ports)

    def is_open(self):
        return self.state in STATES_OPEN

    def is_ready(self):
        return self.state == "ok"

    def moving(self):
        return bool(self.status.get("moving"))

    def position(self):
        return self.status.get("pos")

    def steps_per_mm(self):
        return float(self.info.get("steps_per_mm") or 0.0)

    def steps_per_rev(self):
        return int(self.info.get("steps_per_rev") or 0)

    def next_id(self):
        self._ids = self._ids % 1_000_000 + 1
        return self._ids

    # commands
    def open_port(self, port):
        self._do_open.emit(port)

    def close_port(self):
        self._do_close.emit()

    def set_auto_port(self, port):
        self._do_auto.emit(port or "")

    def set_blocked(self, ports):
        self._do_blocked.emit(list(ports or []))

    def rescan(self):
        self._do_rescan.emit()

    def goto(self, pos, move_id=None):
        """Returns the move's id (DONE carries it back)."""
        mid = self.next_id() if move_id is None else int(move_id)
        self.status["moving"] = True
        self._do_goto.emit(int(round(pos)), mid)
        return mid

    def move(self, steps, move_id=None):
        mid = self.next_id() if move_id is None else int(move_id)
        self.status["moving"] = True
        self._do_move.emit(int(round(steps)), mid)
        return mid

    def stop(self):
        self._do_stop.emit()

    def zero(self):
        self._do_zero.emit()

    def home(self):
        mid = self.next_id()
        self.status["moving"] = True
        self._do_home.emit(mid)
        return mid

    def speed(self, steps_per_s, accel=0):
        self._do_speed.emit(int(steps_per_s), int(accel or 0))

    def ask_status(self):
        self._do_status.emit()

    def shutdown(self, stop=True):
        if self._thread is None:
            self.link.shutdown(stop)
            return
        if self._thread.isRunning():
            from PySide6.QtCore import Q_ARG
            QMetaObject.invokeMethod(self.link, "shutdown",
                                     Qt.BlockingQueuedConnection, Q_ARG(bool, bool(stop)))
            self._thread.quit()
            self._thread.wait(3000)
