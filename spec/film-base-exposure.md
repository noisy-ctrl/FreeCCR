# Expose for the film base: a shutter speed per colour

Status: refined 2026-10-30 with the user's answers to the draft's open points,
then built; ISO rule and LED start revised the same day (the user: "ISO 100 might
be a stretch sometimes. Going up a couple of stops is ok if it needs", and the
light starting from 100 %). Builds on `spec/usb-exposure-control.md` (setting shutter speed and
ISO over USB) and `spec/live-capture-monitor.md` §8 (the Shoot sequence).

## Why
A negative's film base (the clear orange mask, in the gaps between frames and
the rebate beside the sprocket holes) is the thinnest film on the roll: nothing
in the picture lets more light through, and the scene's deepest shadows sit
just under it. So the base is the top of the useful range, and placing it
just under the sensor's ceiling in each colour gives every dye layer the most
light, which is the least noise in the inverted shadows (the noise the parked
density experiment in CLAUDE.md ran into).

Under white light one exposure has to serve all three colours, and the orange
mask holds blue (and green) below red. On the user's film under the
Scanlight's white LED (test 10): green 0.21 and blue 1.56 stops below red, so
one exposure gives blue about a third of red's light. A trichrome set shoots
each colour on its own, so each can be exposed to the base on its own.

The user's rules:
- **The shutter speed is the control.** On a copy stand nothing moves, so a
  longer exposure costs only time, and more light on the sensor is more
  signal.
