# The film base's Measure sets the roll's black point

The user, 2026-11-09, after hearing that frames shot with "Per shot, scale
exposure to the frame's density" clip their film base (so Convert can't read
it from their border) and fall back to a hand-sampled black point scaled by
exposure: "If you could build that - that could be great" — Capture's Measure
already reads the film base at known exposures, so it should give the roll's
black point itself.

## Goals
- A successful Measure (Capture → Film base → Measure… / Measure Again)
  also measures the black point Convert needs, in Convert's own units, and
  keeps it in the film base's entry with the exposure it was read at.
- That black point becomes the roll's Black Point (as if sampled by hand):
  straight after Measure, and when that film base is picked in Capture's list.
  Convert already scales a black point to each frame's exposure
  (`black_point_for`, spec/frame-exposure.md §5), so per-frame exposures
  convert correctly with no hand sampling.
- Never applied to a setup it wasn't measured on (another camera, camera
  profile or merge mode): it says so and leaves the black point alone.

Non-goals: measuring a black point for film bases saved before this (they
need Measure Again); changing how Convert picks a frame's own border first
(a frame whose border reads cleanly still uses it).

## Why decode the check shots, not convert the RAF reading
The film base reading Measure keeps (`base`) is a mosaic photosite level of
one colour; Convert's black point is a value of FreeCCR's decoded,
profiled, merged preview (`resized_raw`, B G R). Mapping one to the other
would mean modelling the decode (black level, scaling, the camera profile's
matrix, the merge's exposure normalisation): the codebase's rule is to
measure on the real pipeline instead (spec/white-balance-restore.md). So the
shots Measure accepted are decoded exactly as Open would decode them.

## Processing
- During Measure each shot's file is kept in the temp folder under its key
  (colour, shutter, ISO, LED levels) instead of being deleted at once.
- On success, the accepted shot of each colour (the key matching
  `cal.result[c]`) is taken: R, G, B for trichrome (a 3-way merge), or the
  one white-light shot. They are loaded as a `CCRImage` exactly as Open loads
  them (current camera profile and merge mode), on a thread.
- `core/base_black_point.measure(raw, rect)`: the patch rectangle Measure
  read (frame fractions; a RAW decodes as the sensor sees it, like the live
  view, so the same fractions apply), read the way Measure reads its own
  patch: the largest group of its 4 × 4 cells that agree
  (`be._agreeing_cells`, on the channel mean), so a patch touching a frame's
  edge still reads its base; the per-channel median of those pixels of
  `resized_raw` → (B, G, R). Refused (None, why) when clipped (median ≥ 0.98
  of full scale in a channel) or uneven (IQR/median > 0.08 in a channel).
- Exposure = `CCRBackend.image_exposure(img)` (a merge's shortest colour,
  which the merge normalises to).
- Stored as the entry's `black_point`: {"bgr": [b, g, r], "exposure": E,
  "setup": {"camera", "profile", "demosaic", "mono", "scan_type"}}.
  `be.normalise` keeps it; Measure Again replaces it (a new one or none);
  Edit and Rename keep it (it carries its own exposure, so changed exposures
  don't invalidate it).
- Usable when its setup equals the current one (`be.black_point_usable`):
  camera model (when both are known), camera profile file name, merge
  demosaic / mono, scan type.
- Temp folder removed after the decode (or at once when there is nothing to
  decode).

## Integration
- `LiveCaptureMonitor`: `_measure_next` remembers the shot's key; `_on_patch_read`
  keeps the file under it; `_measure_finish` on success starts
  `_measure_base_black_point(m, entry name)` (thread → `_base_bp_read`
  signal) before removing the temp folder; `_on_base_bp_read` stores it,
  saves, and calls `_apply_base_black_point(base, why)`.
- `_on_film_base_selected` → `_apply_base_black_point` for the newly picked
  base.
- `MainWindow.set_roll_black_point(bgr, exposure, note)`: `set_black_point`,
  `persist_bwpoint`, the panel's anchor label and a hint — the same as Set
  Black Point → Auto from This Frame's Border.
- Captions: "Black Point set from film base “name” (read at 1/2 s):
  frames convert from it at their own exposure." / "Film base “name” has no
  black point (measured before this): Measure Again to add one." / "…
  measured with another camera / profile / merge mode: Black Point left as it
  was."
- The film base summary line shows "· black point" when the entry has one.

## Tests (`tests/test_film_base_black_point.py`)
- `measure`: an even patch gives its medians (B G R); clipped and uneven
  patches are refused with a reason; the inset is applied.
- Entries: `normalise` keeps a valid `black_point`, drops a malformed one;
  `replace` drops the old one unless given a new one; `edit`/`rename` keep it;
  `black_point_usable` per setup field.
- Monitor (fake USB camera, fake light): Measure keeps each accepted shot,
  decodes them (decoder injected), stores the black point in the entry and
  sets the roll's black point with its exposure; a failed decode stores
  nothing and still removes the temp folder; picking a film base applies its
  black point; another setup is refused with the caption.
- Backend: `black_point_for` of a frame shot two stops longer is 4× the film
  base's black point (the existing scaling, pinned here for this route).

## Measured (2026-11-09)
On the user's R0001 shot 010 (GFX 50S II trichrome, loaded through
`load_shots` exactly as Measure now loads its shots), the patch read on the
left border gave B G R 56,007 / 41,866 / 62,060, against `find_rebate`'s
56,028 / 41,976 / 62,070 on the same preview: within 0.3 %. On shot 003 the
border is ~11 px wide in the preview and a 3 % square around it is refused as
uneven (104 %), as Measure's own check would have refused that patch.
