# Your frame guide, followed, and the crop it gives

Status: specified 2026-11-07 (the user's request). Revises Follow the Frame
(`spec/auto-frame-guide.md`, revision 1 is what the app runs; revision 2 is
parked) and adds the crop.

The user: "Can the reference frame in capture help inform the autocrop?" and,
on the plan: "Select by hand but have it track the frame as focus etc. (in
this instance it shouldn't change much). So basically a revision to follow
the frame and it informs crop. It's tethered using libgphoto2 so there's no
hud in the way?"

Why a revision: revision 1 placed the guide by finding the picture, and the
user found it "grabbing the film border and too much of the mask", "changing
the format when I scroll through the other frames, mistaking where the frame
is", and moving with the noise when nothing moved. Now he places the guide;
FreeCCR only keeps it in place on the frame.

## Goals
- You place the guide (drag, the menu's Size / Position / Rotation, Format,
  Reset). With Follow the Frame on, it then moves with the frame as focusing
  or the rail changes its size and position, and stays put while the film is
  wound on (the carrier doesn't move).
- Steady: no movement from noise when nothing moves.
- Every shot Capture takes carries the guide to its files; auto crop on those
  frames crops to the guide, snapping each side to an edge it finds close by.
- Frames without a guide (Open, other routes, the setting off) crop exactly
  as before.

Non-goals: finding the frame by itself (revision 1's finder no longer places
the guide); several frames at once; reading the guide for exposure (the
frame estimate keeps reading inside the guide as it does).

## 1. Following your guide (`core/guide_tracker.py`, Qt-free)
In the user's carrier the black mask is the carrier's opening. Focusing and
rail steps scale and shift everything, mask and film together; winding moves
only the film inside the opening. So the guide follows the OPENING, not the
picture.

**The opening** (`find_opening(bgr)`, from parked revision 2's mask-first
finder): on a copy whose long side is 640 px, holder = darker than black + ¼
of (brightest − black) when that spread is ≥ 40 levels; each side found in 16
slices coming in from the view's edge (the first run of 3 film pixels; the
crossing found to a fraction of a pixel), a robust line per side (Theil–Sen,
least squares on the points within 1.5 px, ≥ 6 slices, RMS ≤ 1.2 px). A side
whose slices mostly start on film runs off the view (cut). Reported per side
as a position in view fractions: top / bottom at the view's middle column,
left / right at its middle row. None with fewer than 2 sides.

**The model**: scale `s` about the view's centre plus a shift `(dx, dy)`, in
view fractions: `f' = 0.5 + s·(f − 0.5) + d`. No turn (the camera doesn't
turn when focusing). From the sides seen both when the guide was anchored
and now:
- `s` from each opposite pair seen (left + right: the opening's width ratio;
  top + bottom: its height ratio), weighted by the span; no pair → the last
  `s`;
- `dx` the mean over the left / right sides seen of `now − (0.5 + s·(ref −
  0.5))`; none seen → the last `dx`; `dy` the same;
- the guide: centre mapped by the model, width and height × `s`, its angle
  and format kept.
A side first seen after anchoring is added to the reference through the
current solution (back-mapped), when that solution came from ≥ 2 sides.

**Steady**: a solution with `s` outside 0.85–1.15 or a shift over 0.15 is
ignored. A move from the last accepted one of more than 5 % in `s` or 0.04
in shift is taken only when the next solution agrees within 1 % (two
agreeing). The guide shown is the per-value median of the last 5 accepted
solutions and moves only when that leaves a dead band (centre 0.25 % of the
view, size 0.35 %).

**States**: `following` (a side in common seen); `lost` after 2 ticks with
none: the guide is held, drawn dashed with "Not following: no edge of the
carrier in view". Anchoring with no opening in view keeps the guide and
anchors at the first opening seen.

## 2. Capture
- **Follow the Frame** (`live/guide_auto`, default on; the guide's menu)
  now means "keep my guide on the frame". Tooltip: "The guide stays where you
  put it on the frame and moves with the frame as focusing changes its size
  or position. Winding on doesn't move it: it follows the carrier's opening."
- Placing the guide no longer turns following off: a drag, Size / Position /
  Rotation, Format, Reset and turning following on all ANCHOR the tracker on
  the guide as it now is (`GuideTracker.anchor`).
- The timer (`_auto_guide_tick`, 250 ms) runs while the guide is shown and
  following, the camera is Whole, Capture is awake and no Shoot / measuring
  runs (as before). `find_frame` now runs only once a second, for the
  remembered film base (`base8`).
- The live estimate no longer pauses "between frames": the old follower's
  waiting state is gone; a new frame is still noticed by the meter's
  `NewFrameDetector` (`FrameResult.new_frame` → `_frame_changed`).
- The canvas's dashed label says the reason it is given.

## 3. The guide goes with the shot (`core/capture_guide.py`, Qt-free)
- **Recorded at Shoot** (`shoot()`, before the sequence starts), when the
  guide is shown AND it is anchored (following with an opening) or was placed
  this session, and it isn't the untouched default box (`GUIDE_DEFAULT`, 60 %
  of the view: a guide shown for the histogram but never placed would
  otherwise crop every shot to it): `{guide, live (a grey copy of the live picture, long side
  480), live_size, following, t}` → `capture_guide.begin_shot(record)`.
  `_on_sequence_finished` → `end_shot()`: the record stays active
  `GRACE_S` (60 s) for files that arrive late; the next Shoot replaces it.
- **Tied to files**: over USB, `UsbCamera.file_saved(path)` →
  `capture_guide.remember(path)` (the shot's own file, exact). For the
  Scanlight's shutter cable (files from the camera's own software), the main
  window stamps each hot-folder file as it becomes ready (`stamp(path)`: the
  active record, if the path has none). At most 300 paths are kept.
- **On arrival** (`TetherWatchWorker._finish`, before the inherited look and
  the conversion): `lookup(paths)` (the first of the frame's files that has
  one) → `map_guide(record, img.resized_raw)` → `img.capture_guide` (catalog
  key, additive): `{cx, cy, w, h (fractions of the scan), angle, how, score}`
  or None. A frame with a guide does not inherit the previous frame's crop
  (it gets its own from the guide).

### Lining the live picture up with the RAW (`map_guide`)
The libgphoto2 live view is the camera's clean picture (no overlays), and it
should show the RAW's field, but that isn't assumed:
1. Both as log luminance, normalised to their 1st–99.5th percentiles, then
   the gradient magnitude of a light blur, at 320 px on the long side.
2. **Orientation**: the live picture turned 0 / 90 / 180 / 270°; only turns
   whose shape matches the scan's within 3 %; score = the cosine of the two
   (signed) gradient fields, so a picture turned half round scores low even
   where the mask is symmetric. A RAW is decoded as the sensor sees it
   (`user_flip=0`), like the live view, so no turn is taken whenever it
   scores ≥ 0.30; otherwise the best turn needs ≥ 0.30 and to beat the next
   by 0.08 (other decoders may store a turn).
3. **Refine**: ECC (`cv2.findTransformECC`, affine) of the turned live
   gradients onto the RAW's, from identity. Kept when its correlation is
   ≥ 0.50 and the affine is near a similarity (scale 0.9–1.1, shear < 2 %,
   turn < 2°): `how` "matched". Otherwise the orientation alone is used when
   its score is ≥ 0.45 (`how` "assumed"); else no guide.
4. The guide's four corners go through the turn and the affine; the box is
   their centre, mean side lengths and the top edge's angle (brought to
   ±45° by swapping width and height).
