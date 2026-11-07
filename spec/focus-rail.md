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

## Revision 2 (2026-11-07): autofocus before each shot, his belt, an ESP32-S3
The user: "start to build the architecture of autofocus … I need to get the
esp32 up and running … a 'linear rail autofocus' box is checked, the software
punches into 8x or max, moves the motor to find optimum sharpness, captures
(in whatever mode its in). Currently, the nema 17 I have is attached to a 30t
pulley. The linear rail has a 20t pulley. We're talking very very slow
speeds, incredibly fine degrees of turn to focus." Answers: still the SFU1204
(4 mm a turn); an **ESP32-S3-N16R8**; a NEMA 17 **pancake** motor; a 12 V 2 A
supply. On the pulleys: he fitted 30 T on the motor and 20 T on the screw
"to give very fine control … a little more torque might not be a bad thing";
a belt does the reverse (see A), and once told he chose to **swap them: 20 T
on the motor, 30 T on the screw**. The firmware's defaults are the swapped
pair; they must match what is fitted.

Most of the architecture is already here (§3-§7): the protocol, the link,
the Focus Rail section, the sweep and the firmware. This revision adds:

### A. The belt (firmware and protocol)
- `FocusRail.ino`: `MOTOR_PULLEY_TEETH` 20, `SCREW_PULLEY_TEETH` 30 (1 and 1
  for a coupler). Motor turns per screw turn = screw teeth / motor teeth, so
  steps per screw turn = `FULL_STEPS_PER_REV · MICROSTEPS · SCREW / MOTOR` =
  200 · 16 · 30/20 = **4800**, and **1200 steps/mm, 0.83 µm a step, 0.075° of
  the screw a step**. Depth of field is 0.26-0.75 mm (§2): a step is about
  1/300 to 1/900 of it, so 1/16 microsteps (interpolated to 256 inside the
  TMC2209) stay. The same arithmetic lives in `RailCore.h` as
  `orp::screwStepsPerTurn(full, micro, motorTeeth, screwTeeth)` so the host
  build tests it. (As first fitted, 30 → 20: 2133⅓ a turn, 533⅓ a mm, 1.9 µm.)
- 20 T → 30 T is a reduction: the screw turns 2/3 per motor turn and gets
  1.5× the motor's torque. Holding a ~2 kg camera on the 4 mm screw takes
  ~14 mN·m at the screw (rev. 1), ~9 mN·m at the motor; a pancake NEMA 17
  (~0.13 N·m held at ~1 A) at half of 0.6 A is several times that, so
  `HOLD_FRACTION` stays 0.5 (the comment says to raise it if the camera
  creeps).
