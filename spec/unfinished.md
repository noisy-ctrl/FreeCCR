# Unfinished work

A running list of what is started but not done, or waiting on something
(the user, 2026-11-01: "Keep a list on unfinished things"). Newest first;
remove an entry when it ships.

## Waiting on the user
- **Portra 400 Lab look: the second lab's frames** (148684250): 2 of its 3
  usable frames agree with the look (CIEDE2000 median 0.66, 1.00 with their
  own Channel Levels), the third doesn't (2.16, it needs −0.5 density). Not
  added to the fit (one lab's rendering per look); add them if the user
  wants a both-labs look.

## Parked
- **Follow the Frame, revision 2's picture finder** (branch
  `fix/auto-guide-mask-first`, not merged): superseded on 2026-11-07 by
  `spec/guide-follow-and-crop.md` (you place the guide; it follows the
  carrier's opening, taken from this branch's mask-first finder). The rest
  of the branch (rebate trimming, remembered rebate widths, format shaping)
  is only worth reviving if the guide should ever place itself again.

## Known, not yet fixed
- **Hot Folder Tether: one missed or extra frame shifts every following
  triplet** (2026-11-02): it takes new RAWs three at a time in name order, so
  after a missed frame each triplet is R2 G2 R3, … and is held as "Not red,
  green, blue" (now also "a different picture") until the 30 s stall flushes
  the queue. Open's 3-way merge doesn't have this (it walks by colour and
  picture). A fix: when a triplet fails, hold only the frames before the
  first one that can start a new shot, and keep that one queued.
- **6 tests fail in the full suite** (first full run since 2026-11-01,
  2026-11-02; all 6 fail on 1e767c0 too, so they came with earlier work, not
  the Portra look or Organics): `test_merge_catalog` ×3 and `test_merge_replace`
  ×1 expect a catalogue entry's settings to be exactly what was set, but the
  Details defaults are now written down with them (`detail_defaults.written_down`);
  `test_capture_routes::test_monitor_busy_camera_offers_release_on_a_mac`
  finds the Camera zoom list hidden (the toolbar overflows at the test's width
  since the Format list joined it); `test_export_naming::TestUniquifyInBatch::
  test_case_insensitive` expects "photo-1.jpg" beside "Photo.jpg" on a
  case-sensitive (Linux) disk. Also: one 2,907-test run stopped at 79 % with
  no error (the rest passed when run on their own) — likely memory.
- **The Camera list names libgphoto2's table entry** ("Fuji Fujifilm GFX100")
  where the Shutter row shows the model the camera reports ("GFX50S II").

## To check on the user's machine
- **Live view after Measure on the GFX** (2026-11-07,
  `spec/capture-routes.md` §22): his log showed the camera refusing live view
  whenever its priority was Camera, which libgphoto2's event check sets; the
  backend now sets USB first. Check a Measure (and a Shoot) brings live view
  straight back; the Terminal says "priority set to USB before starting live
  view" once. If it still doesn't, the same two logs again.
- **"New frame: best reset" on 35 mm** (2026-11-07,
  `spec/sharpness-meter-v2.md` revision 3): it now looks inside the frame
  guide. Replayed on his recordings it fires once per new frame and never
  while focusing; check it live with Sharpness on, guide shown, winding on
  a 35 mm strip and a 120 roll.
- **Capture's frame guide and the crop** (2026-11-07,
  `spec/guide-follow-and-crop.md`): built and tested on synthetic pictures
  only. On his rig: does the guide follow focusing and stay put while
  winding on (his carrier, 120 and 35 mm; on 35 mm the opening may show
  sprocket holes), and do the captures line up with their RAWs? Each
  capture prints "Capture guide for NAME: matched 0.xx" (or "didn't line
  up") in the Terminal: the scores tell whether `ECC_MIN` / `ORIENT_MIN`
  suit real pairs. `GUIDE_SNAP` (2.5 %) was set from auto crop's inset on
  synthetic scans. Revision 2 (snapping onto a new frame) was measured on
  his 35 mm screen recording (−5.3 to −5.6 px, matching the frame's own
  shift); check it on 120, where only the left rebate shows.
- **Film Look auto balance (experimental) and the colour-only looks**
  (2026-11-04, `spec/film-look-auto-balance.md`): measured on one lab's nine
  Portra frames only. Needs the re-issued "Portra 400 Lab.cube" (same table,
  plus its `FREECCR_BALANCE` line) in the looks library; a cube without the
  line gets no auto balance. Worth checking on frames the look was NOT fitted
  on, and on the second lab's frames.
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
