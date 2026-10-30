"""The connection to the Scanlight (spec/live-capture-monitor.md §7.2-7.3).

`LightLink` owns the serial port and is the only thing that touches it: it
lives on its own thread (`LightClient` puts it there), reads without
blocking on a 15 ms tick, writes what the GUI queues, checks that the light
actually answers, notices an unplug and reconnects to the remembered port.
The serial object, the port lister, the clock and the sleep are injectable,
so all of it is tested without hardware.
"""
import time

from PySide6.QtCore import QObject, QTimer, Signal, Slot, Qt, QMetaObject, Q_ARG

from core import capture_light as cl
from core import olp

STATES_OPEN = ("waiting", "ok", "silent")


def _pyserial_factory():
    return cl.serial.Serial()


class LightLink(QObject):
    """States: "unavailable" (no pyserial), "off", "error" (could not open),
    "waiting" (open, nothing heard yet), "ok" (the light answers), "silent"
    (open, nothing heard with DTR either way), "lost" (an I/O error closed
    it; it reconnects when the port comes back)."""

    state_changed = Signal(str, str, str)      # state, port, detail
    telemetry_changed = Signal(object)         # {"vbus": volts, "temp": celsius}
    ports_changed = Signal(list)

    TICK_MS = 15
    SCAN_MS = 3000
    LINK_CHECK_S = 2.5
    COLOUR_GAP_S = 0.016
    TELEMETRY_GAP_S = 0.25
    SETTLE_S = 0.08          # a board that reset on open needs a moment
    BAUD = 115200

    def __init__(self, serial_factory=None, port_lister=None,
                 clock=time.monotonic, sleep=time.sleep, available=None):
        super().__init__()
        self._available = cl.SERIAL_AVAILABLE if available is None else bool(available)
        self._factory = serial_factory or _pyserial_factory
        self._lister = port_lister or cl.list_ports
        self._clock = clock
        self._sleep = sleep
        self._ser = None
        self._port = ""
        self._auto_port = ""
        self._user_closed = False
        self._parser = cl.PacketParser()
        # Two protocols on one port (spec/open-light-protocol.md): the
        # Scanlight's binary packets (the default, so nothing changes for it)
        # and the Open Light Protocol's text lines, taken up the moment a
        # light answers HELLO with "OLP 1 ...".
        self._olp_parser = olp.LineParser()
        self._dialect = "scanlight"
        self._light_info = {}
        self._levels_now = None
        self._rx = 0
        self._dtr = True
        self._retried = False
        self._opened_at = 0.0
        self._pending = None
        self._last_colour = -1e9
        self._telemetry = {}
        self._telemetry_dirty = False
        self._last_telemetry = -1e9
        self._ports = []
        self._state = ("off", "", "")
        self._tick_timer = None
        self._scan_timer = None

    # ------------------------------------------------------------------ #
    # lifecycle (runs on the link's own thread)
    # ------------------------------------------------------------------ #
    @Slot()
    def start(self):
        if not self._available:
            self._set_state("unavailable", "", "Light control needs the pyserial package")
            return
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(self.TICK_MS)
        self._tick_timer.timeout.connect(self.tick)
        self._tick_timer.start()
        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(self.SCAN_MS)
        self._scan_timer.timeout.connect(self.scan)
        self._scan_timer.start()
        self._emit_state(force=True)
        self.scan()

    @Slot(bool)
    def shutdown(self, light_off=True):
        """Stop for good: the light off (or the last queued levels sent),
        then the port closed."""
        for t in (self._tick_timer, self._scan_timer):
            if t is not None:
                t.stop()
        if self._ser is not None:
            if light_off:
                self._pending = None
                self._write(self._off_bytes())
            else:
                self._flush_colour(force=True)
        self._close_quietly()
        self._auto_port = ""

    # ------------------------------------------------------------------ #
    # commands (queued from the GUI)
    # ------------------------------------------------------------------ #
    @Slot(str)
    def open_port(self, port):
        """A connect the user asked for (or the remembered port appearing)."""
        self._user_closed = False
        self._retried = False
        self._open(port, assert_dtr=True)

    @Slot()
    def close_port(self):
        """The user's Disconnect: the port closes and stays closed (no
        automatic reconnect) until they connect again."""
        self._user_closed = True
        if self._ser is not None:
            self._flush_colour(force=True)
        self._close_quietly()
        self._set_state("off", self._port, "")

    @Slot(str)
    def set_auto_port(self, port):
        self._auto_port = port or ""

    @Slot()
    def rescan(self):
        self.scan(force_emit=True)

    @Slot(object)
    def set_levels(self, levels):
        """Coalesced: at most one colour write per 16 ms; the last one wins."""
        self._pending = cl.clean_levels(levels)
        self._flush_colour()

    @Slot(int)
    def pulse(self, ms):
        self._flush_colour(force=True)
        self._write(olp.shutter_line(ms) if self._dialect == "olp" else cl.shutter_packet(ms))

    @Slot(bool)
    def focus(self, on):
        self._flush_colour(force=True)
        self._write(olp.focus_line(on) if self._dialect == "olp" else cl.focus_packet(on))

    # ------------------------------------------------------------------ #
    # the tick and the port scan
    # ------------------------------------------------------------------ #
    @Slot()
    def tick(self):
        ser = self._ser
        if ser is not None:
            try:
                n = ser.in_waiting
                chunk = ser.read(n) if n else b""
            except Exception as e:  # unplugged, mostly
                self._lose(e)
                return
            if chunk:
                self._rx += len(chunk)
                for header, data in self._parser.feed(chunk):
                    t = cl.telemetry(header, data)
                    if t is not None:
                        self._telemetry[t[0]] = t[1]
                        self._telemetry_dirty = True
                for line in self._olp_parser.feed(chunk):
                    self._on_olp_line(line)
                if self._ser is None:            # a write in there failed
                    return
            now = self._clock()
            if self._state[0] in ("waiting", "silent") and self._rx > 0:
                self._set_state("ok", self._port, "Responding")
            elif self._state[0] == "waiting" and now - self._opened_at >= self.LINK_CHECK_S:
                if not self._retried:
                    # Silent: DTR may have reset the board, or the firmware
                    # may wait for DTR before it talks. Try the other way once.
                    self._retried = True
                    self._open(self._port, assert_dtr=not self._dtr)
                    return
                self._set_state("silent", self._port,
                                "Port open but silent: another program may have it")
            self._flush_colour()
            if (self._telemetry_dirty
                    and now - self._last_telemetry >= self.TELEMETRY_GAP_S):
                self._telemetry_dirty = False
                self._last_telemetry = now
                self.telemetry_changed.emit(dict(self._telemetry))

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
                and self._auto_port in ports):
            self._retried = False
            self._open(self._auto_port, assert_dtr=True)

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def is_open(self):
        return self._ser is not None

    def _open(self, port, assert_dtr):
        if not self._available:
            return
        self._close_quietly()
        self._port = port
        try:
            ser = self._factory()
            ser.port = port
            ser.baudrate = self.BAUD
            ser.timeout = 0               # non-blocking reads
            try:
                ser.write_timeout = 0.5
            except Exception:
                pass
            # Set BEFORE open, so it applies as the port opens (asserting
            # first and correcting a moment later would already have reset
            # an auto-reset board). RTS stays off: nothing needs flow control.
            ser.dtr = bool(assert_dtr)
            ser.rts = False
            ser.open()
        except Exception as e:
            self._set_state("error", port, f"Could not open {port}: {e}")
            return
        self._ser = ser
        self._dtr = bool(assert_dtr)
        self._rx = 0
        self._parser = cl.PacketParser()
        self._olp_parser = olp.LineParser()
        self._dialect = "scanlight"
        self._light_info = {}
        self._telemetry = {}
        try:
            self._sleep(self.SETTLE_S)
            ser.reset_input_buffer()
        except Exception:
            pass
        self._opened_at = self._clock()
        self._set_state("waiting", port, "Waiting for the light…")
        self._write(cl.fw_version_packet())     # ask it to say something...
        self._write(olp.HELLO)                  # ...in either protocol
        if self._pending is not None:
            self._flush_colour(force=True)

    def _flush_colour(self, force=False):
        if self._pending is None or self._ser is None:
            return
        now = self._clock()
        if not force and now - self._last_colour < self.COLOUR_GAP_S:
            return
        levels = self._pending
        pkt = olp.set_line(levels) if self._dialect == "olp" else cl.colour_packet(levels)
        self._pending = None
        self._levels_now = levels
        self._last_colour = now
        self._write(pkt)

    def _off_bytes(self):
        return olp.OFF if self._dialect == "olp" else cl.colour_packet(cl.OFF)

    def _on_olp_line(self, line):
        reply = olp.parse_reply(line)
        if reply is None:
            return
        kind, data = reply
        if kind == "hello":
            first = self._dialect != "olp"
            self._dialect = "olp"
            self._light_info = dict(data)
            if data.get("name"):
                self._telemetry["name"] = data["name"]
                self._telemetry_dirty = True
            if first:
                # whatever went out before it answered was in the other protocol:
                # end that junk line, then say it again in text
                self._write(olp.NEWLINE)
                again = self._pending if self._pending is not None else self._levels_now
                if again is not None:
                    self._pending = again
                    self._flush_colour(force=True)
                self._write(olp.STATUS)
        elif kind == "status" and data:
            self._telemetry.update(data)
            self._telemetry_dirty = True

    def light_info(self):
        """What an Open Light Protocol light said about itself ({} for a
        Scanlight): version, channels, shutter, focus, name."""
        return dict(self._light_info)

    def dialect(self):
        return self._dialect

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
            self._emit_state(force=True)

    def _emit_state(self, force=False):
        self.state_changed.emit(*self._state)