- **ISO 100 first** (the GFX 50S II's lowest standard sensitivity), with the
  speed up to `PREFERRED_S` (1 s). Past that ISO may rise, by at most two
  stops (to 400), and only then does the speed go longer (to the 30 s safety
  ceiling). Raising ISO amplifies the light that arrived and adds none: each
  stop of it means half the light for the same base level, so it is the
  second resort, not the first. (The user would like speeds faster than 1/20
  against movement; the Scanlight can't give that at a usable ISO, and the
  camera is clamped. A brighter light later moves everything faster.)
- **Each LED starts at full** for trichrome (not the preset's level): the
  shutter balances the colours, so a dimmed LED would only slow its colour
  down. The light is dimmed by FreeCCR only if even the shortest allowed
  speed (1/250) is too bright: a shorter speed gives the same result in less
  time and with less risk of movement, so it goes first. (Unlikely with this
  light; the dimmed level is kept with the film base.)
- **Film bases are their own list**: stock, batch and chemistry all move the
  base, so each roll can be measured and named, and picked again later.

## What the camera does (tests 8-10)
Tests 8-9: with live view off, the GFX 50S II takes a shutter speed or ISO in
~50 ms; with live view running it refuses every setting, and the only way out
is a reconnect (0.4-0.7 s). After a shot, live view is off until it is asked
for again (`Gphoto2Backend.fire` clears `_live_asked`).

Test 10 (9 shots, live view off, film base under the white LED, ISO 250):
1. **Setting between shots works**: every shutter speed and ISO was taken
   right after the previous shot's file came in, in 76-125 ms, and read back.
2. **Each file carries it**: EXIF matched every setting, and the RAW followed.
3. **The shutter speeds are the dial numbers, not true thirds**: 1/30 -> 1/25
   gave x1.198 (dial x1.200, true thirds x1.260); 1/15 -> 1/8 gave x1.882
   (dial x1.875, true thirds x2.000). So exposure is computed from the
   camera's own numbers (the EXIF value), never snapped.
4. **ISO is linear**: 250 -> 500 gave x1.999.
5. **No dark frame at 1 s**: the 1 s shot was ready 2.16 s after firing,
   about 1 s more than a short one (1.0-1.3 s): the exposure itself.
6. **The light doesn't drift**: the first shot repeated at the end matched to
   0.00 %. One check shot per colour is enough.
7. **Time per shot**: file ready 1.0-1.3 s after firing, downloaded 3.4-3.5 s
   later, ~4.5 s a shot plus the exposure. A trichrome frame is ~14 s plus its
   three exposures; a measurement (~6 shots) ~30 s.
8. **The ceiling**: saturated photosites read 3.4 % above the white level
   LibRaw reports. FreeCCR clips at the reported level, so levels and the
   target are measured against that (the extra 3 % is never used).
9. **What it wins here**: at ISO 100 under the white LED, red ~1/3 s, green
   ~1/2.5 s, blue ~1 s would put all three near the target; one exposure
   leaves blue x2.9 under. (The trichrome LEDs differ; measuring reads each.)

## Goals
1. **Measure** a film base with one click: test shots, the base read in the
   RAW, and for each colour (R, G and B for trichrome; one exposure for a
   single shot) the exposure that puts the base at the target. Saved as a
   named entry in a list of film bases.
2. **Shoot uses it**: with "Shoot at these exposures" ticked, each colour's
   LED level and shutter speed are set before its shot, and the shot is never
   fired until the camera has taken the setting.
3. **Colour doesn't change with the exposures**: a triplet merged from
   frames shot at different speeds comes out the same as one shot at equal
   speeds (the merge, below). Only the noise is lower.
4. Test shots never reach the hot folder; everything measuring changed
   (light, camera exposure, download folder, live view) is put back, on
   success, failure and cancel alike.

## Non-goals
- Metering each frame. The base is a property of the roll; metering every
  picture would move the base frame to frame and the conversion would have to
  undo it.
- Measuring on live view: the live picture is the camera's processed JPEG
  (tone curve, white balance), so it can't say where the RAW sits. Live view
  is used only to find WHERE the base is.
- Raising ISO. `plan_exposure` keeps a `max_iso` rule for other cameras, but
  the monitor passes the base, so it never moves.
- Aperture (the lens is manual), the Scanlight-cable shutter route (FreeCCR
  can't set the camera's exposure there), digiCamControl and HDMI devices.
- An exposure for the IR (dust) frame: later, same machinery.

## Where the base is read
On the RAW of each test shot, inside a patch fixed on the live view before the
first test shot:
1. The **Film Base** point last clicked in Negative Preview: a square of
   `PATCH_SIDE` (3 %) of the frame's short side around it.
2. Else the **rebate** `auto_black_point.find_rebate` finds on the last full
   live frame (what Negative Preview's Auto uses), its rect inset by 20 % each
   side, but only at HIGH confidence (two edges agreeing). Checked on a real
   35mm negative's live picture: it took the right edge at medium confidence,
   and that edge was bare light beside the film, which would have set every
   exposure a stop or more short. A lone edge therefore asks for the click.
3. Else Measure asks for a click on the film base (Negative Preview's Film
   Base pick) and starts after it.

Live view fractions map to the RAW's visible area (both 4:3 on the GFX 50S
II; the inset and the evenness check cover a small offset between the two).
The reading is the median of each colour's photosites in the patch,
black-subtracted, as a fraction of the RAW's black-to-white range (the
`triplet_plan.frame_quality` arithmetic), and its spread (IQR / median). A
trichrome colour reads its own channel; a single shot reads its brightest
channel (all three must stay under the target). A spread over `MAX_SPREAD`
(0.08) stops measuring: "the patch isn't even: dust, edge print or the
picture's edge? Click the film base in Negative Preview."

**Target**: base at `TARGET` = 0.85 of the range. The chosen speed is the
longest whose predicted base is at or below it, so with 1/3-stop steps the
base lands between ~0.67 and 0.85. The margin covers base elsewhere in the
frame being a little brighter than the patch (lens falloff, light evenness).
The sprocket holes are clear film and will clip; they hold no picture.

## The exposure plan (a pure function)
`plan_exposure(k, shutters, isos, rules) -> Plan(shutter, iso, predicted, dim, short)`

- `k` is the base level per unit exposure read on the last shot:
  `level / (t x ISO/100)`, with `t` and ISO the camera's own numbers.
- `rules`: `base_iso` "100" (else the camera's lowest numeric ISO),
  `max_iso` two stops up from it in the camera's own list ("400"),
  `preferred` 1 s, `longest` 30 s, `shortest` 1/250 s, `target` 0.85. Auto
  ISO values (negative numbers) are never used.
- The camera's shutter speeds within [`shortest`, `longest`], then:
  1. at `base_iso`, the longest speed up to `preferred` whose predicted base
     is at or below `target`;
  2. if that is `preferred` itself and still more than 1/3 stop under:
     the highest ISO up to `max_iso` that keeps it at or under, at that speed;
  3. if that is `max_iso` and still more than 1/3 stop under: at `max_iso`,
     the longest speed up to `longest` that fits.
- Even `longest` at `max_iso` leaves it more than 1/3 stop under `target`:
  `short` is set; measuring stops with "Blue would need about 4 min at ISO
  400: is the blue LED on, and the film base under the lens?". Nothing is
  saved.
- Even `shortest` is over `target`: `shortest`, and `dim` = the factor the
  LED must come down by (`target / predicted`). Measuring dims that LED
  (`level x dim x 0.95`, at least 1) and shoots again; with no light
  connected it stops: "even 1/250 s is too bright: dim the light".

## Measuring (`base_exposure.Calibration`, a Qt-free state machine)
`Calibration(colours, start_levels, shutters, isos, first_shutter, rules,
can_dim)`; the caller is the camera: `next_shot()` gives `(colour, levels,
{"shutter", "iso"})` to shoot (None when finished), `landed(reading)` takes
that shot's patch reading, `failed(text)` and `cancel()` end it; `done`,
`result` and `error` say how it ended.

Per colour:
1. **First shot** at the camera's current speed (clamped into the range) at
   `base_iso`, the colour's start levels.
2. Reading over `CLIP_TRUST` (0.95, too near the ceiling to extrapolate):
   3 stops shorter and again (at `shortest`: dim). Under `TOO_DARK` (0.02):
   3 stops longer and again (at `longest`: stop, as above).
3. Otherwise plan from it. If the plan is the setting just shot and its
   reading is at or under `target` x 1.03, accept: the colour's speed, ISO,
   LED levels and base reading are kept. Else shoot the plan (a **check
   shot**). A plan that comes back to a setting already shot accepts the
   longest tried setting whose reading was within `target` x 1.03.
4. At most `MAX_SHOTS` (6) shots per colour; then the best tried, or stop.
Typically 2 shots per colour, 6 for trichrome.

## The monitor
Capture section, under the Exposure row and shown with it (a USB camera whose
shutter speed can be set):

    Film base  [ Portra 400 · Oct ▾ ]  [ Measure… ]
    R 1/3 · G 1/2.5 · B 1 s, ISO 100 (base 82 / 85 / 83 %)
    [x] Shoot at these exposures

- The combo lists the film bases (`live/film_bases` in QSettings,
  `{"bases": [...], "selected": i}`; empty: "No film base yet"). Right-click:
  **Measure Again** (same name, new exposures), **Rename…**, **Delete…**.
- **Measure…** asks for a name ("Film base · 30 Oct" by default) and measures
  for the current scan type: trichrome measures R, G and B, each LED alone
  from full (the light must be connected); colour / B&W measures one exposure
  with the light as it is.
  While it runs the button reads **Stop Measuring** (Esc too), Shoot is
  disabled, and the caption says "Film base: blue, shot 5…".
- The progress caption says where the base is read ("where you clicked",
  "the top border").
- What measuring does: fixes the patch, pauses live view
  (`UsbCamera.pause_live(True)`: no preview requests, so every setting goes
  straight in; the first may reconnect once if live view was running), points
  downloads at a temp folder (`set_dest`), sets each colour's LED alone at
  full (for one exposure, the light as it is), waits the
  Settle time, fires with the exposure (`UsbCamera.fire(exposure)`), reads
  the patch on a worker thread when the file lands (a 50 MB RAF takes ~0.5 s),
  and moves on. At the end, in this order: the light's levels, the camera's
  exposure (the pickers' values), the download folder, live view; the temp
  folder is deleted.
- The summary line names each colour's speed, the ISO and the base reading;
  a dimmed LED is listed ("blue LED 180"). A film base measured for the other
  scan type says so ("measured for trichrome").
- **Shoot at these exposures** (`live/film_base_use`, default on): Shoot over
  USB with a film base that has this scan type's exposures passes them to the
  sequence; Shoot via the Scanlight cable, or a film base for the other scan
  type, shoots as before with a caption note.

## Shoot with a film base
- `CaptureSequence.start(..., exposures={"r": {...}, "g": {...}, "b": {...}})`
  (or `{"w": {...}}` for one shot); each entry `{"shutter", "iso", "levels"}`.
- A colour's light is its entry's `levels` (not `cl.solo(preset)`); a single
  shot sets the entry's levels when the light is connected.
- The camera step is colour -> settle -> `shutter.fire(exposure)`;
  `UsbCamera.fire(exposure)` sets it and fires in ONE worker turn (a refused
  setting emits `fire_failed("The camera didn't take 1/3 (...)")`, which ends
  the sequence as "failed"), so nothing can slip between them.
- Live view is paused for the sequence (a reconnect per colour would add
  0.4-0.7 s each); the last picture stays and live view resumes after. The
  pickers' exposure is sent back at the end, before anything picked during
  the sequence.
- The file timeout and the progress ring include the exposure time.

## The merge
`ccr_merge.combine_channels(..., exposure_scales=(1, 1, 1))`;
`merge_raw_channels` reads each source's exposure (`shot_exposure`: seconds x
ISO/100 from `read_shot_exposure`, cached per path, size and modification
time) and passes `E_min / E_c` per channel. All scales are at most 1, so
nothing new can clip, and the scaling is done in float before the uint16 (no
extra quantisation that matters: the 14-bit data sits in 16 bits with room to
spare). Equal exposures, or any source whose exposure can't be read, give
1, 1, 1: exactly today's merge. Always on (the user: whatever gives the best
picture; it costs nothing in noise, which is fixed when the light lands on the
sensor).

Why it is needed: the merged frame goes through a camera profile (a matrix
that mixes the channels) before any per-channel normalisation, so a channel
that is simply brighter because it was exposed longer would come out a
different colour, and would change roll to roll with the base. Undoing the
exposures first keeps the colour tied to the film. Trichrome IT8 profiles are
fitted on the same merge; one made from a triplet shot at unequal exposures
before this change would need making again (one at equal exposures is
unaffected). The roll's black point is in merged values, whose overall level
follows `E_min`: a roll measured again mid-way needs its black point sampled
again.

## Revision: a busy camera is fired again (2026-10-30)
The user's first measure on the GFX 50S II stopped at once with "The camera
didn't fire ([-110] I/O in progress)". With live view running, the first
shot's settings go in through the reconnect that ends live view, and the
camera then answered the fire, sent straight after, with "busy". Test 10
never fired that soon after connecting. "Busy" means no picture was taken, so
`Gphoto2Backend.fire` now tries again every `FIRE_RETRY_S` (0.15 s) for up to
`FIRE_BUSY_S` (5 s), on the trigger and the capture call alike, and prints how
many tries it took; only after that is it a failure (and busy still never
counts towards a lost camera). Helps Shoot after a picker change too.

## Revision 2: busy can mean "shot taken" (2026-10-30)
Revision 1 didn't fix it, and the user saw why: the shutter HAD fired. The
GFX 50S II answered the fire with "busy" and took the picture anyway, so
every retry was answered "busy" again (the camera holding that shot), live
view then stalled ("the camera is still holding the last shot"), and a retry
that got through would have taken a second picture. So, in `Gphoto2Backend`:
- **No fire within `RECONNECT_SETTLE_S` (1 s) of connecting** (`_ready_at`,
  set by `open` and `_reconnect`): the busy answer came to a fire sent
  straight after the exposure reconnect; test 10 fired seconds after its
  connect and never saw it.
- **Busy from a fire is "maybe shot"**: `_confirm_shot` waits up to
  `FIRE_CONFIRM_S` (3 s) + the shutter time (`_shutter_s`, from the last
  reading or setting) for the shot's file, as an event or found waiting in
  the camera's own memory (`held_files()`: image files in a store with no
  DCIM folder, so never a card). Found: it counts as fired and the file is
  queued like any shot's. Not found: one more fire (`FIRE_BUSY_TRIES`).
  Revision 1's blind retries are gone.
