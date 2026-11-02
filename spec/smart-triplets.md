# Trichrome sets grouped by their light

Status: built 2026-10-30 (the user's request).

## Problem
Opening files with 3-way merge sorted them by name, took them three at a
time, and refused the whole selection unless the count was a multiple of 3
("needs a multiple of 3 images"). A missing frame, a retake or a stray white
shot stopped the import.

## Behaviour (Open / Open Folder / command line, 3-way merge on)
`core/triplet_plan.py`, called by `CCRBackend._load_merged_triplets`:

1. **Each RAW's light** is read off its mosaic (`scan_type.light_shares`, the
   same check the tether uses): red, green or blue when that channel's share is
   the largest and ≥ `LIGHT_SHARE_MIN` (0.60); otherwise "white or mixed";
   "?" when it can't be told (a monochrome sensor, an unreadable file). Up to
   four files are read at once (`ThreadPoolExecutor`; each holds a whole RAW,
   and on a 2-core machine more threads were slower).
2. **Walk in name order**, collecting a shot until it has all three colours.
   A shot starts again at the "start" colour (R for FreeCCR's R → G → B
   sequence); since a folder can begin mid-shot or another app may shoot in
   another order, the plan is made with each of R, G, B as the start and the
   one with the most complete sets (then the fewest notes; red on a tie) is
   kept.
3. **A colour shot twice in one shot** keeps one take (`pick_best`): an
   unclipped frame (≤ 1 % of the lit channel's photosites in the middle half at
   the ceiling) beats a clipped one, then the sharpest (variance of the
   Laplacian of the lit channel's plane over its mean squared, so exposure
   doesn't read as sharpness); within 3 % the later take (the retake) wins.
   Each frame is scored once (memoised).
4. **Every complete set merges** (ordered R, G, B for `create_images_for_merge`).
5. **Everything else is reported, nothing guessed** — attention entries
   (`last_merge_attention`, with thumbnails), shown greyed out under the
   frames after the load (`MainWindow._cleanup_loader` →
   `_on_needs_attention(entry, hint=False)`) with no warning box:
   - "Frames missing": a shot without all three colours (its files);
   - "Not one colour": a frame lit by white or mixed light;
   - "Unused retake": the spare take, carried with the other two frames of its
     set so **Merge Anyway** makes the frame with it instead;
   - "Not a RAW file": a non-RAW file in the selection.
   The usual right-click choices apply (Choose Files…, Merge Anyway, Load as
   Single Frames, Remove).
6. **No light to go by at all** (every file "?", e.g. a monochrome camera):
   name order, three at a time, as before, with any remainder reported
   ("Left over") instead of refusing the import.
7. Refused up front only when there is **no RAW at all** in the selection
   (`validate_merge_inputs(..., whole_sets=False)`); the "multiple of 3" rule
   still applies where three specific files are chosen by hand (Choose
   Files…, the IT8 wizard).

## Measured
On the user's two trichrome folders (15 RAFs each, five R, G, B shots): five
sets each, in the right order, no notes; with one frame removed, four sets and
that shot's other two frames reported as "Frames missing". Reading the light
and planning took ~1.6 s for 15 files with two readers on the 2-core test
machine (files cached); the merge itself decodes every frame anyway.

## Not changed
Hot Folder Tether's trichrome grouping (`TripletGrouper`, three at a time as
they arrive, checked per frame) is unchanged.

## Tests (`tests/test_smart_triplets.py`)
Complete sets; an odd count; a folder starting mid-shot; a white frame; an
abandoned shot; another capture order; a retake (sharper, unclipped, later on
a tie or within 3 %); each frame scored once; the name-order fallback; the
backend merging one set of four files and reporting the fourth; the window
showing the entries after the load with no warning box; and on the user's RAFs
when present.

## Revision 2026-11-02: the light rule from the user's frames, and one picture per set

**Problem** (the user, with screenshots): real trichrome shots were flagged
"Not red, green, blue" (Hot Folder Tether) and, on Open, frames were merged
across two shots (overlapping pictures) with many sets rejected. Cause: on
dense Portra frames the green and blue frames' shares fell below
`LIGHT_SHARE_MIN` 0.60 (7 of the 51 frames sent: green 0.54–0.60, blue
0.55–0.60), so they read "white or mixed light"; a shot missing one frame
then shifted the walk, and with the best-scoring start (blue) a set could
take the blue of one shot with the red and green of the next.