class LightClient(QObject):
    """The GUI's handle on a `LightLink`: puts it on its own thread and
    forwards commands as queued signals, so the port is only ever touched on
    that thread. `threaded=False` (tests) keeps the link on the caller's
    thread and calls it directly."""

    state_changed = Signal(str, str, str)
    telemetry_changed = Signal(object)
    ports_changed = Signal(list)

    _do_open = Signal(str)
    _do_close = Signal()
    _do_levels = Signal(object)
    _do_pulse = Signal(int)
    _do_focus = Signal(bool)
    _do_auto = Signal(str)
    _do_rescan = Signal()

    def __init__(self, link=None, threaded=True, parent=None):
        super().__init__(parent)
        self.link = link or LightLink()
        self.state, self.port, self.detail = "off", "", ""
        self.telemetry = {}
        self.ports = []
        self._thread = None
        self.link.state_changed.connect(self._on_state)
        self.link.telemetry_changed.connect(self._on_telemetry)
        self.link.ports_changed.connect(self._on_ports)
        self._do_open.connect(self.link.open_port)
        self._do_close.connect(self.link.close_port)
        self._do_levels.connect(self.link.set_levels)
        self._do_pulse.connect(self.link.pulse)
        self._do_focus.connect(self.link.focus)
        self._do_auto.connect(self.link.set_auto_port)
        self._do_rescan.connect(self.link.rescan)
        if threaded:
            from utils.qt_threads import new_thread
            self._thread = new_thread()
            self.link.moveToThread(self._thread)
            self._thread.started.connect(self.link.start)
            self._thread.start()
        else:
            self.link.start()

    # state
    def _on_state(self, state, port, detail):
        self.state, self.port, self.detail = state, port, detail
        if state not in STATES_OPEN:
            self.telemetry = {}
        self.state_changed.emit(state, port, detail)

    def _on_telemetry(self, t):
        self.telemetry = dict(t)
        self.telemetry_changed.emit(self.telemetry)

    def _on_ports(self, ports):
        self.ports = list(ports)
        self.ports_changed.emit(self.ports)

    def is_open(self):
        return self.state in STATES_OPEN

    # commands
    def open_port(self, port):
        self._do_open.emit(port)

    def close_port(self):
        self._do_close.emit()

    def set_levels(self, levels):
        self._do_levels.emit(cl.clean_levels(levels))

    def pulse(self, ms):
        self._do_pulse.emit(int(ms))

    def focus(self, on):
        self._do_focus.emit(bool(on))

    def set_auto_port(self, port):
        self._do_auto.emit(port or "")

    def rescan(self):
        self._do_rescan.emit()

    def shutdown(self, light_off=True):
        """Blocks until the link has turned the light off (when asked) and
        closed the port, then stops its thread."""
        if self._thread is None:
            self.link.shutdown(light_off)
            return
        if self._thread.isRunning():
            QMetaObject.invokeMethod(self.link, "shutdown",
                                     Qt.BlockingQueuedConnection, Q_ARG(bool, bool(light_off)))
            self._thread.quit()
            self._thread.wait(3000)