- **A stalled live view collects held shots**: when `live_stalled` turns on,
  the worker calls `collect_held()` once, so a shot whose file never came is
  brought in (downloaded and cleared like any other), which frees live view.
- `FREECCR_GPHOTO2_LOG=<file>` writes libgphoto2's own debug log, to see what
  the camera answered if this happens again.

## Revision 3: the capture call on a Fujifilm (2026-10-30)
The user ran FreeCCR with `FREECCR_GPHOTO2_LOG` and sent the log. It settled
it: two shots in a row went through libgphoto2's full capture call
(`camera_fuji_capture`: half-press, AF status, release, the new object found
and fetched), because live view had been asked for between them; the third,
with no live view asked, went through the trigger call, whose
`InitiateCapture` the camera answered `Device Busy`, and so did the retry.
No picture was taken that time (no new object). Test 10 only ever used the
trigger call on a fresh connection, so it never mixed the two.
So `Gphoto2Backend._capture_call()` now picks the capture call always on a
Fujifilm (the libgphoto2 table name has "Fuji"), as well as with live view
asked, and on any other camera once its trigger was refused busy with no
shot (sticky for the connection, `_use_capture`). The capture call holds the
worker for the shot and its download (~4-5 s), which Shoot and measuring
wait for anyway.

## Revision 4: editing a film base by hand (2026-10-31)
The user: "We need an edit button for the measured profiles." Measuring picks
the exposures; he wants to set them himself too (it also covers his earlier
idea of a shutter speed per colour in Timing: the film base IS that table).

