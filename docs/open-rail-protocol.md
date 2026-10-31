# Open Rail Protocol (ORP) v1

A plain-text protocol a motorised focusing rail (or any single-axis stepper
stage) can speak over a USB serial port, so FreeCCR (and anything else) can
move it, home it and autofocus with it. It follows the
[Open Light Protocol](open-light-protocol.md)'s line rules. It is free to
implement; the reference firmware in `examples/focus_rail/` is MIT licensed.

## The connection
- USB serial (a USB CDC device, or a USB–UART bridge at **115200 baud, 8N1**).
- ASCII, one message per line, ending in `\n` (a `\r` before it is fine).
- Commands are case-insensitive; keys (`ID=`, `POS=` …) too.
- The rail **ignores every byte outside printable ASCII** (0x20–0x7E) other
  than `\n` and `\r`, and **ignores empty lines**, so a host probing ports
  for several kinds of device can't confuse it.
- Every command gets **one immediate reply line**: `OK`, `ERR <why>`, or the
  answer to `HELLO` / `STATUS`. Two kinds of line can also arrive on their
  own, between replies: `DONE …` (a move finished) and `STATUS …`.
- Positions are whole **steps**, signed 32-bit. Position 0 is the home end
  (or wherever `ZERO` was sent); positions grow away from it.

## Commands (host → rail)

| Command | Reply | Meaning |
|---|---|---|
| `HELLO` | `ORP 1 STEPS_PER_MM=400 MIN=0 MAX=40000 HOME=SWITCH NAME=My rail` | Who are you? |
| `STATUS` | `STATUS POS=1234 MOVING=0 HOMED=1 ENABLED=1` | Where are you? |
| `GOTO 1500 ID=7` | `OK`, later `DONE ID=7 POS=1500` | Move to a position. `ID=` is optional and echoed on the DONE. |
| `MOVE -200 ID=8` | `OK`, later `DONE ID=8 POS=1300` | Move by this many steps from the current move's **target** (from the position when stopped), so quick jog presses add up. |
| `STOP` | `OK` | Decelerate to a stop. The move in progress ends `DONE … STOPPED=1`. |
| `ZERO` | `OK` or `ERR moving` | This position is now 0. Clears `HOMED`. |
| `HOME ID=9` | `OK` or `ERR no home`, later `DONE ID=9 POS=200` | Find the home end (a switch, or a stall), call it 0, back off a little, `HOMED=1`. A failure ends `DONE ID=9 POS=… FAIL=<why>`. |
| `SPEED 3200 ACCEL=20000` | `OK` | Move speed in steps/s (and acceleration in steps/s², optional). The rail may clamp them. |
| `ENABLE 0` / `ENABLE 1` | `OK` | Motor current off / on. Off clears `HOMED` (the rail can be turned by hand). |

Anything else: `ERR <what was wrong>`. While homing, `GOTO`, `MOVE` and
`HOME` answer `ERR homing` (`STOP` aborts the homing).

A `GOTO` or `MOVE` sent while a move is running **retargets** it: no `DONE`
comes for the old one, only for the last.

### The HELLO reply
`ORP 1` (the protocol version), then `KEY=VALUE` fields in any order:
- `STEPS_PER_MM=` steps per millimetre of travel (decimals allowed; 0 or
  absent: unknown, and hosts show steps).
- `MIN=` / `MAX=` soft limits in steps, applied only while `HOMED=1`: a
  target beyond one is clamped and its `DONE` carries `LIMIT=1`.
- `HOME=SWITCH`, `HOME=STALL` (the driver's stall detection, no switch) or
  `HOME=NONE` (no `HOME`; use `ZERO`).
- `NAME=` last: the rail's name as people should see it, spaces allowed (the
  rest of the line).

### STATUS
`STATUS` then `POS=`, `MOVING=0|1`, `HOMED=0|1`, `ENABLED=0|1`; optional
`DRIVER=0|1` (0: the motor driver doesn't answer — check its wiring) and any
others, which hosts ignore. The rail sends `STATUS` lines on its own about
every 200 ms while moving, and one after each `DONE`.

### DONE
`DONE` then `ID=` (if the move had one), `POS=`, and flags `STOPPED=1` (by
`STOP`), `LIMIT=1` (the target was clamped) and `FAIL=<why>` (last, the rest
of the line).

## Example session
```
> HELLO
< ORP 1 STEPS_PER_MM=400 MIN=0 MAX=40000 HOME=SWITCH NAME=Bench focus rail
> HOME ID=1
< OK
< STATUS POS=-3120 MOVING=1 HOMED=0 ENABLED=1
< DONE ID=1 POS=200
< STATUS POS=200 MOVING=0 HOMED=1 ENABLED=1
> GOTO 4800 ID=2
< OK
< DONE ID=2 POS=4800
< STATUS POS=4800 MOVING=0 HOMED=1 ENABLED=1
> MOVE 8 ID=3
< OK
< DONE ID=3 POS=4808
< STATUS POS=4808 MOVING=0 HOMED=1 ENABLED=1
```

## Building a rail
- A NEMA 17 on a lead screw, through a TMC2209 driver in StealthChop, is
  quiet and fine: with 1/16 microsteps and an 8 mm lead, 400 steps/mm
  (2.5 µm a step); a 2 mm lead gives 1600 steps/mm.
- An ESP32 dev board's USB bridge resets the chip when RTS is asserted
  without DTR. Hosts should open the port **DTR on, RTS off** (FreeCCR does);
  a board that resets anyway answers a later `HELLO`, so hosts repeat it for
  a few seconds.
- A lead screw has play: approach every position from the same side (FreeCCR's
  autofocus does) or compensate in firmware.
- On a vertical stand, keep the motor enabled: a fast lead screw can be
  back-driven by the camera's weight.
- Reference firmware: `examples/focus_rail/arduino/FocusRail/` (ESP32 +
  TMC2209, homing by switch, by stall or none). Its protocol and motion logic,
  `RailCore.h`, has no Arduino headers and is compiled and tested on a
  computer by FreeCCR's test suite (`examples/focus_rail/host/`).

## In FreeCCR
File → Live Capture Monitor → Focus Rail: pick the rail's port and Connect.
FreeCCR sends `HELLO` (repeating it for a few seconds) and drives a rail
that answers `ORP 1 …`: jog buttons and keys send `MOVE`, Set Zero `ZERO`,
Home `HOME`, Stop and Esc `STOP`, and Autofocus sweeps with `GOTO`, reading
the live view's sharpness at each position.