- `STEPS_PER_REV` can be **fractional** (2133.33 with the first pulleys; any
  belt ratio that isn't a whole number): `Config::stepsPerRev`
  is a float, HELLO prints it with `%g` (as STEPS_PER_MM), the public doc says
  a host must accept a decimal, `orp.parse_reply` reads a float and
  `RailClient.steps_per_rev()` returns one. A whole number prints as before
  ("3200"), so older rails and tests read the same.
- **Slow by default**: moves 2 mm/s (half a screw turn a second; was 8),
  acceleration 10 mm/s² (was 50), at most 10 mm/s; HOME 2 then 0.25 mm/s. A
  20 µm autofocus step (24 steps) takes ~0.1 s at these.
- `rail_host` takes `--belt <motor teeth> <screw teeth> --lead <mm>
  --microsteps <n>` and fills `STEPS_PER_MM` / `STEPS_PER_REV` through the
  same helper, so a test reads 1200 / 4800 (and 533.333 / 2133.33 for the
  first pulleys) and "4 mm a turn" end to end.

### B. The ESP32-S3 (firmware)
- Pins by target (`CONFIG_IDF_TARGET_ESP32S3`): STEP 4, DIR 5, EN 6, the
  TMC2209's UART on 17 (TX) / 18 (RX), DIAG 7, switch 15. All on the
  DevKitC-1's headers and clear of what the N16R8 module uses: 26-32 (flash),
  35-37 (its octal PSRAM), the strapping pins 0, 3, 45, 46, USB 19/20, UART0
  43/44 and the RGB LED (38 or 48). A classic ESP32 keeps §7's pins.
- The TMC2209's UART is `Serial1` (UART1 exists on every ESP32; `Serial2`
  doesn't on all of them).
- **The driver is set up whenever it starts answering.** A TMC2209 answers
  on its UART only while VM (the 12 V) is on and forgets its settings when VM
  goes; without them it microsteps by its MS1/MS2 pins (1/8 with both to GND:
  every distance halved) at the VREF current. The sketch configured it once
  in `setup()`, so 12 V switched on after the ESP32 left it unconfigured.
  `superviseDriver` (once a second from `loop()`) checks it answers and writes
  the settings when it starts answering or its GSTAT reset flag is set
  (cleared after writing); `DRIVER=` in STATUS follows it. So the power-up
  order doesn't matter, and the guide says so.
- FreeCCR talks to `Serial`: on the DevKitC-1 that is the port marked
  **UART/COM** (its USB bridge) with *USB CDC On Boot: Disabled*, or the port
  marked **USB** with it *Enabled*. Both are ranked first in the rail's list
  (Espressif 0x303A, Silicon Labs, WCH).
- Not compiled for the ESP32 here (the toolchain downloads are blocked from
  this machine): the first compile is on the user's Mac. `RailCore.h` and the
  new helper are compiled and tested on the host as before.

### C. Autofocus before each shot (FreeCCR)
A row under the Focus Rail buttons: **Autofocus before each shot** (checkbox,
`live/rail_af_shoot`, default off) and a list **8× / Max / No zoom**
(`live/rail_af_zoom`, the camera zoom levels "8" / "16" / "", default "8";
disabled when the camera has no magnifier). With it ticked, Shoot does:
1. **Refusals first** (caption says why, nothing moves): the rail not
   connected ("…untick the box to shoot without it"), no live view in the
   last 2 s, and everything Shoot already refuses.
2. **The shot is prepared now, at the whole frame**: the scan type, the USB
   checks (trichrome needs the Scanlight, the hot folder is turned on), the
   exposures (`_shoot_exposures`: an armed Use for This Frame, or this
   frame's suggestion, measured on the whole frame) and the frame guide's
   record (`_shot_guide_record`, which needs the whole picture). Shoot used to
   do these as it started the sequence; they now sit in `_prepare_shot()`
   and `_start_shot(prepared)`, and plain Shoot calls both at once, so it is
   unchanged.
3. **Magnify**: to the chosen level when the camera has a magnifier and isn't
   at it already (`_set_cam_zoom`); wait for the camera to report it, then
   for a picture at least `AF_SHOT_SETTLE_S` (1.0 s) after the report (frames
   already on their way are at the old scale); give up after
   `AF_SHOT_ZOOM_S` (10 s): "The camera didn't magnify".
4. **Focus**: the existing sweep (`autofocus(then=…)`; it now returns
   whether it started and calls `then(result)` when it ends).
5. **Back to Whole** (when not at Whole), wait for the camera to report it
   (`SHOOT_UNZOOM_S`, as Shoot already does), then **shoot** with the
   prepared values: single, trichrome, film-base or frame exposures, as set
   up.
- **Fails** (no peak, flat, no readings, the camera didn't magnify): the
  sweep already goes back to where it started; the camera goes back to the
  zoom it had; **nothing is shot**; the capture caption says why and
  "Nothing was shot". (A failed focus on someone's film is worse than a
  missed frame.)
- **Cancel**: Shoot again (it reads "Stop (Esc)" meanwhile) or Esc: the sweep
  and the rail stop, the camera goes back to the zoom it had, nothing is shot.
- It focuses under the light **as it is** (white light, or whatever the
  Scanlight shows); trichrome then shoots its three colours at that focus.
  Focusing under green for trichrome is a later option if colour shifts
  focus.
- Measure, Meter and the jog keys are refused meanwhile, as during a sweep
  (`_rail_busy` counts the pre-shot autofocus); closing the monitor drops it.
- The state is one dict, `_af_shot` {stage zoom/focus/whole, want, back,
  zoom_at, prepared}, so a late zoom report or sweep result after a cancel is
  ignored.

Refinements (second pass):
- Already at the chosen level (or "No zoom", or no magnifier): straight to
  the sweep, no wait. The tests set `AF_SHOT_SETTLE_S` to 0 and feed a
  picture after the zoom report.
- A zoom change during the sweep already stops it ("the camera zoom
  changed"); in a pre-shot sweep that is a failure: nothing shot.
- After a good sweep at Whole already (no magnifier): shoot at once.
- `_rail_can_move` refuses jogs, Set Zero and Home while `_af_shot` is set,
  in every stage (the zoom stage has no sweep running yet).
- `_refresh_shoot_enabled`: Shoot is enabled and reads "Stop   (Esc)" while
  `_af_shot` is set (a sweep alone still disables it, as before).
- Plain Autofocus (the button) is unchanged: no zoom, no shot.

### D. A bring-up guide (`examples/focus_rail/README.md`)
For his parts: ESP32-S3-N16R8 DevKitC-1, a TMC2209 module, a pancake NEMA 17,
the 12 V 2 A supply with its screw-terminal plug. Wiring table, the
capacitor, powering the ESP32 from the Mac's USB (not the 12 V), the order to
connect things, Arduino IDE (the esp32 board package, *ESP32S3 Dev Module*,
the two libraries), the settings at the top of the sketch, a first test in
the Serial Monitor (HELLO, STATUS with DRIVER=1, MOVE 533 = 1 mm, which way
"up" goes, INVERT_DIR), then FreeCCR (Connect, jog, Set Zero, Autofocus at
8×, the new box). Troubleshooting: no port (a charge-only cable), upload
(BOOT + RESET), DRIVER=0 (the UART wiring), a motor that hums (coil pairs), a
hot motor (current), a camera that slides with the power off (ball screw).

### Tests
- `orp`: a fractional STEPS_PER_REV parsed; "4 mm a turn" and 1.8° per 20 µm
  from 533.333 / 2133.33 and from 1200 / 4800.
- Firmware (host build): `--belt 20 30 --lead 4 --microsteps 16` → HELLO
  `STEPS_PER_MM=1200 STEPS_PER_REV=4800`, `--belt 30 20` → `533.333` /
  `2133.33`; the link reads them; a whole number still prints "3200".
- Monitor: with the box ticked and no magnifier, Shoot focuses then starts
  the sequence; with a magnifier, the order is zoom 8× → (a picture) → sweep →
  Whole → sequence, with the exposures and guide prepared before the zoom; a
  failed sweep shoots nothing and goes back to Whole; Shoot again cancels
  (STOP, back to Whole, no shot); the rail not connected refuses; the box and
  level are remembered; Measure is refused meanwhile; the panel still fits.
