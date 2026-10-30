# Expose for the film base: a shutter speed per colour

Status: DRAFT for the user's review (2026-10-30). Not built. Open points are
at the end; test 10 (`gfx50sii_test_v10.py`) answers the camera questions.
Builds on `spec/usb-exposure-control.md` (setting shutter speed and ISO over
USB) and `spec/live-capture-monitor.md` §8 (the Shoot sequence).

## Why
A negative's film base (the clear orange mask, in the gaps between frames and
the rebate beside the sprocket holes) is the thinnest film on the roll: nothing
in the picture lets more light through, and the scene's deepest shadows sit
just under it. So the base is the top of the useful range, and placing it
just under the sensor's ceiling in each colour gives every dye layer the most
light, which is the least noise in the inverted shadows (the noise the parked
density experiment in CLAUDE.md ran into).

Under white light one exposure has to serve all three colours, and the orange
mask holds blue (and green) well below red: blue typically gets 1.5-2.5 stops
less light than red at the base (test 10 measures it on the user's film).
A trichrome set shoots each colour on its own, so each can be exposed to the
base on its own. Today the balance between colours comes from the light
preset (dimming the brighter LEDs) at one camera exposure, which throws away
light in red and green instead of adding it in blue.

What varies, and why the shutter (the user's rules):
- **The shutter speed is the control.** On a copy stand nothing moves, so a
  longer exposure costs only time, and more light on the sensor at the same
  ISO is more signal.
- **ISO stays at the base**, and goes up (never past a cap, 640 by default)
  only when the longest allowed shutter speed is not enough. Raising ISO
  amplifies the light that arrived; it does not add any, and it costs
  dynamic range.
- **The light stays where the preset puts it.** Dimming an LED and lengthening
  the shutter are the same trade (the same light on the sensor, more time),
  so with per-colour shutter speeds the LEDs can all sit at full and the
  shutter does the balancing.

## What the camera does (tests 8-9) and what test 10 must confirm
Known: with live view off, the GFX 50S II takes a shutter speed or ISO in
~50 ms; with live view running it refuses every setting, and the only way out
is a reconnect (0.4-0.7 s). After a shot, live view is off until it is asked
for again (`Gphoto2Backend.fire` clears `_live_asked`).

Test 10 (FreeCCR's own camera code, no live view) checks:
1. A new shutter speed / ISO is taken right after the previous shot's file
   lands, and how long that takes.
2. The next file really carries it (EXIF) and the RAW follows it.
3. Whether 1/3-stop speeds are true thirds (the "1/15" setting exposing
   1/16 s) or the dial numbers: the model below must use whichever is true.
4. One stop of ISO doubles the RAW values.
5. Firing-to-file time at 1 s (detects the camera's Long Exposure NR, which
   doubles every long exposure with a dark frame).
6. Light drift over a few minutes; and, on film base, the white-light R/G/B
   gap in stops.

## Goals
1. **Calibrate** with one click: test shots measure the film base in the RAW
   and work out, for each colour (R, G and B for a trichrome Shoot, W for a
   single white shot), the exposure that puts the base at the target.
2. **Shoot uses it**: each colour's shutter speed (and ISO) is set before its
   shot, and the shot is never fired until the camera has taken it.
3. **Colour doesn't change with the exposures**: a triplet merged from
   frames shot at different speeds comes out the same as one shot at equal
   speeds (see "The merge" below). Only the noise is lower.
4. **Shown and kept**: the monitor shows what was calibrated; it stays until
   recalibrated, and is set aside when the light levels it was made with
   change.
5. Test shots never reach the hot folder.

## Non-goals
- Metering each frame. The base is a property of the roll (stock and
  processing); metering every picture would move the base frame to frame and
  the conversion would have to undo it.
- Measuring on live view: the live picture is the camera's processed JPEG
  (tone curve, white balance), so it can't say where the RAW sits. Live view
  is used only to find WHERE the base is.
- Aperture (the lens is manual), the Scanlight-cable shutter route (FreeCCR
  can't set the camera's exposure there), digiCamControl and HDMI devices.
- An exposure for the IR (dust) frame: later, same machinery.
- Changing the light automatically. If even the shortest allowed shutter speed
  puts the base over the target, the calibration says so and names the LED
  to dim (open point 4).

## Where the base is measured
On the RAW of each test shot, inside a patch located on the live view before
the test shots:
1. The **Film Base** point clicked in Negative Preview, if there is one: a
   square of 2 % of the short side around it.
2. Else the **rebate** `auto_black_point.find_rebate` finds on the last full
   live frame (Negative Preview's Auto uses the same), its rect inset by 20 %.
3. Else the button asks for a click on the film base (Negative Preview's
   Film Base pick mode).

Live view fractions map to the RAW's visible area (both 4:3 on the GFX 50S
II; the inset and the evenness check below cover a small offset between the
two). The measurement is the median of the lit colour's
photosites in the patch, black-subtracted, as a fraction of the RAW's range
(the `triplet_plan.frame_quality` arithmetic). A patch whose spread (IQR /
median) is over 0.06 is reported ("the patch isn't even: dust, edge print or
the picture edge?") and the calibration stops.

**Target**: base median at `TARGET` = 0.85 of the range. With 1/3-stop steps
and the rule "never above", the base lands between 0.67 and 0.85. The margin
covers base elsewhere in the frame being a little brighter than the patch
(lens falloff, light evenness). The sprocket holes are clear film and will
clip; they hold no picture.

## The exposure ladder (a pure function)
`plan_exposure(k, shutters, isos, rules) -> (shutter, iso, predicted, note)`

- `k` is the measured base level per unit exposure (level / (t x ISO/100)),
  with `t` and ISO snapped to true thirds of a stop if test 10 shows the
  camera works that way.
- `rules`: `base_iso` (default: open point 1), `max_iso` 640, `longest`
  (default 4 s, open point 2), `shortest` (default 1/250 s).
- At `base_iso`, the longest shutter speed within [`shortest`, `longest`]
  whose predicted base is at or below `TARGET`.
- If even `longest` leaves it under `TARGET`: `longest`, and the highest ISO
  step (the camera's own, Auto modes never) up to `max_iso` whose predicted
  base stays at or below `TARGET`. Still short: those settings, and a note ("blue's base at 52 %: allow a longer
  shutter speed, or a brighter blue").
- If even `shortest` at `base_iso` is over: `shortest`, and a note naming the
  LED to dim.

## Calibration
Button **Expose for Film Base…** in the Capture section, under the Exposure
row; enabled with a USB camera open whose shutter speed can be set (and, for
trichrome, the light connected). Esc / the button again cancels.

1. Find the patch (above). Pause live view (no previews until the end, so
   every setting goes straight in). Point the camera's downloads at a temp
   folder (`UsbCamera.set_dest`).
2. For each colour, its LED alone at the preset's level (`cl.solo`), then:
   - **First shot** at the current shutter speed, at `base_iso`.
   - Over 0.95 (too close to the ceiling to trust): 3 stops shorter and
     again; under 0.02: 3 stops longer and again (each at most 3 times).
   - Otherwise plan from it, then a **check shot** at the plan. Every shot
     re-measures `k`; if re-planning from the check gives a different
     setting (the base came out over `TARGET` x 1.03, or a step under where
     a longer one fits), shoot that and check again (at most 2 re-plans).
     The last check's level is what is recorded.
   - Typically 2 shots per colour, 6 for trichrome.
3. Restore: the hot folder, the white positioning light, the shutter speed
   and ISO the pickers had, live view. Temp files deleted.
4. Store and show (below). Any failure (camera lost, refused setting, no
   file) restores the same way and captions why.

## Shoot with calibrated exposures
- A checkbox under the button, **Use for Shoot** (on once calibrated).
- `CaptureSequence.start(..., exposures={"r": {...}, "g": {...}, "b": {...}})`
  (or `{"w": ...}` for a single shot). The camera-shutter step becomes:
  colour -> settle -> `shutter.fire(exposure)`.
- `UsbCamera.fire(exposure=None)`: the worker sets the exposure and fires in
  one go (`set_exposure`, then `fire` only if every field was taken; a
  refusal emits `fire_failed("The camera didn't take 1/4 s: ...")`, which
  ends the sequence as "failed"). Setting and firing in one worker turn
  means nothing can slip between them.
- Live view is paused for the whole sequence, so every setting goes straight
  in (a reconnect per colour would add 0.4-0.7 s each); the last picture
  stays on screen and live view resumes after.
- The file timeout and the progress ring include the exposure time.
- At the end the camera goes back to the pickers' values, then any change
  picked during the sequence is applied (the existing `_exp_held`).

## The merge
`ccr_merge.combine_channels` gains per-channel exposure scales: each frame's
plane is multiplied by `E_min / E_c`, where `E_c` = its exposure (t x ISO/100,
from its EXIF, snapped like the plan) and `E_min` the shortest of the three.
All scales are at most 1, so nothing new can clip; frames shot at equal
exposures get 1, 1, 1 and merge exactly as today.

Why it is needed: the merged frame goes through a camera profile (a matrix
that mixes the channels) before any per-channel normalisation, so a channel
that is simply brighter because it was exposed longer would come out a
different colour, and would change roll to roll with the base. Undoing the
exposures first keeps the colour tied to the film, not to the calibration,
and keeps trichrome IT8 profiles valid (they are fitted on the same merge).
A trichrome profile made from a triplet shot at unequal exposures before this
change would need making again; one shot at equal exposures is unaffected.

Consequence: the merged values' overall level follows `E_min`. The roll's
black point is sampled in merged values, so recalibrating mid-roll means
sampling the black point again (the calibration says so when one is set).

## Data
In the light preset, beside its levels (`light_presets`):

    "exposure": {"r": {"shutter": "1/8", "iso": "100", "base": 0.81},
                 "g": {...}, "b": {...},
                 "levels": {"r": 255, "g": 255, "b": 255, "w": 255, "ir": 0},
                 "camera": "Fujifilm GFX 50S II", "when": "2026-10-30"}

- `normalise` keeps a well-formed `exposure` and drops a malformed one;
  `overwrite` (new levels) drops it; import / export carry it.
- Used only when `levels` matches the preset's and `camera` matches the open
  camera; otherwise shown as "set aside: made with other light levels".
- Different film stocks: one calibration per preset, so a stock gets its own
  preset ("Full · Portra 400"). Open point 3.

## UX (Capture section)

    Exposure  [ 1/20 ▾ ] [ ISO 100 ▾ ]
    [ Expose for Film Base… ]
    Film base: R 1/8 · G 1/4 · B 1/2 s, ISO 100 (base 81 / 80 / 83 %)   [x] Use for Shoot

- Progress in the capture caption: "Film base: blue, check shot (5 of ~6)…".
- Notes from the ladder go in the same line, in the warning colour.
- Settings → Capture: Base ISO, Highest ISO, Longest shutter speed, Shortest
  shutter speed.

## Code
- `core/base_exposure.py` (new, Qt-free): `seconds(text)`, `third(x)`,
  `patch_level(path, rect, colour)`, `plan_exposure(...)`, and
  `Calibration`, a small state machine with an injectable camera / light /
  measure (tested like `CaptureSequence`).
- `capture_sequence`: `exposures` on `start`; `fire(exposure)`; timeouts.
- `usb_camera`: `fire(exposure=None)`; `pause_live(on)` (no previews while
  on; distinct from the user's live view switch).
- `ccr_merge.combine_channels(..., exposure_scales=(1, 1, 1))`;
  `merge_raw_channels` reads each source's exposure.
- `light_presets`: the `exposure` field.
- `live_monitor`: the button, the line, the checkbox, the patch lookup, the
  pickers restored after a sequence.

## Tests
- `seconds` / `third` on the camera's real strings (test 10 prints them).
- `plan_exposure`: in range at base ISO; longest reached -> ISO up by the
  shortfall; `max_iso` reached -> note; too bright at `shortest` -> note
  naming the LED; never above `TARGET`; ISO Auto values never chosen.
- `patch_level` on a synthetic mosaic: the lit colour only, black-subtracted;
  an uneven patch refused.
- `Calibration` with a fake linear camera (level = k_c x t x ISO/100, clipped
  at 1): converges in 2 shots per colour from a sensible start; a clipped
  first shot backs off; a check over target steps down; the hot folder, live
  view, light and pickers restored on success, failure and cancel.
- Sequence: per-colour exposure passed to `fire`; a refused exposure ends it
  as "failed"; no exposures -> exactly today's behaviour; held picks applied
  after.
- Merge: equal exposures -> identical to today; R 1/8, G 1/4, B 1/2 of the
  same scene -> the same merged ratios as equal exposures; no scale above 1.
- Presets: `exposure` survives normalise / dumps / import; dropped on new
  levels; set aside for another camera.

## Open points (for the user)
1. **Base ISO.** Fujifilm's spec for the GFX 50S II lists ISO 100-12800 as
   standard sensitivities (50 is the extended one), so 100 is a full native
   setting on this body; 160 is the base on the X-series. Default 100, or
   160 as you said?
2. **Longest shutter speed** before ISO goes up: 4 s? Each trichrome frame
   takes the three exposures plus ~1.5-2 s of download each.
3. **One calibration per light preset** (a preset per film stock), or a
   separate list of film-base calibrations to pick from?
4. If even the shortest shutter speed is too bright: **say which LED to dim**
   (proposed), or dim it automatically?
5. The exposure-normalised merge: **on always** (proposed; equal exposures
   are unchanged), or a setting?