**Measured** (shares off the RAW mosaic, every RAF the user has sent):

| frames | lit share |
| --- | --- |
| red, trichrome (51 + 31) | 0.77 – 0.85 |
| green, trichrome | 0.54 – 0.74 (the two densest frames 0.54, 0.55) |
| blue, trichrome | 0.55 – 0.70 |
| white light through a negative (39, two cameras) | largest 0.42 – 0.49 |

Why dense frames fall: the LED is not one wavelength, and a dense dye eats
the middle of its band, so the photosites of the other two colours see
relatively more of what gets through.

**Rule 1 — the share.** `LIGHT_SHARE_MIN` = **0.51**, halfway between the
highest white-light frame (0.49) and the lowest trichrome frame (0.54).

**Rule 2 — one picture per set.** Each frame also yields a small picture
signature from the same subsampled mosaic the shares come from (no second
read): every photosite divided by its colour's mean (under one LED the
mosaic is then one picture, whatever the colour), downsampled to 160 px on
the long side, log, the middle 60 % each way (rebates, sprocket holes and
the holder left out: they are in the same place on every shot and made
different 35 mm shots correlate at up to 0.77), Sobel gradient magnitude,
zero mean and unit norm. Two frames show the same picture when the
signatures correlate at ≥ `PICTURE_SAME` **0.35**. Measured on the user's
trichrome shots: same shot 0.45–0.99 (red against blue the lowest, 0.61–0.65
on Portra, 0.45 on one sparse frame); different shots ≤ 0.20 (trichrome),
≤ 0.24 between neighbouring white-light frames. A signature is only used
when the frame's two halves (alternate rows of the subsample) give the same
signature (correlation ≥ `PICTURE_RELIABLE` 0.5; measured 0.84–0.997);
otherwise the frame's picture is "can't tell" and sets are formed by colour
alone, as before.

**Rule 3 — a weak frame in a shot that is otherwise complete.** A frame
below 0.51 whose largest share is the one colour its shot is missing, at
least `RESCUE_SHARE_MIN` 0.40, and whose picture matches the other two frames
of the shot (both known the same, not "can't tell"), is taken as that colour.
A white-light frame of the same picture in the green slot would be taken too;
in a trichrome session that is not a shot anyone takes.

**Open (planner).** `plan_triplets` reads shares and the picture together
(`scan_type.read_light`). Before the walk, Rule 3 is applied
(`rescue_weak_frames`): the weak frame and its neighbours (two either side)
that show the same picture must be exactly one red, one green and one blue,
another weak frame counting by its largest share (so a shot whose green AND
blue are both weak is taken whole). In the walk, a frame whose picture is
known to differ from the shot being collected closes that shot first, so a
set never spans two shots, whichever start colour is tried.

**Measured on the 51 Portra RAFs** (reading and planning 12.5 s, two
readers): 17 sets, no notes, each one shot. As shipped (0.60, no picture):
13 sets, and shot 1's red offered as a "retake" in shot 2's set, which is
how the overlapping merges happened (the sharper red wins, from the other
shot). With the picture, even a 0.60 or 0.70 share rule gives the 17 sets
(the weak frames are taken by Rule 3). One frame removed: 16 sets and that
shot's other two reported as "Frames missing", for each of four frames tried;
the signature as built: same shot ≥ 0.63, other shots ≤ 0.20, reliability
≥ 0.976. In the tether check, 17 of 17 shots pass; a shot's red and green
with the next shot's blue, or the next shot's red with this shot's green
and blue, are held in 16 of 16 cases ("frame 3 (…) shows a different picture
from frames 1 and 2").

**Hot Folder Tether.** `judge_triplet(shares, names, pictures)`: each frame
must be lit by its slot's colour (Rule 1, or Rule 3 within the triplet), and
no two frames may be known to show different pictures ("frame 3 (…) shows a
different picture from frames 1 and 2"). Grouping three at a time is
unchanged (a missed frame still shifts the following triplets until the
30 s stall; listed in `spec/unfinished.md`).

**Tests:** the share table above as cases (dense green/blue pass, white
light fails); the signature on synthetic mosaics (same scene under three
"LEDs" matches, a shifted/other scene does not, a flat noisy frame is "can't
tell"); a set is never formed across a picture change, even when one frame is
weak; Rule 3 in the planner and the tether; on the user's RAFs when present:
17 sets from the 51 frames, no notes.
