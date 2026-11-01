# Unfinished work

A running list of what is started but not done, or waiting on something
(the user, 2026-11-01: "Keep a list on unfinished things"). Newest first;
remove an entry when it ships.

## Waiting on the user
- **Colour Organics overhaul**: waiting for the FreeCCR scans the user will
  send. After it, run the FULL test suite once (the user asked for no full
  runs until then, to save usage; only the touched test files run meanwhile).
- **A Portra Film Look**: to fit with `tools/fit_look.py` from the user's
  FreeCCR scans + lab scans when they arrive; it gets headroom by default
  (`spec/film-look-lut.md` revision 4).

## Parked
- **Follow the Frame, revision 2** (`spec/auto-frame-guide.md`, branch
  `fix/auto-guide-mask-first`, not merged; the app runs revision 1). Built:
  the carrier's opening first, the rebate trimmed by colour on all three
  channels (10 px rebates to ±1 px on synthetic frames), remembered rebate
  widths (`FrameFinder`), a steady follower (median of 5 finds + dead band),
  film-format shaping; the user's two recordings pass. Not done:
  - (the Format list and the outline's right-click shipped on their own:
    revision (3));
  - **35 mm**: sprocket holes are bare light beside the rebate (the user's
    screenshots, 2026-11-01); untested;
  - speed: 20–40 ms a find at 640 px on the GUI thread, 4 times a second —
    move it to a worker or cut it;
  - tests for the new cases (patch / stripe / noise helpers exist in
    `tests/test_auto_frame_guide.py`, unused).

## Known, not yet fixed
- **Live view doesn't come back after Measure** (GFX 50S II, 2026-11-01: 0.0
  fps, the last frame held). Waiting on the user's `usb camera:` Terminal
  lines and a `FREECCR_GPHOTO2_LOG` log of one Measure.
- **The Camera list names libgphoto2's table entry** ("Fuji Fujifilm GFX100")
  where the Shutter row shows the model the camera reports ("GFX50S II").

## To check on the user's machine
- **Film base Measure on a 35 mm roll** (2026-11-01): the cause was the base's
  grain (9 % photosite to photosite at 1:1, measured on the user's kept shots);
  evenness is now judged on 16-px block means (2.6–3.2 % on those shots).
  Confirm a Measure completes.
- **The light at Capture's close / the Quit? box** (2026-11-01): the OFF bytes
  are now drained before the port closes; confirm the Scanlight goes dark.
