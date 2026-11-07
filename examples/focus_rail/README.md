# Focus rail: getting the ESP32 running

This gets the focus rail moving from FreeCCR's Capture window: an ESP32-S3
drives the NEMA 17 through a TMC2209. FreeCCR talks to it over USB with the
Open Rail Protocol (`docs/open-rail-protocol.md`). The sketch is in
`arduino/FocusRail/`: `FocusRail.ino` and `RailCore.h`, kept in the same folder.

Written for this setup:
- an ESP32-S3-N16R8 DevKitC-1;
- a TMC2209 driver module;
- a pancake NEMA 17 on a belt: 20 teeth on the motor, 30 on the screw;
- an SFU1204 ball screw (4 mm a turn);
- a 12 V 2 A supply with a screw-terminal plug.

With that belt and screw, one step is 0.83 µm and 1200 steps make a millimetre.

You also need:
- a 100 µF electrolytic capacitor rated 25 V or more;
- a 1 kΩ resistor;
- jumper wires;
- a USB-C cable that carries data, not one for charging only.

## 1. Wiring

Do all of it with nothing plugged in.

**ESP32-S3 to the TMC2209 (the logic side):**

| ESP32-S3 | TMC2209 | What it is |
|---|---|---|
| GPIO 4 | STEP | one pulse = one step |
| GPIO 5 | DIR | which way |
| GPIO 6 | EN | motor on when low (the sketch drives it) |
| GPIO 17 → **1 kΩ** → | PDN_UART | the ESP32 sends settings |
| GPIO 18 (no resistor) | PDN_UART (the same pin) | the driver answers |
| 3V3 | VIO (some boards say VDD) | the driver's logic supply |
| GND | GND (logic side) | shared ground |
| GND | MS1 and MS2 | driver address 0 |
| GPIO 7 | DIAG | only for stall homing later; leave it off for now |

Both GPIO 17 (through the resistor) and GPIO 18 go to the same PDN_UART pin.
Some boards call it PDN or UART. On a few modules the UART has to be switched
on with a solder jumper: the seller's page says if yours does.

**The 12 V supply to the TMC2209 (the motor side):**
- **+** to VM, **−** to GND. The screw-terminal plug's + and − are marked.
- The 100 µF capacitor goes across VM and GND, as close to the driver as you
  can get it. The side with the stripe goes to GND.
- Never connect the 12 V to the ESP32. The ESP32 is powered by USB from the
  Mac, which is also how FreeCCR talks to it.

**The motor to the TMC2209:** the four wires are two coils. With a multimeter,
a pair from the same coil reads a few ohms and wires from different coils read
nothing. One coil goes to the 1A/1B pins (A1/A2 on some boards), the other to
2A/2B (B1/B2). The direction doesn't matter here: it's set in software later.

**Never plug or unplug the motor while the 12 V is on.** It destroys the
driver.

## 2. Power

- USB first or 12 V first: either order is fine. The TMC2209 only answers
  once it has 12 V, and the sketch checks every second and sets it up when it
  does (and again if the 12 V goes off and comes back).
- Keep the 12 V on while the camera is on the rail. A ball screw turns under
  load, so with no current in the motor the camera can slide down. A stop
  collar on the rail just below the focus range is cheap insurance.

## 3. Arduino IDE

