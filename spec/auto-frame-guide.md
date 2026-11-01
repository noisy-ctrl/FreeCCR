# The frame guide finds the frame by itself

Status: specified and built 2026-11-01.

The user (2026-11-01, with two screen recordings of Capture: the film wound
through the carrier, and focusing on the rail): "Frame guide is automatic,
follows the frame with focus breathing, linear rail inaccuracies. When
changing the frame in the film carrier, it recognises that a complete frame
isn't in shot."

## Goals
- With the frame guide on, the guide sits on the frame by itself and follows
  it as it grows, shrinks or shifts (focus breathing, rail steps).
- While the film is wound on (no whole frame in view), the guide waits: it
  is drawn dashed and dim with a note, and the frame's exposure estimate
  pauses (so Expose each frame for itself shoots at the film base's
  exposures and says why).
- The user's own placing wins: dragging the guide, or a nudge / reset from
  its menu, turns following off; the guide's right-click turns it back on.
- In the user's setup the carrier masks everything but the frame: the film
  base shows only between frames. The base seen then is remembered for the
  exposure estimate.

Non-goals: finding several frames at once (a strip's neighbours); reading
frame numbers; moving the rail or the film.

## What the recordings show (measured)
1024 × 768 live view at 46 fps. The carrier's mask is black (level 0–2);
the frame fills almost all the view, with a 1–3-row rebate strip at some
edges. Winding on, the gap between frames is clear base (B G R ≈ 67 140 231,
the same to ±2 levels in every picture of the wind while the picture beside
it changed: the live view doesn't re-brighten per picture, so a base seen a
moment ago is still the base). Focusing changes the frame's size by ±2 % and
moves it by up to 1.5 % of the view; then it runs off the right/bottom edge.

## Finding the frame (`core/live_frame.find_frame`, Qt-free)
On a copy whose long side is 384 px:
- HOLDER: darker than black + ¼ of (brightest − black), when that spread is
  ≥ 40 levels (else there is no mask in view);
- BASE: the film's brightest level × 0.88 or more AND flat (local gradient
  over local level < 0.06);
- PICTURE: the rest of the film.
The rows and columns that are mostly picture give a rough box; each side is
then found in 8 pieces (the first run of 3 picture rows coming in from
outside, within 6 % of the rough edge), a line is fitted through each side's
pieces (outliers > 2 px dropped once; ≥ 4 pieces, RMS ≤ 2.5 px), and the four
lines' corners give the guide (centre, width, height, turn; in the live
frame's fractions as `LiveCanvas.guide` holds them).
- `between`: a band of clear film (rows, or columns, in the frame's own axes,
  ≥ 60 % base, ≥ 1.5 % of the frame) with ≥ 10 % of the frame on BOTH sides:
  the film is between frames. (A band against an edge is its rebate.)
- `cut`: the frame's outermost 2 rows / columns at a side are mostly
  picture: it runs off the view there.
- `complete`: found, not between, not cut.
- `base8`: the median colour of the base pixels when they are ≥ 0.4 % of the
  view and unclipped (< 250).
~12 ms a picture (median on this machine).

## Following (`GuideFollower`)
Per find (4 times a second): a whole frame, or a cut one with no clear film
across it, is followed:
- a small move (centre within 3 %, size within 5 %) is eased in by half;
- a larger one is taken when two finds in a row agree within 2 % (the film
  moving gives finds that don't);
- a frame smaller than the last whole one by more than 4 % in a dimension
  the view doesn't cut is PART of a frame (the gap has just gone past an
  edge) unless it stays that size 2 s;
- with a film format set on the guide, a whole frame whose shape is off the
  format's ratio by more than 5 % is part of one.
Otherwise `state` is "waiting" (the last guide is kept) with the reason.
Measured on the recordings (4 finds a second): winding on, waiting from the
first find with the gap in view to the first whole frame (13 finds), and
back to following on the new frame; focusing, following throughout (width
0.938–0.970 of the view, centre x 0.514–0.531).

