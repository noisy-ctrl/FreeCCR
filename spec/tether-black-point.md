# Setting the roll's black point while tethering

Status: built 2026-10-29 (the user's request and answers).

## Problem
A Hot Folder Tether session asks once, on its FIRST new capture, for the
roll's black point to be set from that frame (the film lead:
`MainWindow._prompt_bwpoint_for_session`). The user's first capture was
taken with the light off, so the frame held no film base to sample, and the
prompt never came again that session. The question also opened over the main
window, which sat behind the Live Capture Monitor.

## Goals
- The prompt waits for a LIT capture: one too dark to show film base doesn't
  count, and the next one is checked.
- A Black Point button, available whenever the light is ready, in the tether
  banner (main window) AND in the Live Capture Monitor (the user's choice:
  both).
- Prompt and button bring the main window to the front, where sampling is.

Non-goals: converting tethered captures from each frame's own rebate (Auto
black point) instead of a roll black point; changing how sampling works.

## Lit or dark
`core/lead_frame.py`: `lit_level(img)` = the 99.5th percentile of the
brightest channel of the capture's decoded, unconverted data
(`img.resized_raw`, every 2nd pixel), as a share of full scale (uint16 →
65535, uint8 → 255, float → 1). `is_lit(level)`: `level >= LIT_MIN` (0.03),
or True when it couldn't be measured (`None`): an unmeasured frame never
blocks the prompt.

Basis (the user's 87 RAFs, 2026-10-29): on the RAW mosaic, the dimmest lit
frame's brightest 0.5 % sat at 2.2 % of full scale (single-colour trichrome
frames; most film-base frames 6–68 %). Through the load path (`resized_raw`,
which is what is measured) the same frames read far higher, see §Measured. A
frame with the light off is black level plus read noise, close to 0. LIT_MIN
sits between the two.

Measured where: `TetherWatchWorker._finish` sets `img.lit_level` BEFORE any
conversion (the worker thread, on data it already has; no extra decode). A
trichrome merge is measured on the merged frame.

## UX
- **Prompt**: in `_on_capture`, the first new capture of a session that
  `is_lit` opens the prompt (`_tether_prompted` set then). A dark one shows the
  hint "Captured NAME: too dark to show the film base (light off?). FreeCCR
  will ask on the next lit frame." and leaves `_tether_prompted` unset. The
  prompt raises and activates the main window first.
- **Button, tether banner**: "Black Point…" beside the scan type, signal
  `TetherBanner.blackPointRequested` → `MainWindow.start_roll_black_point()`.
- **Button, monitor**: "Set Black Point…" under Shoot in the Capture section,
  enabled while the main window has images; calls
  `main_window.start_roll_black_point()`.
- `start_roll_black_point(img=None)`: the frame is `img`, else this session's
  newest LIT capture (`_tether_last_lit`), else the image on screen. Selects
  it, raises/activates the main window, enters black-point sampling
  (`image_preview.set_bwpoint_mode("black")`) and shows the usual hint. No
  image at all: a hint "Capture the film lead first, with the light on." and
  nothing else. Returns whether sampling started.

## Data
`img.lit_level` (float or None), session-only, not in the catalog.
`MainWindow._tether_last_lit` (reset with the session like
`_tether_lead_img`).

## Tests (`tests/test_tether_black_point.py`)
- `lit_level`: uint16 / uint8 / float scaling, grey and colour, None when
  there is no data; `is_lit` at and around LIT_MIN.
- The worker sets `lit_level` before converting.
- A dark first capture: no prompt, hint shown, `_tether_prompted` unset; the
  next lit capture prompts, and it is the lead.
- `start_roll_black_point` picks the newest lit capture over a later dark
  one, falls back to the current image, and with no images only hints.
- The banner's button and the monitor's button call it.

## Measured
Fourteen of the user's RAFs through `create_images_for_path` (the tether
worker's own load), `lit_level` of `resized_raw`, including the five dimmest
by the mosaic measure above (2.2–4 % there): 0.20 (one file, named by
Capture One), 0.35–0.42 (seven, all five dimmest among them) and 0.59–0.97
(six). The decode is fixed-scale (no auto-brightness: white level to
16 bit, the profile's channel gains), so a dark frame stays dark. Not
measured: an actual light-off frame. Read noise of a few codes in 14 bits
times those gains puts one near 0.001–0.005, so LIT_MIN = 0.03 is ~7× under
the dimmest lit frame and well over that estimate. A light-off RAF from the
user's setup would confirm it.

## Banner layout
The note ("No black point set — …") now takes the room left in the banner
and is clipped when narrow (its full text in the tooltip): with the new
button the banner's fixed width would otherwise have reached 1274 px and set
the main window's minimum width. Minimum now ~720 px.
