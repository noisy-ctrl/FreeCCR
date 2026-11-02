# Unfinished work

A running list of what is started but not done, or waiting on something
(the user, 2026-11-01: "Keep a list on unfinished things"). Newest first;
remove an entry when it ships.

## Waiting on the user
- **The light-colour check rejects some of the user's trichrome frames**
  (found 2026-11-02 on the Portra scans): `LIGHT_SHARE_MIN` 0.60 is the share
  a frame's own channel must reach. His green frames read 0.54–0.63 and blue
  0.55–0.64 (Bayer crosstalk), so 7 of 51 frames would be "not one colour"
  in Open's 3-way merge (`triplet_plan`) and the tethered trichrome check
  (`judge_triplet`); every one is plainly R, G or B in order. A fix (largest
  channel by a margin over the next, measured on these files) is offered,
  not made.
- **Portra 400 Lab look: the second lab's frames** (148684250): 2 of its 3
  usable frames agree with the look (CIEDE2000 median 0.66, 1.00 with their
  own Channel Levels), the third doesn't (2.16, it needs −0.5 density). Not
  added to the fit (one lab's rendering per look); add them if the user
  wants a both-labs look.

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
- **Film base clipped on 5 of 17 Portra triplets** (2026-11-02; DSCF0001,
  0007, 0062, 0068, 0074: the longer exposures): the rebate sits at the clip
  in every channel, so neither Convert nor the look fitter can read that
  frame's own base (Convert falls back to the sampled black point scaled by
  exposure). 9 more frames have 0.5–1.6 % of the picture clipped in a channel.
  Ask whether those were "Expose each frame for itself" shots.
- **The Portra 400 Lab look and the new Colour Organics** on his Mac.
- **Film base Measure on a 35 mm roll** (2026-11-01): the cause was the base's
  grain (9 % photosite to photosite at 1:1, measured on the user's kept shots);
  evenness is now judged on 16-px block means (2.6–3.2 % on those shots).
  Confirm a Measure completes.
- **The light at Capture's close / the Quit? box** (2026-11-01): the OFF bytes
  are now drained before the port closes; confirm the Scanlight goes dark.
