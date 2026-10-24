"""The Scanlight over USB serial: its packets, the telemetry parser, port
ranking and channel rules (spec/live-capture-monitor.md §7). Qt-free; the
connection itself is `core.light_link`.

Packets, both ways: start byte 0xFE, header, data length, data.
- To the light: set colour (R, G, B, W, IR 0..255, save flag), firmware
  version request, shutter pulse (10 ms units, 0..255), focus on/off.
- From the light, about every 200 ms: LED temperature (int32 milli-degrees
  C) and supply voltage (int32 millivolts), big-endian.
"""
import struct

try:  # optional: the rest of the monitor works without it
    import serial  # noqa: F401
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the environment
    serial = None
    SERIAL_AVAILABLE = False

START = 0xFE
H2D_SET_COLOUR = 0
H2D_FW_VERSION = 2
H2D_SHUTTER = 3
H2D_FOCUS = 7
D2H_TEMP = 1
D2H_VBUS = 2

CHANNELS = ("r", "g", "b", "w", "ir")
RGB = ("r", "g", "b")
WIR = ("w", "ir")
OFF = {k: 0 for k in CHANNELS}
NAMES = {"r": "Red", "g": "Green", "b": "Blue", "w": "White", "ir": "Infrared"}

PULSE_UNIT_MS = 10
PULSE_MAX_MS = 255 * PULSE_UNIT_MS

# Port names: real USB serial devices first, macOS's own ports last (an app
# connected to cu.debug-console looks connected and nothing ever answers).
LIKELY = ("usbmodem", "usbserial", "slab_usbtouart", "wchusbserial",
          "ttyusb", "ttyacm")
NOISE = ("debug-console", "bluetooth-incoming", "wlan-debug")


def _byte(v):
    try:
        return max(0, min(255, int(round(float(v)))))
    except (TypeError, ValueError):
        return 0


def packet(header, data=b""):
    data = bytes(data)
    return bytes([START, header & 0xFF, len(data)]) + data


def colour_packet(levels, save=False):
    """Set the five channels (a dict r/g/b/w/ir, 0..255; missing = 0)."""
    levels = levels or {}
    return packet(H2D_SET_COLOUR,
                  [_byte(levels.get(k, 0)) for k in CHANNELS] + [1 if save else 0])


def fw_version_packet():
    return packet(H2D_FW_VERSION)


def pulse_units(ms):
    return max(0, min(255, int(round(float(ms) / PULSE_UNIT_MS))))


def shutter_packet(ms):
    return packet(H2D_SHUTTER, [pulse_units(ms)])


def focus_packet(on):
    return packet(H2D_FOCUS, [1 if on else 0])


class PacketParser:
    """Collects bytes and returns whole packets, resynchronising on the
    start byte: packets may arrive split across reads, several in one read,
    or with junk between them."""

    MAX_BUFFER = 4096

    def __init__(self):
        self.buf = bytearray()

    def feed(self, chunk):
        out = []
        if chunk:
            self.buf.extend(chunk)
        if len(self.buf) > self.MAX_BUFFER:          # never grows without bound
            del self.buf[:-self.MAX_BUFFER]
        while True:
            i = self.buf.find(bytes([START]))
            if i < 0:
                self.buf.clear()
                return out
            if i:
                del self.buf[:i]
            if len(self.buf) < 3:
                return out
            header, length = self.buf[1], self.buf[2]
            if len(self.buf) < 3 + length:
                return out
            out.append((header, bytes(self.buf[3:3 + length])))
            del self.buf[:3 + length]


def telemetry(header, data):
    """("vbus", volts) or ("temp", celsius) for a telemetry packet, else None."""
    if len(data) != 4:
        return None
    value = struct.unpack(">i", data)[0] / 1000.0
    if header == D2H_VBUS:
        return ("vbus", value)
    if header == D2H_TEMP:
        return ("temp", value)
    return None


def port_rank(name):
    n = str(name).lower()
    if any(k in n for k in LIKELY):
        return (0, n)
    if any(k in n for k in NOISE):
        return (2, n)
    return (1, n)


def list_ports():
    """Serial port device names, likely ones first. [] without pyserial."""
    if not SERIAL_AVAILABLE:
        return []
    try:
        names = [p.device for p in serial.tools.list_ports.comports()]
    except Exception:
        return []
    return sorted(names, key=port_rank)


def clean_levels(levels):
    """A full r/g/b/w/ir dict of ints 0..255 (missing = 0). A set holding
    both groups keeps RGB, as the light itself does."""
    out = {k: _byte((levels or {}).get(k, 0)) for k in CHANNELS}
    if any(out[k] for k in RGB) and any(out[k] for k in WIR):
        for k in WIR:
            out[k] = 0
    return out


def exclusive(levels, channel, value):
    """`levels` with `channel` set to `value`; raising a channel of one
    group sets the other group to 0 (the light can't run RGB and White/IR
    together). A new dict."""
    out = {k: _byte((levels or {}).get(k, 0)) for k in CHANNELS}
    out[channel] = _byte(value)
    if out[channel] > 0:
        for k in (WIR if channel in RGB else RGB):
            out[k] = 0
    return out


def solo(preset, channel):
    """Only `channel` lit, at the preset's value."""
    out = dict(OFF)
    out[channel] = _byte((preset or {}).get(channel, 0))
    return out
