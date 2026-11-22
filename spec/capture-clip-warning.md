# Capture warns when a shot clips inside the frame

The user, 2026-11-22: his film base had been measured before he opened the
lens up, so the base's exposures were a stop or more too long and the shots
clipped. Nothing said so until the frames were converted. Proposed and
accepted ("Sounds ideal. Yes please"): Capture warns when a shot clips
inside the frame.

## Why a clip is always a fault here

The film base is the thinnest film on the roll, and its exposures put it at
`base_exposure.TARGET` (0.85 of the RAW's range). Everything inside a frame
is denser than the base, and the per-frame exposure (`frame_meter`) only
lifts a frame's thinnest part to the same target. So with exposures that are
up to date, nothing inside the frame reaches the sensor's clip. A clip there
means the light, the lens or the camera changed since the base was
measured (or a hand-picked exposure is too long), and detail is lost for
good.

## Goals and non-goals

- After each USB shot Capture takes (Shoot, single or trichrome), read the
  RAW and say plainly when any colour clips inside the frame, how much, and
  what to do.
- Not for Measure's, Meter's or Shoot Flat Fields' own shots (they handle
  clipping themselves), nor the IR shot (its exposure has another aim).
- Not for files that arrive only through the hot folder from a shutter
  cable: no exposure Capture chose. (Later, if wanted.)
- Display and the log only; nothing in the conversion changes.

## What counts as clipped (`frame_meter.clipping_in_guide`)

On the RAW's visible mosaic, inside the frame guide the shot was taken with
(the Shoot's guide record, else the canvas's guide when shown: view
fractions mapped onto the sensor as Meter does), `METER_INSET` in from each
side; with no guide, the middle 60 % of the sensor (`CLIP_NO_GUIDE` 0.20 in
from each side: the open light round the film in a carrier clips at the
sensor's edges on every shot, so the middle 90 % warned on his good shot 005). Per colour of the CFA (R, G, B; both
greens together), black subtracted, as a share of black to white:

- `top` = the colour's highest value in the area;
- the colour is CLIPPED when `top ≥ CLIP_FROM` (0.5) and at least
  `CLIP_SHARE` (0.1 %) of its photosites in the area sit within one code of
  `top`. A sensor's clip piles values on one top value; noise spreads an
  unclipped maximum over many, so 0.1 % at one value doesn't happen
  without a clip (Measure's pile rule, `PILE_SHARE`, uses the same idea on a
  patch). This catches a clip below the file's white level too.

Result: `{colour: share}` for the clipped colours (share of that colour's
photosites in the area), empty when nothing clips. Read in strips of rows
like `reading_in_guide`.

## Behaviour in Capture

- `_on_usb_file` (not Measure): unless the file is an IR shot
  (`capture_log.name_slot` slot "ir", or `is_ir_file`), read it on a thread
  (`_clip_read` signal, token per shot) with the shot's guide.
- Results gather per shot (`self._clip_shot`): file name → clipped colours.
- While the sequence runs nothing is said (its own captions run); at its end
  (and straight away for a file that lands after it), if any file clipped,
  the caption turns to the warning colour:
  "This shot clips inside the frame: red 2.3 %, green 0.4 %. That detail is
  lost. If the lens, light or camera changed since the film base was
  measured, Measure it again; otherwise shoot shorter."
  ("in the shot" instead of "inside the frame" when there was no guide.)
- The next Shoot clears it.
- The capture log's line for the shot gets `clip` {slot: {colour: share}}
  (`capture_log.note(path, "clip", value)`), so later measurements can tell
  which shots clipped.
- A file that can't be read says nothing.

## Tests (`tests/test_capture_clip_warning.py`)

- A synthetic mosaic: unclipped noise at 0.85 → nothing; a patch inside the
  guide clipped at 0.93 (below white) in red → red only, share right; a clip
  outside the guide → nothing (with a guide), found with no guide.
- An unclipped maximum (one photosite at the top) is not a clip; a top under
  `CLIP_FROM` is not a clip.
- Capture: a USB file during a sequence warns only at the end; an IR file is
  not read; a Measure file is not read; the next Shoot clears the warning;
  the log line carries `clip`.

## Measured (2026-11-22)

- Speed: 0.3–0.4 s a file on his 51 MP RAFs (2 × 2 pattern read by slicing;
  masks and a histogram took 3.6–4.1 s).
- His shot R0023 005 (after the film base was measured again): nothing
  inside the guide in any colour, nothing in the middle 60 %; with the
  guide grown to the whole sensor red is "clipped" on 7.7 % (the open light
  round the carrier), which is why the fallback is the middle 60 %.
- All 140 non-IR RAFs he has sent, no guide: 11 flagged; the four looked at
  (R0020 001 green, R0001 001 blue, R0001 006 red, a 35 mm DSCF) all clip
  inside the picture itself (sky / rocks / leaves), none false.