**UX.** `Edit…` sits beside `Measure…` in the Film base row (and in the
combo's right-click menu), enabled when a film base is selected and nothing
is measuring or shooting. It opens `FilmBaseEditDialog`
(`widgets/film_base_dialog.py`):

    Name   [ Ektar 100 · batch 2          ]
             Shutter    ISO        LED    Base
    Red     [ 1/4  ▾]  [ISO 100 ▾] [255]  ≈ 79 %
    Green   [ 1/4  ▾]  [ISO 100 ▾] [255]  ≈ 70 %
    Blue    [ 1/5  ▾]  [ISO 100 ▾] [255]  ≈ 71 %
    Base is estimated from the measured reading: twice the time, ISO or LED
    level gives twice the level. Measure Again reads it for real.
                              [Cancel] [Save as New] [Save]

- One row per colour the film base has, in R, G, B, then "One exposure" (w).
- Shutter and ISO list the camera's own values (the Exposure row's lists;
  Auto ISO and Bulb/Time, which have no number, are left out); a value the
  film base holds that the camera doesn't list (another body) is added in
  speed order so opening and saving never changes it.
- LED: the level 1-255, as the light's own sliders and the summary show it
  (the brightest lit channel of the entry's `levels`). A one-exposure entry
  measured with the light off/unconnected has no levels: the field is
  disabled and reads "as it is".
- Base: the estimate, `base x (E_new / E_old) x (L_new / L_old)` with
  `E = seconds x ISO / 100` (`exposure_of`) — test 10 measured speed and ISO
  linear, and the calibration already dims the LED linearly and confirms with
  a shot. At or above `CLIP_TRUST` (0.95) it turns warning-coloured and says
  "may clip"; more than two stops under `TARGET` it says how many stops under.
  Unchanged rows show the measured reading without "≈".
- **Save** overwrites the selected film base (name too; a name another film
  base already has gets " 2"). **Save as New** keeps the measured one and adds
  the edit as a new film base under the Name field (made unique, so leaving
  the name alone gives "… 2"), and selects it. Cancel / Esc changes nothing.

**Data.** A colour changed by hand carries `"edited": true` (kept by
`_clean_colour`; absent = false, so old settings read unchanged) and its
`base` is the estimate. A changed LED scales every lit channel of `levels` by
`L_new / L_old` (rounded, 1-255), so a preset's mix keeps its balance, and
sets `dimmed` true (the summary then lists the hand-set level). An unchanged
colour is copied as it was. `when`, `camera` and `notes` stay on Save; Save as
New takes today's date.

**Summary.** An edited colour's base is shown with "≈" ("base 79 / ≈70 /
71 %"); a lit LED below full or set by hand is listed as before ("red LED
200"; one exposure: "light 200" — it used to read "the base LED", a slip).

**Code.** `base_exposure`: `led_level(colour)`, `scale_levels(levels, level)`,
`estimate_base(colour, shutter, iso, level=None)`, `edited_colours(old,
values)` (values = `{k: {"shutter", "iso", "level"}}`), `edit(data, index,
name, colours)`; the monitor's `_edit_film_base_dialog(base)` is the test seam
returning `(action, name, values)` with action `"save"`, `"new"` or None.

**Tests.** `estimate_base` (time, ISO, LED, unchanged); `edited_colours`
marks only changed colours and scales a preset mix; `edit` renames (unique)
and keeps `when`/`camera`; old entries without `edited` load; summary shows
"≈" and the LED; the dialog lists the camera's values plus a foreign one,
disables LED without levels, updates the estimate and warns at 95 %; the
monitor's Save / Save as New / Cancel, and Edit disabled while measuring.

## Code
- `core/base_exposure.py` (new, Qt-free): `seconds`, `iso_number`,
  `exposure_of`, `Reading` + `reading_from_mosaic` + `read_patch`, `Plan` +
  `plan_exposure`, `default_rules`, `Calibration`, the film-base list
  (`normalise`, `dumps`, `add`, `replace`, `rename`, `delete`, `selected`,
  `summary`, `exposures_for`), `patch_around` / `patch_inset`.
- `capture_sequence`: `exposures` on `start`; `fire(exposure)`; timeouts.
- `usb_camera`: `fire(exposure=None)`, `pause_live(on)`.
- `ccr_merge`: exposure scales; `shot_exposure(path)` cached.
- `live_monitor`: the Film base rows, measuring glue, Shoot hand-over,
  `_neg_base_pos` (the Film Base click, as a frame fraction).

## Tests
- `seconds` / `iso_number` on the GFX's real strings (test 10's log).
- `plan_exposure`: the longest speed at or under the target; ISO stays 100 up
  to 1 s, then rises just enough (at most to 400) at 1 s, then the speed goes
  longer at 400; more than 1/3 stop short at 30 s and 400 -> `short`; too
  bright at `shortest` -> `dim`; Auto ISO never chosen; with `max_iso` = base
  the speed does it all.