## The monitor
- `live/guide_auto` (default on); the Frame guide's right-click gets
  **Follow the Frame** (checkable) first. Dragging the guide, Size /
  Position / Rotation / Reset from its menu, turn it off with a caption that
  says how to turn it back on. Choosing a film format keeps it on (the
  format's ratio is then used, above).
- A 250 ms timer runs `find_frame` on the newest full live picture while the
  guide is on, following is on, the camera is Whole, the light isn't asleep
  and no Shoot / measuring is running. A changed guide goes to
  `canvas.set_guide` (not saved each time: it is found again next session).
- Waiting: `canvas.set_guide_waiting(reason)` draws the guide dashed and dim
  with "No whole frame in view" beside it; following clears it. Waiting →
  following is a new frame: `_frame_changed()` (a metered reading and an
  unused Use for This Frame were the old frame's).
- The exposure estimate: while following is on and the follower is waiting,
  no estimate ("No whole frame in view."), so Expose each frame for itself
  falls back to the film base's exposures and says so.
- **The remembered base**: each find's `base8` is kept with the camera's
  shutter and ISO, the light's levels and the camera zoom
  (`_remembered_base`). `live_estimate(..., remembered=)` uses it after a
  Film Base click and a base beside the guide (source "remembered"), only
  under the same key, and not if it is darker than the frame's thinnest
  part (then the light or the camera changed: it says so).

## Tests
`find_frame` on synthetic live pictures: a frame in a black mask (with a
thin rebate) found to within 1 px and its turn to within 0.3°; the gap
between two frames → between; a frame cut by the view → cut; base8 from the
gap. `GuideFollower`: eases small moves, needs two agreeing finds for a
large one, waits on a part-frame after a whole one, the format ratio, cut
frames followed. On the user's recordings (when present on the machine, as
extracted frames; skipped otherwise): the sequences above. Monitor: the
timer moves the guide; waiting draws dashed and stops the estimate; a drag
turns following off; the menu item; the remembered base feeds the estimate
and is dropped under a different exposure.

## Refinement (second pass)
- The find runs on the GUI thread: 12 ms every 250 ms is ~5 %; the picture
  is the feed's newest FULL frame (`last_full_frame`), never a view-zoomed
  one, so the window's zoom doesn't move it.
- `GuideFollower.update(find, now, ratio, frame_size)`: `ratio` from the
  guide's film format (`film_formats.ratio`), `frame_size` the live
  picture's width and height.
- A cut frame keeps the guide at the view's edge on that side (it is what is
  visible); the meter and histogram read inside the picture only anyway.
- The remembered base key uses the camera readout (`_exposure`), which can
  lag a change by up to 3 s; a base remembered under the old values is then
  refused by the "darker than the frame" check in the common case (more
  exposure makes the frame brighter than the old base).

