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
