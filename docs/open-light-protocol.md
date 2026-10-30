# Open Light Protocol (OLP) v1

A plain-text protocol any film-scanning light can speak over a USB serial
port, so FreeCCR (and anything else) can set its colours, fire the camera
through it and read its status. Use it for a homemade light (an Arduino,
ESP32 or Raspberry Pi Pico board driving LEDs) or in a commercial light's
firmware. It is free to implement; the reference firmware in
`examples/open_light/` is MIT licensed.

## The connection
- USB serial (a USB CDC device, or a USB–UART bridge at **115200 baud, 8N1**).
- ASCII, one message per line, ending in `\n` (a `\r` before it is fine).
- Commands are case-insensitive; keys (`R=`, `TEMP=` …) too.
- A light **ignores every byte outside printable ASCII** (0x20–0x7E) other
  than `\n` and `\r`, and **ignores empty lines**. Hosts probing a port for
  several kinds of light may send other protocols' bytes first; FreeCCR
  sends a newline before its first text command so any such leftovers end up
  on a line of their own, which the light answers with `ERR` and forgets.
- Every command gets exactly one reply line, in order.

## Commands (host → light)

| Command | Reply | Meaning |
|---|---|---|
| `HELLO` | `OLP 1 CH=R,G,B,W,IR SHUTTER=1 FOCUS=1 NAME=My light` | Who are you? |
| `SET R=200 G=0 B=0 W=0 IR=0` | `OK` | Set channels, 0–255 each. Channels not named stay as they are; channels the light hasn't got are ignored. |
| `OFF` | `OK` | All channels to 0. |
| `SHUTTER 300` | `OK` or `ERR no shutter` | Close the camera's release contacts for this many ms (1–10000). |
| `FOCUS 1` / `FOCUS 0` | `OK` or `ERR no focus` | Hold / let go the half-press contacts. |
| `STATUS` | `STATUS TEMP=31.5 VBUS=12.1` | Any measurements the light has. |

Anything else: `ERR <what was wrong>`.

### The HELLO reply
`OLP 1` (the protocol version), then `KEY=VALUE` fields in any order:
- `CH=` the channels it has, from `R`, `G`, `B`, `W` (white), `IR`
  (infrared), comma-separated.
- `SHUTTER=1` if it can fire a camera, `FOCUS=1` if it can hold focus.
- `NAME=` last: the light's name as people should see it, spaces allowed
  (the rest of the line). Name the model, e.g. `NAME=Acme RGB Panel 2`.

### STATUS
`STATUS` followed by any `KEY=VALUE` fields. FreeCCR shows `TEMP=` (°C, the
LEDs or the board) and `VBUS=` (supply volts); others are allowed and
ignored. A light may also send `STATUS …` lines on its own, e.g. every few
seconds.

## Example session
```
> HELLO
< OLP 1 CH=R,G,B,W SHUTTER=1 FOCUS=0 NAME=Bench RGB light
> SET R=0 G=0 B=0 W=180 IR=0
< OK
> SET R=220 G=0 B=0 W=0 IR=0
< OK
> SHUTTER 300
< OK
> STATUS
< STATUS TEMP=34.2
> OFF
< OK
```

## Building a light
- Drive the LEDs through a MOSFET or a constant-current driver's dim input,
  never straight from a pin.
- Run the PWM fast (≥ 20 kHz). At the few hundred Hz many boards default
  to, a fast shutter speed catches the LEDs part-way through a cycle and the
  frames come out uneven. Constant-current dimming avoids it altogether.
- Fire the camera through an optocoupler across its remote-release
  contacts, so the camera is isolated from the light's supply.
- For trichrome scanning, narrowband red, green and blue LEDs (roughly
  450 nm, 525 nm and 660 nm) suit colour negative film.
- Reference firmware: `examples/open_light/micropython/main.py` (tested with
  FreeCCR's own test suite) and `examples/open_light/arduino/OpenLight/`.

## In FreeCCR
File → Live Capture Monitor → Scanlight section: pick the light's port and
Connect. FreeCCR asks `HELLO` on every port it opens; a light that answers
`OLP 1 …` is driven in text from then on (one that speaks the Scanlight's
binary protocol keeps that), shows its name and status, and works with the
presets, solo keys, trichrome sequence, idle light-off and the Scanlight
shutter mode exactly as the Scanlight does.
