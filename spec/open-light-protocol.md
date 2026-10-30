# Open Light Protocol in FreeCCR

Status: built 2026-10-30 (the user's request: "Open protocol sounds good. You
can name the models used.").

The public protocol is `docs/open-light-protocol.md`. This is how FreeCCR
speaks it.

## Code
- `core/olp.py` (Qt-free): `set_line`, `shutter_line`, `focus_line`, the
  constants `HELLO` (`"\nHELLO\n"`: the newline first ends any partial line
  left by the other protocol's bytes), `OFF`, `STATUS`, `NEWLINE`;
  `LineParser` (printable ASCII only, CR ignored, lines capped at 512) and
  `parse_reply` → hello / status / ok / err.
- `core/light_link.LightLink` speaks both protocols on one port. On every
  open it sends the Scanlight's firmware request and then `HELLO`. Default
  dialect "scanlight", so nothing changes for the Scanlight (colour sent at
  once, binary telemetry). The first `OLP 1 …` line switches the dialect to
  "olp": a newline, then the current/pending levels again as `SET`, then
  `STATUS`. From then on colour → `SET`, pulse → `SHUTTER`, focus → `FOCUS`,
  shutdown → `OFF`; `STATUS` lines feed the same telemetry dict (`temp`,
  `vbus`) and the HELLO's `NAME` goes in as `telemetry["name"]`.
  `light_info()` / `dialect()` report what it found.
- The monitor's light status line shows the light's name instead of
  "Responding" when it has one (`Pico RGB · 12.1 V · 31 °C`).
- FreeCCR's internal levels stay 0–255 per channel (r, g, b, w, ir); the
  Scanlight's RGB-or-W/IR exclusivity rule (`clean_levels`) is applied to
  every light alike.

## Naming
The user, 2026-10-30: light makers and models may be named. A light names
itself (`NAME=` in its HELLO) and FreeCCR shows it. The Live Capture
Monitor's section heading and Shutter entry still read "Scanlight" (a
possible follow-up: "Light", with the connected light's name).

## Reference firmware
`examples/open_light/micropython/main.py` (Pico / ESP32 MicroPython;
`OpenLight` is board-independent and imported by the tests) and
`examples/open_light/arduino/OpenLight/OpenLight.ino` (core Arduino API plus
each core's PWM-frequency call; NOT compiled by the tests). Both MIT, so
anyone can put them in their own light.

## Tests (`tests/test_open_light_protocol.py`)
The line helpers and parser (binary bytes dropped, split lines joined); the
reference firmware alone (HELLO, SET with a channel it lacks, a bad value,
SHUTTER, FOCUS without focus, OFF, STATUS, unknown, and the Scanlight's
binary packets before HELLO ignored); FreeCCR's link driving the reference
firmware end to end through a fake port (levels set before it answered are
resent as SET, name and status reach the telemetry, SET / FOCUS / SHUTTER /
OFF); a Scanlight unchanged; the monitor naming the light.