- `reading_from_mosaic`: each colour's own photosites, black-subtracted,
  inside the rect only; spread.
- `Calibration` against a fake linear camera (`level = k_c x t x ISO/100 x
  led/255`, clipped at 1.034): 2 shots per colour from a sensible start;
  a clipped first shot backs off 3 stops; too dark at 30 s stops with the
  message; too bright at 1/250 dims the LED and keeps the dimmed level; an
  uneven patch stops; cancel and failure end it; never above target.
- Film-base list: normalise drops junk, add/replace/rename/delete/selected,
  summary text, `exposures_for`.
- Sequence: per-colour levels and exposure passed to `fire`; timeout grows
  with the exposure; no exposures -> exactly today's behaviour.
- Worker: `fire(exposure)` sets then fires; a refusal reports and doesn't
  fire; `pause_live` stops previews.
- Merge: equal exposures -> byte-identical to today; R 1/8, G 1/4, B 1/2 of
  the same scene -> the same merged values as equal exposures (within
  rounding); no scale above 1; unreadable exposure -> unchanged.
- Monitor: the rows show with the Exposure row; Measure with a fake camera
  runs to a saved, selected film base and puts the light, exposure, folder
  and live view back; cancel puts them back too; Shoot hands the exposures
  to the sequence; rename / delete / measure again.
