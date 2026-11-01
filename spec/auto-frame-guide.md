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

## Revision 2026-11-01 (3): the Format list and the outline's right-click (shipped alone)
Two parts of revision (2) that don't depend on its finding, built on their own
while (2) stays parked (the user, 2026-11-01: "The format drop down box is also
missing from the menu?"):
- **Format** in Capture's toolbar, left of Camera zoom (`format_combo`,
  `_format_action`): Free and the film formats of `core/film_formats`, compact
  in the bar (8 characters wide, the open list as wide as its longest entry).
  Picking one is the Frame guide menu's Format (`_set_guide_format`: the guide
  takes the format's shape at the same area); the list follows the guide's
  format wherever it is set (`_sync_format_combo` in `_set_guide` and when the
  view state loads). With Follow the Frame on, the format is the shape the
  follower checks finds against, as before.
- **A right-click on the guide's outline** (within `OUTLINE_PX` 8 px of an
  edge, inside or out; not while magnified or with the guide off) emits
  `LiveCanvas.guide_menu_requested` → the Frame guide menu (Follow the Frame,
  Format, Size, …); elsewhere on the picture the camera-zoom menu as before.
Tests: the list sits left of Camera zoom, sets the guide's format and shape,
follows the menu's Format; right-clicks on two edges open the guide menu,
inside / well outside / guide off open the picture's.
