# Focus rail: a NEMA 17 on the camera's rail, driven from FreeCCR

Status: specified and built 2026-10-31.

The user (2026-10-31): "Can we lay the groundwork for a nema17 stepper motor?
Connected to an esp32." Answers: a **TMC2209** driver; end stop **not decided
yet**; a **separate ESP32** (not the Scanlight's controller); scope: the link,
the firmware, jog controls in the Live Capture Monitor **plus autofocus**.

His setup: a GFX 50S II on a linear rail above the Scanlight, focused by
moving the camera (lens extension fixed). The camera's 8× magnifier
(spec/live-magnifier.md) reads about one sensor pixel per live view pixel;
the sharpness meter (spec/sharpness-meter-v2.md) was built to be what this
autofocus reads.

## 1. Goals and non-goals
Goals:
- A small open text protocol, the **Open Rail Protocol (ORP) v1**, in the
  same style as the Open Light Protocol: any stepper rail can speak it.
- FreeCCR's side: a link that owns the rail's port (its own thread, like the
  light's), and a **Focus Rail** section in the Live Capture Monitor: connect,
  position, jog fine/coarse (buttons and keys), Set Zero, Home, Stop.
- **Autofocus**: a coarse-then-fine sweep reading the sharpness meter, a
  fitted peak, every reading and the final move approached from the same
  direction so the rail's play can't shift the result.
- Reference firmware for an ESP32 + TMC2209 (Arduino), homing on a switch,
  on the TMC2209's stall detection, or not at all, chosen by one setting, so
  the end-stop decision can be made later without touching FreeCCR.

Non-goals (this pass):
- FreeCCR does not set the camera's magnifier for the sweep (it tells you
  when you aren't magnified). A follow-up could go to 8× and back.
- No saved focus positions per film format or holder (GOTO makes it easy
  later).
- No closed-loop encoder; no motor release from the UI (on a vertical stand
  a released lead screw can let the camera slide; the protocol has `ENABLE`).
- The firmware is not compiled for the ESP32 here; its protocol and motion
  logic (`RailCore.h`, Arduino-free) is compiled on the host and tested.

## 2. Hardware assumptions (documented, not required)
- NEMA 17, 200 full steps/rev; TMC2209 at 1/16 microsteps (interpolated to
  256 internally) → 3200 steps/rev. Lead screw T8 × 8 mm lead → **400
  steps/mm, 2.5 µm a step** (revision 1: his rail is a 4 mm ball screw, 800
  steps/mm); a 2 mm lead → 1600 steps/mm. The firmware
  reports `STEPS_PER_MM`, FreeCCR shows millimetres from it.
- Depth of field at the user's magnifications (m ≈ 0.6–1.2, f/8, c ≈ 2
  pixels = 10.6 µm): `2·N·c·(1+m)/m²` ≈ 0.75 mm at 0.6×, 0.26 mm at 1.2×.
  Hence the defaults: fine jog 20 µm, coarse jog 200 µm; autofocus coarse
  steps 100 µm, fine steps 20 µm, range ±1 mm.
- ESP32 DevKit (CP2102 / CH340 USB bridge, or an S3 on native USB). Its
  auto-reset circuit resets the chip when RTS is asserted with DTR not, so
  the port is opened **DTR on, RTS off** (no reset; native USB needs DTR on to
  send at all). A board that reset anyway (boot chatter) answers a later
  HELLO: the link repeats HELLO while waiting.

## 3. The protocol (public doc: docs/open-rail-protocol.md)
Transport as OLP: USB serial, 115200 8N1, ASCII lines ending `\n`, a `\r`
fine, case-insensitive commands and keys, bytes outside printable ASCII and
empty lines ignored. Every command gets **one immediate reply line**
(`OK`, `ERR …`, or the HELLO/STATUS answer). Two kinds of line can also come
unprompted, between replies: `DONE …` (a move finished) and `STATUS …`.

| Command | Reply | Meaning |
|---|---|---|
| `HELLO` | `ORP 1 STEPS_PER_MM=400 MIN=0 MAX=40000 HOME=SWITCH NAME=…` | Who are you? |
| `STATUS` | `STATUS POS=1234 MOVING=0 HOMED=1 ENABLED=1` | Where are you? |
| `GOTO <pos> [ID=n]` | `OK`; later `DONE ID=n POS=…` | Move to a position (steps). |
| `MOVE <steps> [ID=n]` | `OK`; later `DONE ID=n POS=…` | Move by steps from the current TARGET (so quick jogs add up). |
| `STOP` | `OK` | Decelerate to a stop; the move in progress ends `DONE … STOPPED=1`. |
| `ZERO` | `OK` / `ERR moving` | This position is 0. Clears HOMED (limits are relative to a home). |
| `HOME [ID=n]` | `OK` / `ERR no home`; later `DONE ID=n POS=…` | Find zero (switch or stall), back off a little, HOMED=1. A failure ends `DONE ID=n POS=… FAIL=<why>`. |
| `SPEED <steps/s> [ACCEL=<steps/s²>]` | `OK` | Move speed (clamped by the firmware). |
| `ENABLE 0` / `ENABLE 1` | `OK` | Motor current off / on. Off clears HOMED. |

HELLO fields: `STEPS_PER_MM` (0 or absent = unknown), `MIN`/`MAX` (soft
limits in steps, enforced only while HOMED=1), `HOME=SWITCH|STALL|NONE`,
`NAME=` last (rest of the line). DONE fields: `ID` (echoed when the move had
one), `POS`, and flags `STOPPED=1` (by STOP), `LIMIT=1` (the target was
clamped to a soft limit), `FAIL=` last (rest of the line). A new GOTO/MOVE
while moving **retargets** (no DONE for the old one); HOME while homing →
`ERR homing`; GOTO/MOVE while homing → `ERR homing`. STATUS lines come every
200 ms while moving and once after it stops.

Refinements (second pass over this spec):
- After HOME the position is the back-off distance (0 is the switch or stall
  point itself, so a held switch never sits at a limit); `MIN` defaults to 0.
- STOP during HOME aborts it: `DONE … STOPPED=1`, HOMED stays 0.
- MOVE is relative to the target while moving and to the position when
  stopped (they are equal then); a clamped target sets `LIMIT=1` on its DONE.
- A rail that doesn't report `STEPS_PER_MM` is shown in steps; the
  autofocus and jog steps (in µm) assume 400 steps/mm for it, and the status
  line says so.
- Autofocus refuses to start while the rail is moving or not connected, and
  starts from the last reported position.
- Positions show as millimetres to 3 decimals (1 µm), jog labels to the
  precision of the step ("+0.02", "+0.2", "+0.005").

## 4. FreeCCR code
- `core/orp.py` (Qt-free): line builders (`goto_line`, `move_line`, …),
  `HELLO` (`"\nHELLO\n"`, as OLP), `parse_reply` → hello / status / done /
  ok / err / light (an `OLP 1` answer: that port is a light), and
  `rank_port(device, vid)` (ESP32 USB bridges first: Espressif 0x303A,
  Silicon Labs 0x10C4, WCH 0x1A86, FTDI 0x0403; the Scanlight's RP2040 last
  among USB) and `list_ports()`.
- `core/rail_link.py`: `RailLink` (QObject on its own thread; injectable
  serial factory, port lister, clock, sleep — the `LightLink` pattern) and
  `RailClient` (the GUI's handle; queued signals; `threaded=False` in tests).
  States: unavailable / off / error / waiting / ok / silent / lost. While
  waiting it repeats HELLO every 0.6 s; after 2.5 s silent it reopens once
  with DTR off, then "silent". An `OLP 1` answer → "error", "That port is a
  light, not a rail", closed. Opens with `exclusive=True` where pyserial
  supports it. A `blocked` port (the light's) is never opened.
  Signals: `state_changed(state, port, detail)`, `info_changed(dict)`
  (`steps_per_mm`, `min`, `max`, `home`, `name`), `status_changed(dict)`
  (`pos`, `moving`, `homed`, `enabled`), `move_done(int id, int pos, dict
  flags)`, `rail_error(str)`. `RailClient.next_id()` numbers moves.
- `core/rail_autofocus.py` (Qt-free): `Autofocus` (§6). Options in µm/ms:
  `range_um` 1000 (200–5000), `coarse_um` 100 (20–500), `fine_um` 20
  (5–100), `backlash_um` 100 (0–500), `settle_ms` 300 (50–2000),
  `readings` 3 (1–9). Converted to steps with the rail's `STEPS_PER_MM`
  (400 assumed when the rail doesn't say).
- `core/live_feed.FrameResult.reading` (appended LAST): the fresh,
  unsteadied sharpness reading made on this frame (None when this frame was
  not measured). The sweep must not read the meter's 15-reading median, which
  lags half a second behind a move.

## 5. The monitor (Focus Rail section)
A `CollapsibleSection` "Focus Rail" between Capture and Negative Preview,
open state remembered (`live/rail_open`, default closed).
- Row: ● dot, port combo (rail-ranked; the light's open port left out, and
  the light's combo leaves out the rail's open port), Rescan, Connect. The
  port is remembered (`live/rail_port`) and reconnected on opening, like the
  light.
- Status: the rail's name, position, "homed" / "zero set here" / "position
  not set", or the link's state.
- Jog: `−0.20  −0.02  [12.345 mm]  +0.02  +0.20` (labels from the jog
  steps, in mm, `live/rail_jog` {fine_um 20, coarse_um 200}, right-click a
  jog button for them). Keys (monitor only): `[` / `]` fine, `{` / `}`
  coarse. A press is a `MOVE` (relative to the target, so presses add up).
- Buttons: **Set Zero**, **Home** (disabled for `HOME=NONE`), **Stop**,
  **Autofocus** (reads "Focusing…" while running, and a click stops it; right-click → the
  options popup, `live/rail_af`). Esc stops the rail and the sweep.
- A caption under them shows the sweep's progress and result.
- Refused (caption says why): jog / Home / Autofocus during a Shoot sequence
  or film-base measuring, and Shoot / Measure while the rail moves or the
  sweep runs (a moving camera blurs the shot). While asleep (idle) the keys
  do nothing, as the other keys.
- Autofocus needs live view frames and turns the Sharpness meter on if it
  was off. It reads the meter's area (a drawn area, else the whole view).
  Not magnified with a camera that has a magnifier → the caption says
  "Reads best at Camera zoom 8× (Z)" and it runs anyway. A camera zoom
  change during the sweep stops it (the picture's scale changed). The
  new-frame note is quiet during the sweep.
- Closing the monitor stops a sweep, sends STOP if moving, closes the port
  (the motor stays energised: the rail holds the camera).

## 6. Autofocus
A sequence of moves and measurements, written as a generator (`_plan`) the
engine drives: `("goto", pos)` waits for that move's DONE; `("measure",)`
waits `settle_ms` after the move, then takes the median of the next
`readings` readings.
1. **Direction.** Every measured position and the final one is approached
   moving **up** (increasing steps): going down to a target means going to
   `target − backlash` first, then up. So the lead screw's play is always
   taken up the same way.
2. **Coarse.** From `p0 − range` to `p0 + range` in `coarse` steps (clamped
   to the soft limits when homed). Stop early once past the peak: at least 3
   readings, the best is not among the last two, and both of the last two
   are under 0.8 × the best.
3. **Peak at an end.** If the best coarse reading is the first (or the last,
   with no early stop), extend once that way by another `range`. Still at an
   end → fail "No focus peak within ±R mm".
4. **Flat.** Best / worst < 1.15 over the coarse readings → fail "Focus
   barely changed: nothing sharp in the meter's area, or the range is too
   small".
5. **Fine.** From `pc − coarse` to `pc + coarse` in `fine` steps around the
   coarse best `pc` (approached from below), same early stop.
6. **Fit.** Around the best fine reading (±2 points), least-squares
   parabola on log(score) (a Gaussian peak); if it opens downward its vertex,
   clamped to the neighbouring readings, else the best reading. Rounded to a
   step.
7. **Final.** Approach the peak from below, read once more. Under 0.9 × the
   fine best → done with a warning ("the rail may have more play than the
   Backlash setting"); else "Focused at X mm".
- Fail → go back to where it started (approached from below), say why.
- Timeouts: a move 20 s (`MOVE_TIMEOUT_S`), readings 3 s after settling
  (`READ_TIMEOUT_S`: "No sharpness readings: is live view running?").
- Stop (button / Esc) → the rail's STOP, no return move.
Typical run with the defaults: ~10–21 coarse + ~11 fine positions at
~0.5–0.7 s each, 15–25 s.

## 7. Reference firmware (examples/focus_rail/)
- `arduino/FocusRail/RailCore.h`: the protocol and motion logic, plain C++
  (no Arduino headers), over a `Motor` interface (moveTo, stop, position,
  setPosition, running, setSpeed/Accel, enable, runToward(dir), homeSensed()).
  Handles lines, IDs, retargets, soft limits (HOMED only), HOME as a state
  machine (fast toward the end → back off → slow in → zero → HOMED), DONE and
  STATUS emission.
- `arduino/FocusRail/FocusRail.ino`: ESP32 + TMC2209 with **FastAccelStepper**
  (hardware-timed steps and ramps) and **TMCStepper** (UART: current, 1/16
  microsteps, StealthChop, StallGuard threshold). `HOME_MODE` = `HOME_SWITCH`
  (a switch to GND on a pull-up pin), `HOME_STALL` (the TMC2209's DIAG pin;
  StealthChop only, needs tuning `STALL_THRESHOLD`) or `HOME_NONE`. Pins
  chosen off the ESP32's strapping pins. MIT.
- `host/rail_host.cpp`: compiles `RailCore.h` on a computer with a simulated
  motor (steps per simulated ms) and a simulated switch; lines on stdin, a
  harness-only `#tick <ms>` advances time. FreeCCR's tests run it.

## 8. Tests
- `tests/test_focus_rail.py`: orp lines and parsing (fields, NAME/FAIL rest
  of line, an OLP answer seen as a light); port ranking; the link against a
  Python fake rail (HELLO repeated until answered, ok, info, GOTO → DONE with
  its id, status, ERR, a light on the port, silent → DTR off → silent, a
  blocked port refused, unplug → lost); the autofocus engine on a simulated
  rail with a Gaussian focus curve, 2 % noise and 50 µm of play (peak found
  within half a fine step from below, above and near the range's ends; every
  measured position and the final one approached upward; flat → fail and
  return; peak beyond the range → fail; stop; a reading timeout); the monitor
  (jog buttons and keys send MOVEs, the light's port left out of the rail's
  list, Autofocus runs on fed readings and reports, Shoot refused while it
  runs, Esc stops).
- `tests/test_focus_rail_firmware.py` (skipped without a C++ compiler): the
  host build answers HELLO/STATUS, ignores binary bytes, GOTO with an id ends
  in DONE at the target, MOVE adds to the target, a retarget, STOP mid-move,
  ZERO, soft limits only when homed, HOME with the simulated switch, HOME
  NONE refused, unknown → ERR; and FreeCCR's link driving the host build end
  to end.

## Revision 1 (2026-11-01): his rail is a ball screw, focus within a quarter turn
The user: "the macro lens is a 120mm Schneider lens on my GFX body. The Focus
is likely to be within a 90 degree turn of the ballscrew at absolute most.
There is no focus ring."
- No focus ring: the rail is the only focus (as §2 assumed: extension fixed,
  the whole camera moves). Nothing to add for a lens motor.
- A **ball screw**, not a lead screw. The firmware's `LEAD_MM` default is now
  **4** (SFU1204, the usual small ball screw; the comment lists SFU1605 = 5,
  SFU1610 = 10, read off the screw's code): 1/16 microsteps → 3200 steps a
  turn, **800 steps/mm, 1.25 µm a step**, a quarter turn = 800 steps = 1 mm.
  Confirmed by the user the same day: "SFU1204 ballscrew, full length is
  300mm". `SCREW_LENGTH_MM` 300 is now HOME's give-up distance (it was
  TRAVEL + 10 mm, which an underestimated TRAVEL could make too short to
  reach home from the far end), and `TRAVEL_MM`, the soft limit once homed,
  is 200: roughly the stroke a 300 mm SFU1204 leaves after its end blocks
  and nut (an estimate; the comment says to measure it). Neither matters
  until an end stop is fitted (`HOME_MODE` none: no HOME, no limits).
- The rail reports **`STEPS_PER_REV`** (optional HELLO field;
  `RailCore::Config::stepsPerRev`; `orp.parse_reply` → `steps_per_rev`,
  `RailClient.steps_per_rev()`), so FreeCCR shows distances in his terms:
  the status line says "4 mm a turn" (`orp.lead_mm`), the jog tooltips give
  each step as an angle of the screw ("0.02 mm, 1.8° of the screw";
  `orp.turn_text`), and the Autofocus options show the Range as "±1.00 mm ·
  90°" (its value column widened to 104 px when the angle is known;
  `OptionsPopup(value_w=)`).
- Autofocus defaults unchanged: ±1 mm is ±90° on a 4 mm lead, which covers
  "within a 90 degree turn at absolute most" from anywhere in that zone
  (±72° on a 5 mm lead, ±36° on 10 mm: set Range to a quarter turn there).
  The DoF (§2) is 0.26–0.75 mm, so the 100 µm coarse step is 9° of a 4 mm
  screw and the 20 µm fine step 1.8°.
- A ball screw back-drives: on a vertical stand the camera slides when the
  motor has no current (supply off, the ESP32 restarting, `ENABLE 0`).
  FreeCCR never sends `ENABLE 0`; the firmware header says to keep the motor
  supply on and fit a stop collar below the focus range. Holding about 2 kg
  on a 4–5 mm lead is roughly 15 mN·m (`F·lead / 2π·η`, η ≈ 0.9), a small
  share of a NEMA 17's torque at the hold current; ~2.5× that on a 10 mm
  lead (raise `HOLD_FRACTION`). An estimate, not measured.
- Backlash stays 100 µm: a ball screw has little play, but the approach from
  below also takes up the coupling's wind-up, and on a vertical stand
  gravity already loads the nut one way; it costs one short move per pass.
- Tests: `STEPS_PER_REV` parsed and in the host build's HELLO; `turn_text`
  / `lead_mm` (20 µm on a 4 mm lead = 1.8°, 1 mm = 90°); the monitor's status
  line and jog tooltip with a rail that reports it.