1. Install the Arduino IDE (version 2).
2. **Arduino IDE → Settings → Additional boards manager URLs**: add
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`.
3. **Boards Manager**: install **esp32 by Espressif Systems**.
4. **Library Manager**: install **FastAccelStepper** (by gin66) and
   **TMCStepper** (by teemuatlut).
5. Open `arduino/FocusRail/FocusRail.ino`.
6. **Tools**:
   - Board: **ESP32S3 Dev Module**
   - Flash Size: **16MB**
   - PSRAM: OPI PSRAM, or Disabled (the sketch doesn't use it)
   - USB CDC On Boot: depends on which socket you use (below)
   - Port: the ESP32's port (below)

The DevKitC-1 has two USB-C sockets:
- **UART** (or COM): goes through a USB-to-serial chip. Set *USB CDC On Boot*
  to **Disabled**. The port looks like `/dev/cu.usbserial-…` or
  `/dev/cu.wchusbserial…`.
- **USB**: the S3's own USB. Set *USB CDC On Boot* to **Enabled**. The port
  looks like `/dev/cu.usbmodem…`.

Either works with FreeCCR. Start with UART. If no port appears with it, use
the USB socket instead.

## 4. The settings at the top of `FocusRail.ino`

| Setting | Yours | Note |
|---|---|---|
| `MOTOR_PULLEY_TEETH` / `SCREW_PULLEY_TEETH` | 20 / 30 | must match the pulleys fitted, or every distance is wrong |
| `LEAD_MM` | 4 | SFU1204 |
| `RUN_CURRENT_MA` | 600 | never above the current on the motor's label (pancake motors are often about 1 A) |
| `HOLD_FRACTION` | 0.5 | raise to 0.7 if the camera creeps down when the rail stops |
| `INVERT_DIR` | false | set in step 6 |
| `HOME_MODE` | `orp::HOME_NONE` | no end switch yet: use Set Zero in FreeCCR |
| `SPEED_MM_S` | 2 | half a turn of the screw a second |

## 5. Upload

Click Upload (→). If it says *Failed to connect*, hold **BOOT**, press and
release **RST**, release BOOT, and upload again. Press RST once afterwards.

I couldn't compile the sketch for the ESP32 where it was written, so this is
its first compile. If it fails, send the error message.

## 6. A first test, in the Serial Monitor

Open **Tools → Serial Monitor**, set **115200 baud** and **New Line**, and
type these one at a time:

- `HELLO` → `ORP 1 STEPS_PER_MM=1200 STEPS_PER_REV=4800 HOME=NONE NAME=DIY Focus Rail (ESP32 + TMC2209)`
- `STATUS` → `STATUS POS=0 MOVING=0 HOMED=0 ENABLED=1 DRIVER=1`
  - **DRIVER=1**: the ESP32 is talking to the TMC2209.
  - **DRIVER=0**: see Troubleshooting. Don't go on until it says 1, because
    without its settings the driver moves at the wrong step size.
- `MOVE 1200` → `OK`, then `DONE POS=1200`. The camera should move 1 mm, a
  quarter turn of the screw, smoothly and almost silently.
  - Note which way it went. **+ should take the camera up, away from the
    film.** If it went down, set `INVERT_DIR` to `true` and upload again.
- `MOVE -1200` → back where it was.

Then **close the Serial Monitor**: only one program can use the port at a
time.

## 7. In FreeCCR

1. **File → Capture…**, open the **Focus Rail** section.
2. Pick the ESP32's port (it's listed first) and press **Connect**. The
   status line reads "DIY Focus Rail (ESP32 + TMC2209) · position not set · 4
   mm a turn".
3. **Jog**: the ±0.02 and ±0.2 mm buttons, or the keys `[` `]` (fine) and
   `{` `}` (coarse). Right-click a jog button to change its step.
4. **Set Zero** wherever is convenient. Positions are counted from there.
5. **Autofocus**: set Camera zoom to 8× and press Autofocus.
   - It sweeps ±1 mm (a quarter turn of the screw) in 0.1 mm steps, then
     0.02 mm steps around the best, and stops at the sharpest point.
   - It always arrives from below, so play in the belt and the screw can't
     shift the result.
   - Right-click it for the Range, the steps, the Backlash (0.1 mm) and the
     Settle time. Raise Settle (300 ms) if the camera still wobbles after a
     move.
6. **Autofocus before each shot**: tick the box and choose **at 8×** or **at
   Max**. Shoot then:
   - magnifies the camera;
   - focuses;
   - goes back to the whole frame;
   - shoots, as set up (one colour or red, green and blue).

   If no focus is found, nothing is shot and the caption says why. Shoot or
   Esc stops it.

## Troubleshooting

- **No port**:
  - The cable may be a charging-only one.
  - Try the other USB socket, with *USB CDC On Boot* set to match.
- **Upload fails**: BOOT + RST, as in step 5.
- **DRIVER=0**:
  - Is the 12 V on? The TMC2209 doesn't answer without it.
  - GPIO 17 goes through the 1 kΩ resistor and GPIO 18 goes straight, both to
    PDN_UART.
  - VIO to 3V3, GND shared with the ESP32, MS1 and MS2 to GND.
  - Some modules need their UART solder jumper set.
- **The motor hums or shudders but doesn't turn**: the coil pairs are mixed.
  Unplug the 12 V, then check the pairs with the multimeter.
- **It moves the wrong distance**:
  - The pulley numbers or `LEAD_MM` don't match the hardware.
  - Or DRIVER=0, so the driver is using its own step size.
- **The motor gets too hot to keep a finger on**: lower `RUN_CURRENT_MA` or
  `HOLD_FRACTION`.
- **The camera creeps down after a move**: raise `HOLD_FRACTION` to 0.7.
- **FreeCCR says "That port is a light, not a rail"**: that's the Scanlight's
  port. Pick the other one.