A line is printed per capture ("Capture guide for NAME: matched 0.83").

## 4. The crop (`core/auto_crop.detect_guided`)
Settings → Auto crop → **Crop to Capture's frame guide** (default on,
`convert/crop_from_guide`, `ccr_backend.crop_from_guide`). With it on and a
`capture_guide` on the frame, `CCRBackend.detect_frame_crop` (what Convert's
crop, Auto Crop, Auto Crop All and the Crop panel's Auto all use) calls
`detect_guided(raw, guide, priors)`:
1. Auto crop's own detection, as without a guide. The guide is NOT given to
   it as a prior: tried first, a size prior made candidate boxes of the
   guide's size and a side placed 4 % inside snapped to an "edge" only the
   prior had put there.
2. **Each side** of the guide snaps to the detection's matching side when
   that is within `GUIDE_SNAP` (2.5 %) of the guide's size on that axis
   (auto crop's own box sits ~1 % inside the frame's edge: its inset and the
   edge's blur, measured 0.7–1.1 % on the synthetic 645 scan)
   (measured at the side's middle, in the guide's own turned frame);
   otherwise it stays where you put it.
3. All four snapped and the angles within 0.5° → the detection exactly;
   otherwise the guide's angle with the snapped sides.
4. No usable detection → the guide's box.
`FrameCrop.learned = "guide"` ("from Capture's frame guide" in the summary);
confidence high when all four sides snapped to a high-confidence detection,
else medium (always applied). A guided crop is never learned from (Done on
it teaches nothing) and the roll check leaves it alone.
A guide smaller than 35 % of the scan on a side, or mostly outside it, is
ignored.

## Tests
- `guide_tracker`: a synthetic carrier opening with a picture: scaled 2 %
  about the centre and shifted, the guide follows to 0.1 % of the view;
  the picture moving inside a still opening (winding) doesn't move it; noise
  over 20 ticks doesn't move it; only left + top seen keeps `s` and follows
  the shift; a large jump needs two agreeing; no opening → lost after 2
  ticks; a side appearing later is used.
- `capture_guide`: begin / remember / stamp / lookup / grace / bound.
- `map_guide`: a synthetic RAW (the live picture ×N, a 1.5 % different field
  and shift, another tone curve, noise) → corners within 0.3 % of the scan;
  the RAW turned 90° → found; a different picture → None.
- `detect_guided`: a synthetic scan with a frame in a holder: a guide a
  few px off snaps to the detection; a side placed 4 % inside stays; no
  frame → the guide; frames without a guide → `detect_frame` unchanged; the roll
  check skips it; learning skips it; the setting off → unchanged.
- Capture: a drag anchors and keeps following on; the menu tooltip; Shoot
  records the guide; a USB file is remembered; the worker sets
  `img.capture_guide`; the catalogue round-trips it; an inherited crop is
  not applied to a guided capture.

## Refinement (second pass)
- `find_opening` returns side positions in view fractions plus the raw line
  fits; the tracker keeps only positions (resolution independent: the USB
  live view and HDMI differ in size).
- The record's live copy is grey uint8 (≤ 480 × 360 = 170 kB): kept only in
  memory, never in the catalogue.
- `map_guide` runs on the tether worker thread, ~20 ms; `detect_guided` adds
  nothing but the snapping to a detection that was running anyway.
- The guide's angle and the crop angle share one convention (Qt
  clockwise-positive in y-down coordinates: `p.rotate(angle)` draws the
  guide, `apply_crop_to_image` samples `C + R(angle)q`), so corners map
  straight across.
- The tracker's solution is relative to the anchor; re-anchoring resets the
  history, so a placed guide never jumps back to an older median.
- A guide placed while the tracker is lost becomes the new reference at the
  next opening seen (identity at that moment).


## Revision 2 (2026-11-07): snapping onto a new frame
The user, with a screen recording of a 35 mm roll: "It doesn't quite snap
to a new frame when movement is settled. Being able to do so would be
ideal." Following the carrier's opening keeps the guide still while the
film is wound on, but the next frame doesn't land exactly where the last
one did: on the recording it landed 4.5 px (left edge) / 6.2 px (right
edge) further along in the 1024 px live view (0.55 % of the frame), so the
guide's right side ran onto the clear film.

**The frame's own edges** (`guide_tracker.frame_edges`): beside each side of
the guide, lines across the middle 80 % of that side (columns for left /
right, rows for top / bottom), from 6 % inside the guide to 10 % outside
(of its size on that axis), at up to 1280 px. Clear film (the gap between
frames, or the rebate between the sprocket holes and the picture) is a run
of ≥ 3 lines that are even (IQR ≤ 6 % of the line's median of the brightest
channel), bright (≥ 0.3 × the view's 99.5th percentile) and within 6 % of
the brightest such line; coming in from outside, the first such run's inner
end is the frame's edge, placed where the line median crosses halfway to
the picture's level beside it, provided the picture is at least 12 %
darker. Sprocket-hole rows aren't even and the black mask isn't bright, so
neither counts. ~40 ms; only when a guide is placed or a new frame settles.

**Your margins**: anchoring with the live picture (`anchor(..., frame=)`,
which Capture now passes) records, per side found, how far inside the edge
you put the guide (`GuideTracker.rel`). **Snapping**
(`GuideTracker.snap(frame, opening)`): the edges are looked for within 8 %
(of the guide's size) of where your margins put them; each axis moves by
its sides' shift, the mean when both sides were found, and NOT AT ALL when
those two disagree by more than 0.4 % of the view (a shadow as clear as the
film beside an edge reads as film: tested, the guide stays rather than
jump) or the move is over 8 % of the guide; a guide turned more than 3° isn't
snapped. Size, angle and format are kept, the result becomes the tracker's
new reference (the margins stay yours).

**When**: the picture moved and settled. `guide_tracker.SettleWatch` gets
each of Capture's looks (4 a second, `_auto_guide_tick`, so only while
following, the camera at Whole, no Shoot / measuring / sleep): the mean
difference of 64 × 36 grey thumbnails between looks ≥ 3 levels is
movement, < 2.5 is still, and three still looks after a movement (0.75 s)
snap once. Measured on the user's three recordings: still < 2.5, focusing
3–9, winding on 15–33. The feed's new-frame test (`NewFrameDetector`) was
tried first and can't serve: on 35 mm in his carrier the strip (holes,
film edges, mask) dominates its thumbnail and two frames still correlate
0.87 (it needs < 0.40), so it never fires there (nor, so, does the
sharpness meter's "new frame" reset). Settling after focusing snaps too:
the tracker has followed the scale, the snap takes off what's left. Also
on the guide's right-click **Snap to the Frame** (enabled while following;
says why when it can't). Caption: "The frame guide moved onto the new
frame."

**Measured on the recording** (its live picture cut out of the screen
recording and scaled back to 1024 × 768, the guide placed as in it): the
margins 5.7 / 9.5 / 3.1 / 5.2 px (left, right, top, bottom); on the new
frame the guide moves −5.3 to −5.6 px across and −1.4 px down
(14–22 s, four frames), and mid-wind (11–12 s) no side is found. Run whole
as Capture runs it (following + SettleWatch + snap, from the first frame):
two small snaps after focusing (5.25 s, 7.75 s), one after the wind
(14.25 s, −5.2 px), ending 5.8 / 8.8 px inside the new frame's left / right
edges against the 6.3 / 9.9 px placed. On the
user's two 120 recordings of 2026-11-01 (the carrier masks all but the
frame) only the left rebate is ever found, so a snap there moves only along
that side's axis, by that side.

Tests (`tests/test_guide_tracker.py`, `tests/test_auto_frame_guide.py`): a
synthetic 35 mm strip in a carrier (mask, film edges, sprocket holes, frames
with clear film between them): margins found to 0.5 px; frames landing 5 px
short and 7 px on → the guide follows to 0.5 px, the other axis within
0.3 px; the same frame or noise → no move; a side the carrier hides → the
other side alone; a shadow as clear as the film → no move; no margins / 13 %
away / turned 4° → no snap; `SettleWatch` says once after a move and
three still looks, never for noise; Capture snaps after settling only with
following on, and its menu item.
