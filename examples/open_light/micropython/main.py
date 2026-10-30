"""Open Light Protocol v1: reference firmware for MicroPython boards
(Raspberry Pi Pico / Pico W / Pico 2, ESP32-S2/S3/C3 with native USB, ...).

Copy this file to the board as main.py, set the pins below, and FreeCCR's
Live Capture Monitor finds the light on its USB serial port. The protocol is
in FreeCCR's docs/open-light-protocol.md. This file is MIT licensed (see the
end), so it can go into your own light's firmware.

Wiring, in short: each channel's PWM pin drives that colour's LED driver (a
logic-level MOSFET or a constant-current driver's PWM/dim input, never the
LEDs straight off a GPIO). The shutter and focus pins drive optocouplers
across the camera's remote-release contacts, so the camera is isolated from
the light. Keep the PWM frequency high (20 kHz here): at a few hundred Hz a
fast shutter speed catches the LEDs part-way through a cycle and frames come
out uneven.
"""
import sys

NAME = "DIY Open Light (MicroPython)"
PINS = {"R": 16, "G": 17, "B": 18, "W": 19, "IR": None}   # None: no such channel
SHUTTER_PIN = 20          # optocoupler for the release; None if not fitted
FOCUS_PIN = 21            # optocoupler for the half-press; None if not fitted
PWM_HZ = 20000
STATUS_EVERY_MS = 2000    # unprompted STATUS lines (0 = only when asked)
MAX_LINE = 512
SHUTTER_MAX_MS = 10000
ORDER = ("R", "G", "B", "W", "IR")


class OpenLight:
    """The protocol, independent of the board: feed it bytes, write back what
    it returns. `hw` sets channels (0..255), pulses the shutter, holds focus
    and reports status; see PicoHardware below."""

    def __init__(self, hw, name=NAME):
        self.hw = hw
        self.name = name
        self.buf = bytearray()
        self.levels = {k: 0 for k in ORDER if hw.has(k)}

    def feed(self, data):
        """Bytes in; reply lines (str, without newline) out. Anything outside
        printable ASCII is dropped, so another protocol's bytes on the same
        port are harmless."""
        out = []
        for b in bytes(data):
            if b == 0x0A:
                line = self.buf.decode().strip()
                self.buf = bytearray()
                if line:
                    out.append(self.handle(line))
            elif 0x20 <= b <= 0x7E and len(self.buf) < MAX_LINE:
                self.buf.append(b)
        return out

    def hello(self):
        chans = ",".join(k for k in ORDER if self.hw.has(k))
        return "OLP 1 CH={} SHUTTER={} FOCUS={} NAME={}".format(
            chans, 1 if self.hw.has_shutter() else 0, 1 if self.hw.has_focus() else 0,
            self.name)

    def status(self):
        st = self.hw.status()
        return "STATUS" + "".join(" {}={}".format(k, v) for k, v in st.items())

    def handle(self, line):
        parts = line.split()
        cmd = parts[0].upper()
        if cmd == "HELLO":
            return self.hello()
        if cmd == "SET":
            for tok in parts[1:]:
                if "=" not in tok:
                    return "ERR bad SET"
                k, v = tok.split("=", 1)
                k = k.upper()
                try:
                    v = max(0, min(255, int(v)))
                except ValueError:
                    return "ERR bad value " + tok
                if k in self.levels:              # channels it hasn't got: ignored
                    self.levels[k] = v
                    self.hw.set(k, v)
            return "OK"
        if cmd == "OFF":
            for k in self.levels:
                self.levels[k] = 0
                self.hw.set(k, 0)
            return "OK"
        if cmd == "SHUTTER":
            if not self.hw.has_shutter():
                return "ERR no shutter"
            try:
                ms = max(1, min(SHUTTER_MAX_MS, int(parts[1])))
            except (IndexError, ValueError):
                return "ERR bad SHUTTER"
            self.hw.pulse(ms)
            return "OK"
        if cmd == "FOCUS":
            if not self.hw.has_focus():
                return "ERR no focus"
            self.hw.focus(len(parts) > 1 and parts[1] == "1")
            return "OK"
        if cmd == "STATUS":
            return self.status()
        return "ERR unknown " + cmd


class PicoHardware:
    """MicroPython pins: PWM per channel, optocoupler pins for the camera."""

    def __init__(self):
        from machine import Pin, PWM
        self._pwm = {}
        for k, pin in PINS.items():
            if pin is not None:
                p = PWM(Pin(pin))
                p.freq(PWM_HZ)
                p.duty_u16(0)
                self._pwm[k] = p
        self._shutter = Pin(SHUTTER_PIN, Pin.OUT, value=0) if SHUTTER_PIN is not None else None
        self._focus = Pin(FOCUS_PIN, Pin.OUT, value=0) if FOCUS_PIN is not None else None

    def has(self, k):
        return k in self._pwm

    def has_shutter(self):
        return self._shutter is not None

    def has_focus(self):
        return self._focus is not None

    def set(self, k, v):
        self._pwm[k].duty_u16(v * 257)            # 0..255 -> 0..65535

    def pulse(self, ms):
        import time
        self._shutter.value(1)
        time.sleep_ms(ms)
        self._shutter.value(0)

    def focus(self, on):
        self._focus.value(1 if on else 0)

    def status(self):
        out = {}
        if sys.platform == "rp2":                  # the RP2040's own temperature sensor
            try:
                from machine import ADC
                v = ADC(4).read_u16() * 3.3 / 65535
                out["TEMP"] = round(27 - (v - 0.706) / 0.001721, 1)
            except Exception:
                pass
        return out


def main():
    import select
    import time
    try:
        import micropython
        micropython.kbd_intr(-1)       # a 0x03 byte in the data must not stop us
    except Exception:
        pass
    light = OpenLight(PicoHardware())
    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN)
    src = getattr(sys.stdin, "buffer", sys.stdin)     # bytes, not text
    last = time.ticks_ms()
    while True:
        if poll.poll(50):
            ch = src.read(1)
            for reply in light.feed(ch.encode() if isinstance(ch, str) else ch):
                sys.stdout.write(reply + "\n")
        if STATUS_EVERY_MS and time.ticks_diff(time.ticks_ms(), last) >= STATUS_EVERY_MS:
            last = time.ticks_ms()
            sys.stdout.write(light.status() + "\n")


if __name__ == "__main__":
    main()

# MIT License
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
