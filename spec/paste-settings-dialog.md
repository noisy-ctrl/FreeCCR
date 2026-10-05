# Copy / Paste Settings with a paste-time dialog

Supersedes the copy-time group dialog of `spec/copy-settings-dialog.md`.
Sync to All is unchanged.

## Goals

- **Copy Settings** and **Paste Settings…** in the thumbnail right-click menu,
  alongside the existing Cmd/Ctrl+C and Cmd/Ctrl+V.
- Copy takes a full snapshot with no dialog.
- Paste opens a dialog listing only what differs from default **on the copied
  image**, each with its own tick:
  - every changed slider individually, with its value;
  - Cineon Log → Workspace, Colour Profile (B&W), Curves;
  - Subtractive Saturations as one tick (all bands + feather);
  - Crop (size and angle), Rotation (90° steps), Flip horizontal,
    Flip vertical, Fine rotation.
- Pasting from the thumbnail menu applies to every selected thumbnail.

## UX

Dialog sections: Adjustments, Channel Levels, Channel Balance, Colour,
Geometry. Select All / Deselect All, Cancel / Paste. Rows un-ticked at the
last paste stay un-ticked while the app is open. If the copied image has no
changes from default, Paste says so instead of opening an empty dialog.

## Semantics

- Settings at their default on the copied image are not offered, so they never
  reset the target.
- Un-ticked items are left exactly as they are on each target.
- Flips paste as "flipped": they never toggle a target that is already flipped.
- **Fine rotation is skipped on cropped targets**: a crop holds its own
  straighten angle, and the app keeps the two from stacking (crop entry folds
  fine rotation into the crop angle). The hint reports the skip.
- The current image is pasted into its active layer (global or area, as
  before); other selected images into their global layer. Cineon is never
  written into an area layer. One undo step per image.
- Copy from the current image reads the live sliders (as before); copy from
  another thumbnail reads that image's global layer.

## Data model

`SlidersPanel.clipboard`: `adjustments` (every slider key), `curves`,
`cineon_log`, `profile`, `crop` `(rect, angle)`, `rotation`, `flip_h`,
`flip_v`, `fine_rotation`. `SlidersPanel._paste_unticked`: item ids.
`paste_options(clip, default_for)` is a pure function producing the dialog
rows `(section, item_id, label)`.

## Tests

`tests/test_paste_settings_dialog.py` (replaces
`tests/test_copy_settings_dialog.py`); the copy/paste cases in
`tests/test_orientation_sync_group.py` moved there.