## Revision 2026-11-01 (2): the carrier's opening first, steady, and a Format list
**Status: PARKED, not merged** (the user, 2026-11-01: "Forget about the frame
tracking for now. We'll come back to that."). Built so far on branch
`fix/auto-guide-mask-first`: mask-first finding with rebate trimming,
`FrameFinder` (remembered rebate widths), the steady follower and format
shaping; synthetic rebates of 10 px trimmed to ±1 px, the recordings pass.
Not done: the Format toolbar list, right-click on the outline, 35 mm
(sprocket holes: see the user's 2026-11-01 screenshots), timing (20–40 ms
at 640 px, on the GUI thread), tests for the new cases.

The user, after using it: "Follow the frame feature seems to be grabbing the
film border and too much of the mask. It also seems to be changing the format
when I scroll through the other frames, mistaking where the frame is. But I
like the feeling generally. The noise in the image seems to also make the
frame guide change every fraction of a second when the film, lens and camera
isn't moving. I wonder if there should be a 'Format' drop down window to the
left of the Camera Zoom drop down. … Maybe also, hovering over the frame
guide itself (the outline) and right clicking - should allow you to change
format also."

### Why the first version failed (measured on the recordings and screenshots)
- **Clear film can't be told from the picture by colour.** In the user's
  live view the rebate reads B 65 G 139 R 230; a bright tree trunk in the
  picture 64 / 139 / 230. The max-channel test (`BASE_NEAR`) called both base
  and both picture alike. The trunk became a "gap" edge (screenshot 3: the
  guide's left edge on the trunk), and the rebate, blurred into the mask's
  edge by the 5×5 texture blur, counted as picture (screenshot 2: the guide
  on the opening, rebate and all).
- **Jitter**: every find was taken (eased by half), so noise of a fraction
  of a working pixel moved the guide 4 times a second.

### Finding (mask first)
1. **The opening.** Work at `WORK_SIDE` 640 (was 384: a 10 px rebate at 1024
   was 4 px). Holder = the dark mask, as before. Each side of the opening is
   found in 16 slices coming in from the view's edge: a slice that STARTS in
   the mask gives an edge (sub-pixel: where the slice's level crosses half
   way between the mask and the film just inside); a slice that starts on film
   votes "this side runs off the view". A robust line (Theil–Sen, then a
   least-squares refit on the points within 1.5 px) per side; a side is found
   with ≥ 6 agreeing slices, cut with ≥ half its slices starting on film.
   Only the outermost dark-to-film step counts, so nothing inside the
   picture can move the opening.
2. **The rebate, trimmed by colour on all three channels.** Inside each found
   side: skip the mask's edge (`EDGE_SKIP`, ~0.6 % of the long side), take
   each slice's colour there; the side has a rebate when those colours agree
   across the side (≥ 60 % of slices within 10 % per channel of their
   median) and are bright (max channel ≥ 0.75 of the film's p99). Each slice
   then runs inward while its colour stays within 12 % per channel of that
   rebate colour; the side's width is the 30th percentile of the slices'
   runs (a picture area the rebate's colour, like the trunk, only makes ITS
   slices longer), the line is fitted through the slices that agree with it
   (± max(1.5 px, 25 %)), else the opening's line moved in by the width. A
   side with no rebate is the opening's line.
3. **A band wider than a rebate** (`REBATE_MAX` 6 % of the frame's size that
   way) is clear film beyond the picture: that side goes in `gap_sides` (part
   of a frame: the follower waits, as before).
4. **Between frames**: clear film ACROSS the trimmed frame, judged with the
   rebate's own colour (all channels, within 10 %) and flatness, and
   `GAP_SHARE` raised to 0.85 (a gap is clear all the way across; a trunk
   isn't).
5. **base8** is the rebate colour (the median of the sides that had one), or
   a gap's, never a bright picture area's.
6. No mask in view (≤ 1 side found): the picture-first method as before.

### Following
- **Steady**: the guide is the per-value MEDIAN of the last 5 agreeing finds,
  and it moves only when that median leaves a dead band (centre 0.25 % of the
  view, size 0.35 %, turn 0.12°); then it goes straight there (the median
  has already smoothed it, so no easing). Measured target: no movement over
  the recording's still stretches.
- Large moves, part frames and waiting are unchanged.
- **Format**: with a film format set, the found frame is given the format's
  shape (largest rectangle of that ratio inside it, same centre and turn) when
  it is within `RATIO_TOL` (now 8 %) of it; further off it is part of a frame,
  as before. Free keeps the found shape.

### Capture
- **Format** list in the toolbar, left of Camera zoom: Free and the film
  formats (`core/film_formats`); always shown; picking one sets the guide's
  format (the same as the Frame guide menu's Format, which it stays in step
  with).
- **Right-click on the guide's outline** opens the Frame guide menu (Follow
  the Frame, Format, …) instead of the camera zoom menu; elsewhere on the
  picture the zoom menu as before.

### Tests
Synthetic: a rebate of 3 and 10 px is trimmed (guide on the picture, ≤ 1 px);
a picture area in the rebate's exact colour touching one side doesn't move
that side; a vertical stripe in the base colour (the trunk) is neither an
edge nor a gap; a wide band on one side → `gap_sides`; still frame + noise →
the guide doesn't move over 20 finds; format shaping. Recordings (with
`FREECCR_AFG_FRAMES`): as before, plus the guide's edges lie inside the
rebate on r2 and it doesn't move over still runs; crops of the two
screenshots: the guide is inside the opening and its left edge isn't on the
trunk. UI: the Format combo sets the guide's format and follows the menu; a
right-click on the outline opens the guide menu.

### Refinement (second pass)
- Slices cover the middle 80 % of the rough opening (film rows / columns ≥
  half film), so a rounded corner of the mask never feeds a side.
- The opening's corners come from the side lines as before; a CUT side is
  the view's own edge (x = 0 / w, y = 0 / h), so a frame focusing bigger than
  the view is still followed with `cut` set.
- The rebate colour per slice is the mean of 3 positions after the skip; the
  run inward stops at the first 2 positions in a row off the colour, so the
  picture's edge halo (sharpening in the camera's live view) ends the run.
- The dead band is in the follower, so the canvas, the frame estimate and
  "new frame" logic see a guide that only moves when the frame does.
- `find_frame` stays one call (~ ms budget: under 25 ms at 640 on the user's
  1024 × 768 live view).
